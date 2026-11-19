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
from telryn.__main__ import main
from telryn.contracts import InputError
from telryn.weekly_report import read_state


class WeeklyWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

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
        self.assertEqual(json.loads(stdout.getvalue()), result)
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
        state = read_state(path)
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

    def test_local_model_receipt_and_projection(self):
        events = self.events()
        calls = []

        def respond(path, data=None):
            if path == "/api/tags":
                return {"models": [{"name": "test:latest", "digest": "a" * 64}]}
            if path == "/api/show":
                return {"model_info": {"fixture": True}}
            calls.append(data)
            return {
                "message": {"content": json.dumps({"flag": len(calls) == 1})},
                "done": True,
                "done_reason": "stop",
                "model": "test:latest",
            }

        with patch.object(screen, "_api", side_effect=respond):
            result = screen.screen(
                events, self.base / "screen", "test:latest", "account-compromise"
            )
        self.assertEqual(result["cases"], 2)
        self.assertEqual(
            result["events_fingerprint"], screen.fingerprint(screen._signals(events))
        )
        self.assertEqual(len(calls), 2)
        self.assertNotIn("private", json.dumps(calls))
        self.assertEqual(
            set(json.loads(calls[0]["messages"][1]["content"])), set(screen.SIGNALS)
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

    def test_ambiguous_or_misattributed_classifications(self):
        events = self.events()
        baseline = {
            "done": True,
            "done_reason": "stop",
            "model": "test:latest",
            "message": {"content": '{"flag":true}'},
        }
        for change in (
            {"done_reason": "length"},
            {"model": "other:latest"},
            {"message": {"content": {}}},
            {"message": {"content": '{"flag":false,"flag":true}'}},
        ):
            with (
                self.subTest(change=change),
                patch.object(screen, "_installed", return_value="a" * 64),
                patch.object(screen, "_api", return_value={**baseline, **change}),
                self.assertRaises(InputError),
            ):
                screen.screen(
                    events, self.base / "screen", "test:latest", "account-compromise"
                )
            self.assertFalse((self.base / "screen").exists())

    def test_invalid_model_output_or_cloud_model_is_atomic(self):
        events = self.events()
        with (
            patch.object(screen, "_installed", return_value="b" * 64),
            patch.object(
                screen,
                "_api",
                return_value={"done": True, "message": {"content": '{"flag": "no"}'}},
            ),
            self.assertRaises(InputError),
        ):
            screen.screen(
                events, self.base / "screen", "test:local", "account-compromise"
            )
        self.assertFalse((self.base / "screen").exists())
        with self.assertRaisesRegex(InputError, "local model"):
            screen._installed("test:cloud")
        events.write_text(events.read_text().replace(",unknown,", ",personal name,", 1))
        with (
            patch.object(screen, "_api", side_effect=AssertionError),
            self.assertRaises(InputError),
        ):
            screen.screen(
                events, self.base / "screen", "test:local", "account-compromise"
            )
