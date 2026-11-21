import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from telryn import episodes, policy, proposal, weekly_report
from telryn.__main__ import main
from telryn.contracts import InputError
from tests import test_complaints


BASE = (
    Path(__file__).resolve().parents[1] / "examples/complaints_pilot/clock-policy.json"
)


def contact(cid, at, event=None, *, alias=None, matter="private-business-reference"):
    passage = "The first disputed charge still needs investigation."
    return {
        "case_id": cid,
        "revision": "v1",
        "evidence_ref": f"conversations/{cid}/v1",
        "channel": "phone" if cid == "phone" else "chat",
        "subject_ref": "private subject value",
        "matter_ref": matter,
        "match_key": alias or cid,
        "contact_at": at,
        "passages": [{"at": at, "text": f"A private unquoted sentence. {passage}"}],
        "events": [{"event": event, "passage": 1, "quote": passage}] if event else [],
    }


class EpisodeTests(unittest.TestCase):
    def setUp(self):
        self.f = test_complaints.ComplaintRecordsTests()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.f.manifest.update(
            episode_evidence="contacts.json", clock_policy="policy.json"
        )
        self.f.manifest["cases"][0]["deadline"] = None
        self.policy_file = self.f.root / "policy.json"
        self.policy_doc = policy.load(BASE)
        self.policy_file.write_text(json.dumps(self.policy_doc))
        self.contacts = [
            contact("C1", "2026-09-01T11:00:00Z", "dissatisfaction"),
            contact("phone", "2026-11-09T10:00:00Z", "final_response_sent"),
        ]
        self.f.register = [self.f.entry("phone", "R1", self.f.later)]
        self.f.actions = [self.f.event("phone", "A1", "accepted", self.f.later)]

    def save(self):
        (self.f.root / "contacts.json").write_text(
            json.dumps({"schema": 1, "contacts": self.contacts})
        )

    def attach(self):
        self.save()
        return self.f.attach()

    def review(self, updated, *paths):
        updated_path = self.f.root / "updated.json"
        updated_path.write_text(json.dumps(updated))
        result = io.StringIO()
        with redirect_stdout(result):
            code = main(
                [
                    "policy-review",
                    str(updated_path),
                    *map(str, paths or [self.f.report]),
                ]
            )
        return code, json.loads(result.getvalue()) if code == 0 else None

    def test_attachment_scope_and_contact_channels_agree_with_control_inventory(self):
        from telryn import complaints

        self.save()
        self.f.save()
        state = weekly_report.read_state(self.f.report)
        state["control"] = {
            "scope": "another tenant",
            "channels": [
                {"name": "chat", "coverage": "covered"},
                {"name": "phone", "coverage": "uncovered"},
            ],
        }
        with self.assertRaisesRegex(InputError, "control scope"):
            complaints.attach(state, self.f.path, self.f.audit_id)
        state["control"]["scope"] = self.f.manifest["scope"]
        with self.assertRaisesRegex(InputError, "channel in the signed inventory"):
            complaints.attach(state, self.f.path, self.f.audit_id)
        state["control"]["channels"][1]["coverage"] = "partial"
        sampled = next(c for c in state["cases"] if c["case_id"] == "C1")
        sampled["channel"] = "phone"
        with self.assertRaisesRegex(InputError, "frozen source"):
            complaints.attach(state, self.f.path, self.f.audit_id)
        sampled["channel"] = "chat"
        self.assertTrue(complaints.attach(state, self.f.path, self.f.audit_id))

    def test_frozen_case_cannot_supply_a_later_cited_event(self):
        from telryn import complaints

        self.contacts[0]["passages"][0]["at"] = self.f.cutoff
        self.save()
        self.f.save()
        state = weekly_report.read_state(self.f.report)
        row = next(c for c in state["cases"] if c["case_id"] == "C1")
        row["completed_at"] = "2026-09-02T00:00:00Z"
        with self.assertRaisesRegex(InputError, "cited event postdates"):
            complaints.attach(state, self.f.path, self.f.audit_id)

    def test_on_time_registration_still_needs_missing_response_evidence(self):
        from telryn import control

        self.f.register = [self.f.entry("C1", "R1", "2026-09-02T00:00:00Z")]
        self.contacts[1]["events"] = []
        summary, rows = self.attach()
        self.assertEqual(rows["C1"]["registration_at_cutoff"], "on_time")
        self.assertEqual(rows["C1"]["follow_up"], "verify_policy_clock_evidence")
        self.assertEqual(
            rows["C1"]["policy_clauses_needing_evidence"], ["final-response"]
        )
        self.assertIn("C1", summary["worklist"]["policy_clock_review"])
        self.assertIn(
            "Policy deadline passed with no cited completion", self.f.report.read_text()
        )
        state = weekly_report.read_state(self.f.report)
        state["control"] = {
            "scope": self.f.manifest["scope"],
            "gap_owner": "Case manager",
            "gap_review_date": "2026-11-11",
            "onboarding_minutes": 0,
        }
        item = next(
            r for r in control.summary(state)["unresolved"] if r["case_id"] == "C1"
        )
        self.assertEqual((item["owner"], item["review_date"]), ("owner", "2026-11-11"))

    def test_owner_event_predating_its_source_contact_requires_reconciliation(self):
        self.f.register = []
        accepted = self.f.event("C1", "A1", "accepted", self.contacts[0]["contact_at"])
        impossible = self.f.event("phone", "A2", "completed", "2026-11-09T09:00:00Z")
        self.f.actions = [accepted, impossible]
        result, rows = self.attach()
        row = rows["C1"]
        self.assertEqual(row["owner_at_cutoff"], "accepted")
        self.assertEqual(row["owner_as_of"], "unresolved")
        self.assertEqual(row["follow_up"], "verify_owner_work")
        self.assertIn("C1", result["worklist"]["unresolved"])
        self.assertEqual(row["actions"], [accepted, impossible])

    def test_two_sources_for_the_same_contact_must_agree_on_when_it_opened(self):
        source = self.contacts[0]
        citation = {
            "revision": source["revision"],
            "evidence_ref": source["evidence_ref"],
            "passage": 1,
            "quote": source["events"][0]["quote"],
            "passages": source["passages"],
            "contact_at": "2026-09-01T10:00:00Z",
        }
        self.f.manifest["cases"][0]["source_citation"] = citation
        with self.assertRaisesRegex(InputError, "disagree on the contact opening"):
            self.attach()
        del citation["contact_at"]
        citation["passages"] = [
            {"at": "2026-09-01T10:00:00Z", "text": citation["quote"]}
        ]
        with self.assertRaisesRegex(InputError, "predates the linked contact opening"):
            self.attach()
        citation["passages"] = source["passages"]
        citation["contact_at"] = source["contact_at"]
        _, rows = self.attach()
        self.assertEqual(
            rows["C1"]["source_citation"]["contact_at"], source["contact_at"]
        )

    def test_later_phone_closes_episode_without_rewriting_cutoff(self):
        packet = self.f.packet.read_bytes()
        _, rows = self.attach()
        row = rows["C1"]
        self.assertEqual(row["repeat_contacts"], 1)
        self.assertEqual(row["registration_at_cutoff"], "missing")
        self.assertEqual(row["registration_as_of"], "registered")
        self.assertEqual(row["owner_at_cutoff"], "unobserved")
        self.assertEqual(row["owner_as_of"], "accepted")
        reg = row["clocks"]["registration"]
        self.assertEqual(reg["deadline"], "2026-09-04T16:00:00Z")
        self.assertEqual(reg["start"]["case_id"], "C1")
        self.assertEqual(reg["stop"]["entry_id"], "R1")
        self.assertEqual(reg["status"], "late")
        final = row["clocks"]["final-response"]
        self.assertEqual(final["deadline"], "2026-10-27T17:00:00Z")
        self.assertEqual(final["stop"]["case_id"], "phone")
        self.assertEqual(
            row["clocks_at_cutoff"]["final-response"]["status"],
            "past_due_without_evidence",
        )
        report = self.f.report.read_text()
        self.assertIn("first disputed charge", report)
        for secret in (
            "private subject value",
            "private-business-reference",
            "private unquoted sentence",
        ):
            self.assertNotIn(secret, report)
        self.assertEqual(weekly_report.verified_packet(self.f.report), packet)

    def test_episode_identity_scope_missing_keys_window_and_conflicting_aliases(self):
        manual_case = self.f.manifest["cases"][1]
        manual_case["deadline"] = None
        self.contacts.append(
            contact("C2", "2026-09-02T12:00:00Z", "dissatisfaction", matter="separate")
        )
        self.contacts += [
            contact(
                "unrelated customer contact",
                "2026-11-09T12:00:00Z",
                alias="C1",
                matter="other",
            ),
            contact(
                "outside window",
                "2026-05-01T11:00:00Z",
                matter="private-business-reference",
            ),
        ]
        self.contacts[-1]["subject_ref"] = None
        _, rows = self.attach()
        self.assertNotEqual(rows["C1"]["episode_id"], rows["C2"]["episode_id"])
        self.assertEqual(rows["C1"]["subject"], rows["C2"]["subject"])
        self.assertEqual(rows["C1"]["match_status"], "shared_key")
        self.assertEqual(rows["C1"]["registration_at_cutoff"], "missing")
        self.assertEqual(rows["C1"]["registration_as_of"], "unresolved")
        self.assertEqual(rows["C2"]["match_status"], "exact_key")
        self.assertNotIn("unrelated customer contact", self.f.report.read_text())
        now = episodes.instant(self.f.later)
        sample = weekly_report.read_state(self.f.report)["cases"]
        by_id = {r["case_id"]: r for r in sample}
        alternate = dict(
            self.f.manifest,
            scope="independent queue",
            cases={r["case_id"]: r for r in self.f.manifest["cases"]},
        )
        archive = episodes.load(
            self.f.root / "contacts.json", self.policy_file, alternate, by_id
        )
        self.assertNotEqual(archive["contacts"][0]["subject"], rows["C1"]["subject"])
        same = [
            {"case_id": str(i), "subject": "s", "matter": "m", "contact_at": at}
            for i, at in enumerate(
                [
                    "2026-11-01T00:00:00Z",
                    "2026-11-04T00:00:00Z",
                    "2026-11-04T00:00:00.000000001Z",
                ]
            )
        ]
        groups = episodes.groups(same, 3, now)
        self.assertEqual(groups["0"], same[:2])
        self.assertEqual(groups["2"], same[2:])

    def test_missing_identity_and_bad_citation_fail_closed(self):
        signed = self.f.report.read_bytes()
        self.contacts[0]["events"][0]["quote"] = "absent"
        with self.assertRaisesRegex(InputError, "matching source passage"):
            self.attach()
        self.assertEqual(signed, self.f.report.read_bytes())
        self.contacts[0] = contact(
            "C1", "2026-11-03T12:00:00Z", "dissatisfaction", matter=None
        )
        _, rows = self.attach()
        self.assertIsNone(rows["C1"]["episode_id"])
        self.assertEqual(rows["C1"]["registration_at_cutoff"], "unresolved")
        self.assertEqual(rows["C1"]["match_status"], "unresolved_episode")

    def test_followup_is_append_only_and_policy_dependencies_mark_only_affected_weeks(
        self,
    ):
        later = self.contacts.pop()
        original, _ = self.attach()
        # The original observation precedes the first phone contact in the new export.
        later["contact_at"] = later["passages"][0]["at"] = "2026-11-11T09:00:00Z"
        self.contacts.append(later)
        self.f.manifest.update(
            previous_receipt=original["receipt"], as_of="2026-11-12T00:00:00Z"
        )
        self.f.register[0]["recorded_at"] = self.f.manifest["as_of"]
        next_, rows = self.attach()
        self.assertNotEqual(original["receipt"], next_["receipt"])
        self.assertEqual(rows["C1"]["repeat_contacts"], 1)
        packet = self.f.packet.read_bytes()
        baseline = self.f.report.read_bytes()
        update = copy.deepcopy(self.policy_doc)
        update["version"] = "v2"
        update["clauses"]["unrelated"] = dict(
            update["clauses"]["final-response"], start_event="later_fraud_discovery"
        )
        self.assertEqual(self.review(update)[1]["affected"], [])
        self.assertEqual(baseline, self.f.report.read_bytes())
        update["holidays"].append("2026-09-03")
        code, result = self.review(update, self.f.root)
        self.assertEqual(code, 0)
        self.assertEqual(result["affected"][0]["changed_clauses"], ["registration"])
        self.assertEqual(result["affected"][0]["week"], "2026-11-02")
        marked = self.f.report.read_bytes()
        self.assertNotEqual(marked, baseline)
        self.assertIn(b"Policy re-review required", marked)
        self.assertEqual(self.review(update)[1]["affected"], result["affected"])
        self.assertEqual(marked, self.f.report.read_bytes())
        self.assertEqual(weekly_report.verified_packet(self.f.report), packet)
        self.assertTrue(self.f.cli()[1]["complaints"]["policy_reviews"])
        with self.assertRaisesRegex(InputError, "policy re-review"):
            proposal.propose(self.f.report, self.f.root / "candidate")
        self.f.manifest.update(
            previous_receipt=next_["receipt"], as_of="2026-11-13T00:00:00Z"
        )
        updated, _ = self.attach()
        self.assertTrue(updated["policy_reviews"])
        self.f.manifest["previous_receipt"] = updated["receipt"]
        self.contacts[0]["channel"] = "changed"
        with self.assertRaisesRegex(InputError, "changed or backfilled"):
            self.attach()

    def test_context_alias_cannot_gain_a_historical_collision_at_followup(self):
        phone = self.contacts[1]
        phone["contact_at"] = phone["passages"][0]["at"] = "2026-11-06T10:00:00Z"
        self.f.register = []
        baseline, rows = self.attach()
        self.assertEqual(rows["C1"]["registration_at_cutoff"], "missing")
        self.f.manifest.update(
            previous_receipt=baseline["receipt"], as_of="2026-11-12T00:00:00Z"
        )
        self.contacts.append(
            contact(
                "separate-call",
                "2026-11-07T10:00:00Z",
                alias="phone",
                matter="unrelated",
            )
        )
        signed = self.f.report.read_bytes()
        with self.assertRaisesRegex(InputError, "historical episode alias coverage"):
            self.attach()
        self.assertEqual(self.f.report.read_bytes(), signed)
        followup_date = "2026-11-11T10:00:00Z"
        self.contacts[-1]["contact_at"] = self.contacts[-1]["passages"][0]["at"] = (
            followup_date
        )
        _, rows = self.attach()
        self.assertEqual(rows["C1"]["registration_at_cutoff"], "missing")
        self.assertEqual(rows["C1"]["registration_as_of"], "unresolved")

    def test_scan_skips_only_verified_comparison_reports(self):
        self.attach()
        folder = self.f.root / "comparison"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["demo", "--out", str(folder)]), 0)
        code, result = self.review(self.policy_doc, self.f.root)
        self.assertEqual(code, 0)
        self.assertEqual(
            result["comparison_reports_skipped"],
            [str((folder / "report.html").resolve())],
        )
        self.assertEqual(result["checked"], 1)

    def test_directory_scan_accepts_only_reports_within_its_selected_scope(self):
        self.attach()
        selected = self.f.root / "selected"
        selected.mkdir()
        link = selected / "report.html"
        link.symlink_to(self.f.report)
        original = self.f.report.read_bytes()
        with self.assertRaisesRegex(InputError, "outside the selected directory"):
            list(policy._directory_reports(selected))
        self.assertEqual(original, self.f.report.read_bytes())
        code, checked = self.review(self.policy_doc, self.f.report)
        self.assertEqual((code, checked["checked"]), (0, 1))

    def test_scan_rejects_damaged_files(self):
        self.attach()
        other = self.f.root / "another"
        other.mkdir()
        (other / "report.html").write_text("broken weekly evidence")
        with redirect_stderr(io.StringIO()):
            self.assertNotEqual(self.review(self.policy_doc, self.f.root)[0], 0)

    def test_identical_alias_safe_within_episode_multiple_registrations_ambiguous(self):
        self.contacts[1]["match_key"] = "C1"
        self.f.register[0]["match_key"] = "C1"
        self.f.register.append(self.f.entry("C1", "extra", self.f.later))
        _, rows = self.attach()
        self.assertEqual(rows["C1"]["registration_as_of"], "unresolved")
        self.assertEqual(rows["C1"]["match_status"], "multiple_entries")


class ClockTests(unittest.TestCase):
    def test_calendars_dst_and_exact_time(self):
        p = policy.load(BASE)
        start = "2026-08-28T22:00:00.000000001Z"
        rule = dict(p["clauses"]["registration"], count=1, due_time="same")
        self.assertEqual(
            policy.deadline(start, rule, p), "2026-09-01T22:00:00.000000001Z"
        )
        rule.update(unit="calendar_days")
        self.assertEqual(
            policy.deadline(start, rule, p), "2026-08-29T22:00:00.000000001Z"
        )
        rule.update(due_time="01:30")
        with self.assertRaisesRegex(InputError, "timezone transition"):
            policy.deadline("2026-10-24T12:00:00Z", rule, p)
        with self.assertRaisesRegex(InputError, "timezone transition"):
            policy.deadline("2026-03-28T12:00:00Z", rule, p)
        for key, value in (
            ("holidays", ["2026-02-30"]),
            ("timezone", "no/such/timezone"),
        ):
            with (
                self.subTest(key=key),
                patch.object(
                    policy, "read_input", return_value=dict(p, **{key: value})
                ),
                self.assertRaises(InputError),
            ):
                policy.load(BASE)
