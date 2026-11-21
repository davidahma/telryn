"""A synthetic audit can nominate a frozen candidate and carry it through a later trial."""

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.fintech.__main__ import FIXTURE, run, sample_return
from tests.audit_support import isolate_trust
from telryn.contracts import InputError, fingerprint
from telryn.proposal import propose
from telryn.replay import load_selector
from telryn.report import load_report, recommendation
from telryn.screen import _signals
from telryn.session import compare, publish, record_shadow
from telryn.weekly_report import refresh, second_review, start


class WeeklyProposalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        isolate_trust(self, self.base)
        self.out = self.base / "run"

    def completed_proposal(self):
        report = start(FIXTURE / "development.toml", self.base / "weekly")
        returned = self.base / "return.csv"
        sample_return(
            report.with_name("grades.csv"), returned, "development-grades.csv"
        )
        refresh(report, returned)
        propose(report, self.base / "candidate")
        return self.base / "candidate" / "proposal.json"

    def test_fintech_module_runs_all_three_weeks_and_replays_without_live_services(
        self,
    ):
        completed = subprocess.run(
            [sys.executable, "-m", "examples.fintech", "--out", str(self.out)],
            cwd=FIXTURE.parent.parent,
            check=True,
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(["T03", "T04", "T07"], result["new_cases"])
        self.assertEqual([4, 4], result["weekly"]["interval"])
        self.assertEqual(37, result["weekly"]["review_minutes"])
        self.assertEqual("flagged", result["reserved_seat"])
        self.assertEqual("Try in shadow", result["held_out"])
        self.assertEqual([1, 1], result["net_additional_serious_cases"])
        self.assertEqual("continue", result["shadow"])
        path = Path(result["comparison_report"])
        state = load_report(path)
        obs = self.out / "shadow-observations.json"
        self.assertEqual(state, record_shadow(path, obs))
        replayed = state["shifts"]["monday"]
        self.assertEqual(["H01", "H02"], replayed["live"])
        self.assertEqual(["H03", "H01"], replayed["candidate"])
        self.assertEqual(2, replayed["capacity"])
        self.assertEqual(state["plan"]["serious"]["rubric"], state["plan"]["rubric"])
        with self.assertRaisesRegex(InputError, "already exists"):
            run(self.out)

    def test_combined_shortlists_cannot_support_a_queue_routing_proposal(self):
        from examples.complaints_pilot.__main__ import run as rehearse

        weeks = rehearse(self.out)["weeks"]
        for week in weeks:
            with self.assertRaisesRegex(InputError, "combined shortlists"):
                propose(Path(week["report"]), self.base / "candidate")
        self.assertFalse((self.base / "candidate").exists())

    def test_fixture_event_file_matches_model_projection(self):
        signals = _signals(FIXTURE / "signals.csv")
        with (FIXTURE / "population.csv").open(encoding="utf-8", newline="") as file:
            population = list(csv.DictReader(file))
        self.assertEqual(
            [
                (row["case_id"], row["revision"], row["evidence_ref"])
                for row in population
            ],
            [(row["case_id"], row["revision"], row["evidence_ref"]) for row in signals],
        )

    def test_needs_complete_labels_and_observed_difference_in_newly_found_rates(self):
        weekly = start(FIXTURE / "week.toml", self.base / "audit")
        candidate = self.base / "candidate"
        with self.assertRaisesRegex(InputError, "complete grading"):
            propose(weekly, candidate)
        returned = self.base / "returned.csv"
        sample_return(weekly.with_name("grades.csv"), returned)
        refresh(weekly, returned)
        with self.assertRaisesRegex(InputError, "no segment"):
            propose(weekly, candidate)
        # An independent fixture also checks a tie without changing any accepted grade.
        weekly = start(FIXTURE / "week.toml", self.base / "tie")
        sample_return(weekly.with_name("grades.csv"), returned)
        with returned.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            # All serious cases were already known; no subgroup has a new-case advantage.
            if row["serious"] == "yes":
                row["disposition"] = "already_known"
        with returned.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        refresh(weekly, returned)
        with self.assertRaisesRegex(InputError, "no segment"):
            propose(weekly, candidate)
        self.assertFalse(candidate.exists())

    def test_heldout_requires_disjoint_population_later_dates_and_matching_capacity_screen_and_rubric(
        self,
    ):
        proposal = self.completed_proposal()
        for name, message in [
            ("ordinary_id", "disjoint"),
            ("cutoff", "later"),
            ("capacity", "capacity"),
            ("screen", "screen_version"),
            ("priority", "numeric priority"),
            ("membership", "membership"),
        ]:
            with self.subTest(name=name):
                data = json.loads((FIXTURE / "held-out.json").read_text())
                batch = data["shifts"]["monday"]
                if name == "ordinary_id":
                    # D00 was ordinarily reviewed, so it never entered the tuning-week packet.
                    batch["membership"]["ids"][0] = "D00"
                elif name == "cutoff":
                    batch["cutoff"] = "2026-11-08T23:59:59Z"
                elif name == "capacity":
                    batch["capacity"] += 1
                elif name == "screen":
                    batch["requests"]["H01"]["inputs"]["screen_version"] = "new-model"
                elif name == "priority":
                    batch["requests"]["H01"]["inputs"]["priority"] = False
                else:
                    batch["membership"] = "bad"
                path = self.base / "export.json"
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(InputError, message):
                    compare(
                        FIXTURE / "held-out.toml",
                        export_path=path,
                        proposal_path=proposal,
                    )
        plan = self.base / "plan.toml"
        plan.write_text(
            (FIXTURE / "held-out.toml")
            .read_text()
            .replace("max_lost = 0", "max_lost = 1")
        )
        with self.assertRaisesRegex(InputError, "same serious rubric"):
            compare(plan, export_path=FIXTURE / "held-out.json", proposal_path=proposal)

    def test_proposal_requires_authentic_current_evidence(self):
        proposal = self.completed_proposal()
        original = json.loads(proposal.read_text())
        public_copy = {
            k: v for k, v in original.items() if k not in {"proposal_id", "signature"}
        }
        public_copy["sampled_in_segment"] += 1
        public_copy["proposal_id"] = fingerprint(public_copy)
        public_copy["signature"] = original["signature"]
        proposal.write_text(json.dumps(public_copy))
        with self.assertRaisesRegex(InputError, "signature"):
            compare(FIXTURE / "held-out.toml", proposal_path=proposal)
        legacy = {"schema": 1, "priority_flag": True}
        legacy["proposal_id"] = fingerprint(legacy)
        proposal.write_text(json.dumps(legacy))
        with self.assertRaisesRegex(InputError, "unsigned historical"):
            compare(FIXTURE / "held-out.toml", proposal_path=proposal)
        proposal.write_text(json.dumps(original))
        source = self.base / "weekly" / "report.html"
        second_review(source, self.base / "return.csv", reviewer="Independent check")
        with self.assertRaisesRegex(InputError, "stale"):
            compare(FIXTURE / "held-out.toml", proposal_path=proposal)

    def test_exact_bytes_of_loaded_selector_must_match_proposal(self):
        proposal = self.completed_proposal()

        def change_before_load(base, rule):
            if rule["file"] == "candidate.sql":
                (base / rule["file"]).write_text(
                    "SELECT id FROM requests ORDER BY id LIMIT :capacity"
                )
            return load_selector(base, rule)

        with patch("telryn.session.load_selector", side_effect=change_before_load):
            with self.assertRaisesRegex(InputError, "proposal SQL"):
                compare(FIXTURE / "held-out.toml", proposal_path=proposal)

    def test_a_serious_loss_or_staffing_breach_can_hold_the_proposed_rule(self):
        proposal = self.completed_proposal()
        for name in (
            "held-out.toml",
            "live.sql",
            "held-out.json",
            "held-out-primary-grades.json",
            "held-out-serious-grades.json",
        ):
            (self.base / name).write_bytes((FIXTURE / name).read_bytes())
        plan = self.base / "held-out.toml"
        source = self.base / "held-out-serious-grades.json"
        grades = json.loads(source.read_text())
        grades["grades"]["H02"]["grade"] = "meets"
        source.write_text(json.dumps(grades))
        result = recommendation(compare(plan, proposal_path=proposal))
        self.assertEqual("Hold", result["recommendation"])
        self.assertEqual(["H02"], result["serious_losses"]["confirmed"])
        source.write_bytes((FIXTURE / source.name).read_bytes())
        plan.write_text(
            plan.read_text().replace("staff_minutes = 12", "staff_minutes = 5")
        )
        result = recommendation(compare(plan, proposal_path=proposal))
        self.assertEqual("Hold", result["recommendation"])
        self.assertTrue(any("workload" in reason for reason in result["vetoes"]))

    def test_minor_defect_gain_cannot_qualify_and_missing_serious_grades_use_one_question(
        self,
    ):
        proposal = self.completed_proposal()
        for name in (
            "held-out.toml",
            "live.sql",
            "held-out.json",
            "held-out-primary-grades.json",
            "held-out-serious-grades.json",
        ):
            (self.base / name).write_bytes((FIXTURE / name).read_bytes())
        plan = self.base / "held-out.toml"
        archived = self.base / "held-out-serious-grades.json"
        source = json.loads(archived.read_text())
        source["grades"]["H03"]["grade"] = "outside"
        archived.write_text(json.dumps(source))
        result = recommendation(compare(plan, proposal_path=proposal))
        self.assertEqual("Hold", result["recommendation"])
        self.assertEqual([0, 0], result["bounds"])
        source["grades"] = {}
        archived.write_text(json.dumps(source))
        state = compare(plan, proposal_path=proposal)
        self.assertEqual("Inconclusive", recommendation(state)["recommendation"])
        self.assertEqual(
            ["H02", "H03"], sorted(row["case"] for row in state["packet"].values())
        )
        # Returning the single human grade settles both capture and the serious-loss cap.
        from telryn.session import publish, refresh as refresh_comparison

        report = publish(self.out, state)
        path = self.out / "grading.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            case = state["packet"][row["row_id"]]["case"]
            row["grade"] = "meets" if case == "H03" else "outside"
            row["minutes_spent"] = "5"
        returned = self.out / "return.csv"
        with returned.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        result = recommendation(refresh_comparison(report, returned, None))
        self.assertEqual("Try in shadow", result["recommendation"])
        self.assertEqual(0, result["serious_losses"]["upper"])

    def test_shadow_observations_require_new_measured_work_after_the_heldout_batch(
        self,
    ):
        result = run(self.out)
        path = Path(result["comparison_report"])
        original = json.loads((self.out / "shadow-observations.json").read_text())
        for name in (
            "missing_minutes",
            "no_work",
            "prior_trial",
            "tuning",
            "heldout",
            "empty",
            "same_cutoff",
        ):
            with self.subTest(name=name):
                observation = json.loads(json.dumps(original))
                observation["trial_id"] = "a-second-week"
                row = {"id": "fresh-case", "minutes": 5, "cohort": "Account"}
                observation["batches"]["week-three"]["picks"] = [row]
                if name == "missing_minutes":
                    row.pop("minutes")
                elif name == "no_work":
                    row["minutes"] = 0
                elif name in ("prior_trial", "tuning", "heldout"):
                    row["id"] = {
                        "prior_trial": "S01",
                        "tuning": "D00",
                        "heldout": "H04",
                    }[name]
                elif name == "empty":
                    observation["batches"]["week-three"]["picks"] = []
                else:
                    observation["observed_at"] = "2026-11-09T12:00:00Z"
                file = self.base / "shadow.json"
                file.write_text(json.dumps(observation))
                with self.assertRaisesRegex(InputError, "weekly proposal shadow"):
                    record_shadow(path, file)
                self.assertEqual(
                    ["demo-week-three"], list(load_report(path)["shadow_trials"])
                )

    def test_declared_tuning_shift_is_required_and_stays_excluded_in_shadow(self):
        proposal = self.completed_proposal()
        plan = self.base / "held-out.toml"
        plan.write_text(
            'tuning_shifts = ["development-extra"]\n'
            + (FIXTURE / plan.name).read_text()
        )
        for name in ("held-out-serious-grades.json", "live.sql"):
            (self.base / name).write_bytes((FIXTURE / name).read_bytes())
        export = self.base / "held-out.json"
        export.write_bytes((FIXTURE / export.name).read_bytes())
        with self.assertRaisesRegex(
            InputError, "tuning membership for development-extra"
        ):
            compare(plan, proposal_path=proposal)
        data = json.loads(export.read_text())
        data["shifts"]["development-extra"] = {"membership": {"ids": ["S01"]}}
        export.write_text(json.dumps(data))
        state = compare(plan, proposal_path=proposal)
        report = publish(self.out, state)
        decision = recommendation(state, require_current=True)
        self.assertEqual(decision["recommendation"], "Try in shadow")
        observed = json.loads((FIXTURE / "shadow-template.json").read_text())
        for key in ("comparison_id", "decision_revision"):
            observed[key] = decision[key]
        obs = self.base / "observations.json"
        obs.write_text(json.dumps(observed))
        with self.assertRaisesRegex(InputError, "need new cases"):
            record_shadow(report, obs)
        observed["batches"]["week-three"]["picks"][0]["id"] = "S03"
        obs.write_text(json.dumps(observed))
        self.assertIn("demo-week-three", record_shadow(report, obs)["shadow_trials"])
        # Pre-upgrade snapshots did not retain IDs for these declared tuning shifts.
        del state["weekly_proposal"]["declared_tuning_id_hashes"]
        self.assertEqual(recommendation(state)["recommendation"], "Inconclusive")
