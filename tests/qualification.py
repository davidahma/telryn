"""Run the public CLI against independent expectations and controlled faults."""

from __future__ import annotations

import csv
import itertools
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from telryn.contracts import fingerprint
from telryn.decision import bounds, evaluate
from telryn.report import load_report, render_report

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "refunds"
QA = ROOT / "examples" / "support-qa"
CASES = (
    "demo",
    "membership",
    "cutoff",
    "bounds",
    "grade-return",
    "grading-evidence",
    "return-order",
    "revision-time",
    "lead-report",
    "capacity",
    "coverage",
    "handoff",
    "recovery",
    "repeatability",
    "qa-preflight",
    "qa-sql",
    "qa-serious",
    "qa-shadow",
    "qa-intake-reuse",
    "qa-benchmark",
)
TUESDAY = "tuesday-am"
SATURDAY = "saturday-am"
CUTOFF = "2026-04-07T09:00:00+00:00"


class Qualification(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telryn-qualification-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, *args, expected=0, module="telryn"):
        command = [sys.executable, "-m", module, *map(str, args)]
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, timeout=20
        )
        self.assertEqual(
            result.returncode, expected, f"{command}\n{result.stdout}\n{result.stderr}"
        )
        return result.stdout

    def comparison(
        self,
        name,
        plan=EXAMPLE / "comparison.toml",
        *,
        export=None,
        expected=0,
        gate=False,
    ):
        output = self.root / name
        args = ["compare", plan, "--out", output, "--format", "json"]
        if export is not None:
            args += ["--export", export]
        if gate:
            args += ["--require-shadow"]
        return output, json.loads(self.cli(*args, expected=expected))

    def explanation(self, output):
        return json.loads(
            self.cli("explain", output / "report.html", "--format", "json")
        )

    def existing_export(self):
        return json.loads((EXAMPLE / "cutoff.json").read_text(encoding="utf-8"))

    def write_export(self, data, name="changed.json"):
        destination = self.root / name
        destination.write_text(json.dumps(data), encoding="utf-8")
        return destination

    def plan_copy(self, source, **replacements):
        definition = self.root / "comparison.toml"
        content = (EXAMPLE / source).read_text(encoding="utf-8")
        for before, after in replacements.items():
            self.assertIn(before, content)
            content = content.replace(before, after)
        shutil.copy(EXAMPLE / "rules.py", self.root)
        shutil.copy(EXAMPLE / "known-grades.json", self.root)
        shutil.copy(EXAMPLE / "cutoff.json", self.root)
        definition.write_text(content, encoding="utf-8")
        return definition

    def filled(self, output, answers, filename, minutes=2):
        state = load_report(output / "report.html")
        with (output / "grading.csv").open(encoding="utf-8-sig", newline="") as source:
            packet = csv.DictReader(source)
            rows = list(packet)
            fields = packet.fieldnames
        path = self.root / filename
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                case = state["packet"][row["row_id"]]["case"]
                criterion = state["packet"][row["row_id"]].get("criterion", "primary")
                key = (case, criterion) if (case, criterion) in answers else case
                if key in answers:
                    row["grade"] = answers[key]
                    row["minutes_spent"] = str(minutes)
                    writer.writerow(row)
        return path

    def refresh(self, output, path, expected=0, flags=()):
        return json.loads(
            self.cli(
                "refresh",
                output / "report.html",
                "--grades",
                path,
                "--format",
                "json",
                *flags,
                expected=expected,
            )
        )

    def test_demo(self):
        out = self.root / "demo"
        text = self.cli("demo", "--out", out)
        for item in (
            "HOLD",
            "INCONCLUSIVE",
            "TRY IN SHADOW",
            "R-041",
            "3 favorable",
            "6 unfavorable",
            "8 open grades plus 2 cross-checks",
        ):
            self.assertIn(item, text)
        self.assertEqual(
            {path.name for path in out.iterdir()}, {"report.html", "grading.csv"}
        )
        result = self.explanation(out)
        self.assertEqual(result["bounds"], [-2, 6])
        tuesday = result["shifts"][TUESDAY]
        self.assertEqual(tuesday["shared"], 80)
        self.assertEqual(tuesday["gain"], [f"R-{i:03}" for i in range(101, 121)])
        self.assertEqual(
            tuesday["loss"], ["R-073", *[f"R-{i:03}" for i in range(82, 101)]]
        )
        report = (out / "report.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="en">', report)
        self.assertIn("Content-Security-Policy", report)
        self.assertNotIn("https://", report)

    def test_membership(self):
        data = self.existing_export()
        batch = data["shifts"][TUESDAY]
        self.assertNotIn("R-101", batch["actual"])
        record = batch["requests"].pop("R-101")
        _, missing = self.comparison("missing", export=self.write_export(data))
        self.assertIsNone(missing["bounds"])
        self.assertEqual(missing["shifts"][TUESDAY]["status"], "Not replayable")
        self.assertIn("R-101", missing["shifts"][TUESDAY]["reason"])
        batch["requests"]["R-101"] = record
        _, restored = self.comparison("restored", export=self.write_export(data))
        self.assertIn("R-101", restored["shifts"][TUESDAY]["gain"])

    def test_cutoff(self):
        data = self.existing_export()
        batch = data["shifts"][TUESDAY]
        batch["membership"]["ids"] = ["R-001", "R-002", "R-003", "R-004"]
        batch["requests"] = {
            case: batch["requests"][case] for case in batch["membership"]["ids"]
        }
        batch["capacity"] = 2
        batch["actual"] = ["R-001", "R-004"]
        batch["mandatory"] = []
        batch["overrides"] = [{"kind": "include", "request": "R-004"}]
        inputs = [(4, 3), (2, 1), (2, 4), (1, 0)]
        for row, (amount, conflict) in zip(batch["requests"].values(), inputs):
            row["inputs"].update(refund_cents=amount, conflict_priority=conflict)
        plan = self.plan_copy("comparison.toml", **{"capacity = 100": "capacity = 2"})
        out, tied = self.comparison("tied", plan, export=self.write_export(data))
        saved = load_report(out / "report.html")["shifts"][TUESDAY]
        self.assertEqual(saved["live"], ["R-001", "R-004"])
        self.assertEqual(saved["candidate"], ["R-003", "R-004"])
        self.assertEqual(tied["shifts"][TUESDAY]["loss"], ["R-001"])
        batch["overrides"] = []
        batch["actual"] = ["R-001", "R-002"]
        self.comparison("final-slot-tie", plan, export=self.write_export(data))
        batch["actual"] = ["R-001", "R-003"]
        _, wrong_tie = self.comparison(
            "wrong-tie", plan, export=self.write_export(data)
        )
        self.assertIn("slot 2", wrong_tie["shifts"][TUESDAY]["reason"])
        batch["overrides"] = [{"kind": "swap", "replace": "R-002", "request": "R-004"}]
        batch["actual"] = ["R-001", "R-004"]
        swapped, _ = self.comparison(
            "conditional-swap", plan, export=self.write_export(data)
        )
        self.assertEqual(
            load_report(swapped / "report.html")["shifts"][TUESDAY]["candidate"],
            ["R-003", "R-001"],
        )
        batch["overrides"] = [
            {"kind": "unresolved", "reason": "Discretionary final-slot swap"}
        ]
        _, unresolved = self.comparison(
            "override", plan, export=self.write_export(data)
        )
        self.assertIn("Discretionary", unresolved["shifts"][TUESDAY]["reason"])
        real = self.existing_export()
        row = real["shifts"][TUESDAY]["requests"]["R-121"]
        self.assertEqual(row["revision"], "1")
        row["known_at"] = "2026-04-08T10:00:00+00:00"
        row["effective_at"] = "2026-04-06T10:00:00+00:00"
        _, backdated = self.comparison("backdated", export=self.write_export(real))
        self.assertIn("later than the cutoff", backdated["shifts"][TUESDAY]["reason"])
        del row["inputs"]
        _, erased = self.comparison("erased", export=self.write_export(real))
        self.assertIn("material cutoff revision", erased["shifts"][TUESDAY]["reason"])

    def test_bounds(self):
        case_ids = list("abcdefgh")
        for states in itertools.product((None, "meets", "outside"), repeat=8):
            accepted = dict(zip(case_ids, states))
            unknown = [index for index, value in enumerate(states) if value is None]
            outcomes = []
            for fill in itertools.product(("meets", "outside"), repeat=len(unknown)):
                assignment = list(states)
                for index, answer in zip(unknown, fill):
                    assignment[index] = answer
                outcomes.append(assignment.count("meets") - 2)
            known = {key: value for key, value in accepted.items() if value is not None}
            known.update({"old1": "meets", "old2": "meets"})
            self.assertEqual(
                bounds(case_ids, ["old1", "old2"], known),
                (min(outcomes), max(outcomes)),
            )
        self.assertEqual(bounds(["gain"], ["loss"], {}), (-1, 1))
        self.assertEqual(bounds(["gain"], ["loss"], {"gain": "outside"}), (-1, 0))
        self.assertEqual(bounds(["gain"], ["loss"], {"loss": "outside"}), (0, 1))

    def test_grade_return(self):
        out, _ = self.comparison("tuesday")
        original = {"R-113": "meets", "R-114": "meets", "R-118": "meets"}
        partial = self.filled(out, original, "partial.csv")
        x = self.refresh(out, partial)
        self.assertEqual(x["bounds"], [1, 6])
        self.assertEqual(x["recommendation"], "Inconclusive")
        self.assertEqual(x["pending_cross_checks"], 2)
        control = self.filled(
            out, {"R-091": "outside", "R-101": "meets"}, "controls.csv"
        )
        y = self.refresh(out, control)
        self.assertEqual(y["recommendation"], "Try in shadow")
        self.assertEqual(y["effort"]["grading"]["minutes"], 6)
        self.assertEqual(y["effort"]["cross_checks"]["minutes"], 4)
        reopened = self.filled(out, {"R-091": "meets"}, "dispute.csv")
        z = self.refresh(out, reopened)
        self.assertEqual(z["bounds"], [0, 6])
        self.assertEqual(z["recommendation"], "Inconclusive")
        state = load_report(out / "report.html")
        row_id = next(
            key for key, row in state["packet"].items() if row["case"] == "R-091"
        )
        final = json.loads(
            self.cli(
                "refresh",
                out / "report.html",
                "--adjudicate",
                row_id,
                "outside",
                "Independent evidence review",
                "--review-set",
                z["adjudication"][row_id]["review_set"],
                "--minutes",
                "4",
                "--format",
                "json",
            )
        )
        self.assertEqual(final["recommendation"], "Try in shadow")
        self.assertEqual(final["effort"]["adjudication"]["minutes"], 4)
        blocked = self.plan_copy(
            "comparison.toml", **{"grading_budget = 10": "grading_budget = 2"}
        )
        limited, _ = self.comparison("limited", blocked)
        confirmations = self.filled(
            limited, {"R-091": "outside", "R-101": "meets"}, "limited.csv"
        )
        limited_result = self.refresh(limited, confirmations)
        self.assertEqual(
            (limited_result["recommendation"], limited_result["packet_rows"]),
            ("Inconclusive", 2),
        )
        grades = json.loads((EXAMPLE / "known-grades.json").read_text(encoding="utf-8"))
        grades["grades"]["R-101"]["shared_sources"] = False
        (self.root / "known-grades.json").write_text(
            json.dumps(grades), encoding="utf-8"
        )
        _, call = self.comparison("call", blocked)
        self.assertIn("R-101", call["shifts"][TUESDAY]["unknown"])
        misses, _ = self.comparison("six-negative")
        negative = self.filled(
            misses,
            {
                "R-091": "outside",
                "R-101": "meets",
                **{f"R-{i:03}": "outside" for i in range(113, 119)},
            },
            "outside.csv",
        )
        self.assertEqual(self.refresh(misses, negative)["recommendation"], "Hold")

        disclosed = json.loads(
            self.cli(
                "refresh",
                out / "report.html",
                "--grades",
                control,
                "--reviewer-unblinded",
                "--format",
                "json",
            )
        )
        self.assertTrue(disclosed["reviewer_unblinded"])
        self.assertIn(
            "A reviewer knew", (out / "report.html").read_text(encoding="utf-8")
        )

    def test_grading_evidence(self):
        plan = self.plan_copy("comparison.toml")
        archived = json.loads((self.root / "known-grades.json").read_text())
        archived["grades"]["R-091"]["shared_sources"] = False
        (self.root / "known-grades.json").write_text(json.dumps(archived))
        data = self.existing_export()
        out, first = self.comparison("excluded", plan)
        self.assertIn("R-091", first["excluded_grading"])
        with (out / "grading.csv").open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            rows = list(reader)
            fields = reader.fieldnames
        self.assertFalse(any("R-091" in row["evidence_references"] for row in rows))
        received = self.filled(
            out,
            {
                "R-092": "outside",
                "R-101": "meets",
                "R-113": "meets",
                "R-114": "meets",
                "R-118": "meets",
            },
            "all-admissible.csv",
        )
        result = self.refresh(out, received, expected=1, flags=("--require-shadow",))
        self.assertEqual(
            (result["bounds"], result["recommendation"]), ([0, 6], "Inconclusive")
        )
        illegitimate = self.root / "excluded-revision.csv"
        rows[0]["grade"] = "outside"
        rows[0]["evidence_references"] = json.dumps(
            archived["grades"]["R-091"]["evidence"]
        )
        with illegitimate.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerow(rows[0])
        self.assertEqual(
            self.refresh(out, illegitimate, expected=2)["status"], "execution_error"
        )
        self.assertEqual(self.explanation(out), result)

        data["shifts"][TUESDAY]["requests"]["R-091"]["evidence"] = [
            "delivery-log/R-091@3"
        ]
        replaced, new = self.comparison(
            "independent", plan, export=self.write_export(data)
        )
        self.assertNotIn("R-091", new["excluded_grading"])
        independent = self.filled(
            replaced,
            {
                "R-091": "outside",
                "R-092": "outside",
                "R-101": "meets",
                "R-113": "meets",
                "R-114": "meets",
                "R-118": "meets",
            },
            "independent.csv",
        )
        self.assertEqual(
            self.refresh(replaced, independent, flags=("--require-shadow",))["bounds"],
            [1, 6],
        )

        plan = self.plan_copy(
            "comparison.toml",
            **{
                "minimum_additional_exceptions = 1": "minimum_additional_exceptions = 12"
            },
        )
        unreachable, result = self.comparison("hurdle", plan)
        self.assertEqual(result["packet_rows"], 2)
        self.assertEqual(
            {
                row["case"]
                for row in load_report(unreachable / "report.html")["packet"].values()
            },
            {"R-091", "R-101"},
        )
        returned = self.filled(
            unreachable, {"R-091": "outside", "R-101": "meets"}, "hurdle.csv"
        )
        self.assertEqual(
            self.refresh(
                unreachable, returned, expected=1, flags=("--require-shadow",)
            )["recommendation"],
            "Hold",
        )

    def test_return_order(self):
        for index, answers in enumerate(
            (("meets", "insufficient"), ("insufficient", "meets"))
        ):
            out, _ = self.comparison(f"order-{index}")
            known = self.filled(
                out,
                {
                    "R-091": "outside",
                    "R-101": "meets",
                    "R-113": "meets",
                    "R-114": "meets",
                },
                "known.csv",
            )
            self.refresh(out, known)
            for answer in answers:
                response = self.filled(out, {"R-118": answer}, f"{answer}.csv")
                self.refresh(out, response, flags=("--review-id", f"review-{answer}"))
            disputed = self.explanation(out)
            self.assertEqual(
                (disputed["bounds"], disputed["recommendation"]),
                ([0, 6], "Inconclusive"),
            )
            self.cli("explain", out / "report.html", "--require-shadow", expected=1)
            [(row, details)] = disputed["adjudication"].items()
            self.assertEqual(details["request"], "R-118")
            args = [
                "refresh",
                out / "report.html",
                "--adjudicate",
                row,
                "meets",
                "Independent evidence review",
                "--review-set",
                details["review_set"],
                "--format",
                "json",
            ]
            self.assertEqual(
                json.loads(self.cli(*args))["recommendation"], "Try in shadow"
            )
            retry = self.filled(out, {"R-118": "insufficient"}, "retry.csv")
            self.assertEqual(
                self.refresh(out, retry, flags=("--review-id", "review-insufficient"))[
                    "recommendation"
                ],
                "Try in shadow",
            )
            reused_id = self.filled(
                out, {"R-118": "insufficient"}, "another.csv", minutes=3
            )
            self.assertEqual(
                self.refresh(
                    out,
                    reused_id,
                    expected=2,
                    flags=("--review-id", "review-insufficient"),
                )["status"],
                "execution_error",
            )
            reopened = self.refresh(
                out, reused_id, expected=1, flags=("--require-shadow",)
            )
            self.assertEqual(reopened["bounds"], [0, 6])
            self.assertEqual(reopened["effort"]["grading"]["events"], 5)
            self.assertEqual(json.loads(self.cli(*args)), reopened)
            stale = list(args)
            stale[5] = "Stale adjudication"
            self.assertEqual(
                json.loads(self.cli(*stale, expected=2))["status"], "execution_error"
            )

    def test_revision_time(self):
        db = self.root / "revision.sqlite"
        self.cli("seed", "--db", db, module="examples.refunds.app")
        with sqlite3.connect(db) as connection:
            connection.execute("""UPDATE revision SET known_at = '2026-04-07T08:00:00+00:00'
                WHERE request = 'R-121' AND revision = '1'""")
        outcomes = []
        for index, instant in enumerate(
            ("2026-04-07T08:30:00+00:00", "2026-04-07T10:30:00+02:00")
        ):
            with sqlite3.connect(db) as connection:
                connection.execute(
                    """UPDATE revision SET known_at = ?,
                    inputs = json_set(inputs, '$.conflict_priority', 999999, '$.refund_cents', 900)
                    WHERE request = 'R-121' AND revision = '2'""",
                    (instant,),
                )
            exported = self.root / f"clock-{index}"
            self.cli(
                "export", "--db", db, "--out", exported, module="examples.refunds.app"
            )
            snapshot = exported / "cutoff.json"
            record = json.loads(snapshot.read_text())["shifts"][TUESDAY]["requests"][
                "R-121"
            ]
            self.assertEqual(record["revision"], "2")
            _, result = self.comparison(f"cutoff-{index}", export=snapshot)
            self.assertIn("R-121", result["shifts"][TUESDAY]["gain"])
            plain = json.loads(
                self.cli("--export", snapshot, module="examples.refunds.baseline")
            )
            self.assertEqual(plain["gain"], result["shifts"][TUESDAY]["gain"])
            outcomes.append(result["shifts"][TUESDAY])
        self.assertEqual(outcomes[0], outcomes[1])
        with sqlite3.connect(db) as connection:
            connection.execute("""UPDATE revision SET known_at = '2026-04-07T08:00:00.000001-01:00'
                WHERE request = 'R-121' AND revision = '2'""")
        late_export = self.root / "late-clock"
        self.cli(
            "export", "--db", db, "--out", late_export, module="examples.refunds.app"
        )
        data = json.loads((late_export / "cutoff.json").read_text())
        self.assertEqual(data["shifts"][TUESDAY]["requests"]["R-121"]["revision"], "1")
        data["shifts"][TUESDAY]["requests"]["R-121"]["known_at"] = (
            "2026-04-07T08:00:00.000001-01:00"
        )
        changed = self.write_export(data)
        _, late = self.comparison("late", export=changed)
        self.assertIn("later than the cutoff", late["shifts"][TUESDAY]["reason"])
        plain = json.loads(
            self.cli("--export", changed, module="examples.refunds.baseline")
        )
        self.assertEqual(plain["batch"], "Not replayable")

    def test_lead_report(self):
        out, _ = self.comparison("lead")
        report = out / "report.html"
        text = report.read_text().split('<script type="application/json"')[0]
        self.assertIn(
            "Did the requested quantity exceed the quantity recorded as delivered at cutoff?",
            text,
        )
        self.assertRegex(
            text,
            r"Lose review · 20</h3><p><strong>Confirmed exceptions · 10</strong></p>"
            r'<ul class="cases"><li>R-073</li>',
        )
        dispute = self.filled(out, {"R-091": "meets"}, "dispute.csv")
        result = self.refresh(out, dispute)
        [(row, info)] = result["adjudication"].items()
        text = report.read_text().split('<script type="application/json"')[0]
        self.assertIn("Adjudicate disputed grades", text)
        self.assertIn(
            f"--adjudicate {row} GRADE REASON --review-set {info['review_set']}", text
        )
        shadow, _ = self.comparison("shadow-new", EXAMPLE / "shadow.toml")
        old = load_report(shadow / "report.html")
        old["schema"] = 1
        legacy = self.root / "legacy.html"
        legacy.write_text(render_report(old), encoding="utf-8")
        summary = json.loads(
            self.cli(
                "explain", legacy, "--require-shadow", "--format", "json", expected=1
            )
        )
        self.assertTrue(summary["legacy_report"])
        self.assertEqual(summary["recommendation"], "Inconclusive")
        self.assertIn(
            "re-run compare",
            self.cli(
                "refresh", legacy, "--grades", dispute, "--format", "json", expected=2
            ).lower(),
        )

    def test_capacity(self):
        data = self.existing_export()
        for batch in data["shifts"].values():
            for row in batch["requests"].values():
                row["minutes"] = {"lower": 0, "upper": 0, "basis": "observed"}
        item = data["shifts"][TUESDAY]["requests"]["R-001"]
        plan = EXAMPLE / "shadow.toml"
        for index, (low, high, expect) in enumerate(
            (
                (450, 480, "Try in shadow"),
                (450, 510, "Inconclusive"),
                (490, 510, "Hold"),
                (450, None, "Inconclusive"),
                (490, None, "Hold"),
            )
        ):
            item["minutes"] = {"lower": low, "upper": high, "basis": "estimated"}
            _, result = self.comparison(
                f"staff-{index}", plan, export=self.write_export(data)
            )
            self.assertEqual(result["recommendation"], expect)
        item["minutes"] = None
        batch = data["shifts"][TUESDAY]
        batch["mandatory"] = sorted(batch["requests"])[:101]
        _, infeasible = self.comparison(
            "must-review", plan, export=self.write_export(data)
        )
        self.assertTrue(
            any(
                "101 mandatory reviews cannot fit" in reason
                for reason in infeasible["vetoes"]
            )
        )
        plan = self.plan_copy("comparison.toml")
        with (self.root / "rules.py").open("a", encoding="utf-8") as stream:
            stream.write(
                "\ndef rank_by_order_conflict(batch, capacity):\n"
                "    return [row['id'] for row in batch]\n"
            )
        _, overflow = self.comparison("overflow", plan)
        self.assertTrue(any("slot capacity" in reason for reason in overflow["vetoes"]))

    def test_coverage(self):
        data = self.existing_export()
        del data["shifts"][SATURDAY]
        pending, _ = self.comparison(
            "weekend", EXAMPLE / "weekend.toml", export=self.write_export(data)
        )
        incoming = self.filled(
            pending,
            {
                "R-091": "outside",
                "R-101": "meets",
                "R-113": "meets",
                "R-114": "meets",
                "R-118": "meets",
            },
            "weekend.csv",
        )
        result = self.refresh(pending, incoming)
        self.assertIsNone(result["bounds"])
        self.assertEqual(result["scored_bounds"][0], 1)
        self.assertEqual(result["recommendation"], "Inconclusive")
        plan = self.plan_copy(
            "weekend.toml", **{"rank_by_order_conflict": "rank_without_mandatory"}
        )
        _, veto = self.comparison("missing-veto", plan, export=self.write_export(data))
        self.assertEqual(veto["recommendation"], "Hold")

        data = self.existing_export()
        cutoff = data["shifts"][SATURDAY].pop("cutoff")
        _, tuesday_only = self.comparison(
            "unscoped-cutoff",
            EXAMPLE / "hold.toml",
            export=self.write_export(data),
            gate=True,
            expected=1,
        )
        self.assertEqual(tuesday_only["recommendation"], "Hold")
        self.assertNotIn(SATURDAY, tuesday_only["shifts"])
        _, broken_cutoff = self.comparison(
            "scoped-cutoff", plan, export=self.write_export(data), gate=True, expected=1
        )
        self.assertEqual(broken_cutoff["recommendation"], "Hold")
        self.assertIn("cutoff", broken_cutoff["shifts"][SATURDAY]["reason"])
        data["shifts"][SATURDAY]["cutoff"] = cutoff
        for index, field in enumerate(("conflict_priority", "refund_cents")):
            row = data["shifts"][SATURDAY]["requests"]["S-001"]["inputs"]
            removed = row.pop(field)
            _, broken_input = self.comparison(
                f"missing-ranking-{index}",
                plan,
                export=self.write_export(data),
                gate=True,
                expected=1,
            )
            self.assertEqual(broken_input["recommendation"], "Hold")
            self.assertEqual(
                broken_input["shifts"][SATURDAY]["status"], "Not replayable"
            )
            self.assertIn(field, broken_input["shifts"][SATURDAY]["reason"])
            self.assertTrue(any("R-041" in veto for veto in broken_input["vetoes"]))
            row[field] = removed

        # Independent two-shift arithmetic: one loss on Tuesday, two gains on Saturday.
        def shift(live, candidate):
            return {
                "status": "Replayable",
                "rows": {
                    case: {"minutes": {"lower": 1, "upper": 1, "basis": "observed"}}
                    for case in live + candidate
                },
                "live": live,
                "candidate": candidate,
                "raw_candidate_count": len(candidate),
                "mandatory": [],
                "capacity": 2,
                "staff_minutes": 2,
            }

        simplified = {
            "plan": {"minimum_additional_exceptions": 1},
            "shifts": {TUESDAY: shift(["a"], []), SATURDAY: shift([], ["b", "c"])},
        }
        total = evaluate(simplified, {"a": "meets", "b": "meets", "c": "meets"}, 0)
        self.assertEqual(
            (total["bounds"], total["recommendation"]), ([1, 1], "Try in shadow")
        )

    def test_handoff(self):
        db = self.root / "source.sqlite"
        exported = self.root / "source"
        self.cli("seed", "--db", db, module="examples.refunds.app")
        self.cli("export", "--db", db, "--out", exported, module="examples.refunds.app")
        for path in ("cutoff.json", "known-grades.json"):
            self.assertEqual(
                (exported / path).read_bytes(), (EXAMPLE / path).read_bytes()
            )
        prepared, result = self.comparison(
            "public-export", export=exported / "cutoff.json"
        )
        baseline = json.loads(
            self.cli(
                "--export",
                exported / "cutoff.json",
                "--grades",
                exported / "known-grades.json",
                module="examples.refunds.baseline",
            )
        )
        shift = result["shifts"][TUESDAY]
        for item in ("gain", "loss", "bounds", "mandatory_misses", "unknown"):
            self.assertEqual(sorted(shift[item]), sorted(baseline[item]))
        self.assertEqual(baseline["recommendation"], result["recommendation"])
        self.assertEqual(
            baseline["workload"],
            [shift["workload"]["lower"], shift["workload"]["upper"]],
        )
        self.assertEqual(baseline["grading_budget"], result["packet_rows"])
        self.assertEqual(set(baseline["cross_checks"]), {"R-091", "R-101"})
        output = self.cli(
            "inspect",
            "--db",
            db,
            "order/R-118@2",
            "refund/R-118@1",
            module="examples.refunds.app",
        )
        self.assertIn('"units_delivered": 2', output)
        self.assertIn('"units_requested": 3', output)
        with (prepared / "grading.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(
                reader.fieldnames,
                [
                    "comparison_id",
                    "row_id",
                    "review_revision",
                    "evidence_references",
                    "rubric_question",
                    "grade",
                    "minutes_spent",
                ],
            )
            self.assertTrue(
                all(
                    row["grade"] == "" and row["row_id"].startswith("Q-")
                    for row in reader
                )
            )
        ledger = json.loads((EXAMPLE / "review-ledger.json").read_text())
        paths = []
        for name, records in (
            ("agreement", ledger[:5]),
            ("disagreement", ledger[:6]),
            ("adjudicated", ledger),
        ):
            path = self.root / f"{name}.json"
            path.write_text(json.dumps(records))
            paths.append(path)

        first = {event["case"]: event["grade"] for event in ledger[:5]}
        csv_path = self.filled(prepared, first, "agreement.csv")
        stage_one = self.refresh(prepared, csv_path)
        csv_path = self.filled(prepared, {"R-091": "meets"}, "disagreement.csv")
        stage_two = self.refresh(prepared, csv_path)
        [(row, dispute)] = stage_two["adjudication"].items()
        stage_three = json.loads(
            self.cli(
                "refresh",
                prepared / "report.html",
                "--adjudicate",
                row,
                "outside",
                ledger[-1]["reason"],
                "--review-set",
                dispute["review_set"],
                "--minutes",
                "4",
                "--format",
                "json",
                "--require-shadow",
            )
        )
        self.assertEqual(
            [stage["recommendation"] for stage in (stage_one, stage_two, stage_three)],
            ["Try in shadow", "Inconclusive", "Try in shadow"],
        )
        for path, telryn in zip(paths, (stage_one, stage_two, stage_three)):
            sql = json.loads(
                self.cli(
                    "--export",
                    exported / "cutoff.json",
                    "--grades",
                    exported / "known-grades.json",
                    "--ledger",
                    path,
                    module="examples.refunds.baseline",
                )
            )
            for field in ("bounds", "recommendation", "pending_cross_checks", "effort"):
                self.assertEqual(sql[field], telryn[field], field)
            self.assertEqual(
                sql["disputed"], [v["request"] for v in telryn["adjudication"].values()]
            )
        db.unlink()
        self.assertEqual(self.explanation(prepared), stage_three)

    def test_recovery(self):
        out, before = self.comparison("recover")
        report = out / "report.html"
        file_before = report.read_bytes()
        first = self.filled(out, {"R-113": "meets"}, "first.csv")
        second = self.filled(out, {"R-114": "meets"}, "second.csv")
        interrupt = """
import os
import signal
import sys
from pathlib import Path
from telryn import session

def half_write(fd, state):
    os.write(fd, b'partial')
    os.fsync(fd)
    os.kill(os.getpid(), signal.SIGKILL)

session._write_report = half_write
session.refresh(Path(sys.argv[1]), Path(sys.argv[2]), None)
"""
        interrupted = subprocess.run(
            [sys.executable, "-c", interrupt, str(report), str(first)],
            cwd=ROOT,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(interrupted.returncode, -9, interrupted.stderr)
        self.assertEqual(report.read_bytes(), file_before)
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "telryn",
                    "refresh",
                    str(report),
                    "--grades",
                    str(path),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for path in (first, second)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        after = self.explanation(out)
        self.assertEqual(after["bounds"], [0, 6])
        self.assertEqual(after["packet_returns"], 2)
        contents = report.read_bytes()
        self.assertEqual(self.refresh(out, first), after)
        self.assertEqual(contents, report.read_bytes())
        foreign, _ = self.comparison("other-plan")
        wrong = self.filled(foreign, {"R-113": "meets"}, "other.csv")
        self.assertEqual(self.refresh(out, wrong, 2)["status"], "execution_error")
        self.assertEqual(contents, report.read_bytes())
        self.assertNotEqual(
            before["comparison_id"], self.explanation(foreign)["comparison_id"]
        )
        self.cli("compare", EXAMPLE / "comparison.toml", "--out", out, expected=2)
        self.comparison("gate-inconclusive", expected=1, gate=True)
        self.comparison("gate-hold", EXAMPLE / "hold.toml", expected=1, gate=True)
        self.comparison("gate-shadow", EXAMPLE / "shadow.toml", gate=True)
        data = self.existing_export()
        del data["shifts"][TUESDAY]["membership"]
        self.comparison(
            "gate-unreplayable", export=self.write_export(data), expected=1, gate=True
        )
        plan = self.plan_copy("comparison.toml")
        for index, bad in enumerate(
            (
                'return ["missing-id"]',
                'return [batch[0]["id"]] * 2',
                'raise RuntimeError("crash")',
            )
        ):
            rules = (EXAMPLE / "rules.py").read_text(encoding="utf-8")
            (self.root / "rules.py").write_text(
                rules + f"\ndef rank_by_order_conflict(batch, capacity):\n    {bad}\n",
                encoding="utf-8",
            )
            destination, error = self.comparison(
                f"bad-selector-{index}", plan, expected=2, gate=True
            )
            self.assertEqual(error["status"], "execution_error")
            self.assertFalse(destination.exists())
        corrupted = self.root / "corrupted.html"
        corrupted.write_bytes(
            contents.replace(
                b'"minimum_additional_exceptions":1',
                b'"minimum_additional_exceptions":2',
                1,
            )
        )
        self.cli("explain", corrupted, expected=2)

    def test_repeatability(self):
        for fixture, expect in (
            ("hold", "Hold"),
            ("comparison", "Inconclusive"),
            ("shadow", "Try in shadow"),
        ):
            expected_digest = None
            for attempt in range(30):
                _, result = self.comparison(
                    f"{fixture}-{attempt}", EXAMPLE / f"{fixture}.toml"
                )
                self.assertEqual(result["recommendation"], expect)
                digest = fingerprint(result["shifts"])
                if expected_digest is None:
                    expected_digest = digest
                self.assertEqual(digest, expected_digest)

    def qa_intake(self):
        export = self.root / "qa.json"
        status = json.loads(
            self.cli(
                "preflight", QA / "intake.toml", "--out", export, "--format", "json"
            )
        )
        self.assertEqual(
            status["shifts"]["week-1"],
            {"status": "Ready for replay", "eligible": 12, "selected": 8},
        )
        return export

    def test_qa_preflight(self):
        path = self.qa_intake()
        batch = json.loads(path.read_text())["shifts"]["week-1"]
        self.assertEqual(batch["actual"], [f"Q-{i:03}" for i in range(1, 9)])
        self.assertIn("Q-012", batch["membership"]["ids"])
        example = self.root / "support-qa"
        shutil.copytree(QA, example)
        manifest = example / "intake.toml"
        original = manifest.read_text()
        output = self.root / "bad-qa.json"
        for delete, source, expected in (
            ('eligible = "eligible.csv"', "eligible", "no eligible"),
            ('picks = "picks.csv"', "picks", "no picks"),
            (
                'cutoff_inputs = "cutoff-inputs.csv"',
                "cutoff_inputs",
                "no cutoff_inputs",
            ),
            ('tie_break = "id-asc"', "policy", "tie-break"),
            ("overrides = []", "policy", "override"),
        ):
            manifest.write_text(original.replace(delete, ""))
            report = json.loads(
                self.cli(
                    "preflight",
                    manifest,
                    "--out",
                    output,
                    "--format",
                    "json",
                    expected=1,
                )
            )["shifts"]["week-1"]
            self.assertEqual(report["source"], source)
            self.assertIn(expected, report["reason"])
            self.assertTrue(report["capture"])
            self.assertFalse(output.exists())
        manifest.write_text(
            original.replace('eligible = "eligible.csv"', 'eligible = "picks.csv"')
        )
        self.assertIn("separate source", self.cli("preflight", manifest, expected=1))
        manifest.write_text(original)
        snapshot_path = example / "cutoff-inputs.csv"
        snapshot = snapshot_path.read_text()
        snapshot_path.write_text("\n".join(snapshot.splitlines()[:9]) + "\n")
        self.assertIn("Q-009", self.cli("preflight", manifest, expected=1))
        snapshot_path.write_text(snapshot.replace("frozen_risk", "missing_risk"))
        result = self.cli("preflight", manifest, expected=1)
        self.assertIn("frozen_risk", result)
        self.cli("preflight", QA / "intake.toml", "--out", path, expected=2)

    def test_qa_sql(self):
        exported = self.qa_intake()
        sql_out, sql = self.comparison(
            "sqlite", QA / "comparison.toml", export=exported
        )
        python_out, pure = self.comparison(
            "python", QA / "legacy.toml", export=exported
        )
        sqlite_state = load_report(sql_out / "report.html")
        py_state = load_report(python_out / "report.html")
        for side in ("live", "candidate"):
            self.assertEqual(
                sqlite_state["shifts"]["week-1"][side],
                py_state["shifts"]["week-1"][side],
            )
        self.assertEqual(sql["bounds"], pure["bounds"])
        self.assertEqual(
            sqlite_state["frozen"]["sqlite_version"], sqlite3.sqlite_version
        )
        self.assertEqual(
            sql["shifts"]["week-1"]["gain"], ["Q-009", "Q-010", "Q-011", "Q-012"]
        )
        example = self.root / "copy"
        shutil.copytree(QA, example)
        query = example / "candidate.sql"
        original = query.read_text()
        query.write_text("WITH ranked AS (" + original + ") SELECT id FROM ranked")
        self.comparison("cte", example / "comparison.toml", export=exported)
        for index, source in enumerate(
            (
                "UPDATE requests SET inputs = '{}' RETURNING id",
                "SELECT id FROM requests ORDER BY random() LIMIT :capacity",
                "SELECT id, inputs FROM requests LIMIT :capacity",
                "SELECT 'unavailable' AS id",
                "SELECT id FROM requests UNION ALL SELECT id FROM requests",
            )
        ):
            query.write_text(source)
            target, result = self.comparison(
                f"invalid-sql-{index}",
                example / "comparison.toml",
                export=exported,
                expected=2,
            )
            self.assertEqual(result["status"], "execution_error")
            self.assertFalse(target.exists())
        query.write_text(original)
        export = json.loads(exported.read_text())
        export["shifts"]["week-1"]["requests"]["Q-012"]["inputs"].pop("risk")
        _, incomplete = self.comparison(
            "missing-score",
            example / "comparison.toml",
            export=self.write_export(export),
            expected=1,
            gate=True,
        )
        self.assertEqual(incomplete["shifts"]["week-1"]["status"], "Not replayable")
        self.assertIn("Q-012", incomplete["shifts"]["week-1"]["reason"])

    def test_qa_serious(self):
        exported = self.qa_intake()
        out, initial = self.comparison(
            "serious", QA / "comparison.toml", export=exported, gate=True, expected=1
        )
        self.assertEqual(
            (initial["recommendation"], initial["bounds"]), ("Inconclusive", [2, 2])
        )
        self.assertEqual(initial["serious_losses"]["unknown"], ["Q-005"])
        self.assertEqual(initial["serious_losses"]["upper"], 1)
        self.assertEqual(initial["packet_rows"], 1)
        packet = self.filled(out, {("Q-005", "serious"): "meets"}, "serious.csv")
        result = self.refresh(out, packet, expected=1, flags=("--require-shadow",))
        self.assertEqual((result["recommendation"], result["bounds"]), ("Hold", [2, 2]))
        self.assertEqual(result["serious_losses"]["confirmed"], ["Q-005"])
        self.assertIn("1 serious case loses review", result["vetoes"][0])
        self.assertIn("Scheduled reviewer hours", (out / "report.html").read_text())
        revised, next_call = self.comparison(
            "keep-serious", QA / "revised.toml", export=exported, gate=True
        )
        self.assertEqual(
            (next_call["recommendation"], next_call["bounds"]),
            ("Try in shadow", [3, 3]),
        )
        self.assertEqual(next_call["serious_losses"]["upper"], 0)
        self.assertEqual(next_call["packet_rows"], 0)

        example = self.root / "dual"
        shutil.copytree(QA, example)
        primary = example / "defect-grades.json"
        known = json.loads(primary.read_text())
        known["grades"].pop("Q-005")
        primary.write_text(json.dumps(known))
        plan = example / "comparison.toml"
        plan.write_text(
            plan.read_text().replace(
                "minimum_additional_exceptions = 1", "minimum_additional_exceptions = 3"
            )
        )
        dual, _ = self.comparison("two-questions", plan, export=exported)
        rows = load_report(dual / "report.html")["packet"].values()
        self.assertEqual(
            {(r["case"], r["criterion"]) for r in rows},
            {("Q-005", "primary"), ("Q-005", "serious")},
        )
        returned = self.filled(
            dual,
            {("Q-005", "primary"): "outside", ("Q-005", "serious"): "meets"},
            "different-answers.csv",
        )
        final = self.refresh(dual, returned, expected=1, flags=("--require-shadow",))
        self.assertEqual(final["bounds"], [3, 3])
        self.assertEqual(final["serious_losses"]["confirmed"], ["Q-005"])
        self.assertEqual(final["recommendation"], "Hold")

        full = json.loads((QA / "defect-grades.json").read_text())
        full["grades"]["Q-005"]["shared_sources"] = False
        primary.write_text(json.dumps(full))
        protected, blocked = self.comparison(
            "excluded-both-rubrics", plan, export=exported, expected=1, gate=True
        )
        self.assertIn("Q-005", blocked["excluded_grading"])
        self.assertEqual(blocked["serious_losses"]["unknown"], ["Q-005"])
        self.assertFalse(
            any(
                row["case"] == "Q-005"
                for row in load_report(protected / "report.html")["packet"].values()
            )
        )

    def test_qa_shadow(self):
        exported = self.qa_intake()
        out, result = self.comparison(
            "pilot-shadow", QA / "revised.toml", export=exported
        )
        report = out / "report.html"
        packet = (out / "grading.csv").read_bytes()
        observation = json.loads((QA / "shadow-observations.json").read_text())
        observation["comparison_id"] = result["comparison_id"]
        readings = self.write_export(observation, "shadow-observations.json")
        stored = json.loads(
            self.cli("record-shadow", report, readings, "--format", "json")
        )
        trial = stored["shadow_trials"]["synthetic-week-2"]
        batch = trial["batches"]["week-2"]
        self.assertEqual(batch["recorded_minutes"], 1240)
        self.assertEqual(batch["projected_minutes"], [960, 1160])
        self.assertEqual(batch["staffing"], "Above historical staffing limit")
        self.assertEqual(batch["mix"]["Account"], {"projected": 5, "observed": 6})
        self.assertEqual(trial["lead_decision"]["action"], "revise")
        self.assertEqual(stored["recommendation"], "Try in shadow")
        self.assertEqual(self.explanation(out), stored)
        self.assertEqual((out / "grading.csv").read_bytes(), packet)
        visible = report.read_text().split('<script type="application/json"')[0]
        self.assertIn("Recorded shadow trials", visible)
        self.assertIn("1240 recorded reviewer minutes", visible)
        saved = report.read_bytes()
        self.cli("record-shadow", report, readings)
        self.assertEqual(report.read_bytes(), saved)
        observation["lead_decision"]["action"] = "adopt"
        self.cli("record-shadow", report, self.write_export(observation), expected=2)
        observation["trial_id"] = "not-this-comparison"
        first, verdict = self.comparison(
            "trial-denied", QA / "comparison.toml", export=exported
        )
        self.cli(
            "record-shadow",
            first / "report.html",
            self.write_export(observation),
            expected=2,
        )
        observation["comparison_id"] = verdict["comparison_id"]
        self.cli(
            "record-shadow",
            first / "report.html",
            self.write_export(observation),
            expected=2,
        )
        self.assertEqual(report.read_bytes(), saved)
        observation["comparison_id"] = result["comparison_id"]
        observation["trial_id"] = "invalid-date"
        observation["observed_at"] = "2026-09-06T12:00:00Z"
        self.cli("record-shadow", report, self.write_export(observation), expected=2)
        observation["trial_id"] = "time-partial"
        observation["observed_at"] = "2026-09-21T12:00:00Z"
        observation["batches"]["week-2"]["picks"][0].pop("minutes")
        new = self.write_export(observation, "partial-time.json")
        observed = json.loads(
            self.cli("record-shadow", report, new, "--format", "json")
        )
        logged = observed["shadow_trials"]["time-partial"]["batches"]["week-2"]
        self.assertEqual(logged["unmeasured_picks"], 1)
        self.assertEqual(logged["staffing"], "Recorded time incomplete")

    def test_qa_intake_reuse(self):
        combined = self.qa_intake()
        profile = self.root / "recipe" / "mapping.toml"
        profile.parent.mkdir()
        originals = (
            ("eligible.csv", "eligible_id", "population_id"),
            ("picks.csv", "picks_id", "assignment_id"),
            ("cutoff-inputs.csv", "cutoff_id", "snapshot_id"),
        )
        definition = (QA / "mapping.toml").read_text()
        aliases = "".join(f'{key} = "{header}"\n' for _, key, header in originals)
        definition = definition.replace("[columns]\n", "[columns]\n" + aliases)
        profile.write_text(definition)
        fingerprint_before = profile.read_bytes()
        for name, prefix in (("first-cutoff", "Q-"), ("second-cutoff", "R-")):
            base = self.root / name
            base.mkdir()
            for filename, _, header in originals:
                raw = (QA / filename).read_text().replace("conversation_id", header, 1)
                (base / filename).write_text(raw.replace("Q-", prefix))
            manifest = base / "batches.toml"
            manifest.write_text((QA / "batches.toml").read_text().replace("Q-", prefix))
            export = base / "reconciled.json"
            status = json.loads(
                self.cli(
                    "preflight",
                    profile,
                    "--batches",
                    manifest,
                    "--out",
                    export,
                    "--format",
                    "json",
                )
            )
            self.assertTrue(status["ready"])
            if prefix == "Q-":
                self.assertEqual(combined.read_bytes(), export.read_bytes())
            out, result = self.comparison(
                name + "-report", QA / "comparison.toml", export=export
            )
            self.assertEqual(result["shifts"]["week-1"]["status"], "Replayable")
            self.assertEqual(
                load_report(out / "report.html")["shifts"]["week-1"]["live"],
                [f"{prefix}{number:03}" for number in range(1, 9)],
            )
        self.assertEqual(profile.read_bytes(), fingerprint_before)
        self.cli("preflight", QA / "intake.toml", "--batches", manifest, expected=2)
        picks = base / "picks.csv"
        (base / "eligible.csv").write_bytes(picks.read_bytes())
        invalid = json.loads(
            self.cli(
                "preflight",
                profile,
                "--batches",
                manifest,
                "--format",
                "json",
                expected=1,
            )
        )
        self.assertFalse(invalid["ready"])

    def test_qa_benchmark(self):
        export = self.qa_intake()
        output, _ = self.comparison("benchmark", QA / "comparison.toml", export=export)
        report = output / "report.html"
        new = self.filled(
            output, {("Q-005", "serious"): "meets"}, "benchmark-return.csv"
        )
        summary = self.refresh(output, new)
        self.assertEqual(summary["recommendation"], "Hold")
        synthetic = json.loads((QA / "pilot-record.json").read_text())
        synthetic["comparison_id"] = summary["comparison_id"]
        synthetic["decision_revision"] = summary["decision_revision"]
        before = report.read_bytes()

        def assess(data, expected=0):
            measure = self.write_export(data, "measure.json")
            return json.loads(
                self.cli(
                    "benchmark", report, measure, "--format", "json", expected=expected
                )
            )

        result = assess(synthetic)
        self.assertEqual(result["evidence"], "synthetic")
        self.assertEqual(result["recurring_minutes_saved"], 120)
        self.assertEqual(result["incremental_setup_minutes"], 360)
        self.assertEqual(result["changes_to_recover_setup"], 3)
        self.assertTrue(result["material_time_win"])
        self.assertFalse(result["setup_recovered_at_recorded_annual_cadence"])

        synthetic["material_minutes_saved"] = 121
        self.assertFalse(assess(synthetic)["material_time_win"])
        synthetic["lead_review"] = {
            "equally_supported": False,
            "baseline_missed_serious_cases": ["Q-005"],
        }
        unmatched = assess(synthetic)
        self.assertFalse(unmatched["timing_comparable"])
        self.assertIsNone(unmatched["recurring_minutes_saved"])
        self.assertIsNone(unmatched["changes_to_recover_setup"])
        self.assertEqual(unmatched["serious_misses_confirmed_by_lead"], ["Q-005"])
        synthetic["matched"]["same_eligible_population"] = False
        unfair = assess(synthetic)
        self.assertEqual(unfair["unmatched_conditions"], ["same_eligible_population"])
        self.assertEqual(unfair["serious_misses_confirmed_by_lead"], [])
        synthetic["lead_review"]["baseline_missed_serious_cases"] = ["Q-006"]
        self.assertIn("not confirmed", assess(synthetic, 2)["message"])
        synthetic["lead_review"] = {"equally_supported": True}
        synthetic["matched"]["same_eligible_population"] = True
        synthetic["telryn"]["analyst_minutes"] = 200
        slower = assess(synthetic)
        self.assertLess(slower["recurring_minutes_saved"], 0)
        self.assertIsNone(slower["changes_to_recover_setup"])
        synthetic["telryn"]["reviewer_minutes"] = -1
        self.assertEqual(assess(synthetic, 2)["status"], "execution_error")
        synthetic["comparison_id"] = "another-comparison"
        self.assertIn("different comparison", assess(synthetic, 2)["message"])
        self.assertEqual(report.read_bytes(), before)
        synthetic["comparison_id"] = summary["comparison_id"]
        synthetic["telryn"]["reviewer_minutes"] = 25
        disputed = self.filled(
            output, {("Q-005", "serious"): "outside"}, "later-return.csv"
        )
        changed = self.refresh(output, disputed)
        self.assertNotEqual(changed["decision_revision"], summary["decision_revision"])
        self.assertIn("saved decision revision", assess(synthetic, 2)["message"])


def qualify(case: str | None = None) -> int:
    if case is not None and case not in CASES:
        print(
            f"Unknown case {case!r}. Choose from: {', '.join(CASES)}.", file=sys.stderr
        )
        return 2
    suite = unittest.TestSuite(
        Qualification(f"test_{name.replace('-', '_')}")
        for name in ([case] if case else CASES)
    )
    if case is None:
        suite.addTests(
            unittest.defaultTestLoader.discover(
                str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT)
            )
        )
    import sqlite3

    print(
        f"CPython {sys.version.split()[0]} · SQLite {sqlite3.sqlite_version}",
        flush=True,
    )
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(
        f"Completed {result.testsRun} qualification case(s) in {time.monotonic() - started:.2f}s."
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(qualify())
