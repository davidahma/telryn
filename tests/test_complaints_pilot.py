"""Verify the two-week field rehearsal through the ordinary signed weekly report."""

import tempfile
import unittest
from pathlib import Path

from examples.complaints_pilot import __main__ as pilot
from telryn import weekly_report
from telryn.contracts import fingerprint
from tests.audit_support import isolate_trust


class ComplaintsPilot(unittest.TestCase):
    def test_blind_union_frozen_before_grades_and_matched_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            isolate_trust(self, root)
            result = pilot.run(root / "trial")
            self.assertEqual(result["evidence"], "synthetic")
            self.assertTrue(result["within_predeclared_help_limit"])
            self.assertEqual(result["week_two_help_minutes"], 3)
            first, second = result["weeks"]
            self.assertEqual(
                first["owner_accepted"],
                {
                    "native_only": [],
                    "telryn_only": [],
                    "both": ["P-012", "P-013"],
                    "unique": 2,
                },
            )
            self.assertEqual(
                first["observed_owner_actions"]["C102"][
                    "minutes_to_owner_acceptance_from_cutoff"
                ],
                180,
            )
            self.assertEqual(
                first["observed_owner_actions"]["C102"][
                    "minutes_to_completed_action_from_cutoff"
                ],
                240,
            )
            self.assertIsNone(
                first["observed_owner_actions"]["C103"][
                    "minutes_to_completed_action_from_cutoff"
                ]
            )
            self.assertNotIn("C104", first["observed_owner_actions"])
            self.assertIn("C202", second["new_awaiting_owner_acceptance"])
            self.assertEqual(second["completed_without_recorded_acceptance"], ["C203"])
            self.assertEqual(
                second["observed_owner_actions"]["C203"][
                    "minutes_to_completed_action_from_cutoff"
                ],
                270,
            )
            self.assertIsNone(
                second["observed_owner_actions"]["C203"][
                    "minutes_to_owner_acceptance_from_cutoff"
                ]
            )
            self.assertEqual(result["two_week_owner_accepted"]["unique"], 3)
            for w in result["weeks"]:
                report = Path(w["report"])
                self.assertEqual(
                    sorted(p.name for p in report.parent.iterdir()),
                    ["grades.csv", "report.html"],
                )
                state = weekly_report.read_state(report)
                self.assertEqual(w["commitment"], fingerprint(w["frozen_selection"]))
                self.assertIn(w["commitment"], state["scope_note"])
                if w is first:
                    cases = {
                        r["case_id"]: r
                        for r in pilot.complaints.reconcile(state)["cases"]
                    }
                    self.assertTrue(cases["C105"]["grade"])
                    self.assertEqual(cases["C105"]["registration_at_cutoff"], "on_time")
                    self.assertEqual(cases["C104"]["registration_at_cutoff"], "late")
                    self.assertEqual(
                        set(w["methods"]["native"]["selected"]),
                        {"C102", "C103", "C107"},
                    )
                self.assertTrue(w["both_within_budget"])
                packet = pilot.read_csv(report.with_name("grades.csv"))
                union = set().union(*w["frozen_selection"]["picks"].values())
                self.assertEqual({r["case_id"] for r in packet}, union)
                self.assertEqual(len(packet), len(union))
                self.assertNotIn("screen_flag", packet[0])
                self.assertNotIn("method", packet[0])
                self.assertNotIn(w["week"] == "2026-10-05" and "C101" or "C201", union)
                for arm, measure in w["methods"].items():
                    self.assertEqual(
                        measure["total_staff_minutes"],
                        sum(measure["staff_minutes"].values()),
                    )
                    self.assertEqual(
                        measure["staff_minutes"]["second_review"],
                        sum(
                            state["case_reviews"][c]["checks"][0]["second"]["minutes"]
                            for c in w["frozen_selection"]["picks"][arm]
                        ),
                    )

    def test_two_contacts_for_one_episode_count_once_across_methods(self):
        self.assertEqual(
            pilot.ownership(
                {
                    "native": ["episode-7", "episode-7"],
                    "telryn": ["episode-7", "episode-8"],
                }
            ),
            {
                "native_only": [],
                "telryn_only": ["episode-8"],
                "both": ["episode-7"],
                "unique": 2,
            },
        )
