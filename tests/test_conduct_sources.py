"""Exercise read-only warehouse capture and pre-release evaluator contracts."""

import csv
import hashlib
import json
import os
from collections import namedtuple
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch

from telryn import complaints, packs, seed, transcripts, warehouse, weekly_report
from telryn.conduct_scorers import ComplaintScorers
from telryn.contracts import InputError


SOURCE = Path(__file__).resolve().parents[1] / "examples/agent_release"


class ConductScorers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = self.enterContext(TemporaryDirectory())
        self.scorers = ComplaintScorers(packs.load("firm-example@1.1.0", Path.cwd()))
        self.case = {
            "case_id": "case",
            "revision": "v3",
            "evidence_ref": "support/case/v3",
            "channel": "chat",
            "completed_at": "2026-10-06T10:00:00Z",
            "passages": [
                {"at": "2026-10-06T09:00:00Z", "text": "The firm charged me twice."},
                {"at": "2026-10-06T09:15:00Z", "text": "Open a complaint please."},
            ],
            "register_entries": [],
            "as_of": "2026-10-08T16:05:00Z",
            "complete_through": "2026-10-08T16:05:00Z",
        }
        self.candidate = {
            "complaint": True,
            "citation": {
                "evidence_ref": "support/case/v3",
                "revision": "v3",
                "passage": 2,
                "quote": "Open a complaint please.",
            },
        }
        self.expected = {
            "complaint": True,
            "trigger_passage": 1,
            "trigger_event": "firm_registration_trigger",
        }

    def test_agreement_and_missed_registration_are_separate_measures(self):
        result = self.scorers.evaluate(self.case, self.candidate, self.expected)
        self.assertTrue(result["rubric"]["passed"])
        self.assertEqual(
            result["deadline"],
            {"status": "missing", "passed": False, "due": "2026-10-07T16:00:00Z"},
        )
        self.case["complete_through"] = None
        self.assertIsNone(
            self.scorers.evaluate(self.case, self.candidate, self.expected)["deadline"][
                "passed"
            ]
        )
        self.candidate["citation"]["quote"] = "a different string"
        self.assertEqual(
            self.scorers.evaluate(self.case, self.candidate, self.expected)["rubric"][
                "status"
            ],
            "invalid_citation",
        )
        self.expected["trigger_event"] = "unverified-restart"
        with self.assertRaisesRegex(InputError, "trigger event"):
            self.scorers.evaluate(self.case, self.candidate, self.expected)

    def test_direct_platform_callbacks_use_expected_sdk_shapes(self):
        score = namedtuple("Score", "name score metadata")
        evaluation = namedtuple("Evaluation", "name value data_type metadata")
        with patch.dict(
            "sys.modules",
            {
                "braintrust": ModuleType("braintrust"),
                "langfuse": ModuleType("langfuse"),
            },
        ):
            import braintrust
            import langfuse

            braintrust.Score = score
            langfuse.Evaluation = evaluation
            self.case["complete_through"] = None
            braintrust_scores = self.scorers.braintrust(
                self.case, self.candidate, self.expected
            )
            langfuse_scores = self.scorers.langfuse(
                input=self.case, output=self.candidate, expected_output=self.expected
            )
            self.assertEqual(
                {x.name: x.score for x in braintrust_scores},
                {x.name: x.value for x in langfuse_scores},
            )
            self.assertNotIn(
                "telryn_registration_deadline", [r.name for r in braintrust_scores]
            )
            self.assertIn(
                "telryn_registration_deadline_determinate",
                [r.name for r in braintrust_scores if not r.score],
            )
            self.assertIn(
                "telryn_complaint_prediction",
                [r.name for r in langfuse_scores if r.value],
            )
            for item in braintrust_scores:
                self.assertNotIn("Open a complaint", json.dumps(item.metadata))

    def test_seed_replay_recomputes_recorded_result_and_ignores_task_receipts(self):
        suite = SOURCE / "suite.json"
        script = SOURCE / "bridge.py"
        with patch.dict(
            os.environ,
            {
                "TELRYN_SEED_DEMO_DB": str(Path(self.temp_dir) / "seed.db"),
                "TELRYN_DEMO_AGENT_REVISION": "demo-prompt-v2",
            },
        ):
            receipt = seed.run(suite, "study", script, script)
        self.assertTrue(seed.verify_receipt(suite, receipt)["passed"])
        revised = deepcopy(receipt)
        revised["cases"][0]["entries"] = []
        with self.assertRaisesRegex(InputError, "status"):
            seed.verify_receipt(suite, revised)
        revised = deepcopy(receipt)
        revised["cases"][0]["deadline"] = "2026-10-06T09:00:00Z"
        with self.assertRaisesRegex(InputError, "deadline"):
            seed.verify_receipt(suite, revised)
        scorer = ComplaintScorers(self.scorers.pack, seed_suite=suite)
        self.candidate["seed_receipt"] = receipt
        self.assertEqual(
            scorer.evaluate(self.case, self.candidate, self.expected)["seed"]["status"],
            "receipt_missing",
        )
        self.case["seed_receipt"] = receipt
        self.assertTrue(
            scorer.evaluate(self.case, self.candidate, self.expected)["seed"]["passed"]
        )


class Warehouse(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.plan = self.root / "warehouse.json"
        self.out = self.root / "snapshot"
        self.cfg = {
            "schema": 1,
            "provider": "snowflake",
            "scope": "Test payments",
            "scope_key": "payments-uk",
            "week": "2026-10-05",
            "snapshot_at": "2026-10-12T18:00:00Z",
            "max_rows": 50,
            "sources": {
                name: {
                    "table": f"credit-prod.audit.{name}",
                    "complete_through": "2026-10-12T18:00:00Z",
                }
                for name in warehouse.SOURCE_COLUMNS
            },
        }
        self.rows = {
            "conversations": [
                (
                    "C1",
                    "c3",
                    "support/C1/v3",
                    False,
                    "chat",
                    "human",
                    "payments",
                    "2026-10-06T10:00:00Z",
                    '[{"role":"customer","text":"I want a review of the second charge."}]',
                )
            ],
            "register": [("payment/C1", "R1", "2026-10-12T11:00:00Z", "ledger/R1")],
            "actions": [
                (
                    "C1",
                    "A1",
                    "accepted",
                    "Complaints ops",
                    "2026-10-12T09:00:00Z",
                    "tasks/A1",
                ),
                (
                    "C1",
                    "A2",
                    "completed",
                    "Complaints ops",
                    "2026-10-12T11:15:00Z",
                    "tasks/A2",
                ),
            ],
        }
        self.queries = []

    def capture(self, provider):
        self.cfg["provider"] = provider
        self.cfg.pop("max_bytes_billed", None)
        if provider == "bigquery":
            self.cfg["max_bytes_billed"] = 1_000_000
        for name, source in self.cfg["sources"].items():
            source["table"] = (
                f"{'credit-prod' if provider == 'bigquery' else 'audit_data'}.audit.{name}"
            )
        self.plan.write_text(json.dumps(self.cfg))
        return warehouse.capture(self.plan, self.out, reader=self.reader)

    def reader(self, query):
        self.queries.append(query)
        name = next(k for k in self.rows if f".{k}" in query)
        return "job-" + name, iter(self.rows[name])

    def test_all_three_generated_sql_contracts(self):
        for provider, clause in (
            ("snowflake", "AT (TIMESTAMP =>"),
            ("bigquery", "FOR SYSTEM_TIME AS OF TIMESTAMP"),
            ("databricks", "TIMESTAMP AS OF"),
        ):
            with self.subTest(provider):
                self.out = self.root / provider
                digest = self.capture(provider)
                receipt = warehouse.verify(self.out, digest)
                self.assertEqual(receipt["provider"], provider)
                self.assertTrue(
                    all(
                        query.startswith("SELECT ")
                        and clause in query
                        and "scope_key = 'payments-uk'" in query
                        for query in self.queries[-3:]
                    )
                )
                self.assertIn("audit_week = '2026-10-05'", self.queries[-3])
                for name, query in zip(self.rows, self.queries[-3:], strict=True):
                    self.assertEqual(
                        receipt["sources"][name]["sql_sha256"],
                        hashlib.sha256(query.encode()).hexdigest(),
                    )
                self.assertEqual(
                    transcripts.read_cases(self.out / "transcripts.jsonl")[0][
                        "revision"
                    ],
                    "c3",
                )
        (self.out / "actions.csv").write_text("modified")
        with self.assertRaisesRegex(InputError, "actions.csv differs"):
            warehouse.verify(self.out, digest)

    def test_warehouse_files_attach_to_a_real_signed_complaints_report(self):
        digest = self.capture("snowflake")
        evidence = warehouse.verify(self.out, digest)
        (self.out / "reviewed.csv").write_text("case_id\n")
        plan = self.out / "week.toml"
        plan.write_text("""
schema = 1
week = "2026-10-05"
rubric = "warehouse-audit-v1"
rubric_question = "Was the second charge a complaint?"
screen_version = "none"
population = "population.csv"
reviewed = "reviewed.csv"
flagged_sample = 0
unflagged_sample = 1
""")
        with patch.dict(os.environ, TELRYN_TRUST_DIR=str(self.root / "team-trust")):
            report = weekly_report.start(plan, self.root / "audit")
            packet = self.root / "audit/grades.csv"
            with packet.open(newline="", encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            rows[0].update(serious="yes", disposition="unknown", minutes="1")
            returned = self.root / "returned.csv"
            with returned.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            weekly_report.refresh(report, returned, reviewer="Independent reviewer")
            records = self.out / "records.json"
            records.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "audit_id": rows[0]["audit_id"],
                        "rubric": "warehouse-audit-v1",
                        "scope": "Test payments",
                        "cutoff": "2026-10-12T08:00:00Z",
                        "as_of": evidence["snapshot_at"],
                        **{
                            kind: evidence["sources"][kind]["complaint_source"]
                            for kind in ("register", "actions")
                        },
                        "cases": [
                            {
                                "case_id": "C1",
                                "revision": "c3",
                                "deadline": "2026-10-07T17:00:00Z",
                                "policy_ref": "approved clock worksheet",
                                "match_key": "payment/C1",
                            }
                        ],
                    }
                )
            )
            state = weekly_report.attach_complaints(report, records)
        issue = complaints.reconcile(state)["cases"][0]
        self.assertEqual(issue["registration_at_cutoff"], "missing")
        self.assertEqual(issue["owner_as_of"], "completed")
        self.assertEqual(issue["register_entries"][0]["entry_id"], "R1")

    def test_rejects_invalid_plan_duplicate_or_over_limit_without_publishing(self):
        self.cfg["max_rows"] = 1
        with self.assertRaisesRegex(InputError, "row limit"):
            self.capture("snowflake")
        self.assertFalse(self.out.exists())
        self.cfg["max_rows"] = 50
        self.rows["conversations"] *= 2
        with self.assertRaisesRegex(InputError, "duplicate frozen"):
            self.capture("databricks")
        self.assertFalse(self.out.exists())
        self.cfg["sources"]["actions"]["table"] = "database.audit.actions"
        self.cfg["sources"]["conversations"]["table"] = "database.audit.conversations"
        self.cfg["sources"]["register"]["table"] = "database.audit.register where true"
        self.plan.write_text(json.dumps(self.cfg))
        with self.assertRaisesRegex(InputError, "warehouse identifier"):
            warehouse.capture(self.plan, self.out, reader=self.reader)
        self.assertFalse(self.out.exists())
