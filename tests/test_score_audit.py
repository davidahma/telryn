"""External QA scores, blinded human classification and register deadlines."""

import csv
import json
import tempfile
import unittest
from datetime import datetime
from itertools import combinations, product
from pathlib import Path

from telryn import (
    complaints,
    integrations_coverage,
    proposal,
    score_audit,
    weekly,
    weekly_report,
)
from telryn.contracts import InputError
from tests.audit_support import isolate_trust


class ScoreAuditTests(unittest.TestCase):
    cutoff = "2026-11-09T08:00:00Z"
    due = "2026-11-07T08:00:00Z"
    closed = "2026-11-05T12:00:00Z"

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        isolate_trust(self, self.root)
        ids = list("ABCDEF")
        self.population = self.root / "population.csv"
        receipt = integrations_coverage.new_receipt(
            datetime(2026, 11, 2), datetime(2026, 11, 9), "Loan queue", "event-ledger"
        )
        receipt.update(
            dimensions=[],
            completed_at=dict.fromkeys(ids, self.closed),
            scanned=6,
            eligible=6,
        )
        integrations_coverage.publish(
            self.population,
            [(i, "v1", "chat/" + i, "true" if i in "AB" else "false") for i in ids],
            receipt,
        )
        (self.root / "reviewed.csv").write_text("case_id\nA\nF\n")
        self.plan = self.root / "plan.toml"
        self.config = """schema=1
week="2026-11-02"
rubric="complaints-v1"
rubric_question="Does this contact meet our complaint definition?"
screen_version="operator-v1"
population="population.csv"
reviewed="reviewed.csv"
flagged_sample="100%"
unflagged_sample="100%"
[score_audit]
file="scores.csv"
scorecard="bank-qa"
version="v2"
scope="all_scored"
cutoff="2026-11-09T08:00:00Z"
"""
        self.plan.write_text(self.config)
        self.scores = [
            dict(
                case_id=i,
                revision="v1",
                scorecard="bank-qa",
                scorecard_version="v2",
                scored_at=self.closed,
                decision="fail" if i == "C" else "pass",
                complaint_prediction=p,
                score_ref="scores/" + i,
            )
            for i, p in zip(ids, ["no", "yes", "no", "unknown", "no"])
        ]
        self.write("scores.csv", score_audit.COLUMNS, self.scores)

    def write(self, name, cols, rows):
        with (self.root / name).open("w", newline="") as f:
            writer = csv.DictWriter(f, cols)
            writer.writeheader()
            writer.writerows(rows)

    def manifest(self, report):
        with report.with_name("grades.csv").open(newline="", encoding="utf-8-sig") as f:
            packet = list(csv.DictReader(f))
        for row in packet:
            grade = "no" if row["case_id"] == "E" else "yes"
            row.update(
                serious=grade,
                disposition="none" if grade == "no" else "already_known",
                minutes=2,
            )
        self.write("returns.csv", packet[0], packet)
        weekly_report.refresh(report, self.root / "returns.csv", "blind lead")
        manifest = dict(
            schema=1,
            audit_id=packet[0]["audit_id"],
            rubric="complaints-v1",
            scope="Loan queue",
            cutoff=self.cutoff,
            as_of="2026-11-11T08:00:00Z",
            register=dict(
                file="register.csv", source_ref="r1", complete_through=self.due
            ),
            actions=dict(
                file="actions.csv", source_ref="a1", complete_through=self.cutoff
            ),
            cases=[
                dict(
                    case_id=r["case_id"],
                    revision="v1",
                    deadline=self.due
                    if r["case_id"] != "D"
                    else "2026-11-10T11:00:00Z",
                    policy_ref="firm/v1",
                    match_key=r["case_id"],
                )
                for r in packet
            ],
        )
        entries = [
            dict(
                match_key=x,
                entry_id="register-" + x,
                recorded_at=t,
                evidence_ref="ledger/" + x,
            )
            for x, t in [("A", self.cutoff), ("C", self.due)]
        ]
        self.write("register.csv", complaints.REGISTER, entries)
        self.write("actions.csv", complaints.ACTIONS, [])
        return manifest

    def test_human_grade_is_separate_from_failure_to_register(self):
        report = weekly_report.start(self.plan, self.root / "run")
        blind = report.with_name("grades.csv").read_text()
        self.assertNotIn("scores/", blind)
        self.assertNotIn("complaint_prediction", blind)
        self.assertNotIn("scorecard", blind)
        summary = weekly.summarize(weekly_report.read_state(report))["score_audit"]
        self.assertEqual(summary["passed_deadline_miss"]["count_interval"], [0, 4])
        manifest = self.manifest(report)
        file = self.root / "records.json"
        file.write_text(
            json.dumps({**manifest, "cutoff": "2026-11-09T08:00:00.000000001Z"})
        )
        with self.assertRaisesRegex(InputError, "frozen score audit cutoff"):
            weekly_report.attach_complaints(report, file)
        file.write_text(json.dumps(manifest))
        state = weekly_report.attach_complaints(report, file)
        result = weekly.summarize(state)["score_audit"]
        self.assertEqual(
            result["coverage"],
            dict(
                source_population=6,
                scored=5,
                unscored=1,
                ordinary_reviewed=2,
                excluded_previously_reviewed=0,
                eligible_previously_reviewed=1,
                eligible_scored=5,
                eligible_passes=4,
                eligible_classified=4,
            ),
        )
        traces = {r["case_id"]: r for r in result["cases"]}
        self.assertEqual(traces["A"]["registration_at_cutoff"], "late")
        self.assertIs(traces["A"]["classification_disagreement"], True)
        # Source coverage ends at the deadline. Missing then is definite; missing at cutoff remains unresolved.
        self.assertEqual(traces["B"]["registration_at_cutoff"], "unresolved")
        self.assertIs(traces["B"]["deadline_miss"], True)
        self.assertIs(traces["B"]["classification_disagreement"], False)
        self.assertEqual(traces["D"]["registration_at_cutoff"], "not_due")
        self.assertIs(traces["D"]["deadline_miss"], False)
        for k in ("classification_disagreement", "passed_deadline_miss"):
            self.assertEqual(result[k]["denominator"], 4)
            self.assertEqual(result[k]["interval"], [0.5, 0.5])
            self.assertEqual(result[k]["estimate"], 0.5)
        self.assertIn("scores/A", report.read_text())
        self.assertEqual(weekly_report._envelope(report.read_bytes())["schema"], 6)
        self.assertEqual(blind, report.with_name("grades.csv").read_text())

    def test_header_only_export_does_not_claim_the_queue_was_clean(self):
        self.write("scores.csv", score_audit.COLUMNS, [])
        report = weekly_report.start(self.plan, self.root / "empty")
        result = weekly.summarize(weekly_report.read_state(report))["score_audit"]
        self.assertEqual(result["coverage"]["unscored"], 6)
        self.assertEqual(result["cases"], [])
        for key in score_audit.MEASURES:
            self.assertEqual(result[key]["denominator"], 0)
            self.assertIsNone(result[key]["estimate"])
            self.assertIsNone(result[key]["interval"])
        with self.assertRaisesRegex(InputError, "ordinary unreviewed queue"):
            proposal.propose(report, self.root / "proposal")

    def test_size_and_dated_coverage_checked_before_draw(self):
        file = self.root / "scores.csv"
        with file.open("wb") as stream:
            stream.truncate(score_audit.MAX_EXPORT + 1)
        with self.assertRaisesRegex(InputError, "24 MiB"):
            weekly.prepare(self.plan)
        self.population.with_suffix(".csv.coverage.json").unlink()
        with self.assertRaisesRegex(InputError, "dated closures"):
            weekly.prepare(self.plan)

    def test_scope_and_frozen_selection(self):
        self.plan.write_text(self.config.replace("all_scored", "unreviewed_scored"))
        first = weekly.prepare(self.plan)
        self.assertEqual([c["case_id"] for c in first["cases"]], list("BCDE"))
        self.assertEqual(
            first["score_audit"]["coverage"]["excluded_previously_reviewed"], 1
        )
        self.write("scores.csv", score_audit.COLUMNS, reversed(self.scores))
        second = weekly.prepare(self.plan)
        self.assertEqual(first["seed"], second["seed"])
        self.assertNotEqual(first["draw_commitment"], second["draw_commitment"])
        self.assertNotEqual(
            first["score_audit"]["sha256"], second["score_audit"]["sha256"]
        )
        self.scores[1]["decision"] = "fail"
        self.write("scores.csv", score_audit.COLUMNS, self.scores)
        changed = weekly.prepare(self.plan)
        self.assertNotEqual(first["draw_commitment"], changed["draw_commitment"])
        self.assertEqual(first["seed"], changed["seed"])

    def test_all_scored_review_ledger_edits_cannot_redraw_the_same_eligible_cases(self):
        self.plan.write_text(self.config.replace('"100%"', "1"))
        before = weekly.prepare(self.plan)
        (self.root / "reviewed.csv").write_text("case_id\nB\nF\n")
        after = weekly.prepare(self.plan)
        self.assertEqual(before["seed"], after["seed"])
        self.assertEqual(before["cases"], after["cases"])
        self.assertNotEqual(before["draw_commitment"], after["draw_commitment"])

    def test_score_record_must_match_identity_revision_version_and_time(self):
        for field, value in [
            ("revision", "v2"),
            ("case_id", "Z"),
            ("scorecard_version", "v0"),
            ("scorecard", "other"),
            ("score_ref", "scores/B"),
            ("scored_at", "2026-11-05T11:59:59.999999999Z"),
            ("scored_at", "2026-11-09T08:00:00.000000001Z"),
            ("decision", "unknown"),
            ("complaint_prediction", "pass"),
        ]:
            with self.subTest(field=field, value=value):
                self.write(
                    "scores.csv",
                    score_audit.COLUMNS,
                    [{**self.scores[0], field: value}, *self.scores[1:]],
                )
                with self.assertRaises(InputError):
                    weekly.prepare(self.plan)
        self.write("scores.csv", score_audit.COLUMNS, [*self.scores, self.scores[0]])
        with self.assertRaisesRegex(InputError, "duplicate"):
            weekly.prepare(self.plan)

    def test_unseen_domain_has_full_bounds_and_zero_denominator_is_undefined(self):
        self.write(
            "scores.csv",
            score_audit.COLUMNS,
            [{**r, "decision": "fail"} for r in self.scores],
        )
        self.plan.write_text(
            self.config.replace('unflagged_sample="100%"', "unflagged_sample=0")
        )
        metrics = weekly.summarize(weekly.prepare(self.plan))["score_audit"]
        self.assertIsNone(metrics["passed_deadline_miss"]["interval"])
        self.assertEqual(metrics["passed_deadline_miss"]["denominator"], 0)
        self.assertEqual(
            metrics["classification_disagreement"]["range_kind"], "uncovered"
        )
        self.assertEqual(metrics["classification_disagreement"]["interval"], [0, 1])

    def test_conditional_domain_interval_contains_truth_in_at_least_95_percent_of_draws(
        self,
    ):
        # Independent enumeration of original stratified draws, with mixed
        # pass/fail denominators and one unresolved human label in each draw.
        pools = {"a": (1, 1, 0, None), "b": (1, 0, 0, None, None)}
        population = {"a": 3, "b": 3}  # None here represents a nonmember.
        draws = covered = 0
        for a, b in product(combinations(range(4), 2), combinations(range(5), 2)):
            rows = [
                (cell, pools[cell][i])
                for cell, choice in (("a", a), ("b", b))
                for i in choice
                if pools[cell][i] is not None
            ]
            if rows:
                rows[0] = (rows[0][0], None)  # withhold one eligible observation
            low, high = score_audit._metric(population, rows)["count_interval"]
            covered += low <= 3 <= high
            draws += 1
        self.assertGreaterEqual(covered / draws, 0.95)
