"""Exercise both evidence revisions through public commands and real on-disk reports."""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.fintech.__main__ import FIXTURE, run
from telryn import benchmark, lineage
from telryn.contracts import InputError
from telryn.report import load_report, recommendation
from telryn.session import compare, publish, record_shadow, refresh
from telryn.weekly_report import second_review
from tests.audit_support import isolate_trust


class ProposalLineage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        isolate_trust(self, self.base)
        self.demo = self.base / "demo"
        run(self.demo)
        self.report = self.demo / "comparison" / "report.html"
        self.proposal = self.demo / "proposal" / "proposal.json"
        self.observation = self.demo / "shadow-observations.json"

    def newer_trial(self):
        data = json.loads(self.observation.read_text())
        data["trial_id"] = "subsequent-trial"
        for p in data["batches"]["week-three"]["picks"]:
            p["id"] += "-new"
        path = self.base / "next.json"
        path.write_text(json.dumps(data))
        return path

    def cli(self, *args, code):
        proc = subprocess.run(
            [sys.executable, "-m", "telryn", *map(str, args), "--format", "json"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, code, proc.stdout or proc.stderr)
        return json.loads(proc.stdout)

    def benchmark_file(self, state):
        record = json.loads(
            (FIXTURE.parent / "support-qa" / "pilot-record.json").read_text()
        )
        record.update(
            comparison_id=state["comparison_id"],
            decision_date="2026-11-17",
            decision_revision=recommendation(state)["decision_revision"],
        )
        path = self.base / "timings.json"
        path.write_text(json.dumps(record))
        return path

    def test_source_dispute_stops_active_gates_and_new_actions_but_retains_history(
        self,
    ):
        before = self.report.read_bytes()
        state = load_report(self.report)
        self.assertTrue(
            benchmark.assess(state, self.benchmark_file(state))["timing_comparable"]
        )
        active = self.cli(
            "explain",
            self.report,
            "--require-shadow",
            "--require-weekly-source",
            code=0,
        )
        self.assertEqual(active["weekly_source"]["status"], "current")
        weekly = self.demo / "development" / "report.html"
        with weekly.with_name("grades.csv").open(encoding="utf-8-sig") as stream:
            row = next(csv.DictReader(stream))
        row.update(serious="insufficient", disposition="unknown", minutes="4")
        returned = self.base / "second.csv"
        with returned.open("w", newline="") as stream:
            w = csv.DictWriter(stream, fieldnames=row)
            w.writeheader()
            w.writerow(row)
        second_review(weekly, returned, reviewer="Independent reviewer")
        frozen = self.cli("explain", self.report, code=0)
        self.assertEqual(frozen["recommendation"], "Try in shadow")
        self.assertEqual(frozen["weekly_source"]["status"], "historical_snapshot")
        refused = self.cli("explain", self.report, "--require-shadow", code=2)
        self.assertIn("stale or unknown audit", refused["message"])
        with self.assertRaisesRegex(InputError, "stale or unknown audit"):
            record_shadow(self.report, self.newer_trial())
        with self.assertRaisesRegex(InputError, "stale or unknown audit"):
            benchmark.assess(state, self.benchmark_file(state))
        # Identical retry of an observation already in the current saved file.
        self.assertEqual(state, record_shadow(self.report, self.observation))
        self.assertEqual(before, self.report.read_bytes())

    def test_copied_report_and_exact_replay_cannot_roll_back_a_comparison(self):
        copy = self.base / "copied.html"
        shutil.copyfile(self.report, copy)
        old = load_report(copy)
        record_shadow(self.report, self.newer_trial())
        with self.assertRaisesRegex(InputError, "stale or diverging"):
            recommendation(old, require_current=True)
        with self.assertRaisesRegex(InputError, "stale or diverging"):
            benchmark.assess(old, self.benchmark_file(old))
        with self.assertRaisesRegex(InputError, "stale or diverging"):
            record_shadow(copy, self.observation)
        with self.assertRaisesRegex(InputError, "stale or diverging"):
            compare(FIXTURE / "held-out.toml", proposal_path=self.proposal)
        self.assertEqual(
            recommendation(old)["weekly_source"]["status"], "historical_snapshot"
        )

    def test_grade_revision_observation_token_and_interrupted_publication_reconcile(
        self,
    ):
        workspace = self.base / "heldout"
        workspace.mkdir()
        for name in (
            "held-out.toml",
            "held-out.json",
            "live.sql",
            "held-out-serious-grades.json",
        ):
            shutil.copy(FIXTURE / name, workspace / name)
        grades = workspace / "held-out-serious-grades.json"
        data = json.loads(grades.read_text())
        data["grades"] = {}
        grades.write_text(json.dumps(data))
        state = compare(workspace / "held-out.toml", proposal_path=self.proposal)
        report = publish(self.base / "grading", state)
        copy = self.base / "initial.html"
        shutil.copy(report, copy)
        first = recommendation(state)["decision_revision"]
        source = report.with_name("grading.csv")
        with source.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            case = state["packet"][row["row_id"]]["case"]
            row.update(grade="meets" if case == "H03" else "outside", minutes_spent="4")
        returned = self.base / "grades.csv"
        with returned.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0])
            w.writeheader()
            w.writerows(rows)
        # Stop immediately before materializing the committed revision.
        with (
            patch.object(Path, "replace", side_effect=OSError("interrupted")),
            self.assertRaises(OSError),
        ):
            refresh(report, returned, None)
        with self.assertRaisesRegex(InputError, "stale"):
            recommendation(load_report(copy), require_current=True)
        current = load_report(report, recover=True)
        result = recommendation(current, require_current=True)
        self.assertEqual(result["recommendation"], "Try in shadow")
        self.assertEqual(result["weekly_source"]["comparison_revision"], 2)
        self.assertNotEqual(first, result["decision_revision"])
        self.assertEqual(refresh(report, returned, None), current)
        with self.assertRaisesRegex(InputError, "stale or diverging"):
            refresh(copy, returned, None)
        path = self.newer_trial()
        data = json.loads(path.read_text())
        data["comparison_id"] = state["comparison_id"]
        for token in (None, first, result["decision_revision"]):
            if token is None:
                data.pop("decision_revision")
            else:
                data["decision_revision"] = token
            path.write_text(json.dumps(data))
            if token == result["decision_revision"]:
                self.assertIn(
                    data["trial_id"], record_shadow(report, path)["shadow_trials"]
                )
            else:
                with self.assertRaisesRegex(InputError, "qualifying decision_revision"):
                    record_shadow(report, path)

    def test_process_death_on_either_side_of_replacement_withdraws_old_authority(self):
        worker = """
import os, sys
from pathlib import Path
from telryn.session import record_shadow
from telryn.weekly_report import second_review
kind, when, report, packet = sys.argv[1:]
replace = Path.replace
def interrupt(source, target):
    if source.name.startswith(".telryn-pending-"):
        if when == "after":
            replace(source, target)
        os._exit(73)
    return replace(source, target)
Path.replace = interrupt
if kind == "weekly":
    second_review(Path(report), Path(packet), "Independent reviewer")
else:
    record_shadow(Path(report), Path(packet))
"""
        for kind in ("weekly", "comparison"):
            for when in ("before", "after"):
                with self.subTest(kind=kind, when=when):
                    cohort = self.base / f"{kind}-{when}"
                    run(cohort)
                    gate = cohort / "comparison/report.html"
                    if kind == "weekly":
                        report = cohort / "development/report.html"
                        with report.with_name("grades.csv").open(
                            encoding="utf-8-sig"
                        ) as f:
                            row = next(csv.DictReader(f))
                        row.update(
                            serious="insufficient", disposition="unknown", minutes="3"
                        )
                        packet = cohort / "dispute.csv"
                        with packet.open("w", newline="") as f:
                            writer = csv.DictWriter(f, fieldnames=row)
                            writer.writeheader()
                            writer.writerow(row)
                    else:
                        report = gate
                        trial = json.loads(
                            (cohort / "shadow-observations.json").read_text()
                        )
                        trial["trial_id"] += "-later"
                        for pick in trial["batches"]["week-three"]["picks"]:
                            pick["id"] += "-later"
                        packet = cohort / "later.json"
                        packet.write_text(json.dumps(trial))
                    copy = cohort / "old.html"
                    shutil.copyfile(report, copy)
                    proc = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            worker,
                            kind,
                            when,
                            str(report),
                            str(packet),
                        ]
                    )
                    self.assertEqual(proc.returncode, 73)
                    if kind == "weekly":
                        self.cli("explain", gate, "--require-shadow", code=2)
                        self.cli("weekly", copy, code=2)
                        self.assertEqual(
                            self.cli("weekly", report, code=0)["review"]["open"], 1
                        )
                    else:
                        self.cli("explain", copy, "--require-shadow", code=2)
                        recovered = self.cli(
                            "explain", report, "--require-shadow", code=0
                        )
                        self.assertIn(trial["trial_id"], recovered["shadow_trials"])

    def test_older_and_foreign_proposal_comparisons_are_historical(self):
        state = load_report(self.report)
        with patch.dict(os.environ, {"TELRYN_TRUST_DIR": str(self.base / "foreign")}):
            self.assertEqual(
                recommendation(state)["weekly_source"]["status"], "historical_snapshot"
            )
            with self.assertRaises(InputError):
                recommendation(state, require_current=True)
        state.pop(lineage.STAMP)
        for name in ("source_signature", "source_publication", "source_revision"):
            del state["weekly_proposal"][name]
        self.assertIsNone(recommendation(state)["weekly_source"]["revision"])
        with self.assertRaisesRegex(
            InputError, "historical weekly proposal comparison"
        ):
            recommendation(state, require_current=True)
        manual = compare(FIXTURE / "held-out.toml")
        self.assertNotIn("weekly_source", recommendation(manual, require_current=True))
