"""Use versioned pack data in the actual desk, grading, register and change review."""

import csv
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from telryn import packs, policy, weekly_report
from telryn.__main__ import main
from telryn.contracts import InputError
from telryn.desk import Desk
from tests.audit_support import isolate_trust

SOURCE = Path(__file__).resolve().parents[1] / "examples/policy_packs"


class PolicyPacks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        isolate_trust(self, self.root)
        self.data = self.root / "inputs"
        shutil.copytree(SOURCE, self.data)

    def cli(self, *args, ok=True):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(map(str, args)))
        if ok:
            self.assertEqual(code, 0, err.getvalue())
        else:
            self.assertEqual(code, 2)
        return out.getvalue() if ok else (out.getvalue() + err.getvalue())

    def test_pinned_catalog_and_firm_extension(self):
        items = json.loads(self.cli("pack", "list"))
        self.assertEqual(
            {i["reference"] for i in items},
            {
                f"{p}@1.0.0"
                for p in ("fca-disp", "cfpb-company-portal", "us-reg-e", "firm-example")
            },
        )
        self.assertIn("exact pack", self.cli("pack", "show", "fca-disp", ok=False))
        for item in items:
            pack = json.loads(self.cli("pack", "show", item["reference"]))
            self.assertEqual(packs.receipt(pack), item)
        pack["categories"].reverse()
        target = self.data / "ours.json"
        target.write_text(json.dumps(pack))
        self.assertIn("reuses a bundled", self.cli("pack", "show", target, ok=False))
        self.assertIn(
            "reuses a bundled", self.cli("policy-review", target, self.root, ok=False)
        )
        pack["clock_policy"]["id"] = "firm-a"
        target.write_text(json.dumps(pack))
        self.assertEqual(
            json.loads(self.cli("pack", "show", target))["categories"],
            pack["categories"],
        )

    def test_desk_packet_pinning_and_conflicting_taxonomy(self):
        pack = json.loads(self.cli("pack", "show", "us-reg-e@1.0.0"))
        form = dict(
            week="2026-10-05",
            screen_version="case-events",
            population=(self.data / "population.csv").read_text(),
            reviewed=(self.data / "reviewed.csv").read_text(),
            control=(self.data / "control.json").read_text(),
            policy_pack=json.dumps(pack),
            flagged_sample=1,
            unflagged_sample=1,
        )
        desk = Desk(self.root / "desk")
        path = desk.report(desk.create(form))
        state = weekly_report.read_state(path)
        self.assertEqual(state["policy_pack"], pack)
        self.assertEqual(weekly_report._envelope(path.read_bytes())["schema"], 4)
        self.assertIn("us-reg-e@1.0.0", path.read_text())
        with path.with_name("grades.csv").open(encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["rubric_question"], pack["rubric_question"])
        self.assertEqual(json.loads(row["category_options"]), pack["categories"])
        self.assertFalse(state["group_by"])
        self.assertEqual({r["channel"] for r in state["cases"]}, {"Chat"})
        self.assertNotIn("screen_flag", row)
        cfg = json.loads(form["control"])
        cfg["categories"] = ["Other"]
        with self.assertRaisesRegex(InputError, "taxonomy differs"):
            desk.create({**form, "control": json.dumps(cfg)})
        with self.assertRaisesRegex(InputError, "rubric differs"):
            desk.create({**form, "rubric": "other"})

    def check_unregistered(self, reference, field_pack, passage):
        plan = self.data / "week.toml"
        plan.write_text(plan.read_text().replace("firm-example@1.0.0", reference))
        report = self.root / "audit" / "report.html"
        self.cli("weekly", plan, "--out", report.parent)
        packet = report.with_name("grades.csv")
        with packet.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            self.assertEqual(row["rubric_question"], field_pack["rubric_question"])
            row.update(
                serious="yes",
                disposition="unknown",
                minutes="3",
                category=field_pack["categories"][0],
                vulnerability="unknown",
                redress="pending",
                classification_reason="Account record",
            )
        with (self.data / "return.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        self.cli(
            "weekly", report, "--grades", self.data / "return.csv", "--reviewer", "QA"
        )
        if reference.endswith(".json"):
            (self.data / reference).unlink()
        (self.data / "reg.csv").write_text(
            "match_key,entry_id,recorded_at,evidence_ref\n"
        )
        (self.data / "work.csv").write_text(
            "case_id,action_id,status,owner,recorded_at,evidence_ref\n"
        )
        contact = dict(
            case_id="A",
            revision="r1",
            evidence_ref="chat/a",
            channel="Chat",
            subject_ref="s",
            matter_ref="txn",
            match_key="a",
            contact_at="2026-10-09T09:00:00Z",
            passages=[{"at": "2026-10-09T09:00:00Z", "text": passage}],
            events=[
                {
                    "event": "firm_registration_trigger",
                    "passage": 1,
                    "quote": passage,
                }
            ],
        )
        (self.data / "contacts.json").write_text(
            json.dumps({"schema": 1, "contacts": [contact]})
        )

        def ledger(filename):
            return dict(
                file=filename,
                source_ref=filename,
                complete_through="2026-10-13T16:00:00Z",
            )

        manifest = dict(
            schema=1,
            audit_id=rows[0]["audit_id"],
            rubric=rows[0]["rubric"],
            scope="Synthetic payments queue",
            cutoff="2026-10-13T12:00:00Z",
            as_of="2026-10-13T16:00:00Z",
            register=ledger("reg.csv"),
            actions=ledger("work.csv"),
            episode_evidence="contacts.json",
            cases=[
                dict(
                    case_id="A",
                    revision="r1",
                    match_key="a",
                    policy_ref=rows[0]["rubric"],
                    deadline=None,
                )
            ],
        )
        path = self.data / "records.json"
        path.write_text(json.dumps(manifest))
        contacts = self.data / "contacts.json"
        original = contacts.read_bytes()
        contacts.write_text(
            json.dumps({"schema": 1, "contacts": [{**contact, "channel": "Phone"}]})
        )
        self.assertIn(
            "frozen source",
            self.cli("weekly", report, "--complaint-records", path, ok=False),
        )
        contacts.write_bytes(original)
        result = json.loads(
            self.cli("weekly", report, "--complaint-records", path, "--format", "json")
        )
        case = next(r for r in result["complaints"]["cases"] if r["case_id"] == "A")
        return report, packet, manifest, path, case

    def test_portal_reviewer_can_identify_complaint_before_register_check(self):
        reference = "cfpb-company-portal@1.0.0"
        pack = json.loads(self.cli("pack", "show", reference))
        self.assertIn("policy requires it to register", pack["rubric_question"])
        _, _, _, _, case = self.check_unregistered(
            reference,
            pack,
            "The CFPB routed the customer's duplicate-charge complaint to our portal.",
        )
        self.assertEqual(case["registration_at_cutoff"], "missing")

    def test_weekly_reconciliation_uses_issued_clocks_and_preserves_revision(self):
        field_pack = json.loads(self.cli("pack", "show", "firm-example@1.0.0"))
        field_pack["clock_policy"].update(
            id="example-lender", business_weekdays=[1, 2, 3, 4, 5, 6]
        )
        profile = self.data / "control.json"
        channels = json.loads(profile.read_text())
        channels["channels"].append({**channels["channels"][0], "name": "Phone"})
        profile.write_text(json.dumps(channels))
        spec = self.data / "firm.json"
        spec.write_text(json.dumps(field_pack))
        report, packet, manifest, path, case = self.check_unregistered(
            "firm.json", field_pack, "I am unhappy."
        )
        self.assertEqual(
            case["clocks"]["firm-register"]["deadline"], "2026-10-10T16:00:00Z"
        )
        self.assertEqual(case["registration_at_cutoff"], "missing")
        issued = report.read_bytes()
        path.write_text(json.dumps({**manifest, "clock_policy": "other.json"}))
        self.assertIn(
            "frozen policy",
            self.cli("weekly", report, "--complaint-records", path, ok=False),
        )
        self.assertEqual(report.read_bytes(), issued)
        field_pack["clock_policy"]["version"] = "1.0.1"
        field_pack["clock_policy"]["clauses"]["firm-register"]["count"] = 2
        spec.write_text(json.dumps(field_pack))
        self.assertEqual(
            json.loads(self.cli("policy-review", spec, report))["affected"][0][
                "changed_clauses"
            ],
            ["firm-register"],
        )
        marked = report.read_bytes()
        spec.write_text(
            json.dumps({**field_pack, "rubric_question": "A different decision?"})
        )
        self.assertIn(
            "reuse a review version", self.cli("policy-review", spec, report, ok=False)
        )
        self.assertEqual(report.read_bytes(), marked)
        field_pack["clock_policy"]["version"] = "1.1.0"
        field_pack["clock_policy"]["clauses"]["firm-register"]["count"] = 3
        field_pack["categories"] = ["Updated review category"]
        spec.write_text(json.dumps(field_pack))
        changed = json.loads(self.cli("policy-review", spec, report))
        self.assertEqual(changed["affected"][0]["changed_clauses"], ["firm-register"])
        self.assertEqual(changed["affected"][0]["changed_pack_fields"], ["categories"])
        self.assertEqual(weekly_report.verified_packet(report), packet.read_bytes())
        marked = report.read_bytes()
        self.assertEqual(json.loads(self.cli("policy-review", spec, report)), changed)
        self.assertEqual(report.read_bytes(), marked)
        self.assertEqual(
            weekly_report.read_state(report)["rubric"], "example-lender@1.0.0"
        )
        self.assertIn("1.1.0", report.read_text())

    def test_unsupported_business_calendars_fail_explicitly(self):
        clock = packs.load("us-reg-e@1.0.0", self.data)["clock_policy"]
        for days in ([], [8], [1, 1], [True], "Monday"):
            with self.subTest(days), self.assertRaisesRegex(InputError, "ISO weekdays"):
                policy.validate({**clock, "business_weekdays": days})
