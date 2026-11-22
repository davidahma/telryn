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
        for visible in (self.report.read_text(), page.decode()):
            self.assertIn(
                "3–4 of 7 QA passes missed the deadline (1 unresolved)", visible
            )
            self.assertIn("Census: no sampling error", visible)
            self.assertNotIn("Estimated rate unavailable", visible)
            self.assertNotIn("33.3%–33.3%", visible)
        self.assertIn(
            "C102 · Missed deadline · registered late on 12 Oct 2026 UTC",
            self.report.read_text(),
        )
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
        changed.write_bytes(page.replace(b"of 7 QA passes", b"Something else", 1))
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

    def test_full_length_calendar_identity_round_trips_in_recipient_statement(self):
        from telryn import policy

        state = bank_report._checked_statement(self.path.read_bytes())
        state["calendar"] = policy.calendar_label(
            {
                "holiday_calendar": {
                    "id": "i" * 1024,
                    "version": "v" * 1024,
                    "valid_from": "2026-01-01",
                    "valid_through": "2028-12-31",
                }
            }
        )
        encoded = bank_report._html(state)
        self.assertEqual(bank_report._checked_statement(encoded), state)

    def test_issuer_must_resolve_a_policy_review_before_export(self):
        from telryn import weekly_report

        content = self.report.read_bytes()
        envelope = weekly_report._envelope(content)
        for container, key in (
            (envelope["state"], "pack_reviews"),
            (envelope["state"]["complaint_records"], "policy_reviews"),
        ):
            with (
                self.subTest(key=key),
                patch.object(weekly_report, "verified_html", return_value=content),
                patch.object(weekly_report, "_envelope", return_value=envelope),
            ):
                container[key] = [{"review_version": "2.0.0"}]
                with self.assertRaisesRegex(InputError, "policy re-review"):
                    bank_report.write_specimen(self.report, self.root / "new-bank")
                del container[key]
