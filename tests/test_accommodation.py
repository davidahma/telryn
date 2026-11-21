"""Accommodations need independent register and delivery evidence."""

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from telryn.contracts import InputError
from telryn.outcomes import LEDGER, accommodation

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "accommodation"


class Accommodation(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.dir = Path(root.name) / "evidence"
        shutil.copytree(EXAMPLE, self.dir)
        self.path = self.dir / "manifest.json"
        self.manifest = json.loads(self.path.read_text())

    def check(self):
        self.path.write_text(json.dumps(self.manifest))
        return accommodation(self.path)

    def change_delivery(self, alter):
        path = self.dir / "delivery.csv"
        with path.open(newline="") as src:
            rows = list(csv.DictReader(src))
        with path.open("w", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=LEDGER)
            writer.writeheader()
            writer.writerows(alter(rows))

    def test_request_and_agreement_to_separate_system_records(self):
        result = self.check()
        self.assertEqual(
            (
                result["case_id"],
                result["revision"],
                result["request"]["passage"],
                result["agreement"]["passage"],
            ),
            ("AC-17", "v2", 1, 2),
        )
        register, delivery = (
            result["milestones"]["register"],
            result["milestones"]["delivery"],
        )
        self.assertEqual(
            (register["at_cutoff"], register["at_follow_up"]), ("on_time", "on_time")
        )
        self.assertEqual(
            (delivery["at_cutoff"], delivery["at_follow_up"]), ("missing", "late")
        )
        self.assertEqual(
            [(r["status"], r["event_id"]) for r in delivery["events"]],
            [("sent", "SEND-17"), ("delivered", "PROOF-17")],
        )
        self.assertEqual(delivery["events"][1]["actor_type"], "human")
        self.assertEqual(delivery["events"][1]["actor"], "Accessible formats team")
        self.assertEqual(self.check(), result)

    def test_cutoff_and_agreed_deadline_are_inclusive(self):
        self.change_delivery(
            lambda rows: [dict(rows[1], recorded_at="2026-10-09T12:00:00-05:00")]
        )
        self.assertEqual(self.check()["milestones"]["delivery"]["at_cutoff"], "on_time")
        self.change_delivery(
            lambda rows: [dict(rows[0], recorded_at="2026-10-09T17:00:00.000000001Z")]
        )
        stage = self.check()["milestones"]["delivery"]
        self.assertEqual(
            (stage["at_cutoff"], stage["at_follow_up"]), ("missing", "late")
        )

    def test_incomplete_proof_pending_and_other_adjustments(self):
        self.manifest["delivery"]["complete_through"] = None
        stage = self.check()["milestones"]["delivery"]
        self.assertEqual(
            (stage["at_cutoff"], stage["at_follow_up"]), ("unresolved", "unresolved")
        )
        self.assertEqual(
            stage["events"][1]["evidence_ref"], "proof-of-delivery/PROOF-17/v1"
        )
        self.change_delivery(
            lambda rows: [dict(rows[1], recorded_at=self.manifest["cutoff"])]
        )
        self.assertEqual(self.check()["milestones"]["delivery"]["at_cutoff"], "on_time")
        self.manifest["delivery"]["complete_through"] = self.manifest["as_of"]
        self.change_delivery(
            lambda rows: [dict(rows[0], accommodation="standard-statement")]
        )
        self.assertEqual(self.check()["milestones"]["delivery"]["at_cutoff"], "missing")
        self.manifest["cutoff"] = "2026-10-06T00:00:00Z"
        self.assertEqual(self.check()["milestones"]["delivery"]["at_cutoff"], "not_due")

    def test_missing_ambiguous_or_unrelated_identity(self):
        self.change_delivery(lambda rows: [rows[0], {**rows[1], "case_id": "ANOTHER"}])
        stage = self.check()["milestones"]["delivery"]
        self.assertEqual(
            (stage["at_cutoff"], stage["at_follow_up"]), ("missing", "unresolved")
        )
        self.manifest["review"]["request_id"] = None
        self.assertEqual(
            self.check()["milestones"]["register"]["at_cutoff"], "unresolved"
        )
        self.manifest["review"]["request_id"] = "ADJ-17"
        self.change_delivery(
            lambda rows: [
                {**rows[1], "case_id": "AC-17", "recorded_at": "2026-09-01T00:00:00Z"}
            ]
        )
        self.assertEqual(
            self.check()["milestones"]["delivery"]["at_follow_up"], "unresolved"
        )

    def test_no_request_and_insufficient_evidence(self):
        self.manifest["review"]["required"] = False
        self.assertEqual(
            self.check()["milestones"]["delivery"]["at_cutoff"], "outside_rubric"
        )
        self.manifest["review"]["required"] = None
        self.assertEqual(
            self.check()["milestones"]["delivery"]["at_cutoff"], "unresolved"
        )
        self.manifest["review"].update(
            required=True, deadline=None, agreement_passage=None
        )
        self.assertEqual(
            self.check()["milestones"]["register"]["at_cutoff"], "unresolved"
        )
        self.manifest["review"]["request_passage"] = None
        with self.assertRaisesRegex(InputError, "cited customer request"):
            self.check()

    def test_customer_request_and_support_agreement_require_distinct_source_roles(self):
        self.manifest["review"]["agreement_passage"] = 1
        with self.assertRaisesRegex(InputError, "separate passages"):
            self.check()
        self.manifest["review"]["agreement_passage"] = 2
        path = self.dir / "chat.json"
        chat = json.loads(path.read_text())
        chat["passages"][1]["speaker_role"] = "customer"
        path.write_text(json.dumps(chat))
        with self.assertRaisesRegex(InputError, "support responder"):
            self.check()
        chat["passages"][1]["speaker_role"] = "human_support"
        path.write_text(json.dumps(chat))
        self.assertEqual(self.check()["agreement"]["speaker_role"], "human_support")
        chat["passages"][0]["speaker_role"] = "ai_support"
        path.write_text(json.dumps(chat))
        with self.assertRaisesRegex(InputError, "request must cite the customer"):
            self.check()

    def test_mismatched_revisions_impossible_times_and_nonlocal_paths(self):
        self.manifest["review"]["revision"] = "wrong"
        with self.assertRaisesRegex(InputError, "does not match"):
            self.check()
        self.manifest["review"]["revision"] = "v2"
        self.manifest["review"]["deadline"] = "2026-10-04T00:00:00Z"
        with self.assertRaisesRegex(InputError, "deadline"):
            self.check()
        self.manifest["review"]["deadline"] = "2026-10-09T17:00:00Z"
        self.manifest["delivery"]["file"] = "../somewhere.csv"
        with self.assertRaisesRegex(InputError, "inside"):
            self.check()
