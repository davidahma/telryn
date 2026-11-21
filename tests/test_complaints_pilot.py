"""Verify the synthetic comparison through the ordinary signed weekly report."""

import io
import json
import os
import shutil
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from examples.complaints_pilot import __main__ as pilot
from examples.complaints_pilot import signals
from telryn import audit_trust, weekly_report
from telryn.__main__ import main
from telryn.contracts import InputError, fingerprint
from tests.audit_support import isolate_trust


class ComplaintsPilot(unittest.TestCase):
    def test_cli_rehearsal_defaults_to_a_separate_store_and_restores_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            team = root / "actual-team"
            demo = root / ".local/state/telryn/rehearsal"
            report = root / "trial/2026-10-05/report.html"
            with patch.dict(
                os.environ,
                {
                    "HOME": tmp,
                    "TELRYN_TRUST_DIR": str(team),
                    "TELRYN_DEMO_TRUST_DIR": "",
                },
            ):
                with redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(
                        main(["demo", "--complaints", "--out", str(root / "trial")]), 0
                    )
                self.assertIn(str(demo), stdout.getvalue())
                self.assertFalse(team.exists())
                self.assertEqual(audit_trust.directory(), team)
                with patch.dict(os.environ, {"TELRYN_TRUST_DIR": str(demo)}):
                    self.assertTrue(weekly_report.read_state(report)["grades"])
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    with audit_trust.rehearse():
                        raise RuntimeError("interrupted")
                self.assertEqual(audit_trust.directory(), team)
                with patch.dict(os.environ, {"TELRYN_DEMO_TRUST_DIR": str(team)}):
                    with self.assertRaisesRegex(InputError, "separate"):
                        with audit_trust.rehearse():
                            self.fail("rehearsal accepted the team's key and registry")
                self.assertFalse(team.exists())

    def test_blind_union_frozen_before_grades_and_matched_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            isolate_trust(self, root)
            result = pilot.run(root / "trial")
            self.assertEqual(
                json.loads((root / "trial/comparison.json").read_text()), result
            )
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
        closing = {**cases["C108"], "closed_at": "2026-10-06T14:00:00.000000002Z"}
        earlier = {**repeat[0], "contact_at": "2026-10-06T14:00:00.000000001Z"}
        self.assertEqual(signals.prior_related(closing, [earlier], 90), [earlier])
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

    def test_both_weeks_reject_conflicting_owner_history_and_credit_linked_contacts(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            isolate_trust(self, root)
            fixture = root / "fixture"
            shutil.copytree(pilot.FIXTURE, fixture)
            with (fixture / "owner-events.csv").open("a") as log:
                log.write(
                    "C102,A99,requested,Other team,2026-10-12T15:00:00Z,follow-up/A99\n"
                    "C103,A98,requested,Other team,2026-10-12T07:00:00Z,follow-up/A98\n"
                    "FIN-42,A96,accepted,Case manager,2026-10-12T12:10:00Z,follow-up/A96\n"
                    "C108,A97,completed,Registration team,2026-10-13T12:00:00Z,actions/A97\n"
                )
            with (fixture / "owner-episodes.csv").open("a") as index:
                index.write("A96,FIN-42,P-LINKED\nA97,C108,P-LINKED\n")
            with patch.object(pilot, "FIXTURE", fixture):
                result = pilot.run(root / "trial")
                first = result["weeks"][0]
                self.assertEqual(first["unresolved_owner_evidence"], ["C102"])
                self.assertEqual(
                    first["methods"]["native"]["owner_accepted_new"],
                    ["P-019", "P-LINKED"],
                )
                linked = first["observed_owner_actions"]["C108"]
                self.assertEqual(linked["acceptance_owner"], "Case manager")
                self.assertEqual(linked["completion_owner"], "Registration team")
                self.assertEqual(linked["action_ref"], "actions/A97")

                week = first["week"]
                protocol = tomllib.loads((fixture / "protocol.toml").read_text())
                population = pilot.read_csv(fixture / "conversations.csv")
                answers = {
                    r["case_id"]: r for r in pilot.read_csv(fixture / "answers.csv")
                }
                history = pilot.visible_contacts(population, pilot.source_cutoff(week))
                self.assertEqual(
                    pilot.fixture_acceptances(
                        root / "check",
                        week,
                        protocol,
                        [c for c in population if c["week"] == week],
                        history,
                        answers,
                    ),
                    {"C109": "P-019", "C108": "P-LINKED"},
                )

    def test_terminal_comparison_failure_leaves_no_published_rehearsal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            isolate_trust(self, root)
            with patch.object(
                pilot, "two_week_result", side_effect=InputError("time ledger mismatch")
            ):
                with self.assertRaisesRegex(InputError, "time ledger mismatch"):
                    pilot.run(root / "trial")
            self.assertFalse((root / "trial").exists())

    def test_cutoff_hides_future_passages_and_events_from_the_frozen_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "fixture"
            shutil.copytree(pilot.FIXTURE, fixture)
            with patch.object(pilot, "FIXTURE", fixture):
                population = pilot.read_csv(fixture / "conversations.csv")
                cutoff = pilot.source_cutoff("2026-10-05")
                first = pilot.visible_contacts(population, cutoff)
                archive = fixture / "prior-contacts.json"
                source = json.loads(archive.read_text())
                contact = source["contacts"][0]
                later = "2026-10-12T08:00:00.000000001Z"
                contact["passages"].append({"at": later, "text": "Later callback"})
                contact["events"].append(
                    {"event": "response", "passage": 3, "quote": "Later callback"}
                )
                archive.write_text(json.dumps(source))
                self.assertEqual(pilot.visible_contacts(population, cutoff), first)
                current = pilot.visible_contacts(
                    population, pilot.source_cutoff("2026-10-12")
                )
                self.assertEqual(current[0]["passages"][-1]["at"], later)
                self.assertEqual(current[0]["events"][-1]["passage"], 3)

    def test_header_only_register_and_owner_exports_retain_their_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            isolate_trust(self, root)
            fixture = root / "fixture"
            shutil.copytree(pilot.FIXTURE, fixture)
            for name in ("register.csv", "owner-events.csv"):
                path = fixture / name
                path.write_text(path.read_text().splitlines()[0] + "\n")
            with patch.object(pilot, "FIXTURE", fixture):
                result = pilot.run(root / "trial")
            self.assertEqual(result["two_week_owner_accepted"]["unique"], 0)
            self.assertIn("C102", result["weeks"][0]["new_awaiting_owner_acceptance"])
            self.assertTrue(
                all(not w["observed_owner_actions"] for w in result["weeks"])
            )

    def test_acceptance_one_nanosecond_after_cutoff_counts_only_at_followup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            isolate_trust(self, root)
            fixture = root / "fixture"
            shutil.copytree(pilot.FIXTURE, fixture)
            log = fixture / "owner-events.csv"
            acceptance = "2026-10-12T08:00:00.000000001Z"
            log.write_text(
                log.read_text().replace(
                    "2026-10-12T11:00:00Z,owner-cases/P-012",
                    acceptance + ",owner-cases/P-012",
                )
            )
            with patch.object(pilot, "FIXTURE", fixture):
                result = pilot.run(root / "trial")
            week = result["weeks"][0]
            state = weekly_report.read_state(Path(week["report"]))
            case = next(
                c
                for c in pilot.complaints.reconcile(state)["cases"]
                if c["case_id"] == "C102"
            )
            self.assertEqual(case["owner_at_cutoff"], "unobserved")
            self.assertEqual(case["actions"][0]["recorded_at"], acceptance)
            self.assertIn("P-012", week["methods"]["native"]["owner_accepted_new"])
            self.assertAlmostEqual(
                week["observed_owner_actions"]["C102"][
                    "minutes_to_owner_acceptance_from_cutoff"
                ],
                1e-9 / 60,
                delta=1e-20,
            )
            self.assertEqual(
                result["fixture_selection_sensitivity"][
                    "owner_accepted_episodes_vs_fixed_sql"
                ],
                {"equal": 324, "fewer": 972, "more": 54},
            )
