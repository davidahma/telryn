"""Public-data regression, CLI handoff and privacy-preserving model boundary."""

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from examples.public_demo import __main__ as public
from telryn import screen, weekly
from tests.audit_support import isolate_trust
from telryn.__main__ import main
from telryn.contracts import InputError
from telryn.weekly_report import read_state


class WeeklyWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        isolate_trust(self, self.base)

    def test_public_census_against_archived_human_labels(self):
        out = self.base / "census"
        demo = public.run(out, census=True)
        result = demo["weekly"]
        self.assertEqual(demo["residual_human_friction_total"], 86)
        self.assertEqual(
            (result["population"], result["reviewed"], result["assigned"]),
            (200, 40, 160),
        )
        self.assertEqual((result["estimate"], result["interval"]), (86, [86, 86]))
        self.assertEqual((result["agreement"], result["recall"]), (0.575, 1))
        self.assertEqual(result["unknown"], 86)
        from telryn.weekly_report import digest

        self.assertIn(
            "Estimated conversations with friction outside ordinary QA · 86",
            (out / "report.html").read_text(),
        )
        self.assertIn(
            "Estimated conversations with friction", digest(out / "report.html")
        )
        self.assertIn("simulated ordinary reviews", digest(out / "report.html"))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                main(["weekly", str(out / "report.html"), "--format", "json"]), 0
            )
        reported = json.loads(stdout.getvalue())
        self.assertEqual(reported.pop("verification"), "team_hmac_verified")
        self.assertEqual(reported.pop("review")["compared"], 0)
        self.assertEqual(reported, result)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(
                main(
                    [
                        "weekly",
                        str(out / "report.html"),
                        "--propose",
                        str(self.base / "rule"),
                    ]
                ),
                2,
            )
        self.assertIn("dispositions", stderr.getvalue())

    def test_version_one_report(self):
        path = Path(__file__).with_name("fixtures") / "weekly-v1.html"
        state = read_state(path, allow_legacy=True)
        self.assertEqual(state["schema"], 1)
        self.assertEqual(weekly.summarize(state)["interval"], [1, 1])

    def events(self):
        events = self.base / "events.csv"
        with events.open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=screen.FIELDS + screen.SIGNALS)
            writer.writeheader()
            for i in (1, 2):
                writer.writerow(
                    {
                        "case_id": f"private-{i}",
                        "revision": "private-revision",
                        "evidence_ref": "private-link",
                        **dict.fromkeys(screen.SIGNALS, "unknown"),
                    }
                )
        return events

    def test_structured_policy_receipt_and_projection(self):
        events = self.events()
        with patch.object(screen, "_api", side_effect=AssertionError):
            result = screen.screen(
                events, self.base / "screen", None, "account-compromise"
            )
        self.assertEqual(result["cases"], 2)
        self.assertEqual(result["mechanism"], "deterministic-event-policy")
        self.assertEqual(
            result["events_fingerprint"], screen.fingerprint(screen._signals(events))
        )
        (self.base / "reviewed.csv").write_text("case_id\n")
        plan = self.base / "week.toml"
        plan.write_text(f'''schema=1
week="2026-11-09"
rubric="test"
rubric_question="Escalation missed?"
screen_version="{result["screen_version"]}"
population="screen/population.csv"
reviewed="reviewed.csv"
screen_receipt="screen/receipt.json"
flagged_sample=2
unflagged_sample=2
''')
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                main(
                    [
                        "weekly",
                        str(plan),
                        "--out",
                        str(self.base / "audit"),
                        "--format",
                        "json",
                    ]
                ),
                0,
            )
        self.assertEqual(json.loads(stdout.getvalue())["assigned"], 2)
        with (self.base / "screen/population.csv").open("a") as stream:
            stream.write("another,v1,e,true\n")
        with self.assertRaisesRegex(InputError, "receipt"):
            weekly.prepare(plan)

    def test_invalid_structured_input_stops_before_publishing(self):
        events = self.events()
        events.write_text(events.read_text().replace(",unknown,", ",personal name,", 1))
        with (
            patch.object(screen, "_api", side_effect=AssertionError),
            self.assertRaises(InputError),
        ):
            screen.screen(events, self.base / "screen", None, "account-compromise")
        self.assertFalse((self.base / "screen").exists())
