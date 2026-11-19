"""Transcript extraction, validated citations, private resume and deterministic routing."""

import csv
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from telryn import screen, transcripts
from telryn.contracts import InputError


class Extraction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "transcripts.jsonl"
        self.model = "fixture:local"
        self.out = self.root / "extract"
        self.cache = self.root / "cache"
        self.cases = [
            {
                "case_id": "sensitive-" + i,
                "revision": "v1",
                "evidence_ref": "secret-location-" + i,
                "messages": [{"role": "customer", "text": text}],
            }
            for i, text in [
                ("1", "Someone accessed my account. customer@example.test"),
                ("2", "The fraud desk opened an escalation."),
                ("3", "I need a receipt."),
            ]
        ]
        self.write_cases()
        self.calls = []
        self.install = patch.object(screen, "_installed", return_value="a" * 64)
        self.install.start()
        self.addCleanup(self.install.stop)

    def write_cases(self):
        self.source.write_text(
            "\n".join(map(json.dumps, self.cases)) + "\n", encoding="utf-8"
        )

    def answer(self, path, data):
        self.assertEqual(path, "/api/chat")
        sent = data["messages"][1]["content"]
        self.assertNotIn("secret-location", sent)
        self.assertNotIn("sensitive-", sent)
        self.assertNotIn("@example.test", sent)
        passage = json.loads(sent)[0]
        self.calls.append(passage["text"])
        vals = {
            k: {"value": "unknown", "passage": 0}
            for k in transcripts.RELEVANT["account-compromise"]
        }
        if "Someone" in passage["text"]:
            for k in ("account_compromise", "escalation_required"):
                vals[k] = {"value": "yes", "passage": passage["passage"]}
        return {
            "model": self.model,
            "done": True,
            "done_reason": "stop",
            "message": {"content": json.dumps(vals)},
        }

    def extract(self, workers=1):
        return screen.screen(
            self.source,
            self.out,
            self.model,
            "account-compromise",
            workers=workers,
            checkpoint=self.cache,
        )

    def test_resume_redaction_policy_and_private_citations(self):
        def fail(path, data):
            if "fraud desk" in data["messages"][1]["content"]:
                raise InputError("fixture interruption")
            return self.answer(path, data)

        with (
            patch.object(screen, "_api", side_effect=fail),
            self.assertRaisesRegex(InputError, "interruption"),
        ):
            self.extract()
        self.assertFalse(self.out.exists())
        self.assertEqual(len(self.calls), 1)
        with patch.object(screen, "_api", side_effect=self.answer):
            receipt = self.extract()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(receipt["cases"], 3)
        with (self.out / "population.csv").open() as stream:
            self.assertEqual(
                [r["screen_flag"] for r in csv.DictReader(stream)],
                ["true", "false", "false"],
            )
        cites = [
            json.loads(x)
            for x in (self.out / "citations.jsonl").read_text().splitlines()
        ]
        quote = cites[0]["citations"]["account_compromise"][0]
        self.assertEqual(quote["message"], 1)
        self.assertEqual(quote["quote"], "Someone accessed my account. [EMAIL]")
        self.assertEqual(cites[0]["signals"]["escalated"], "unknown")
        self.assertEqual((self.out / "citations.jsonl").stat().st_mode & 0o777, 0o600)

    def test_different_input_cannot_reuse_checkpoint(self):
        with (
            patch.object(screen, "_api", side_effect=ValueError("interrupted")),
            self.assertRaises(ValueError),
        ):
            self.extract()
        self.cases[0]["messages"][0]["text"] += " now"
        self.write_cases()
        with (
            patch.object(screen, "_api", side_effect=AssertionError),
            self.assertRaisesRegex(InputError, "different"),
        ):
            self.extract()

    def test_parallel_workers_and_citation_validation(self):
        barrier = threading.Barrier(2)

        def run(path, data):
            if "receipt." not in data["messages"][1]["content"]:
                barrier.wait(timeout=4)
            return self.answer(path, data)

        with patch.object(screen, "_api", side_effect=run):
            self.extract(2)
        self.assertEqual(len(self.calls), 3)
        raw = {"value": "yes", "passage": 999}
        result = self.answer(
            "/api/chat",
            {"messages": [{}, {"content": '[{"text":"none","passage":1}]'}]},
        )
        facts = json.loads(result["message"]["content"])
        facts["escalated"] = raw
        result["message"]["content"] = json.dumps(facts)
        for invalid in [
            result,
            dict(result, model="foreign:tag"),
            dict(result, done_reason="length"),
            dict(result, message={"content": '{"x":1,"x":2}'}),
            dict(result, message={"content": {}}),
        ]:
            with (
                self.subTest(response=invalid),
                patch.object(screen, "_api", return_value=invalid),
                self.assertRaises(InputError),
            ):
                transcripts.extract_case(
                    self.cases[0],
                    self.model,
                    "account-compromise",
                    *transcripts.recipe("account-compromise", "policy"),
                    [],
                )

    def test_long_inputs_are_split_with_numbered_source_spans(self):
        msg = {"role": "agent", "text": "This passage has evidence. " * 650}
        evidence = transcripts.passages([msg], [])
        groups = list(transcripts.chunks(evidence))
        self.assertGreater(len(groups), 1)
        self.assertEqual([p for group in groups for p in group], evidence)
        self.assertEqual("".join(p["quote"] for p in evidence), msg["text"])
        for p in evidence:
            self.assertEqual(p["quote"], msg["text"][p["start"] : p["end"]])

    def test_explicit_fact_table_and_custom_redaction(self):
        vals = dict.fromkeys(screen.SIGNALS, "unknown")
        for policy in screen.POLICIES:
            self.assertFalse(screen.decide(vals, policy))
        vals.update(account_compromise="yes", escalation_required="yes")
        self.assertTrue(screen.decide(vals, "account-compromise"))
        vals["escalated"] = "yes"
        self.assertFalse(screen.decide(vals, "account-compromise"))
        self.assertEqual(
            transcripts.passages(
                [{"role": "customer", "text": "I am Jane Doe."}], ["Jane Doe"]
            )[0]["quote"],
            "I am [REDACTED].",
        )

    def test_local_model_and_bad_source_fail_closed(self):
        with self.assertRaisesRegex(InputError, "--model"):
            screen.screen(self.source, self.out, None, "account-compromise")
        with self.assertRaisesRegex(InputError, "local model"):
            self.install.stop()
            screen._installed("provider:cloud")
        self.cases.append(self.cases[0])
        self.write_cases()
        with self.assertRaisesRegex(InputError, "duplicate"):
            transcripts.read_cases(self.source)
