import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from telryn import complaints, weekly_report
from telryn.__main__ import main
from telryn.contracts import InputError
from tests.audit_support import isolate_trust


class ComplaintRecordsTests(unittest.TestCase):
    deadline = "2026-11-08T12:00:00Z"
    cutoff = "2026-11-09T00:00:00Z"
    later = "2026-11-10T09:00:00Z"

    def write_csv(self, name, header, rows):
        with (self.root / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, header)
            writer.writeheader()
            writer.writerows(rows)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        isolate_trust(self, self.root)
        self.ids = [f"C{i}" for i in range(1, 9)]
        (self.root / "pop.csv").write_text(
            "case_id,revision,evidence_ref,screen_flag\n"
            + "".join(f"{c},v1,conversations/{c}/v1,0\n" for c in self.ids)
        )
        (self.root / "reviewed.csv").write_text("case_id\n")
        plan = self.root / "week.toml"
        plan.write_text("""schema=1
week="2026-11-02"
rubric="complaint-v1"
rubric_question="Did the example policy require complaint registration?"
screen_version="none"
population="pop.csv"
reviewed="reviewed.csv"
flagged_sample=0
unflagged_sample=8
""")
        self.report = weekly_report.start(plan, self.root / "out")
        self.packet = self.report.parent / "grades.csv"
        with self.packet.open(encoding="utf-8-sig") as src:
            grade_rows = list(csv.DictReader(src))
        self.audit_id = grade_rows[0]["audit_id"]
        for row in grade_rows:
            row.update(serious="yes", disposition="unknown", minutes="2")
        self.write_csv("returns.csv", grade_rows[0].keys(), grade_rows)
        weekly_report.refresh(
            self.report, self.root / "returns.csv", reviewer="primary"
        )
        self.register = [
            self.entry("C1", "R1", self.deadline),
            self.entry("C2", "R2", "2026-11-08T12:00:00.000000001Z"),
            self.entry("C3", "R3", self.later),
            self.entry("C4", "R4", "2026-11-07T12:00:00Z"),
            self.entry("C4", "R5", self.cutoff),
            self.entry("not-selected", "R9", self.deadline),
        ]
        self.actions = [
            self.event("C3", "A3", "requested", self.cutoff),
            self.event("C3", "A4", "accepted", self.later),
            self.event("C2", "A2", "completed", self.cutoff),
        ]
        self.manifest = {
            "schema": 1,
            "audit_id": self.audit_id,
            "rubric": "complaint-v1",
            "scope": "synthetic queue",
            "cutoff": self.cutoff,
            "as_of": self.later,
            "register": {
                "file": "register.csv",
                "source_ref": "register/s1",
                "complete_through": self.later,
            },
            "actions": {
                "file": "actions.csv",
                "source_ref": "work/s1",
                "complete_through": self.later,
            },
            "cases": [
                {
                    "case_id": c,
                    "revision": "v1",
                    "deadline": self.later if c == "C5" else self.deadline,
                    "policy_ref": "P/v1",
                    "match_key": "shared" if c in ("C6", "C7") else c,
                }
                for c in self.ids
            ],
        }
        self.path = self.root / "records.json"

    @staticmethod
    def entry(key, id_, at):
        return dict(
            match_key=key, entry_id=id_, recorded_at=at, evidence_ref="register/" + id_
        )

    @staticmethod
    def event(case, id_, status, at):
        return dict(
            case_id=case,
            action_id=id_,
            status=status,
            owner="owner",
            recorded_at=at,
            evidence_ref="work/" + id_,
        )

    def save(self):
        self.path.write_text(json.dumps(self.manifest))
        self.write_csv("register.csv", complaints.REGISTER, self.register)
        self.write_csv("actions.csv", complaints.ACTIONS, self.actions)

    def cli(self, *options):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                ["weekly", str(self.report), "--format", "json", *map(str, options)]
            )
        return result, json.loads(output.getvalue())

    def attach(self):
        self.save()
        state = weekly_report.attach_complaints(self.report, self.path)
        result = complaints.reconcile(state)
        return result, {r["case_id"]: r for r in result["cases"]}

    def test_later_mapping_cannot_reassign_a_settled_historical_match(self):
        self.manifest["cases"][3]["match_key"] = None
        first, rows = self.attach()
        self.assertEqual(rows["C1"]["registration_at_cutoff"], "on_time")
        self.manifest["previous_receipt"] = first["receipt"]
        self.manifest["cases"][3]["match_key"] = "C1"
        with self.assertRaisesRegex(InputError, "issued register match"):
            self.attach()
        _, result = self.cli()
        self.assertEqual(result["complaints"]["receipt"], first["receipt"])
        self.manifest["cases"][3]["match_key"] = "separate-case"
        second, rows = self.attach()
        self.assertNotEqual(first["receipt"], second["receipt"])
        self.assertEqual(rows["C1"]["registration_at_cutoff"], "on_time")

    def test_signed_cli_deadline_to_action_packet_and_statistical_invariance(self):
        before = self.packet.read_bytes()
        _, initial = self.cli()
        self.save()
        code, result = self.cli("--complaint-records", self.path)
        self.assertEqual(code, 0, result)
        checked = result.pop("complaints")
        self.assertEqual(initial, result)
        rows = {r["case_id"]: r for r in checked["cases"]}
        self.assertEqual(
            [rows[c]["registration_at_cutoff"] for c in self.ids],
            [
                "on_time",
                "late",
                "missing",
                "unresolved",
                "not_due",
                "unresolved",
                "unresolved",
                "missing",
            ],
        )
        self.assertEqual(rows["C4"]["match_status"], "multiple_entries")
        self.assertEqual(rows["C6"]["match_status"], "shared_key")
        self.assertEqual(rows["C3"]["registration_as_of"], "registered")
        self.assertEqual(rows["C3"]["owner_at_cutoff"], "requested")
        self.assertEqual(rows["C3"]["owner_as_of"], "accepted")
        self.assertEqual(rows["C8"]["follow_up"], "registration_outstanding")
        self.assertEqual(checked["worklist"]["awaiting_acceptance"], ["C5", "C8"])
        self.assertEqual(checked["worklist"]["unresolved"], ["C4", "C6", "C7"])
        self.assertEqual(rows["C3"]["actions"][1]["evidence_ref"], "work/A4")
        self.assertEqual(self.packet.read_bytes(), before)
        self.assertEqual(weekly_report.verified_packet(self.report), before)
        saved = self.report.read_bytes()
        self.assertNotIn(b"not-selected", saved)
        self.assertNotIn("work/A4", weekly_report.digest(self.report))
        self.assertEqual(self.cli("--complaint-records", self.path)[0], 0)
        self.assertEqual(self.report.read_bytes(), saved)
        weekly_report.second_review(
            self.report, self.root / "returns.csv", reviewer="second"
        )
        self.assertEqual(self.cli()[1]["complaints"], checked)
        self.assertEqual(before, weekly_report.verified_packet(self.report))

    def test_already_registered_at_cutoff_with_future_deadline(self):
        for case in self.manifest["cases"]:
            if case["case_id"] in ("C1", "C4", "C8"):
                case["deadline"] = self.later
        _, rows = self.attach()
        self.assertEqual(rows["C1"]["registration_at_cutoff"], "on_time")
        self.assertEqual(rows["C4"]["registration_at_cutoff"], "unresolved")
        self.assertEqual(rows["C8"]["registration_at_cutoff"], "not_due")

    def test_partial_source_and_owner_tie_remain_unresolved(self):
        self.manifest["register"]["complete_through"] = None
        self.manifest["actions"]["complete_through"] = None
        self.manifest["cases"][0]["deadline"] = None
        self.manifest["cases"].pop()
        self.actions.append(self.event("C3", "A5", "completed", self.later))
        before, rows = self.attach()
        for case in ("C1", "C2", "C3", "C8"):
            self.assertEqual(rows[case]["registration_at_cutoff"], "unresolved")
        self.assertEqual(rows["C1"]["registration_as_of"], "registered")
        self.assertEqual(rows["C8"]["registration_as_of"], "unresolved")
        self.assertEqual(rows["C3"]["owner_as_of"], "unresolved")

        self.manifest["actions"]["complete_through"] = self.later
        self.manifest["previous_receipt"] = before["receipt"]
        _, rows = self.attach()
        self.assertEqual(rows["C2"]["owner_at_cutoff"], "completed")
        self.assertEqual(rows["C3"]["owner_as_of"], "unresolved")

    def test_outstanding_queues_distinguish_owner_acceptance_completion_and_reopened_work(
        self,
    ):
        self.register = []
        self.actions = [
            self.event("C1", "one", "accepted", self.cutoff),
            self.event("C2", "two", "completed", self.cutoff),
            self.event("C3", "three", "completed", self.cutoff),
            self.event("C3", "four", "requested", self.later),
            self.event("C8", "eight", "requested", self.cutoff),
        ]
        checked, rows = self.attach()
        self.assertEqual(checked["worklist"]["owner_accepted"], ["C1"])
        self.assertEqual(checked["worklist"]["reconcile_completed"], ["C2"])
        self.assertEqual(checked["worklist"]["awaiting_acceptance"], ["C4", "C5", "C8"])
        self.assertEqual(checked["worklist"]["unresolved"], ["C3", "C6", "C7"])
        self.assertEqual(rows["C3"]["owner_at_cutoff"], "completed")
        self.assertEqual(rows["C3"]["owner_as_of"], "unresolved")

    def test_updates_require_parent_and_keep_source_records_and_earlier_coverage(self):
        old, _ = self.attach()
        self.manifest.update(as_of="2026-11-11T00:00:00Z")
        self.actions.append(
            self.event("C3", "later", "completed", self.manifest["as_of"])
        )
        self.manifest["actions"]["complete_through"] = self.manifest["as_of"]
        original = self.report.read_bytes()
        with self.assertRaisesRegex(InputError, "previous_receipt"):
            self.attach()
        self.assertEqual(original, self.report.read_bytes())
        self.manifest["previous_receipt"] = old["receipt"]
        later, rows = self.attach()
        self.assertEqual(rows["C3"]["owner_as_of"], "completed")
        self.assertEqual(later["previous_receipts"], [old["receipt"]])
        self.manifest["previous_receipt"] = later["receipt"]
        self.register.append(self.entry("C8", "backfill", self.deadline))
        with self.assertRaisesRegex(InputError, "earlier complete"):
            self.attach()
        self.register.pop()
        self.register.pop(0)
        with self.assertRaisesRegex(InputError, "changed earlier"):
            self.attach()

    def test_grade_correction_recomputes_registration_without_changing_source_receipt(
        self,
    ):
        original, rows = self.attach()
        self.assertEqual(rows["C8"]["registration_at_cutoff"], "missing")
        with self.packet.open(encoding="utf-8-sig") as src:
            row = next(r for r in csv.DictReader(src) if r["case_id"] == "C8")
        row.update(serious="no", disposition="none", minutes="4")
        self.write_csv("second.csv", row.keys(), [row])
        state = weekly_report.second_review(
            self.report, self.root / "second.csv", "reviewer-2"
        )
        disputed = complaints.reconcile(state)
        self.assertIn("C8", disputed["worklist"]["unresolved"])
        check = state["case_reviews"]["C8"]["checks"][-1]
        state = weekly_report.adjudicate(
            self.report,
            "C8",
            check["id"],
            "lead",
            "second",
            "Policy exception confirmed",
            1,
        )
        final = complaints.reconcile(state)
        self.assertEqual(final["receipt"], original["receipt"])
        self.assertTrue(all("C8" not in cases for cases in final["worklist"].values()))
        c8 = next(r for r in final["cases"] if r["case_id"] == "C8")
        self.assertEqual(c8["registration_at_cutoff"], "outside_rubric")

    def test_invalid_audit_revision_timestamps_and_duplicates_fail_atomically(self):
        self.save()
        base = self.report.read_bytes()
        for target, field, bad in [
            (self.manifest, "audit_id", "other"),
            (self.manifest, "rubric", "other"),
            (self.manifest["cases"][0], "revision", "v2"),
            (self.manifest["register"], "complete_through", "2027-01-01T00:00:00Z"),
            (self.register[0], "recorded_at", "2027-01-01T00:00:00Z"),
            (self.actions[0], "status", "suggested"),
            (self.manifest["cases"][0], "deadline", "2026-11-01"),
            (self.register[0], "entry_id", "R2"),
        ]:
            old = target[field]
            target[field] = bad
            with self.subTest(field=field):
                with self.assertRaises(InputError):
                    self.attach()
                self.assertEqual(base, self.report.read_bytes())
            target[field] = old
