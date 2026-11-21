"""Independent register evidence decides the release gate."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telryn import seed
from telryn.contracts import InputError


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agent_release"


class SeedReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "db"
        env = patch.dict(
            os.environ,
            {
                "TELRYN_SEED_DEMO_DB": str(self.db),
                "TELRYN_DEMO_AGENT_REVISION": "demo-prompt-v2",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.plan = json.loads((EXAMPLE / "suite.json").read_text())

    def run_suite(self):
        suite = self.root / "suite.json"
        suite.write_text(json.dumps(self.plan))
        return seed.run(suite, "same-run", EXAMPLE / "bridge.py", EXAMPLE / "bridge.py")

    def test_retry_reads_existing_agent_and_register_records(self):
        result = self.run_suite()
        self.assertTrue(result["passed"])
        with sqlite3.connect(self.db) as db:
            db.execute(
                "DELETE FROM register WHERE marker=?", (result["cases"][1]["marker"],)
            )
        repeat = self.run_suite()
        self.assertFalse(repeat["passed"])
        self.assertEqual(
            [c["status"] for c in repeat["cases"]],
            ["on_time", "missing", "on_time", "on_time"],
        )
        with sqlite3.connect(self.db) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM messages").fetchone(), (4,)
            )
        self.plan["cases"][0]["phrase"] += " Changed"
        with self.assertRaisesRegex(InputError, "failed during submit"):
            self.run_suite()

    def test_scope_checked_before_submission(self):
        self.plan["handoff_revision"] = "other"
        with self.assertRaisesRegex(InputError, "deployed revision"):
            self.run_suite()
        self.assertFalse(self.db.exists())
        self.plan["environment"] = "production"
        with self.assertRaisesRegex(InputError, "test suite"):
            self.run_suite()

    def test_uncertain_late_and_boundary_evidence(self):
        self.plan["cases"] = self.plan["cases"][:1]
        instant = "2026-10-05T09:00:00.000000008Z"
        as_of = "2026-10-05T11:00:00Z"
        deadline = "2026-10-05T10:00:00.000000008Z"
        for expected, coverage, observation, records in (
            ("pending", None, instant, []),
            ("incomplete_register", instant, as_of, []),
            ("missing", deadline, as_of, []),
            ("late", as_of, as_of, ["2026-10-05T10:00:00.000000009Z"]),
            ("ambiguous", as_of, as_of, [instant, instant]),
            ("on_time", as_of, as_of, [deadline]),
        ):

            def adapter(path, req):
                if req["operation"] == "identify":
                    return {"role": req["role"]}
                if req["operation"] == "submit":
                    return {
                        "marker": req["marker"],
                        "conversation_id": "chat",
                        "received_at": instant,
                    }
                return {
                    "source_ref": "test snapshot",
                    "as_of": observation,
                    "complete_through": coverage,
                    "entries": [
                        {
                            "entry_id": str(i),
                            "marker": req["markers"][0],
                            "conversation_id": "chat",
                            "recorded_at": record,
                        }
                        for i, record in enumerate(records)
                    ],
                }

            with self.subTest(expected), patch.object(seed, "exchange", adapter):
                result = self.run_suite()
                self.assertEqual(result["cases"][0]["status"], expected)
                self.assertEqual(result["passed"], expected == "on_time")
