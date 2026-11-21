"""Cloud extraction runs the same verified citation pipeline as local extraction."""

import io
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from telryn import model_endpoints, screen, weekly_report
from telryn.__main__ import main
from telryn.contracts import InputError, fingerprint
from tests.audit_support import isolate_trust


AZURE = {
    "schema": 1,
    "provider": "azure-openai",
    "resource": "example-bank",
    "model_revision": "gpt-4o-2024-08-06",
}
BEDROCK = {
    "schema": 1,
    "provider": "bedrock",
    "region": "us-east-1",
    "account_id": "123456789012",
}
MODEL = "amazon.nova-lite-v1:0"
ANSWERS = {
    "complaint": {"value": "yes", "passage": 1},
    "complaint_logged": {"value": "unknown", "passage": 0},
}


class EndpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.src = self.root / "export.jsonl"
        self.out = self.root / "screen"
        self.config = self.root / "endpoint.json"
        self.src.write_text(
            json.dumps(
                {
                    "case_id": "private-case",
                    "revision": "private-v1",
                    "evidence_ref": "private-location",
                    "messages": [
                        {
                            "role": "customer",
                            "text": "I am unhappy. Email customer@example.test",
                        }
                    ],
                }
            )
        )
        self.policy = self.root / "policy.txt"
        self.policy.write_text("Dissatisfaction requires human review.")
        self.env = patch.dict(
            os.environ, {"AZURE_OPENAI_API_KEY": "testing"}, clear=True
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_screen(self, cfg, model, args=()):
        self.config.write_text(json.dumps(cfg))
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(
                [
                    "screen",
                    str(self.src),
                    "--policy",
                    "unlogged-complaint",
                    "--policy-text",
                    str(self.policy),
                    "--out",
                    str(self.out),
                    "--model",
                    model,
                    "--model-endpoint",
                    str(self.config),
                    *args,
                ]
            )
        self.assertEqual(status, 0)
        return json.loads(output.getvalue())

    @staticmethod
    def azure_response(*, revision=AZURE["model_revision"], facts=ANSWERS, stop="stop"):
        return {
            "model": revision,
            "choices": [
                {
                    "finish_reason": stop,
                    "message": {"role": "assistant", "content": json.dumps(facts)},
                }
            ],
        }

    def assert_redacted(self, sent):
        for value in (
            "private-case",
            "private-v1",
            "private-location",
            "customer@example.test",
        ):
            self.assertNotIn(value, sent)
        self.assertIn("[EMAIL]", sent)

    def test_azure_cli_request_receipt_and_checkpoint(self):
        with patch("http.client.HTTPSConnection") as https:
            conn = https.return_value
            res = conn.getresponse.return_value
            res.status = 200
            res.read.return_value = json.dumps(self.azure_response()).encode()
            r = self.run_screen(AZURE, "complaint-extractor")
            https.assert_called_once_with("example-bank.openai.azure.com", timeout=120)
            method, url = conn.request.call_args.args
            self.assertEqual((method, url), ("POST", "/openai/v1/chat/completions"))
            kw = conn.request.call_args.kwargs
            self.assertEqual(kw["headers"]["api-key"], "testing")
            self.assert_redacted(kw["body"])
            data = json.loads(kw["body"])
            self.assertEqual(data["model"], "complaint-extractor")
            self.assertEqual(
                data["response_format"]["json_schema"]["schema"]["required"],
                list(ANSWERS),
            )
            self.assertEqual(
                r["model_endpoint"]["model_revision"], AZURE["model_revision"]
            )
            self.assertTrue(r["screen_version"].startswith("cloud-model:"))
            self.assertNotIn("testing", json.dumps(r))
            citation = json.loads((self.out / "citations.jsonl").read_text())[
                "citations"
            ]["complaint"][0]
            self.assertEqual(
                (citation["message"], citation["quote"]),
                (1, "I am unhappy. Email [EMAIL]"),
            )
            self.assertIn("true", (self.out / "population.csv").read_text())
            shutil.rmtree(self.out)
            again = self.run_screen(AZURE, "complaint-extractor")
            self.assertEqual(again, r)
            conn.request.assert_called_once()
            shutil.rmtree(self.out)
            self.config.write_text(json.dumps({**AZURE, "resource": "other-bank"}))
            with self.assertRaisesRegex(InputError, "different"):
                screen.screen(
                    self.src,
                    self.out,
                    "complaint-extractor",
                    "unlogged-complaint",
                    model_endpoint=self.config,
                    policy_text=self.policy,
                )
            conn.request.assert_called_once()

    def test_weekly_cli_requires_and_binds_cloud_screen_receipt(self):
        isolate_trust(self, self.root)
        with patch("http.client.HTTPSConnection") as https:
            res = https.return_value.getresponse.return_value
            res.status = 200
            res.read.return_value = json.dumps(self.azure_response()).encode()
            receipt = self.run_screen(AZURE, "deployment")
        (self.root / "reviewed.csv").write_text("case_id\n")
        plan = self.root / "week.toml"
        plan.write_text(
            'schema=1\nweek="2026-10-05"\nrubric="complaints-v1"\nrubric_question="Is this a complaint?"\n'
            'population="screen/population.csv"\nreviewed="reviewed.csv"\nflagged_sample=1\nunflagged_sample=0\n'
            + f'screen_version="{receipt["screen_version"]}"\n'
        )
        weekly = self.root / "weekly"
        with self.assertRaisesRegex(InputError, "screen_receipt"):
            weekly_report.start(plan, weekly)
        with plan.open("a") as stream:
            stream.write('screen_receipt="screen/receipt.json"\n')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["weekly", str(plan), "--out", str(weekly)]), 0)
        state = weekly_report.read_state(weekly / "report.html")
        self.assertEqual(state["screen_receipt_digest"], fingerprint(receipt))
        self.assertEqual([row["case_id"] for row in state["cases"]], ["private-case"])

    def test_wrong_revision_truncation_and_bad_citation_rejected(self):
        self.config.write_text(json.dumps(AZURE))
        with patch("http.client.HTTPSConnection") as https:
            res = https.return_value.getresponse.return_value
            res.status = 200
            for invalid in (
                self.azure_response(revision="earlier"),
                self.azure_response(stop="length"),
                self.azure_response(
                    facts={
                        **ANSWERS,
                        "complaint_logged": {"value": "yes", "passage": 1},
                    }
                ),
            ):
                with self.subTest(invalid=invalid):
                    res.read.return_value = json.dumps(invalid).encode()
                    with self.assertRaises(InputError):
                        screen.screen(
                            self.src,
                            self.out,
                            "deployment",
                            "unlogged-complaint",
                            model_endpoint=self.config,
                        )
                    self.assertFalse(self.out.exists())

    def test_bedrock_scope_and_redacted_converse(self):
        sts, runtime = Mock(), Mock()
        with patch.object(
            model_endpoints, "_aws_clients", return_value=(sts, runtime, (OSError,))
        ):
            self.config.write_text(json.dumps(BEDROCK))
            sts.get_caller_identity.return_value = {"Account": "999999999999"}
            with self.assertRaisesRegex(InputError, "different account"):
                screen.screen(
                    self.src,
                    self.out,
                    MODEL,
                    "unlogged-complaint",
                    model_endpoint=self.config,
                )
            runtime.converse.assert_not_called()
            sts.get_caller_identity.return_value = {"Account": BEDROCK["account_id"]}
            runtime.converse.return_value = {
                "stopReason": "end_turn",
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [{"text": json.dumps(ANSWERS)}],
                    }
                },
            }
            r = self.run_screen(BEDROCK, MODEL)
            kw = runtime.converse.call_args.kwargs
            self.assertEqual(
                kw["modelId"], "arn:aws:bedrock:us-east-1::foundation-model/" + MODEL
            )
            self.assert_redacted(json.dumps(kw))
            self.assertEqual(r["model_endpoint"]["account_id"], BEDROCK["account_id"])
            self.assertGreaterEqual(sts.get_caller_identity.call_count, 3)
            self.assertEqual(
                json.loads((self.out / "citations.jsonl").read_text())["signals"][
                    "complaint_logged"
                ],
                "unknown",
            )

    def test_endpoint_files_are_data_and_csv_never_contacts_a_provider(self):
        with (
            patch("http.client.HTTPSConnection") as https,
            patch.object(model_endpoints, "_aws_clients") as aws,
        ):
            for cfg in (
                {**AZURE, "resource": "example.com@other.test"},
                {**AZURE, "url": "https://elsewhere.invalid"},
                {**AZURE, "model_revision": "current"},
                {**BEDROCK, "region": "elsewhere.invalid"},
                {**BEDROCK, "account_id": "unknown"},
            ):
                self.config.write_text(json.dumps(cfg))
                with self.assertRaises(InputError):
                    model_endpoints.load(self.config, MODEL)
            with self.assertRaisesRegex(InputError, "transcript extraction"):
                screen.screen(
                    self.src.with_suffix(".csv"),
                    self.out,
                    MODEL,
                    "unlogged-complaint",
                    model_endpoint=self.config,
                )
            https.assert_not_called()
            aws.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec("boto3"), "optional Bedrock SDK")
class BedrockWire(unittest.TestCase):
    def test_actual_sdk_emits_scoped_signed_requests(self):
        from botocore.awsrequest import AWSResponse

        calls = []

        class Raw:
            def __init__(self, data):
                self.data = data

            def stream(self):
                yield self.data

        def respond(request):
            calls.append(request)
            self.assertTrue(
                request.headers["Authorization"].startswith(b"AWS4-HMAC-SHA256 ")
            )
            if request.url == "https://sts.us-east-1.amazonaws.com/":
                raw = Raw(
                    b"<GetCallerIdentityResponse><GetCallerIdentityResult><Account>123456789012</Account></GetCallerIdentityResult></GetCallerIdentityResponse>"
                )
                return AWSResponse(request.url, 200, {"content-type": "text/xml"}, raw)
            self.assertTrue(
                request.url.startswith(
                    "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
                )
            )
            self.assertTrue(request.url.endswith("/converse"))
            self.assertIn("foundation-model", request.url)
            v = json.loads(request.body)
            self.assertEqual(v["messages"][0]["role"], "user")
            self.assertEqual(
                json.loads(v["messages"][0]["content"][0]["text"]),
                [{"text": "redacted"}],
            )
            payload = {
                "stopReason": "end_turn",
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [{"text": json.dumps(ANSWERS)}],
                    }
                },
            }
            return AWSResponse(
                request.url,
                200,
                {"content-type": "application/json"},
                Raw(json.dumps(payload).encode()),
            )

        env = {
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_ENDPOINT_URL": "https://wrong.invalid/",
            "AWS_BEARER_TOKEN_BEDROCK": "test",
            "AWS_CONFIG_FILE": os.devnull,
            "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("botocore.httpsession.URLLib3Session.send", side_effect=respond),
        ):
            backend = model_endpoints.Bedrock(BEDROCK, MODEL)
            backend.verify()
            self.assertEqual(
                json.loads(backend.complete("policy", [{"text": "redacted"}], {})),
                ANSWERS,
            )
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
