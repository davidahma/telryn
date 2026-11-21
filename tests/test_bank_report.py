"""Check what leaves the team store and what a recipient can reproduce."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.complaints_pilot import qa_passes
from telryn import bank_report
from telryn.contracts import InputError
from tests.audit_support import isolate_trust


class BankReport(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        isolate_trust(self, self.root)
        self.result = qa_passes.run(self.root / "demo")
        self.path = Path(self.result["bank_report"])
        self.report = Path(self.result["weeks"][0]["report"])
        self.expected = self.result["bank_report_sha256"]

    def test_bank_rejoins_original_exports_without_signing_authority(self):
        original = self.root / "demo/source-records/2026-10-05"
        files = {
            name: original / file
            for name, file in (
                ("scores", "scores.csv"),
                ("actions", "owner-events.csv"),
                ("register", "register.csv"),
            )
        }
        with patch.dict(os.environ, TELRYN_TRUST_DIR=str(self.root / "recipient")):
            result = bank_report.verify(
                self.path,
                self.expected,
                source_report=self.report,
                case_id="C102",
                sources=files,
            )
            self.assertTrue(result["case"]["qa"]["deadline_miss"])
            self.assertEqual(result["case"]["complaint"]["owner_as_of"], "completed")
            self.assertFalse((self.root / "recipient").exists())
            with self.assertRaises((OSError, InputError)):
                bank_report.write_specimen(self.report, self.root / "unauthorized")
        page = self.path.read_bytes()
        for secret in (
            b"C102",
            b"Please open a complaint",
            b"synthetic-qa/S102",
            b"owner-cases/P-012",
            b"actions/A13",
        ):
            self.assertNotIn(secret, page)
        statement = bank_report._checked_statement(page)
        self.assertEqual(sum(statement["sampled_owner_status_after_miss"].values()), 3)
        self.assertEqual(statement["human_rubric"], "example-complaints-v1")
        self.assertIn(b"Human policy rubric", page)
        swapped = self.root / "demo/source-records/2026-10-12/register.csv"
        with self.assertRaisesRegex(InputError, "export differs"):
            bank_report.verify(
                self.path,
                self.expected,
                source_report=self.report,
                sources={"register": swapped},
            )
        with self.assertRaisesRegex(InputError, "frozen source revision"):
            bank_report.verify(
                self.path,
                self.expected,
                source_report=Path(self.result["weeks"][1]["report"]),
            )

    def test_verification_requires_pinned_bytes_and_consistent_display(self):
        page = self.path.read_bytes()
        changed = self.root / "changed.html"
        changed.write_bytes(page.replace(b"Which QA passes", b"Something else", 1))
        with self.assertRaisesRegex(InputError, "supplied SHA"):
            bank_report.verify(changed, self.expected)
        with self.assertRaisesRegex(InputError, "displayed bank report"):
            bank_report.verify(
                changed, hashlib.sha256(changed.read_bytes()).hexdigest()
            )
        for payload in ({"schema": 1}, {"schema": False}):
            malformed = (
                bank_report.MARKER + json.dumps(payload) + "</script>"
            ).encode()
            changed.write_bytes(malformed)
            with self.assertRaises(InputError):
                bank_report.verify(changed, hashlib.sha256(malformed).hexdigest())
