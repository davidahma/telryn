"""Exercise the ordinary export, shadow handoff and pilot evidence boundaries."""

import csv
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from examples.refunds import app, baseline
from telryn import benchmark, intake, shadow
from telryn.contracts import InputError, read_json
from telryn.grades import COLUMNS, adjudicate, import_packet, packet_fields, review_set
from telryn.report import recommendation
from telryn.session import compare

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "examples/support-qa"
REFUNDS = ROOT / "examples/refunds"


class WorkflowReview(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_preflight_names_the_source_that_needs_repair(self):
        local = self.root / "qa"
        shutil.copytree(QA, local)
        for file, before, after, expected in (
            ("eligible.csv", "Q-012", "Q-011", "eligible"),
            ("picks.csv", "Q-008,8", "Q-008,eight", "picks"),
            ("picks.csv", "Q-008,8", "Q-007,8", "picks"),
            ("picks.csv", "Q-008,8", "Q-800,8", "picks"),
            ("cutoff-inputs.csv", "11:55:00Z", "12:05:00Z", "cutoff_inputs"),
            ("cutoff-inputs.csv", "120,145", "150,145", "cutoff_inputs"),
            ("intake.toml", "transcript_reference", "missing_column", "cutoff_inputs"),
            ("intake.toml", "2026-09-07T12:00:00Z", "invalid cutoff", "policy"),
        ):
            with self.subTest(file=file, changed=after):
                path = local / file
                original = path.read_text()
                path.write_text(original.replace(before, after))
                report, _ = intake.inspect(local / "intake.toml")
                self.assertFalse(report["ready"])
                self.assertEqual(report["shifts"]["week-1"]["source"], expected)
                self.assertEqual(
                    report["shifts"]["week-1"]["capture"], intake.CAPTURE[expected]
                )
                path.write_text(original)

    def test_preflight_preserves_score_order_or_identifies_lost_precision(self):
        local = self.root / "qa"
        shutil.copytree(QA, local)
        manifest = local / "intake.toml"
        source = local / "cutoff-inputs.csv"
        original = source.read_text()
        for score in ("0.1234567890123456789", "1e-999"):
            with self.subTest(value=score):
                source.write_text(original.replace(",99,99,99,", f",99,{score},99,"))
                report, _ = intake.inspect(manifest)
                issue = report["shifts"]["week-1"]
                self.assertEqual(issue["source"], "cutoff_inputs")
                self.assertIn("Q-001 input risk would lose precision", issue["reason"])
        source.write_text(
            original.replace(",99,99,99,", ",0.1,9.007199254740993e15,99,")
        )
        report, exported = intake.inspect(manifest)
        self.assertTrue(report["ready"])
        values = exported["shifts"]["week-1"]["requests"]["Q-001"]["inputs"]
        self.assertEqual(values["priority"], 0.1)
        self.assertIs(type(values["risk"]), int)
        self.assertEqual(values["risk"], 9007199254740993)
        manifest.write_text(
            manifest.read_text().replace(
                "overrides = []",
                'overrides = [{kind="include", request="Q-001", recorded_on=2026-09-06}]',
            )
        )
        report, _ = intake.inspect(manifest)
        self.assertEqual(report["shifts"]["week-1"]["source"], "policy")

    def test_direct_json_scores_use_the_same_precision_contract(self):
        path = self.root / "direct.json"
        for token in ("0.1234567890123456789", "1e-999", "NaN"):
            path.write_text('{"schema":1, "score":' + token + "}")
            with self.subTest(token=token), self.assertRaises(InputError):
                read_json(path, "export")
        path.write_text('{"schema":1, "score":9.007199254740993e15}')
        self.assertEqual(read_json(path, "export")["score"], 9007199254740993)

    def test_preflight_strict_csv_and_atomic_output(self):
        local = self.root / "qa"
        shutil.copytree(QA, local)
        with (local / "picks.csv").open("a") as stream:
            stream.write('"Q-009,9\n')
        report, _ = intake.inspect(local / "intake.toml")
        self.assertFalse(report["ready"])
        self.assertEqual(report["shifts"]["week-1"]["source"], "picks")
        out = self.root / "export.json"
        with patch.object(intake.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                intake.write_export(out, {"example": 1})
        self.assertFalse(out.exists())
        self.assertEqual(list(out.parent.glob(".telryn-intake-*")), [])
        intake.write_export(out, {"example": 1})
        with self.assertRaises(InputError):
            intake.write_export(out, {"example": 2})
        self.assertEqual(json.loads(out.read_text()), {"example": 1})

    def state(self, cutoff=None):
        _, snapshot = intake.inspect(QA / "intake.toml")
        if cutoff is not None:
            snapshot["shifts"]["week-1"]["cutoff"] = cutoff
        path = self.root / "cutoff.json"
        intake.write_export(path, snapshot)
        return compare(QA / "revised.toml", path)

    def test_shadow_records_only_declared_evidence_and_qualifying_revision(self):
        state = self.state()
        decision = recommendation(state)
        observed = json.loads((QA / "shadow-observations.json").read_text())
        observed["comparison_id"] = state["comparison_id"]
        dest = self.root / "observed.json"
        pick = observed["batches"]["week-2"]["picks"][0]
        for record in (
            observed,
            observed["lead_decision"],
            observed["batches"]["week-2"],
            pick,
        ):
            with self.subTest(fields=list(record)):
                record["private_material"] = "unrelated"
                dest.write_text(json.dumps(observed))
                with self.assertRaisesRegex(InputError, "unexpected"):
                    shadow.record(state, dest, decision)
                del record["private_material"]
        self.assertNotIn("shadow_trials", state)
        pick["minutes"] = 1
        dest.write_text(json.dumps(observed))
        self.assertTrue(shadow.record(state, dest, decision))
        self.assertFalse(shadow.record(state, dest, decision))
        self.assertEqual(
            shadow.summarize(state)[observed["trial_id"]]["decision_revision"],
            decision["decision_revision"],
        )
        pick["minutes"] = True
        dest.write_text(json.dumps(observed))
        with self.assertRaisesRegex(InputError, "different shadow observation"):
            shadow.record(state, dest, decision)
        # A report from an earlier release can still acknowledge an exact retry.
        state.pop("shadow_decision_revisions")
        stored = state["shadow_trials"][observed["trial_id"]]
        stored["old_metadata"] = "retained"
        state["schema"] = 2
        dest.write_text(json.dumps(stored))
        self.assertFalse(shadow.record(state, dest, decision))
        self.assertIsNone(
            shadow.summarize(state)[observed["trial_id"]]["decision_revision"]
        )

    def test_benchmark_date_respects_utc_and_cutoff(self):
        state = self.state("2026-09-07T23:30:00-04:00")
        decision = recommendation(state)
        data = json.loads((QA / "pilot-record.json").read_text())
        data.update(
            comparison_id=state["comparison_id"],
            decision_revision=decision["decision_revision"],
        )
        file = self.root / "benchmark.json"
        data["decision_date"] = "2026-09-07"
        file.write_text(json.dumps(data))
        with self.assertRaisesRegex(InputError, "predates"):
            benchmark.assess(state, file)
        data["decision_date"] = "2026-09-08"
        file.write_text(json.dumps(data))
        self.assertTrue(benchmark.assess(state, file)["timing_comparable"])

    def test_benchmark_recurring_effort_covers_recorded_times(self):
        state = compare(REFUNDS / "comparison.toml")
        returns = self.root / "returns.csv"
        answers = {
            "R-091": ("meets", "3"),
            "R-101": ("meets", "2"),
            "R-118": ("meets", "6"),
            "R-113": ("meets", ""),
        }
        with returns.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            for row_id, row in state["packet"].items():
                if row["case"] in answers:
                    grade, minutes = answers[row["case"]]
                    writer.writerow(
                        {
                            **packet_fields(state, row_id, row),
                            "grade": grade,
                            "minutes_spent": minutes,
                        }
                    )
        import_packet(returns, state)
        row_id = next(i for i, row in state["packet"].items() if row["case"] == "R-091")
        adjudicate(
            state,
            row_id,
            "outside",
            "Independent check",
            7,
            review_set(row_id, state["grade_history"][row_id]),
        )
        data = json.loads((QA / "pilot-record.json").read_text())
        data.update(
            comparison_id=state["comparison_id"],
            decision_revision=recommendation(state)["decision_revision"],
        )
        file = self.root / "pilot.json"
        for reviewer, adjudication, expected_error in (
            (10, 7, "below the 11 minutes"),
            (11, 6, "below the 7 minutes"),
            (20, 8, None),
        ):
            with self.subTest(reviewer=reviewer, adjudication=adjudication):
                data["telryn"].update(
                    reviewer_minutes=reviewer, adjudication_minutes=adjudication
                )
                file.write_text(json.dumps(data))
                if expected_error:
                    with self.assertRaisesRegex(InputError, expected_error):
                        benchmark.assess(state, file)
                else:
                    self.assertTrue(benchmark.assess(state, file)["timing_comparable"])

    def test_baseline_rejects_wrong_rubric_provenance_and_population(self):
        export = json.loads((REFUNDS / "cutoff.json").read_text())
        archived = json.loads((REFUNDS / "known-grades.json").read_text())
        worked = json.loads((REFUNDS / "review-ledger.json").read_text())
        with self.assertRaisesRegex(ValueError, "rubric|archive"):
            baseline.analyze(export, {**archived, "rubric": "other-policy"}, worked)
        case = archived["grades"]["R-091"]
        case["shared_sources"] = "false"
        with self.assertRaisesRegex(ValueError, "provenance"):
            baseline.analyze(export, archived, worked)
        case["shared_sources"] = True
        batch = export["shifts"]["tuesday-am"]
        batch["membership"]["ids"].append("R-091")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            baseline.analyze(export, archived, worked)

    def test_refund_export_is_one_snapshot_and_conflicts_leave_both_files_intact(self):
        db, dest = self.root / "refunds.sqlite", self.root / "export"
        app.seed(db)
        with (
            closing(sqlite3.connect(db)) as reader,
            closing(sqlite3.connect(db)) as writer,
        ):
            writer.execute("PRAGMA journal_mode=WAL")
            original = app.write_json

            def fail_second_write(path, value):
                if path.name == "known-grades.json":
                    raise OSError("disk full")
                original(path, value)

            with patch.object(app, "write_json", side_effect=fail_second_write):
                with self.assertRaisesRegex(OSError, "disk full"):
                    app.export_snapshot(reader, dest)
            self.assertFalse(dest.exists())
            self.assertFalse(list(self.root.glob(".refunds-export-*")))

            def update_during_read(statement):
                if statement.startswith("SELECT request, grade, evidence"):
                    writer.execute(
                        "UPDATE archived_grade SET grade='meets' WHERE request='R-091'"
                    )
                    writer.commit()

            reader.set_trace_callback(update_during_read)
            app.export_snapshot(reader, dest)
            reader.set_trace_callback(None)
            before = {file.name: file.read_bytes() for file in dest.iterdir()}
            self.assertEqual(
                json.loads(before["known-grades.json"])["grades"]["R-091"]["grade"],
                "outside",
            )
            with self.assertRaisesRegex(ValueError, "new export directory"):
                app.export_snapshot(reader, dest)
            self.assertEqual(
                {file.name: file.read_bytes() for file in dest.iterdir()}, before
            )
            with writer:
                writer.execute(
                    "UPDATE archived_grade SET grade='outside' WHERE request='R-091'"
                )
            app.export_snapshot(reader, dest)
            self.assertEqual(
                {file.name: file.read_bytes() for file in dest.iterdir()}, before
            )
