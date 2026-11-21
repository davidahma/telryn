"""Weekly audit contracts and finite-population inference."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from telryn.contracts import InputError, fingerprint
from telryn.weekly import CASE_COLUMNS, _interval, prepare, summarize


class WeeklyAuditTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {"TELRYN_TRUST_DIR": str(self.root / "trust")})
        env.start()
        self.addCleanup(env.stop)
        self.plan = self.root / "weekly.toml"
        self.population = self.root / "population.csv"
        self.reviewed = self.root / "reviewed.csv"
        # 4 flagged and 8 unflagged residual cases; one ordinary review per stratum.
        self.rows = [
            (
                f"case{i:02d}",
                f"revision-{i}",
                f"evidence-{i}",
                "true" if i < 5 else "false",
            )
            for i in range(14)
        ]
        self.write_csv(self.population, CASE_COLUMNS, self.rows)
        self.write_csv(self.reviewed, ["case_id"], [["case00"], ["case05"]])
        self.write_plan()

    @staticmethod
    def write_csv(path, headers, rows):
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(rows)

    def write_plan(self, flagged=2, unflagged=2, screen="imported-v1", extra=""):
        self.plan.write_text(
            f'schema = 1\nweek = 2026-11-16\nrubric = "test-policy-v1"\n'
            f'rubric_question = "Is this serious?"\nscreen_version = "{screen}"\n'
            f'population = "population.csv"\nreviewed = "reviewed.csv"\n'
            f"flagged_sample = {flagged}\nunflagged_sample = {unflagged}\n{extra}",
            encoding="utf-8",
        )

    @staticmethod
    def grade(serious, disposition, minutes=3):
        return dict(
            serious=serious,
            disposition=disposition,
            minutes=minutes,
            owner="QA lead" if disposition == "new" else "",
            action="Open escalation" if disposition == "new" else "",
        )

    def test_scheduled_delivery_checks_the_audit_period_before_issuing_a_draw(self):
        for day, error in (
            ("2026-11-16", "seven days before"),
            ("2026-11-24", "Monday"),
            ("20261123", "YYYY-MM-DD"),
        ):
            with self.subTest(day=day), self.assertRaisesRegex(InputError, error):
                prepare(self.plan, delivery_date=day)
            self.assertFalse((self.root / "trust").exists())
        scheduled = prepare(self.plan, delivery_date="2026-11-23")
        self.assertEqual(
            scheduled["draw_commitment"], prepare(self.plan)["draw_commitment"]
        )

    def test_stratified_probabilities_replay_independent_of_file_order(self):
        original = prepare(self.plan)
        self.write_csv(self.population, CASE_COLUMNS, self.rows[::-1])
        replay = prepare(self.plan)
        self.assertEqual(original["week"], "2026-11-16")
        self.assertEqual(original["cases"], replay["cases"])
        self.assertNotEqual(original["population_sha256"], replay["population_sha256"])
        self.assertEqual(
            original["reviewed_id_hashes"],
            sorted(map(fingerprint, ["case00", "case05"])),
        )
        self.assertEqual(
            original["strata"],
            {
                "flagged": {"population": 4, "sample": 2},
                "unflagged": {"population": 8, "sample": 2},
            },
        )
        self.assertTrue(
            {"case00", "case05"}.isdisjoint(r["case_id"] for r in original["cases"])
        )
        self.assertEqual(original["seed"], prepare(self.plan)["seed"])

    def test_estimates_and_agreement_use_the_actual_selection_probabilities(self):
        state = prepare(self.plan)
        answers = {
            True: iter(["new", "unknown"]),
            False: iter(["none", "already_known"]),
        }
        for row in state["cases"]:
            status = next(answers[row["screen_flag"]])
            state["grades"][row["case_id"]] = self.grade(status != "none", status)
        results = summarize(state)
        self.assertEqual(3, results["confirmed_serious"])
        self.assertEqual(1, results["new"])
        self.assertEqual(1, results["already_known"])
        self.assertEqual(1, results["unknown"])
        self.assertEqual(8, results["estimate"])
        self.assertLessEqual(results["interval"][0], 8)
        self.assertGreaterEqual(results["interval"][1], 8)
        self.assertEqual(12, results["review_minutes"])
        self.assertEqual(1.0, results["precision"])
        self.assertEqual(0.5, results["recall"])
        self.assertAlmostEqual(8 / 12, results["agreement"])

    def test_percentage_targets_are_applied_to_residual_strata_and_rounded_up(self):
        self.write_plan(flagged='"100%"', unflagged='"12.5%"')
        state = prepare(self.plan)
        self.assertEqual(4, state["strata"]["flagged"]["sample"])
        self.assertEqual(1, state["strata"]["unflagged"]["sample"])
        self.assertEqual(
            {"flagged": "100%", "unflagged": "12.5%"}, state["sample_targets"]
        )
        self.write_plan(flagged='"0%"', unflagged='"2%"')
        state = prepare(self.plan)
        self.assertEqual(1, state["strata"]["unflagged"]["sample"])
        self.assertIsNone(summarize(state)["estimate"])
        for bad in ('"100.1%"', '"-2%"', '"2 pct"', "0.05", "true"):
            self.write_plan(unflagged=bad)
            with self.subTest(bad=bad), self.assertRaises(InputError):
                prepare(self.plan)

    def test_incomplete_grades_do_not_enter_totals_and_inconclusive_time_is_kept(self):
        state = prepare(self.plan)
        for i, row in enumerate(state["cases"][:-1]):
            state["grades"][row["case_id"]] = (
                self.grade(None, "unknown", 5) if i == 0 else self.grade(True, "new")
            )
        result = summarize(state)
        self.assertEqual(
            (2, 1, 11, 2),
            (
                result["graded"],
                result["insufficient"],
                result["review_minutes"],
                result["confirmed_serious"],
            ),
        )
        self.assertEqual("incomplete", result["range_kind"])
        self.assertLess(result["estimate_range"][0], result["estimate_range"][1])
        self.assertLessEqual(result["interval"][0], result["estimate_range"][0])
        self.assertGreaterEqual(result["interval"][1], result["estimate_range"][1])
        for metric in ("estimate", "recall", "precision", "agreement"):
            self.assertIsNone(result[metric])
        self.assertTrue(result["estimate_reason"])

    def test_nonempty_unsampled_stratum_blocks_estimation_but_actions_survive(self):
        self.write_plan(flagged=4, unflagged=0)
        state = prepare(self.plan)
        state["grades"] = {
            r["case_id"]: self.grade(True, "new") for r in state["cases"]
        }
        result = summarize(state)
        self.assertEqual(4, result["new"])
        self.assertIsNone(result["estimate"])
        self.assertIsNone(result["interval"])
        self.assertIn("zero probability", result["estimate_reason"])
        groups = result["stratum_results"]
        self.assertEqual(4, groups["flagged"]["graded"])
        self.assertEqual(4, groups["flagged"]["confirmed"])
        self.assertEqual([4, 4], groups["flagged"]["interval"])
        self.assertEqual("uncovered", groups["unflagged"]["range_kind"])
        self.assertIsNone(groups["unflagged"]["estimate_range"])
        self.assertEqual([0, 8], groups["unflagged"]["interval"])
        self.assertEqual(state["draw_commitment"], result["draw_commitment"])
        self.assertIsNone(result["coverage"])

    def test_oversubscribed_strata_are_censused_and_empty_populations_are_exact(self):
        self.write_plan(flagged=200, unflagged=20)
        state = prepare(self.plan)
        state["grades"] = {
            r["case_id"]: self.grade(False, "none") for r in state["cases"]
        }
        result = summarize(state)
        self.assertEqual([0, 0], result["interval"])
        self.assertIsNone(result["recall"])
        self.assertEqual(8 / 12, result["agreement"])
        self.write_csv(self.reviewed, ["case_id"], [(r[0],) for r in self.rows])
        result = summarize(prepare(self.plan))
        self.assertEqual(0, result["unreviewed"])
        self.assertEqual([0, 0], result["interval"])
        self.assertIsNone(result["agreement"])

    def test_without_screen_still_measures_misses_but_has_no_classifier_metrics(self):
        self.write_plan(screen="none")
        with self.assertRaisesRegex(InputError, "every screen_flag"):
            prepare(self.plan)
        self.write_csv(
            self.population, CASE_COLUMNS, [(*r[:3], "false") for r in self.rows]
        )
        state = prepare(self.plan)
        state["grades"] = {
            r["case_id"]: self.grade(False, "none") for r in state["cases"]
        }
        result = summarize(state)
        self.assertEqual(0, result["estimate"])
        self.assertGreater(result["interval"][1], 0)
        self.assertTrue(
            all(result[k] is None for k in ("agreement", "precision", "recall"))
        )

    def test_return_requires_membership_determinate_status_and_action_ownership(self):
        state = prepare(self.plan)
        first = state["cases"][0]["case_id"]
        broken = [
            ("case00", self.grade(False, "none")),
            (first, self.grade(False, "new")),
            (first, dict(self.grade(True, "new"), owner="")),
            (first, dict(self.grade(True, "new"), action="")),
            (first, self.grade(True, "escalated")),
            (first, self.grade(1, "new")),
            (first, self.grade(None, "none")),
            (first, self.grade(True, "new", True)),
        ]
        for case, grade in broken:
            with self.subTest(grade=grade), self.assertRaises(InputError):
                summarize({**state, "grades": {case: grade}})

    def test_rejects_mismatched_scope_duplicate_headers_ids_and_transcript_columns(
        self,
    ):
        self.write_csv(self.reviewed, ["case_id"], [["not-in-this-week"]])
        with self.assertRaisesRegex(InputError, "weekly population"):
            prepare(self.plan)
        self.write_csv(self.reviewed, ["case_id"], [])
        for columns, rows in [
            (CASE_COLUMNS, [self.rows[0], self.rows[0]]),
            (CASE_COLUMNS, [(*self.rows[0][:3], "maybe")]),
            (CASE_COLUMNS, [self.rows[0][:3]]),
            (CASE_COLUMNS, [("a\nb", *self.rows[0][1:])]),
            (CASE_COLUMNS, [(" padded", *self.rows[0][1:])]),
            (CASE_COLUMNS + ("transcript",), [(*self.rows[0], "private")]),
            (CASE_COLUMNS + ("revision",), [(*self.rows[0], "extra")]),
        ]:
            with (
                self.subTest(columns=columns, rows=rows),
                self.assertRaises(InputError),
            ):
                self.write_csv(self.population, columns, rows)
                prepare(self.plan)

    def test_run_state_fails_closed_on_bad_format_and_membership(self):
        state = prepare(self.plan)
        for updates in (
            dict(sampling="new-v7"),
            dict(seed="oops"),
            dict(week="2026-99-01"),
            dict(population_count=20),
            dict(cases=state["cases"] * 2),
            dict(
                cases=[{**state["cases"][0], "case_id": "case00"}, *state["cases"][1:]]
            ),
            dict(
                strata={
                    "flagged": {"population": 4, "sample": 0},
                    "unflagged": {"population": 8, "sample": 2},
                }
            ),
        ):
            with self.subTest(updates=updates), self.assertRaises(InputError):
                summarize({**state, **updates})

    def test_model_receipt_binds_full_population_including_reviewed_and_version(self):
        version = "local-model:test-v1"
        self.write_plan(screen=version)
        with self.assertRaisesRegex(InputError, "screen_receipt"):
            prepare(self.plan)
        self.write_plan(screen=version, extra='screen_receipt = "receipt.json"')
        receipt = {
            "schema": 1,
            "screen_version": version,
            "population_sha256": hashlib.sha256(
                self.population.read_bytes()
            ).hexdigest(),
        }
        receipt_path = self.root / "receipt.json"
        receipt_path.write_text(json.dumps(receipt))
        self.assertEqual(
            fingerprint(receipt), prepare(self.plan)["screen_receipt_digest"]
        )
        receipt_path.write_text(json.dumps({**receipt, "screen_version": "earlier"}))
        with self.assertRaisesRegex(InputError, "screen receipt"):
            prepare(self.plan)
        receipt_path.write_text(json.dumps(receipt))
        self.write_csv(
            self.population,
            CASE_COLUMNS,
            [(*self.rows[0][:3], "false")] + self.rows[1:],
        )
        with self.assertRaisesRegex(InputError, "screen receipt"):
            prepare(self.plan)


class ConfidenceBoundTests(unittest.TestCase):
    def test_exact_limits_match_full_hypergeometric_inversion_including_complements(
        self,
    ):
        # Independent enumeration for every possible observation in small populations.
        for size in range(1, 16):
            for sample in range(1, size + 1):
                choices = math.comb(size, sample)
                for found in range(sample + 1):
                    accepted = []
                    for serious in range(size + 1):
                        counts = [
                            math.comb(serious, k)
                            * math.comb(size - serious, sample - k)
                            for k in range(sample + 1)
                        ]
                        if (
                            min(sum(counts[: found + 1]), sum(counts[found:])) * 40
                            >= choices
                        ):
                            accepted.append(serious)
                    expected = (min(accepted), max(accepted))
                    self.assertEqual(
                        expected,
                        _interval(size, sample, found, 1),
                        (size, sample, found),
                    )

    def test_two_strata_total_interval_has_simultaneous_coverage(self):
        size1, size2 = 21, 31
        sample1, sample2 = 4, 6
        serious1, serious2 = 7, 10
        lower_bound_hits = Fraction(0)
        for a in range(sample1 + 1):
            for b in range(sample2 + 1):
                lo1, hi1 = _interval(size1, sample1, a, 2)
                lo2, hi2 = _interval(size2, sample2, b, 2)
                if lo1 + lo2 <= serious1 + serious2 <= hi1 + hi2:
                    lower_bound_hits += Fraction(
                        math.comb(serious1, a)
                        * math.comb(size1 - serious1, sample1 - a),
                        math.comb(size1, sample1),
                    ) * Fraction(
                        math.comb(serious2, b)
                        * math.comb(size2 - serious2, sample2 - b),
                        math.comb(size2, sample2),
                    )
        self.assertGreaterEqual(lower_bound_hits, Fraction(95, 100))

    def test_zero_rare_cases_comes_with_a_nonzero_upper_bound(self):
        low, high = _interval(10_000, 200, 0, 1)
        self.assertEqual(0, low)
        self.assertGreaterEqual(high, 10)
        self.assertLess(high, 200)
        self.assertLess(_interval(10_000, 500, 0, 1)[1], high)

    def test_conservative_large_frame_and_near_census_intervals(self):
        self.assertEqual((2, 2), _interval(10_000, 9_999, 2, 1))
        low, high = _interval(100_000, 5_000, 2_500, 2)
        self.assertTrue(2_500 <= low <= 50_000 <= high <= 97_500)
        self.assertLess(high - low, 5_000)
