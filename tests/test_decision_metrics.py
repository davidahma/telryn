"""Check population-level new findings and screen ranges against observable evidence."""

import csv
import io
import itertools
import tempfile
import unittest
from pathlib import Path

from telryn.weekly import _screen_metrics, summarize
from telryn.weekly_report import digest, read_state, refresh, start
from tests.audit_support import isolate_trust


class DecisionMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        isolate_trust(self, self.root)

    def audit(self, sizes, samples, screen="toy-v1"):
        population = self.root / "population.csv"
        with population.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["case_id", "revision", "evidence_ref", "screen_flag"])
            for flag, size in zip((True, False), sizes):
                for i in range(size):
                    case = f"{flag}-{i}"
                    writer.writerow([case, "r1", f"archive/{case}", flag])
        (self.root / "reviewed.csv").write_text("case_id\n")
        plan = self.root / "plan.toml"
        plan.write_text(
            f'schema=1\nweek="2026-11-02"\nrubric="r"\nrubric_question="Was there a miss?"\n'
            f'screen_version="{screen}"\npopulation="population.csv"\nreviewed="reviewed.csv"\n'
            f"flagged_sample={samples[0]}\nunflagged_sample={samples[1]}\n"
        )
        return start(plan, self.root / f"audit-{len(list(self.root.glob('audit-*')))}")

    def returns(self, report, classify):
        with report.with_name("grades.csv").open(
            encoding="utf-8-sig", newline=""
        ) as source:
            reader = csv.DictReader(source)
            data = io.StringIO()
            writer = csv.DictWriter(data, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                answer = classify(row["case_id"])
                if answer is None:
                    continue
                serious, disposition = answer
                row.update(serious=serious, disposition=disposition, minutes=1)
                if disposition == "new":
                    row.update(owner="Example lead", action="Example follow-up")
                writer.writerow(row)
        path = self.root / "return.csv"
        path.write_text(data.getvalue())
        return summarize(refresh(report, path, reviewer="Synthetic reviewer"))

    def test_perfect_two_case_sample_displays_wide_intervals(self):
        report = self.audit((50, 50), (1, 1))
        result = self.returns(
            report,
            lambda case: ("yes", "new") if case.startswith("True") else ("no", "none"),
        )
        self.assertEqual((result["new"], result["new_findings"]["estimate"]), (1, 50))
        self.assertEqual(result["new_findings"]["interval"], [1, 99])
        self.assertEqual(result["screen_metrics"]["recall"]["denominator"], [1, 99])
        for metric in result["screen_metrics"].values():
            self.assertEqual(metric["estimate"], 1)
            self.assertEqual(metric["interval"], [0.02, 1.0])
            self.assertEqual(metric["denominator_status"], "nonzero")
        self.assertIn("2.0%–100.0%", report.read_text())
        self.assertIn("recall 2.0%–100.0%", digest(report))

    def test_unknown_severity_and_prior_handling_give_distinct_bounds(self):
        report = self.audit((2, 3), (2, 3))
        values = {
            "True-0": ("yes", "new"),
            "True-1": ("yes", "unknown"),
            "False-0": ("yes", "already_known"),
            "False-1": ("no", "none"),
        }
        result = self.returns(report, values.get)
        self.assertEqual(result["interval"], [3, 4])
        self.assertEqual(
            result["new_findings"],
            dict(
                estimate=None,
                estimate_range=[1.0, 3.0],
                interval=[1, 3],
                range_kind="incomplete",
                confirmed=1,
                classified=3,
            ),
        )
        # All flagged assignments are determinate. Unreturned unflagged cases widen recall only.
        self.assertEqual(result["screen_metrics"]["precision"]["estimate"], 1)
        self.assertIsNone(result["precision"])  # v0.5 scalar completion contract
        self.assertEqual(result["screen_metrics"]["recall"]["interval"], [0.5, 2 / 3])
        self.assertIn("1 await a first return", digest(report))
        self.assertIn("Findings new to the team · 1 to 3", digest(report))

    def test_unsampled_group_and_missing_denominators(self):
        report = self.audit((2, 3), (2, 0))
        result = self.returns(report, lambda _: ("no", "none"))
        novel = result["new_findings"]
        self.assertEqual(novel["range_kind"], "uncovered")
        self.assertIsNone(novel["estimate_range"])
        self.assertEqual(novel["interval"], [0, 3])
        self.assertEqual(
            result["screen_metrics"]["recall"],
            dict(
                estimate=None,
                interval=[0, 0],
                denominator=[0, 3],
                denominator_status="possibly_zero",
            ),
        )
        self.assertIn("if any positives exist", digest(report))
        report = self.audit((0, 3), (0, 3))
        result = self.returns(report, lambda _: ("no", "none"))
        for metric in ("precision", "recall"):
            self.assertEqual(
                result["screen_metrics"][metric],
                dict(
                    estimate=None,
                    interval=None,
                    denominator=[0, 0],
                    denominator_status="zero",
                ),
            )
        self.assertEqual(result["new_findings"]["interval"], [0, 0])
        self.assertEqual(result["screen_metrics"]["agreement"]["interval"], [1, 1])
        no_screen = self.audit((0, 3), (0, 3), screen="none")
        self.assertIsNone(summarize(read_state(no_screen))["screen_metrics"])

    def test_metric_bounds_equal_extrema_over_every_feasible_small_table(self):
        strata = {
            "flagged": {"population": 3, "sample": 1},
            "unflagged": {"population": 3, "sample": 1},
        }
        for tp_bounds, fn_bounds in itertools.product(
            itertools.combinations_with_replacement(range(4), 2), repeat=2
        ):
            groups = {
                name: {"interval": pair, "range_kind": "incomplete"}
                for name, pair in zip(strata, (tp_bounds, fn_bounds))
            }
            result = _screen_metrics(strata, groups, False)
            attainable = {key: [] for key in result}
            for tp in range(tp_bounds[0], tp_bounds[1] + 1):
                for fn in range(fn_bounds[0], fn_bounds[1] + 1):
                    attainable["agreement"].append((tp + 3 - fn) / 6)
                    attainable["precision"].append(tp / 3)
                    if tp + fn:
                        attainable["recall"].append(tp / (tp + fn))
            for name, values in attainable.items():
                self.assertEqual(
                    result[name]["interval"],
                    [min(values), max(values)] if values else None,
                )
