"""Check CLI rendering, held-out membership, and saved-report compatibility."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from telryn.__main__ import main
from telryn.contracts import canonical, fingerprint
from telryn.report import STATE_CLOSE, STATE_OPEN
from telryn.session import compare

REFUNDS = Path(__file__).resolve().parents[1] / "examples" / "refunds"


class ReportReview(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def cli(self, *args, expected=0):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([str(arg) for arg in args])
        self.assertEqual(code, expected, output.getvalue())
        return output.getvalue()

    def test_terminal_text_preserves_one_line_per_reason(self):
        data = json.loads((REFUNDS / "cutoff.json").read_text())
        note = "Needs confirmation\nsecond column\tend"
        data["shifts"]["tuesday-am"]["overrides"] = [
            {"kind": "unresolved", "reason": note}
        ]
        path = self.root / "export.json"
        path.write_text(json.dumps(data))
        out = self.root / "report"
        args = ("compare", REFUNDS / "shadow.toml", "--export", path, "--out", out)
        text = self.cli(*args, "--require-shadow", expected=1)
        self.assertIn(r"Needs confirmation\nsecond column\tend", text)
        self.assertNotIn(note, text)
        summary = json.loads(
            self.cli("explain", out / "report.html", "--format", "json")
        )
        self.assertIn(note, summary["shifts"]["tuesday-am"]["reason"])

    def test_known_tuning_membership_cannot_qualify_as_held_out(self):
        data = json.loads((REFUNDS / "cutoff.json").read_text())
        reference = data["shifts"]["tuesday-am"]["membership"]["ids"][0]
        data["shifts"]["development"] = {"membership": {"ids": [reference]}}
        path = self.root / "cutoff.json"
        path.write_text(json.dumps(data))
        plan = (
            (REFUNDS / "shadow.toml")
            .read_text()
            .replace("tuning_shifts = []", 'tuning_shifts = ["development"]')
        )
        for name in ("rules.py", "known-grades.json"):
            plan = plan.replace(f'"{name}"', json.dumps(str(REFUNDS / name)))
        definition = self.root / "heldout.toml"
        definition.write_text(plan)
        summary = json.loads(
            self.cli(
                "compare",
                definition,
                "--out",
                self.root / "heldout",
                "--require-shadow",
                "--format",
                "json",
                expected=1,
            )
        )
        self.assertEqual(summary["missing_shifts"], ["tuesday-am"])
        self.assertIn(reference, summary["shifts"]["tuesday-am"]["reason"])
        self.assertIsNone(summary["bounds"])

    def test_older_replay_requires_new_source_and_packet(self):
        state = compare(REFUNDS / "shadow.toml")
        state["schema"] = 2
        report = self.root / "historical.html"
        envelope = {"schema": 1, "state": state, "checksum": fingerprint(state)}
        report.write_text(STATE_OPEN + canonical(envelope) + STATE_CLOSE)
        before = report.read_bytes()
        result = json.loads(
            self.cli(
                "explain", report, "--format", "json", "--require-shadow", expected=1
            )
        )
        self.assertTrue(result["legacy_report"])
        self.assertEqual(result["recommendation"], "Inconclusive")
        error = json.loads(
            self.cli(
                "refresh",
                report,
                "--grades",
                "unused.csv",
                "--format",
                "json",
                expected=2,
            )
        )
        self.assertIn("fresh packet", error["message"])
        self.assertEqual(report.read_bytes(), before)
