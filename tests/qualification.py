"""Run the public CLI against independent expectations and controlled faults."""

from __future__ import annotations

import csv
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from telryn.contracts import fingerprint
from telryn.decision import bounds, evaluate
from telryn.report import load_report

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "refunds"
CASES = (
    "demo",
    "membership",
    "cutoff",
    "bounds",
    "grade-return",
    "capacity",
    "coverage",
    "handoff",
    "recovery",
    "repeatability",
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

    def filled(self, output, answers, filename):
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
                if case in answers:
                    row["grade"] = answers[case]
                    row["minutes_spent"] = "2"
                    writer.writerow(row)
        return path

    def refresh(self, output, path, expected=0):
        return json.loads(
            self.cli(
                "refresh",
                output / "report.html",
                "--grades",
                path,
                "--format",
                "json",
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
        db.unlink()
        self.assertEqual(self.explanation(prepared), result)

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
