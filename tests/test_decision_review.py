"""Regression checks for cutoff precision and the grading return lifecycle."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from examples.refunds import app, baseline
from telryn.contracts import InputError, timestamp
from telryn.grades import COLUMNS, initial_grades
from telryn.intake import inspect
from telryn.report import recommendation
from telryn.session import compare, publish, refresh

REFUNDS = Path(__file__).resolve().parents[1] / "examples" / "refunds"


class DecisionReview(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def packet(self):
        state = compare(REFUNDS / "comparison.toml")
        report = publish(self.root / "comparison", state)
        with report.with_name("grading.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            rows = {
                state["packet"][entry["row_id"]]["case"]: entry
                for entry in csv.DictReader(stream)
            }
        return report, rows

    def returned(self, rows):
        path = self.root / "returned.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_insufficient_adjudication_requires_resolution_after_new_definitive_review(
        self,
    ):
        report, rows = self.packet()
        agreed = {
            "R-091": "outside",
            "R-101": "meets",
            "R-113": "meets",
            "R-114": "meets",
            "R-118": "meets",
        }
        path = self.returned(
            [{**rows[case], "grade": grade} for case, grade in agreed.items()]
        )
        result = recommendation(refresh(report, path, None, review_id="first"))
        self.assertEqual(result["recommendation"], "Try in shadow")

        def submit(grade, reviewer):
            path = self.returned([{**rows["R-091"], "grade": grade}])
            return recommendation(refresh(report, path, None, review_id=reviewer))

        row_id = rows["R-091"]["row_id"]

        def resolve(result, grade):
            revision = result["adjudication"][row_id]["review_set"]
            return recommendation(
                refresh(
                    report,
                    None,
                    (row_id, grade, "Independent read"),
                    expected_review_set=revision,
                )
            )

        initial_dispute = submit("meets", "second")
        insufficient = resolve(initial_dispute, "insufficient")
        self.assertEqual(insufficient["pending_cross_checks"], 1)
        reopened = submit("outside", "fourth")
        self.assertEqual(reopened["recommendation"], "Inconclusive")
        self.assertEqual(reopened["pending_cross_checks"], 1)
        self.assertEqual(reopened["adjudication"][row_id]["request"], "R-091")
        settled = resolve(reopened, "outside")
        self.assertEqual(settled["recommendation"], "Try in shadow")

        ledger = json.loads((REFUNDS / "review-ledger.json").read_text())
        ledger[-1]["grade"] = "insufficient"
        ledger.append({**ledger[0], "id": "fourth"})
        reference = baseline.analyze(
            json.loads((REFUNDS / "cutoff.json").read_text()),
            json.loads((REFUNDS / "known-grades.json").read_text()),
            ledger,
        )
        self.assertEqual(reference["disputed"], ["R-091"])
        self.assertEqual(reference["recommendation"], reopened["recommendation"])

    def test_duplicate_rows_reject_the_entire_delivery(self):
        report, rows = self.packet()
        before = report.read_bytes()
        row = rows["R-091"]
        for first, duplicate in [
            ("outside", "meets"),
            ("outside", "outside"),
            ("", "outside"),
        ]:
            with self.subTest(first=first, duplicate=duplicate):
                path = self.returned(
                    [{**row, "grade": first}, {**row, "grade": duplicate}]
                )
                with self.assertRaisesRegex(InputError, "duplicate row"):
                    refresh(report, path, None)
                self.assertEqual(report.read_bytes(), before)
        path = self.returned([{**row, "grade": "outside"}])
        accepted = refresh(report, path, None)
        self.assertEqual(refresh(report, path, None), accepted)

    def test_packet_is_owner_only_under_permissive_umask(self):
        old_mask = os.umask(0)
        try:
            report, _ = self.packet()
        finally:
            os.umask(old_mask)
        self.assertEqual(
            stat.S_IMODE(report.with_name("grading.csv").stat().st_mode), 0o600
        )

    def test_nanosecond_availability_is_compared_as_an_instant(self):
        export = json.loads((REFUNDS / "cutoff.json").read_text())
        batch = export["shifts"]["tuesday-am"]
        batch["requests"]["R-001"]["known_at"] = "2026-04-07T11:00:00.000000001+02:00"
        path = self.root / "export.json"
        path.write_text(json.dumps(export))
        result = recommendation(compare(REFUNDS / "shadow.toml", path))
        self.assertEqual(result["recommendation"], "Inconclusive")
        self.assertIn("later than the cutoff", result["shifts"]["tuesday-am"]["reason"])
        archived = json.loads((REFUNDS / "known-grades.json").read_text())
        self.assertEqual(baseline.analyze(export, archived)["batch"], "Not replayable")
        batch["cutoff"] = "2026-04-07T09:00:00.0000000010Z"
        path.write_text(json.dumps(export))
        self.assertEqual(
            compare(REFUNDS / "shadow.toml", path)["shifts"]["tuesday-am"]["status"],
            "Replayable",
        )
        self.assertEqual(baseline.analyze(export, archived)["batch"], "Replayable")
        with self.assertRaises(InputError):
            timestamp("2026-04-07T09:00:00+00:00:00.5", "cutoff")

    def test_evidence_window_retains_cutoff_fraction(self):
        state = compare(REFUNDS / "comparison.toml")
        shift = state["shifts"]["tuesday-am"]
        shift["cutoff"] = "2026-04-07T09:00:00.000000001Z"
        archive = json.loads((REFUNDS / "known-grades.json").read_text())
        archive["grades"] = {"R-101": archive["grades"]["R-101"]}
        path = self.root / "grades.json"
        for instant, valid in [
            ("2026-04-07T09:00:00.000000000Z", False),
            ("2026-04-07T09:00:00.000000001Z", True),
            ("2026-04-08T10:00:00.0000000010+01:00", True),
            ("2026-04-08T09:00:00.000000002Z", False),
        ]:
            with self.subTest(instant=instant):
                archive["grades"]["R-101"]["evidence_at"] = instant
                path.write_text(json.dumps(archive))
                accepted, _, excluded = initial_grades(
                    path, state["plan"], state["shifts"]
                )
                reference, omitted, _ = baseline.worklist(
                    ["R-101"],
                    [],
                    archive,
                    shift["rows"],
                    baseline.instant(shift["cutoff"]),
                )
                self.assertEqual("R-101" in accepted, valid)
                self.assertEqual("R-101" in excluded, not valid)
                self.assertEqual(reference, accepted)
                self.assertEqual(omitted, set(excluded))

    def test_sqlite_source_selects_the_exact_cutoff_revision(self):
        database = self.root / "refund.sqlite"
        app.seed(database)
        with closing(sqlite3.connect(database)) as connection:
            for instant, expected in [
                ("2026-04-07T11:00:00.000000001+02:00", "1"),
                ("2026-04-07T11:00:00.000000000+02:00", "2"),
            ]:
                with self.subTest(instant=instant):
                    connection.execute(
                        "UPDATE revision SET known_at=? WHERE request='R-121' AND revision='2'",
                        (instant,),
                    )
                    connection.commit()
                    export, _ = app.snapshot(connection)
                    self.assertEqual(
                        export["shifts"]["tuesday-am"]["requests"]["R-121"]["revision"],
                        expected,
                    )

    def test_sqlite_input_cannot_silently_round_to_another_cases_score(self):
        qa = REFUNDS.parent / "support-qa"
        _, export = inspect(qa / "intake.toml")
        rows = export["shifts"]["week-1"]["requests"]
        path = self.root / "export.json"
        for index, row in enumerate(rows.values(), start=1):
            row["inputs"]["risk"] = 2**63 + index
        path.write_text(json.dumps(export))
        sql = compare(qa / "comparison.toml", path)["shifts"]["week-1"]
        self.assertEqual(sql["status"], "Not replayable")
        self.assertIn("64-bit integer", sql["reason"])
        python = compare(qa / "legacy.toml", path)["shifts"]["week-1"]
        self.assertEqual(python["candidate"][:2], ["Q-012", "Q-011"])
        rows["Q-001"]["inputs"]["risk"] = -(2**63)
        for row in list(rows.values())[1:]:
            row["inputs"]["risk"] = 2**63 - 1
        path.write_text(json.dumps(export))
        self.assertEqual(
            compare(qa / "comparison.toml", path)["shifts"]["week-1"]["status"],
            "Replayable",
        )

    def test_refund_worksheet_distinguishes_policy_gaps_and_optional_evidence(self):
        export = json.loads((REFUNDS / "cutoff.json").read_text())
        archived = json.loads((REFUNDS / "known-grades.json").read_text())
        batch = export["shifts"]["tuesday-am"]
        batch["mandatory"].append("missing case")
        self.assertEqual(baseline.analyze(export, archived)["batch"], "Not replayable")
        batch["mandatory"].pop()
        row = batch["requests"]["R-101"]
        del row["evidence"]
        row["minutes"] = {"lower": 1, "basis": "estimated"}
        path = self.root / "export.json"
        path.write_text(json.dumps(export))
        state = compare(REFUNDS / "comparison.toml", path)
        result = recommendation(state)
        self.assertIn(
            "upper unknown", publish(self.root / "unbounded", state).read_text()
        )
        reference = baseline.analyze(export, archived)
        self.assertEqual(reference["batch"], "Replayable")
        self.assertEqual(reference["bounds"], result["bounds"])
        self.assertEqual(
            reference["unknown"], sorted(result["shifts"]["tuesday-am"]["unknown"])
        )
        self.assertIsNone(reference["workload"][1])
        self.assertEqual(reference["recommendation"], result["recommendation"])
        row["inputs"]["refund_cents"] = 2**63
        self.assertEqual(baseline.analyze(export, archived)["batch"], "Not replayable")
        path.write_text('{"schema": 1, "schema": 1}')
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            baseline.read(path)
