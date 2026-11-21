"""Exercise the desk, blind packet, scope, register evidence and classification decisions."""

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telryn import complaints, control, weekly, weekly_report
from telryn.contracts import InputError
from telryn.desk import Desk


class ComplaintControlTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {"TELRYN_TRUST_DIR": str(self.root / "trust")})
        env.start()
        self.addCleanup(env.stop)
        self.desk = Desk(self.root / "runs")
        self.cfg = {
            "schema": 1,
            "categories": ["service", "harm"],
            "scope": "Private queue",
            "gap_owner": "Private case lead",
            "gap_review_date": "2026-10-30",
            "onboarding_minutes": 25,
            "channels": [
                {
                    "name": "phone",
                    "coverage": "uncovered",
                    "owner": "Contact centre",
                    "ordinary_qa": "sample",
                    "retention": "90 days",
                    "register_link": "manual",
                    "review_date": "2026-10-20",
                },
                {
                    "name": "chat",
                    "coverage": "covered",
                    "owner": "Helpdesk",
                    "ordinary_qa": "weekly",
                    "retention": "30 days",
                    "register_link": "ticket key",
                    "review_date": "2026-11-01",
                },
            ],
        }
        self.fields = {
            "week": "2026-10-05",
            "rubric": "P-v1",
            "rubric_question": "Is this a complaint?",
            "screen_version": "none",
            "population": "case_id,revision,evidence_ref,screen_flag\nA,r1,private-A,0\nB,r1,private-B,0\nC,r1,private-C,0\n",
            "reviewed": "case_id\n",
            "flagged_sample": 0,
            "unflagged_sample": 3,
            "control": json.dumps(self.cfg),
        }
        self.report = self.desk.report(self.desk.create(self.fields))
        self.packet = self.report.parent / "grades.csv"
        with self.packet.open(encoding="utf-8-sig", newline="") as stream:
            self.rows = sorted(csv.DictReader(stream), key=lambda r: r["case_id"])

    def vote(self, index, **changes):
        row = self.rows[index]
        data = {
            **row,
            "serious": "yes",
            "disposition": "unknown",
            "minutes": "1",
            "category": "harm",
            "vulnerability": "yes",
            "redress": "completed",
            "classification_reason": "Repeated report under P1",
            **changes,
        }
        return data

    def submit(self, rows, actor, *, second=False):
        path = self.root / "return.csv"
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        fn = weekly_report.second_review if second else weekly_report.refresh
        return fn(self.report, path, reviewer=actor)

    def test_downgrade_is_blind_independent_and_separate_from_redress(self):
        self.assertEqual(
            json.loads(self.rows[0]["category_options"]), self.cfg["categories"]
        )
        self.assertFalse(
            {"screen_flag", "registration_status", "gap_owner"} & self.rows[0].keys()
        )
        with self.assertRaisesRegex(InputError, "classification reason"):
            self.submit([self.vote(0, classification_reason="")], "first")
        self.submit([self.vote(0)], "first")
        lower = self.vote(
            0,
            category="service",
            disposition="new",
            owner="followup",
            action="file",
            classification_reason="Service taxonomy clause 2",
        )
        with self.assertRaisesRegex(InputError, "conflicting"):
            self.submit([lower], "first")
        with self.assertRaisesRegex(InputError, "independent"):
            self.submit([lower], "first", second=True)
        lower["disposition"] = "unknown"
        lower.update(owner="", action="")
        state = self.submit([lower], "second", second=True)
        check = state["case_reviews"]["A"]["checks"][-1]
        self.assertTrue(check["downgrade_requested"])
        self.assertEqual(state["grades"]["A"]["category"], "unknown")
        self.assertIs(state["grades"]["A"]["serious"], True)
        self.assertEqual(state["grades"]["A"]["redress"], "completed")
        with self.assertRaises(InputError):
            weekly_report.adjudicate(
                self.report, "A", check["id"], "first", "second", "policy check", 1
            )
        state = weekly_report.adjudicate(
            self.report, "A", check["id"], "third", "second", "clause verified", 2
        )
        self.assertEqual(state["grades"]["A"]["category"], "service")
        html = weekly_report.verified_html(self.report).decode()
        self.assertIn("Category downgrade requested", html)
        self.assertIn("Service taxonomy clause 2", html)
        self.assertIn(
            "Channels not covered: phone (owner Contact centre, review 2026-10-20)",
            html,
        )
        self.assertLess(
            html.index("Weekly complaints control statement"),
            html.index("Confirmed cases"),
        )
        self.assertIn(
            "Attach dated register evidence", weekly_report.digest(self.report)
        )
        for term in [
            "Private case lead",
            "Contact centre",
            "Private queue",
            "clause verified",
            "private-A",
        ]:
            self.assertNotIn(term, weekly_report.digest(self.report))

    def test_overdue_unregistered_is_a_distinct_outcome(self):
        self.submit([self.vote(0), self.vote(1)], "first")
        audit = self.rows[0]["audit_id"]
        cutoff, later = "2026-10-12T00:00:00Z", "2026-10-14T00:00:00Z"
        manifest = {
            "schema": 1,
            "scope": "Private queue",
            "audit_id": audit,
            "rubric": "P-v1",
            "cutoff": cutoff,
            "as_of": later,
            "register": {
                "file": "reg.csv",
                "source_ref": "register/1",
                "complete_through": later,
            },
            "actions": {
                "file": "act.csv",
                "source_ref": "actions/1",
                "complete_through": later,
            },
            "cases": [
                {
                    "case_id": c,
                    "revision": "r1",
                    "match_key": c,
                    "deadline": "2026-10-11T00:00:00Z",
                    "policy_ref": "P1",
                }
                for c in "ABC"
            ],
        }
        (self.root / "reg.csv").write_text(
            ",".join(complaints.REGISTER)
            + "\nA,e1,2026-10-13T00:00:00Z,reg/a\nB,e2,2026-10-10T00:00:00Z,reg/b\n"
        )
        (self.root / "act.csv").write_text(",".join(complaints.ACTIONS) + "\n")
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        state = weekly_report.attach_complaints(self.report, manifest_path)
        result = weekly.summarize(state)["control_statement"]
        self.assertEqual(result["confirmed_unregistered_episodes"], 0)
        self.assertEqual(result["since_registered_episodes"], 0)
        self.assertEqual(result["confirmed_unregistered_unlinked_conversations"], 1)
        self.assertEqual(result["since_registered_unlinked_conversations"], 1)
        self.assertIn(
            "conversations with unverified episode identity",
            weekly_report.digest(self.report),
        )
        self.assertEqual(result["unregistered_conversations"]["interval"], [1, 2])
        self.assertEqual([g["case_id"] for g in result["unresolved"]], ["C"])
        self.assertIsNone(result["unresolved"][0]["age_days_since_close"])
        self.assertEqual(result["unresolved"][0]["owner"], self.cfg["gap_owner"])
        self.assertEqual(result["onboarding_minutes"], 25)
        digest = weekly_report.digest(self.report)
        self.assertIn("1–2", digest)
        self.assertNotIn("scope", digest)

    def issue_approaching_deadlines(self):
        self.submit([self.vote(i) for i in range(3)], "first")
        cutoff = "2026-10-12T00:00:00Z"
        later = "2026-10-14T00:00:00Z"
        path = self.root / "records.json"
        manifest = {
            "schema": 1,
            "scope": self.cfg["scope"],
            "audit_id": self.rows[0]["audit_id"],
            "rubric": "P-v1",
            "cutoff": cutoff,
            "as_of": later,
            "register": {
                "file": "register.csv",
                "source_ref": "register/v1",
                "complete_through": later,
            },
            "actions": {
                "file": "actions.csv",
                "source_ref": "actions/v1",
                "complete_through": later,
            },
            "cases": [
                {
                    "case_id": cid,
                    "revision": "r1",
                    "match_key": cid,
                    "deadline": later,
                    "policy_ref": "P1",
                }
                for cid in "ABC"
            ],
        }
        path.write_text(json.dumps(manifest))
        (self.root / "register.csv").write_text(",".join(complaints.REGISTER) + "\n")
        (self.root / "actions.csv").write_text(
            ",".join(complaints.ACTIONS)
            + f"\nB,a,accepted,owner-b,{later},source/b\nC,b,completed,owner-c,{later},source/c\n"
        )
        return path, manifest, weekly_report.attach_complaints(self.report, path)

    def test_deadline_after_cutoff_enters_current_work_without_changing_historical_estimate(
        self,
    ):
        _, _, state = self.issue_approaching_deadlines()
        ledger = complaints.reconcile(state)
        self.assertTrue(
            all(
                row["registration_at_cutoff"] == "not_due"
                and row["registration_overdue_as_of"]
                for row in ledger["cases"]
            )
        )
        self.assertEqual(
            ledger["worklist"],
            {
                "awaiting_acceptance": ["A"],
                "owner_accepted": ["B"],
                "reconcile_completed": ["C"],
                "unresolved": [],
            },
        )
        result = weekly.summarize(state)["control_statement"]
        self.assertEqual(result["unregistered_conversations"]["interval"], [0, 0])
        self.assertEqual(result["confirmed_unregistered_episodes"], 0)
        self.assertEqual(result["overdue_at_followup_episodes"], 0)
        self.assertEqual(result["newly_due_since_cutoff_episodes"], 0)
        self.assertEqual(result["overdue_at_followup_unlinked_conversations"], 3)
        self.assertEqual(result["newly_due_since_cutoff_unlinked_conversations"], 3)
        self.assertEqual(
            {
                r["case_id"]: (r["owner"], r["review_date"])
                for r in result["unresolved"]
            },
            {
                cid: (owner, self.cfg["gap_review_date"])
                for cid, owner in zip(
                    "ABC", [self.cfg["gap_owner"], "owner-b", "owner-c"]
                )
            },
        )
        self.assertIn(
            "3 whose deadlines passed after cutoff", weekly_report.digest(self.report)
        )
        self.assertIn(
            b"3 whose deadlines passed after cutoff",
            weekly_report.verified_html(self.report),
        )

    def test_unresolved_owner_history_routes_to_the_control_owner(self):
        path, manifest, state = self.issue_approaching_deadlines()
        as_of = "2026-10-15T00:00:00Z"
        with (self.root / "actions.csv").open("a") as stream:
            stream.write(
                f"B,bb,accepted,Case manager,{as_of},source/bb\n"
                f"B,bc,accepted,Other manager,{as_of},source/bc\n"
                f"C,cc,requested,New requester,{as_of},source/cc\n"
            )
        manifest["as_of"] = as_of
        manifest["register"]["complete_through"] = as_of
        for through in (manifest["actions"]["complete_through"], as_of):
            with self.subTest(complete_through=through):
                manifest["actions"]["complete_through"] = through
                manifest["previous_receipt"] = complaints.reconcile(state)["receipt"]
                path.write_text(json.dumps(manifest))
                state = weekly_report.attach_complaints(self.report, path)
                ledger = complaints.reconcile(state)
                self.assertTrue({"B", "C"} <= set(ledger["worklist"]["unresolved"]))
                case_b = next(r for r in ledger["cases"] if r["case_id"] == "B")
                self.assertEqual(case_b["owner_as_of"], "unresolved")
                self.assertEqual(case_b["actions"][0]["evidence_ref"], "source/b")
                follow_up = control.summary(state)["unresolved"]
                self.assertEqual(
                    {r["owner"] for r in follow_up if r["case_id"] in ("B", "C")},
                    {self.cfg["gap_owner"]},
                )

    def test_phrasing_limits_and_config_errors(self):
        self.cfg["population_basis"] = "shortlist_union"
        self.fields["control"] = json.dumps(self.cfg)
        report = self.desk.report(self.desk.create(self.fields))
        self.assertIn("in the combined shortlists", weekly_report.digest(report))
        for key, value in [
            ("categories", ["High", "high"]),
            ("gap_review_date", "today"),
            ("onboarding_minutes", -1),
        ]:
            with self.subTest(key=key), self.assertRaises(InputError):
                control.profile({**self.cfg, key: value})
        duplicate = self.cfg["channels"] * 2
        with self.assertRaisesRegex(InputError, "distinct"):
            control.profile({**self.cfg, "channels": duplicate})

    def test_linked_conversations_and_unknown_coverage_have_different_units(self):
        state = self.submit([self.vote(0), self.vote(1), self.vote(2)], "first")
        state["complaint_records"] = {}
        for case in state["cases"]:
            case["completed_at"] = "2026-10-05T09:00:00Z"
        rows = [
            {
                "case_id": cid,
                "episode_id": "episode",
                "registration_at_cutoff": "missing",
                "registration_as_of": "registered",
                "register_entries": [{"recorded_at": "2026-10-12T09:00:00Z"}],
            }
            for cid in "AB"
        ]
        rows.append(
            {
                "case_id": "C",
                "registration_at_cutoff": "unresolved",
                "registration_as_of": "unresolved",
            }
        )
        observed = {
            "cases": rows,
            "cutoff": "2026-10-10T00:00:00Z",
            "as_of": "2026-10-13T09:00:00Z",
            "worklist": {"unresolved": ["C"]},
        }
        with patch.object(complaints, "reconcile", return_value=observed):
            result = control.summary(state)
        self.assertEqual(result["unregistered_conversations"]["interval"], [2, 3])
        self.assertEqual(result["confirmed_unregistered_episodes"], 1)
        self.assertEqual(result["unresolved"][0]["age_days_since_close"], 8)
        self.assertEqual(
            result["close_to_register_hours"], {"count": 2, "median": 168.0}
        )

    def test_observed_channel_has_to_be_in_the_signed_inventory(self):
        self.fields["population"] = (
            "case_id,revision,evidence_ref,screen_flag,channel\nA,r1,source,0,phone\n"
        )
        with self.assertRaisesRegex(InputError, "channel inventory"):
            self.desk.create(self.fields)
        self.fields["population"] = self.fields["population"].replace("phone", "chat")
        self.desk.create(self.fields)
