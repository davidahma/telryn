"""Independent checks for bounded allocation, missing outcomes and rule selection."""

import csv
import itertools
import math
import os
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from telryn.contracts import InputError
from telryn.planning import design, preview
from telryn.proposal import _candidate_sql, _search
from telryn.weekly import _interval, prepare, summarize
from telryn.replay import SqlSelector


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trust = self.root / "external-trust"
        env = patch.dict(os.environ, {"TELRYN_TRUST_DIR": str(self.trust)})
        env.start()
        self.addCleanup(env.stop)
        self.plan = self.root / "week.toml"
        self.plan.write_text(
            'schema=1\nweek="2026-11-02"\nrubric="r1"\nrubric_question="Escalation missed?"\nscreen_version="risk-v1"\npopulation="p.csv"\nreviewed="r.csv"\ngroup_by=["channel","agent_type"]\nflagged_sample=2\nunflagged_sample=2\n'
        )
        (self.root / "r.csv").write_text("case_id\n")
        with (self.root / "p.csv").open("w", newline="") as stream:
            w = csv.writer(stream)
            w.writerow(
                [
                    "case_id",
                    "revision",
                    "evidence_ref",
                    "screen_flag",
                    "channel",
                    "agent_type",
                    "product",
                ]
            )
            for i in range(20):
                w.writerow(
                    [
                        f"q{i}",
                        "r1",
                        f"ref{i}",
                        str(i < 10).lower(),
                        "email" if i % 2 else "chat",
                        "AI" if i < 10 else "human",
                        "billing",
                    ]
                )

    def test_preview_and_committed_draw_use_same_budget_by_group(self):
        config = dict(
            minutes=18, minutes_per_grade=3, expected_rate=0.1, flagged_rate=0.4
        )
        result = preview(self.plan, **config)
        self.assertFalse(self.trust.exists())
        self.assertEqual(result["assigned"], 6)
        self.assertEqual(len(result["allocations"]), 4)
        self.assertTrue(result["coverage_complete"])
        with self.plan.open("a") as f:
            f.write(
                "[planning]\nminutes=18\nminutes_per_grade=3\nexpected_rate=0.1\nflagged_rate=0.4\n"
            )
        self.assertEqual(result, preview(self.plan))
        state = prepare(self.plan)
        self.assertEqual(state["planning"], result)
        self.assertEqual(result["assigned"], len(state["cases"]))
        self.assertEqual(
            {r["key"]: r["sample"] for r in result["allocations"]},
            {k: v["sample"] for k, v in state["strata"].items()},
        )
        self.assertTrue(all("product" not in r for r in state["cases"]))
        self.assertTrue(self.trust.exists())
        state["grades"] = {
            r["case_id"]: self.grade(r["channel"] == "chat") for r in state["cases"]
        }
        self.assertEqual(summarize(state)["estimate"], 10)
        self.assertEqual(summarize(state)["agreement"], 0.5)

    def test_partial_bounds_include_every_completion_of_pending_and_insufficient(self):
        state = prepare(self.plan)
        rows = state["cases"]
        state["grades"] = {
            rows[0]["case_id"]: self.grade(True),
            rows[1]["case_id"]: self.grade(None),
        }
        partial = summarize(state)
        estimates, intervals = [], []
        for labels in itertools.product([True, False], repeat=len(rows) - 1):
            state["grades"].update(
                {r["case_id"]: self.grade(v) for r, v in zip(rows[1:], labels)}
            )
            r = summarize(state)
            estimates.append(r["estimate"])
            intervals.append(r["interval"])
        self.assertEqual(partial["estimate_range"], [min(estimates), max(estimates)])
        self.assertEqual(
            partial["interval"],
            [min(i[0] for i in intervals), max(i[1] for i in intervals)],
        )
        self.assertEqual(partial["range_kind"], "incomplete")
        self.assertEqual(partial["confirmed_serious"], 1)

    @staticmethod
    def grade(value):
        return {
            "serious": value,
            "disposition": "unknown" if value is None else ("new" if value else "none"),
            "owner": "lead",
            "action": "audit",
            "minutes": 2,
        }

    def test_budget_limits_partial_coverage_and_empty_frame(self):
        r = preview(self.plan, minutes=9, minutes_per_grade=3, expected_rate=0.4)
        self.assertFalse(r["coverage_complete"])
        self.assertIsNone(r["expected_interval_width"])
        with self.plan.open("a") as f:
            f.write("[planning]\nminutes=9\nminutes_per_grade=3\nexpected_rate=0.1\n")
        with self.assertRaisesRegex(InputError, "cover every"):
            prepare(self.plan)
        self.assertFalse(self.trust.exists())
        with self.assertRaisesRegex(InputError, "probability"):
            preview(
                self.plan, minutes=6, minutes_per_grade=3, expected_rate=float("nan")
            )

    def test_forecast_matches_brute_force_expectation_on_small_frame(self):
        N, n, K = 12, 4, 4
        result = design(
            {"unflagged": {"population": N}},
            {"minutes": n, "minutes_per_grade": 1, "expected_rate": K / N},
        )
        weights = [
            Fraction(math.comb(K, i) * math.comb(N - K, n - i), math.comb(N, n))
            for i in range(n + 1)
        ]
        expected = sum(
            w * (hi - lo)
            for i, w in enumerate(weights)
            for lo, hi in [_interval(N, n, i, 1)]
        )
        self.assertEqual(result["expected_interval_width"], round(float(expected), 2))

    def test_large_tail_inversion_matches_exact_kernel_and_preserves_precision(self):
        for N, n in ((21, 7), (24, 15), (5000, 1001)):
            for y in [0, 1, 2, n // 2, n - 1, n] if N > 24 else range(n + 1):
                with patch("telryn.weekly.EXACT_LIMIT", N):
                    expected = _interval(N, n, y, 2)
                with patch("telryn.weekly.EXACT_LIMIT", 0):
                    self.assertEqual(_interval(N, n, y, 2), expected)
        self.assertEqual(_interval(5000, 1000, 0, 1)[1], _interval(5000, 1001, 0, 1)[1])

    def test_weak_census_abstains_and_strong_segment_beats_flag(self):
        weak = {
            "strata": {
                "flagged": {"population": 4, "sample": 4},
                "unflagged": {"population": 4, "sample": 4},
            },
            "cases": [{"case_id": str(i), "screen_flag": i < 4} for i in range(8)],
            "grades": {str(i): self.grade(i == 0) for i in range(8)},
        }
        with self.assertRaisesRegex(InputError, "inconclusive"):
            _search(weak, 1)
        self.plan.write_text(
            self.plan.read_text()
            .replace('["channel","agent_type"]', '["product"]')
            .replace("sample=2", "sample=100")
        )
        with (self.root / "p.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                ["case_id", "revision", "evidence_ref", "screen_flag", "product"]
            )
            for i in range(240):
                writer.writerow(
                    [
                        f"q{i}",
                        "r1",
                        "ref",
                        int(i % 2),
                        "owner's card" if i < 120 else "deposit",
                    ]
                )
        state = prepare(self.plan)
        state["grades"] = {
            r["case_id"]: self.grade(r["product"] == "owner's card")
            for r in state["cases"]
        }
        suggestion = _search(state, 3)
        self.assertEqual(suggestion["predicate"], {"product": "owner's card"})
        self.assertEqual(suggestion["reserved_seats"], 3)
        sql = self.root / "candidate.sql"
        sql.write_text(_candidate_sql(suggestion["predicate"], 3))
        rule = SqlSelector(
            self.root,
            {
                "file": sql.name,
                "version": "test",
                "required_inputs": ["product", "priority"],
            },
        )
        rows = {
            str(i): {
                "inputs": {
                    "product": "owner's card" if i >= 3 else "deposit",
                    "priority": 10 - i,
                }
            }
            for i in range(6)
        }
        self.assertEqual(rule.run(rows, 2), ["3", "4"])
        self.assertEqual(rule.run(rows, 4), ["3", "4", "5", "0"])

    def test_proposal_can_select_an_intersection_across_both_flag_groups(self):
        from telryn.weekly import _stratum

        dims = ["product", "channel"]
        state = {"group_by": dims, "strata": {}, "cases": [], "grades": {}}
        for flag in (False, True):
            for product in ("card", "loan"):
                for channel in ("chat", "phone"):
                    groups = {"product": product, "channel": channel}
                    fields = {"screen_flag": flag, **groups}
                    key = _stratum(fields, dims)
                    state["strata"][key] = {
                        "population": 80,
                        "sample": 80,
                        "screen_flag": flag,
                        "groups": groups,
                    }
                    for i in range(80):
                        case = f"{key}:{i}"
                        state["cases"].append({"case_id": case, **fields})
                        state["grades"][case] = self.grade(
                            product == "card" and channel == "chat"
                        )
        suggestion = _search(state, 2)
        self.assertEqual(
            suggestion["predicate"], {"channel": "chat", "product": "card"}
        )
        self.assertEqual(suggestion["reserved_seats"], 2)
