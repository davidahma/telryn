"""Exercise signed series, group protections and both reference adapter contracts."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import io
import hashlib
import json
import os
import shutil

from examples.complaints_pilot import qa_passes
from telryn import oversight, seed_adapters, vulnerability
from telryn.contracts import InputError

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class Oversight(TestCase):
    def test_weekly_native_decision_settings_must_match(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "audit"
            demo = qa_passes.run(base, isolated=True)
            paths = [Path(w["report"]) for w in demo["weeks"]]
            with patch.dict(os.environ, TELRYN_TRUST_DIR=str(base / "trust")):
                states = [oversight.weekly_report.read_state(p) for p in paths]
                for left, right in (
                    (
                        {"format": "maestroqa", "pass_percent": 80},
                        {"format": "maestroqa", "pass_percent": 90},
                    ),
                    (
                        {"format": "langfuse-v2", "complaint_evaluator": "c1"},
                        {"format": "langfuse-v2", "complaint_evaluator": "c2"},
                    ),
                ):
                    for state, settings in zip(states, (left, right), strict=True):
                        state["score_audit"]["native"] = dict(
                            settings, attest_revisions=True
                        )
                    with patch.object(
                        oversight.weekly_report, "read_state", side_effect=states
                    ):
                        with self.assertRaisesRegex(
                            InputError, "native decision settings"
                        ):
                            oversight.compile_series(
                                paths, "2026-Q4", require_scores=True
                            )

    def test_board_vendor_and_benchmark_from_real_signed_weeks(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "audit"
            demo = qa_passes.run(base, isolated=True)
            paths = [Path(w["report"]) for w in demo["weeks"]]
            with patch.dict(os.environ, TELRYN_TRUST_DIR=str(base / "trust")):
                out = oversight.compile_series(
                    paths[::-1], "2026-Q4", require_scores=True
                )
                self.assertEqual(len(out["missing_weeks"]), 11)
                self.assertEqual(
                    [
                        r["metrics"]["passed_deadline_miss"]["confirmed"]
                        for r in out["weeks"]
                    ],
                    [3, 3],
                )
                self.assertAlmostEqual(
                    out["weeks"][0]["agreement_rate_bounds"][0], 2 / 3
                )
                self.assertAlmostEqual(
                    out["weeks"][1]["disagreement_change_bounds"][0], 1 / 7 - 3 / 9
                )
                for form in ("board", "vendor"):
                    page = oversight.html(out, form)
                    self.assertNotIn("C102", page)
                    self.assertIn("2026-10-05", page)
                    self.assertIn(
                        "Different clock policy, deadline source or cutoff lag", page
                    )
                    self.assertIn("Issuer-supplied deadlines", page)
                    self.assertIn("Determinate grades / assigned", page)
                public = oversight.benchmark(out, "vendor", "v1", 5)
                self.assertNotIn(out["scope"]["queue"], json.dumps(public))
                self.assertTrue(
                    public["weeks"][0]["metrics"]["passed_deadline_miss"]["suppressed"]
                )
                with self.assertRaises(InputError):
                    oversight.compile_series(
                        [paths[0], paths[0]], "2026-Q4", require_scores=False
                    )
                with self.assertRaises(InputError):
                    oversight.compile_series(paths, "2026-Q3", require_scores=True)
                with self.assertRaises(InputError):
                    oversight.benchmark(out, "customer@example.org", "v1", 5)
                damaged = Path(tmp) / "damaged.html"
                damaged.write_bytes(paths[0].read_bytes() + b"damage")
                with self.assertRaises(InputError):
                    oversight.compile_series([damaged], "2026-Q4", require_scores=True)
                from telryn import policy, weekly_report

                revised = policy.load(EXAMPLES / "complaints_pilot/clock-policy.json")
                revised["version"] += ".revised"
                revised["clauses"]["registration"]["count"] += 1
                weekly_report._update(
                    paths[0], lambda state: policy.mark(state, revised)
                )
                for score_required in (True, False):
                    with self.assertRaisesRegex(InputError, "policy re-review"):
                        oversight.compile_series(
                            paths, "2026-Q4", require_scores=score_required
                        )

    def test_vulnerability_source_join_partial_coverage_and_small_cells(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "cohort"
            shutil.copytree(EXAMPLES / "vulnerability", base)
            path = base / "manifest.json"
            result = vulnerability.assessment(path)
            self.assertEqual(
                [g["rate_bounds"] for g in result["groups"]], [[0.5, 0.5], [0, 0]]
            )
            self.assertEqual(result["differences"][0]["difference_bounds"], [0.5, 0.5])
            self.assertNotIn("letter", json.dumps(result))
            doc = json.loads(path.read_text())
            doc["recognition_complete_through"] = None
            path.write_text(json.dumps(doc))
            self.assertEqual(
                vulnerability.assessment(path)["groups"][0]["rate_bounds"], [0, 0.5]
            )
            review = base / doc["reviews"]["file"]
            data = json.loads(review.read_text())
            data[0]["passage"] = None
            review.write_text(json.dumps(data))
            with self.assertRaisesRegex(InputError, "digest"):
                vulnerability.assessment(path)
            doc["reviews"]["sha256"] = hashlib.sha256(review.read_bytes()).hexdigest()
            path.write_text(json.dumps(doc))
            with self.assertRaisesRegex(InputError, "cited"):
                vulnerability.assessment(path)

    def test_later_disclosure_and_earlier_or_unrelated_register_entry(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "cohort"
            shutil.copytree(EXAMPLES / "vulnerability", base)
            path = base / "manifest.json"
            doc = json.loads(path.read_text())

            def revise(kind, rows):
                source = base / doc[kind]["file"]
                source.write_text(json.dumps(rows))
                doc[kind]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                path.write_text(json.dumps(doc))

            ledger = json.loads((base / "recognition.json").read_bytes())
            for row in ledger:
                row["recorded_at"] = "2026-10-05T09:00:00Z"
            revise("recognition", ledger)
            phone = next(
                row
                for row in vulnerability.assessment(path)["groups"]
                if row["group"] == "phone"
            )
            self.assertEqual(phone["rate_bounds"], [0, 1])
            for row in ledger:
                row["need_code"] = "other-need"
            revise("recognition", ledger)
            phone = next(
                row
                for row in vulnerability.assessment(path)["groups"]
                if row["group"] == "phone"
            )
            self.assertEqual(phone["rate_bounds"], [1, 1])
            cohort = json.loads((base / "population.json").read_text())
            cohort[0]["passages"][0]["at"] = "2026-10-08T12:00:00Z"
            revise("population", cohort)
            with self.assertRaisesRegex(InputError, "postdates"):
                vulnerability.assessment(path)


class SeedAdapters(TestCase):
    scope = {
        "workspace": "sandbox-one",
        "agent_revision": "v3",
        "policy_revision": "1",
        "handoff_revision": "r1",
    }

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.req = {
            "scope": self.scope,
            "environment": "test",
            "schema": 1,
            "run_id": "R1",
            "suite_digest": "suite",
        }
        self.manifest = {"schema": 1, "environment": "test", "scope": self.scope}
        self.path = self.base / "test.json"

    def test_csv_hash_and_coverage(self):
        raw = b"marker,conversation_id,entry_id,recorded_at\nmark,123,reg,2026-01-02T12:00:00Z\n"
        csv = self.base / "register.csv"
        csv.write_bytes(raw)
        self.path.write_text(
            json.dumps(
                {
                    **self.manifest,
                    "file": "register.csv",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "complete_through": None,
                    "source_ref": "register/v1",
                    "as_of": "2026-01-02T12:01:00Z",
                }
            )
        )
        with patch.dict(os.environ, TELRYN_SEED_REGISTER_MANIFEST=str(self.path)):
            result = seed_adapters.exchange(
                "csv-register",
                {**self.req, "operation": "read_register", "markers": ["mark"]},
            )
            self.assertIsNone(result["complete_through"])
            self.assertEqual(result["entries"][0]["conversation_id"], "123")
            self.assertEqual(
                seed_adapters.exchange(
                    "csv-register",
                    {**self.req, "operation": "identify", "role": "register"},
                )["role"],
                "register",
            )
            csv.write_bytes(raw + b"\n")
            with self.assertRaisesRegex(InputError, "digest"):
                seed_adapters.exchange(
                    "csv-register",
                    {**self.req, "operation": "identify", "role": "register"},
                )
            with self.assertRaisesRegex(InputError, "digest"):
                seed_adapters.exchange(
                    "csv-register",
                    {**self.req, "operation": "read_register", "markers": ["mark"]},
                )

    def test_sandbox_identity_and_idempotent_submission(self):
        config = {"environment": "test", "scope": self.scope, "test_requester_id": 12}
        self.path.write_text(
            json.dumps(
                {
                    **self.manifest,
                    "account_id": 78,
                    "test_requester_id": 12,
                    "integration": "support",
                }
            )
        )
        docs = {
            "configs": [
                {
                    "scope": "telryn-release",
                    "integration": "support",
                    "config": config,
                    "zendesk_account_id": 78,
                }
            ],
            "meta": {"has_more": False},
        }
        calls = []

        class WireReply(io.BytesIO):
            def __init__(self, value, status=200):
                super().__init__(json.dumps(value).encode())
                self.status = status
                self.headers = {}

        class SandboxOpener:
            def open(self, request, timeout):
                assert timeout == 30
                if (
                    request.full_url
                    == "https://sandbox-one.zendesk.com/api/services/zis/integrations/support/configs?filter%5Bscope%5D=telryn-release"
                ):
                    assert request.get_header("Authorization") == "Bearer test-zis"
                    return WireReply(docs)
                assert (
                    request.full_url
                    == "https://sandbox-one.zendesk.com/api/v2/tickets.json"
                )
                assert request.method == "POST"
                assert request.get_header("Authorization") == "Bearer test-tickets"
                body = json.loads(request.data)["ticket"]
                assert body["external_id"] == request.get_header("Idempotency-key")
                assert body["submitter_id"] == body["requester_id"] == 12
                calls.append(body)
                return WireReply(
                    {
                        "ticket": {
                            **body,
                            "id": 456,
                            "created_at": "2026-01-02T12:00:00Z",
                        }
                    },
                    201,
                )

        submit = {
            **self.req,
            "operation": "submit",
            "marker": "1" * 64,
            "phrase": "Synthetic request",
        }
        with (
            patch.dict(
                os.environ,
                TELRYN_SEED_ZENDESK_MANIFEST=str(self.path),
                TELRYN_SEED_ZIS_TOKEN="test-zis",
                TELRYN_ZENDESK_TOKEN="test-tickets",
                TELRYN_ZENDESK_SUBDOMAIN="sandbox-one",
            ),
            patch(
                "telryn.integrations_http.build_opener", return_value=SandboxOpener()
            ),
        ):
            docs["configs"][0]["zendesk_account_id"] = 99
            with self.assertRaisesRegex(InputError, "deployment"):
                seed_adapters.exchange("zendesk-sandbox", submit)
            self.assertEqual(calls, [])
            docs["configs"][0]["zendesk_account_id"] = 78
            first = seed_adapters.exchange("zendesk-sandbox", submit)
            self.assertEqual(first, seed_adapters.exchange("zendesk-sandbox", submit))
            with self.assertRaisesRegex(InputError, "run ID"):
                seed_adapters.exchange(
                    "zendesk-sandbox", {**submit, "suite_digest": "changed"}
                )
            self.assertEqual(len(calls), 1)
