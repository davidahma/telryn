"""Verify the synthetic comparison through the ordinary signed weekly report."""

import tempfile
import unittest
from pathlib import Path

from examples.complaints_pilot import __main__ as pilot
from examples.complaints_pilot import signals
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
            self.assertEqual(result["week_two_help_minutes"], 3)
            self.assertTrue(result["within_predeclared_help_limit"])
            first, second = result["weeks"]
            self.assertEqual(
                first["methods"]["native"]["selected"],
                ["C108", "C109", "C110", "C102", "C103"],
            )
            self.assertEqual(
                first["methods"]["native"]["owner_accepted_new"],
                ["P-012", "P-013", "P-019"],
            )
            self.assertEqual(result["two_week_staff_minutes"]["native"], 125)
            self.assertEqual(
                result["fixture_selection_sensitivity"],
                {
                    "possible_two_week_selections": 1350,
                    "owner_accepted_episodes_vs_fixed_sql": {
                        "equal": 324,
                        "fewer": 972,
                        "more": 54,
                    },
                },
            )
            self.assertIn("C108", first["new_awaiting_owner_acceptance"])
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
            for w in result["weeks"]:
                report = Path(w["report"])
                self.assertEqual(
                    sorted(p.name for p in report.parent.iterdir()),
                    ["grades.csv", "report.html"],
                )
                state = weekly_report.read_state(report)
                self.assertEqual(w["commitment"], fingerprint(w["frozen_selection"]))
                self.assertIn(w["commitment"], state["scope_note"])
                self.assertTrue(w["both_within_budget"])
                selection = w["frozen_selection"]["picks"]
                packet = pilot.read_csv(report.with_name("grades.csv"))
                union = set().union(*selection.values())
                self.assertEqual({r["case_id"] for r in packet}, union)
                self.assertEqual(len(packet), len(union))
                self.assertNotIn("screen_flag", packet[0])
                self.assertNotIn("method", packet[0])
                self.assertEqual(packet[0]["category"], "")
                self.assertNotIn("C101" if w is first else "C201", union)
                stmt = w["control_statement"]
                self.assertEqual(stmt["population_basis"], "shortlist_union")
                self.assertEqual(stmt["queue"], len(union))
                self.assertEqual(stmt["onboarding_minutes"], 11 if w is first else 0)
                for arm, measure in w["methods"].items():
                    self.assertEqual(len(measure["selected"]), 5)
                    self.assertEqual(
                        measure["total_staff_minutes"],
                        sum(measure["staff_minutes"].values()),
                    )
                    self.assertEqual(
                        measure["staff_minutes"]["second_review"],
                        sum(
                            state["case_reviews"][c]["checks"][0]["second"]["minutes"]
                            for c in selection[arm]
                        ),
                    )
            self.assertTrue(
                weekly_report.digest(Path(first["report"])).startswith(
                    "Synthetic complaints rehearsal"
                )
            )
            state = weekly_report.read_state(Path(first["report"]))
            cases = {
                r["case_id"]: r for r in pilot.complaints.reconcile(state)["cases"]
            }
            mira = cases["C108"]
            self.assertEqual(
                [r["case_id"] for r in mira["contacts"]], ["FIN-42", "CALL-42", "C108"]
            )
            self.assertEqual(mira["registration_at_cutoff"], "missing")
            self.assertEqual(mira["owner_as_of"], "requested")
            self.assertEqual(mira["repeat_contacts"], 2)
            self.assertEqual(
                mira["clocks"]["registration"]["deadline"], "2026-08-07T16:00:00Z"
            )
            response = mira["clocks"]["final-response"]
            self.assertEqual(
                response["start"]["quote"],
                "Even with a refund I want to know how this happened.",
            )
            self.assertEqual(response["deadline"], "2026-09-29T16:00:00Z")
            self.assertEqual(response["status"], "past_due_without_evidence")
            self.assertNotIn("S-Mira", Path(first["report"]).read_text())
            pending = next(
                r
                for r in first["control_statement"]["unresolved"]
                if r["case_id"] == "C108"
            )
            self.assertEqual(
                (
                    pending["owner"],
                    pending["review_date"],
                    pending["age_days_since_close"],
                ),
                ("Case manager", "2026-10-16", 7),
            )

    def test_implicit_signals_and_time_limited_history(self):
        cases = {
            r["case_id"]: r for r in pilot.read_csv(pilot.FIXTURE / "conversations.csv")
        }
        history = pilot.visible_contacts(
            cases.values(), pilot.source_cutoff("2026-10-05")
        )
        self.assertNotIn("C207", {c["case_id"] for c in history})
        repeat = signals.prior_related(cases["C108"], history, 90)
        self.assertEqual([c["case_id"] for c in repeat], ["FIN-42", "CALL-42"])
        self.assertEqual(signals.prior_related(cases["C109"], history, 90), [])
        examples = {
            "C108": "prior_contact_about_same_payment",
            "C109": "external_escalation",
            "C207": "dissatisfaction_after_refund",
            "C208": "essential_expense",
        }
        for cid, cue in examples.items():
            c = cases[cid]
            self.assertNotIn("complain", c["message"].lower())
            flagged, cues = signals.flags(c, repeat if cid == "C108" else [], False)
            self.assertTrue(flagged)
            self.assertIn(cue, cues)
            self.assertFalse(signals.flags(c, repeat, True)[0])
        self.assertFalse(signals.flags(cases["C209"], [], False)[0])
        # The ombudsman information request is an intentional false positive.
        self.assertTrue(signals.flags(cases["C110"], [], False)[0])

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
