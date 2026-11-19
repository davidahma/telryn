"""The archived-scorecard rehearsal must never invent human completion times."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from examples.complaints_pilot import intercom
from telryn.contracts import InputError


class ScorecardRehearsal(unittest.TestCase):
    def setUp(self):
        self.archive = json.loads((intercom.HERE / "intercom-archive.json").read_text())
        self.ledger = json.loads((intercom.HERE / "intercom-ledger.json").read_text())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def run_check(self):
        a, b = self.root / "archive.json", self.root / "ledger.json"
        a.write_text(json.dumps(self.archive))
        b.write_text(json.dumps(self.ledger))
        return intercom.reconcile(a, b)

    def test_witnessed_human_submission_requires_actor_and_time_evidence(self):
        result = self.run_check()
        self.assertEqual(result["both"], ["103", "104"])
        self.assertEqual(result["ledger_only"], ["105"])
        self.assertEqual(result["submitter_or_time_conflicts"], [])
        self.assertEqual(result["setup_trial"]["assisted_minutes"], 41)
        self.assertFalse(result["setup_trial"]["meets_time_threshold"])
        cases = result["cases"]
        self.assertEqual(cases["101"]["last_status_before_cutoff"], "human_assignment")
        self.assertEqual(cases["102"]["last_status_before_cutoff"], "ai_submission")
        self.assertTrue(cases["103"]["later_scorecard_differs"])
        self.assertTrue(cases["105"]["observed_after_cutoff"])
        self.assertEqual(cases["105"]["human_submission_windows"], [])
        self.assertEqual(cases["106"]["last_status_before_cutoff"], "human_submission")
        self.assertEqual(cases["106"]["human_submission_windows"], [])

    def test_reviewer_and_timestamps_are_compared_with_independent_ledger(self):
        self.ledger["submissions"][0]["submitter"] = "assigned-reviewer"
        self.ledger["submissions"][1]["submitted_at"] = (
            self.archive["scope"]["since"] + 5
        )
        self.assertEqual(
            self.run_check()["submitter_or_time_conflicts"], ["103", "104"]
        )

    def test_an_older_conversation_needs_the_same_reviewee_for_a_time_bound(self):
        earlier, later = [
            r["conversation"]
            for r in self.archive["captures"]
            if r["conversation"]["id"] == "104"
        ]
        later["scorecards"][0]["reviewed_teammate"]["admin_id"] = "different"
        self.assertIn("104", self.run_check()["ledger_only"])
        earlier["scorecards"].clear()
        self.assertIn("104", self.run_check()["ledger_only"])

    def test_a_second_submission_is_not_hidden_by_case_level_overlap(self):
        extra = dict(self.ledger["submissions"][0], submitter="another-human")
        self.ledger["submissions"].append(extra)
        result = self.run_check()
        self.assertIn("103", result["both"])
        self.assertIn("103", result["submitter_or_time_conflicts"])
        pending = result["cases"]["103"]["unverified_ledger_submissions"]
        self.assertEqual(
            [(r["submitter"], r["reason"]) for r in pending],
            [("another-human", "no_matching_observation")],
        )
        extra["submitter"] = self.ledger["submissions"][0]["submitter"]
        extra["submitted_at"] += 1
        pending = self.run_check()["cases"]["103"]["unverified_ledger_submissions"]
        self.assertEqual(
            [r["reason"] for r in pending], ["multiple_possible_submissions"] * 2
        )

    def test_ambiguous_unavailable_and_other_version_do_not_exclude_cases(self):
        for i, state in enumerate(
            ("ambiguous_scorecards", "unavailable", "no_matching_scorecard")
        ):
            record = self.archive["captures"][i]["conversation"]
            if i == 0:
                record["scorecards"] *= 2
            elif i == 1:
                del record["scorecards"]
            else:
                record["scorecards"][0]["scorecard_version_id"] = "old"
            with self.subTest(state=state):
                row = self.run_check()["cases"][record["id"]]
                self.assertEqual(row["last_status_before_cutoff"], state)
                self.assertEqual(row["human_submission_windows"], [])

    def test_reject_incomparable_sources_and_duplicate_capture(self):
        for field, value in (
            ("complete", False),
            ("population", ["103"]),
            ("evidence", "observed"),
        ):
            with self.subTest(field=field):
                original = self.ledger[field]
                self.ledger[field] = value
                with self.assertRaises(InputError):
                    self.run_check()
                self.ledger[field] = original
        self.archive["captures"].append(copy.deepcopy(self.archive["captures"][0]))
        with self.assertRaisesRegex(InputError, "duplicate"):
            self.run_check()
