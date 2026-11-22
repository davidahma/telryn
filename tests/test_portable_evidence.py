"""Portable signatures, disclosure gates and quarter continuity."""

from copy import deepcopy
from pathlib import Path
import json
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from examples.complaints_pilot.qa_passes import run
from telryn import (
    evidence_signatures as signatures,
    history,
    peer_benchmark as peers,
    weekly_report,
)
from telryn.contracts import InputError, canonical


@unittest.skipUnless(shutil.which("ssh-keygen"), "portable signing needs OpenSSH")
class PortableEvidence(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.key = self.keypair("issuer")
        self.public = self.key.with_suffix(".pub")

    def keypair(self, name):
        key = self.root / name
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
        )
        return key

    def signed(self, path, body, key, purpose):
        path.write_text(
            canonical(signatures.sign(body, key, purpose)) + "\n", encoding="ascii"
        )
        return path

    def test_signature_purpose_and_pinned_key(self):
        other = self.keypair("other").with_suffix(".pub")
        value = {"yes": [True]}
        signed = signatures.sign(value, self.key, "telryn-test-signing")
        self.assertEqual(
            signatures.verify(signed, self.public, "telryn-test-signing"), value
        )
        for key, purpose in (
            (other, "telryn-test-signing"),
            (self.public, "telryn-different-purpose"),
        ):
            with self.assertRaises(InputError):
                signatures.verify(signed, key, purpose)
        signed["payload"] = {"yes": [False]}
        with self.assertRaises(InputError):
            signatures.verify(signed, self.public, "telryn-test-signing")

    def test_quarter_history_pins_previous_manifest_and_exact_weekly_bytes(self):
        rehearsal = run(self.root / "rehearsal", isolated=True)
        paths = [Path(w["report"]) for w in rehearsal["weeks"]]
        first = self.root / "first"
        with patch.dict(os.environ, TELRYN_TRUST_DIR=rehearsal["trust_directory"]):
            earlier = history.issue(
                paths,
                first,
                series="series",
                quarter="2026-Q4",
                private_key=self.key,
                public_key=self.public,
            )
            old = weekly_report.read_state(paths[0])
        self.assertEqual(len(earlier["quarters"][0]["missing_weeks"]), 11)
        # Model the next quarter with a different clock horizon, keeping the
        # existing signed weekly file shape and one reproducible byte sequence.
        later = deepcopy(old)
        later["week"] = "2027-01-04"
        later["complaint_records"]["snapshot"]["cutoff"] = "2027-01-18T08:00:00Z"
        next_week = self.root / "next.html"
        next_raw, _ = weekly_report._signed(later, b"x" * 32, 1, None)
        next_week.write_bytes(next_raw)
        second = self.root / "second"
        with patch.object(weekly_report, "verified_html", return_value=next_raw):
            current = history.issue(
                [next_week],
                second,
                series="series",
                quarter="2027-Q1",
                private_key=self.key,
                public_key=self.public,
                prior=first,
            )
        with patch.dict(os.environ, TELRYN_TRUST_DIR=str(self.root / "empty")):
            verified = history.verify(
                second,
                self.public,
                expected=current["sha256"],
                extends=earlier["sha256"],
            )
        self.assertEqual(len(verified["quarters"]), 2)
        self.assertEqual(
            verified["quarters"][1]["continuity"],
            "deadline sources, clock or cutoff timing changed",
        )
        self.assertFalse((self.root / "empty").exists())
        with self.assertRaises(InputError):
            history.verify(first, self.public, expected=current["sha256"])
        preserved = second / "2026-10-05.html"
        self.assertEqual(preserved.read_bytes(), paths[0].read_bytes())
        preserved.write_bytes(preserved.read_bytes() + b"edited")
        with self.assertRaisesRegex(InputError, "signed receipt"):
            history.verify(second, self.public)

    def test_peers_gate_duplicate_cohorts_contributions_and_roster_edits(self):
        keys = {f"firm{i}": self.keypair(f"firm{i}") for i in range(7)}
        roster = self.root / "roster.json"
        doc = {
            "schema": 1,
            "protocols": ["payments-v1"],
            "firms": {n: k.with_suffix(".pub").name for n, k in keys.items()},
        }
        roster.write_text(json.dumps(doc))
        store = self.root / "store"
        agg = {
            "schema": 1,
            "vendor": "vendor",
            "configuration": "v3",
            "quarter": "2026-Q4",
            "synthetic": True,
            "basis": "all_scored",
            "minimum_cell": 10,
            "weeks": [
                {
                    "week": "2026-10-05",
                    "metrics": {
                        "classification_disagreement": {"suppressed": True},
                        "passed_deadline_miss": {
                            "assigned": 20,
                            "denominator": 100,
                            "rate": 0.30,
                            "rate_bounds": [0.1, 0.5],
                        },
                    },
                }
            ],
        }
        c = ""
        for firm in list(keys)[:6]:
            body = {"firm": firm, "protocol": "payments-v1", "aggregate": agg}
            submission = self.signed(
                self.root / (firm + ".json"), body, keys[firm], peers.SUBMISSION
            )
            c = peers.submit(store, roster, submission)["cohort"]
            self.assertEqual(peers.submit(store, roster, submission)["cohort"], c)
            if firm == "firm0":
                with self.assertRaisesRegex(InputError, "at least six"):
                    peers.seal(store, roster, c)
        request = self.signed(
            self.root / "request.json",
            {"firm": "firm0", "cohort": c},
            keys["firm0"],
            peers.REQUEST,
        )
        with self.assertRaisesRegex(InputError, "sealed cohort"):
            peers.view(store, roster, request)
        peers.seal(store, roster, c)
        simple = peers.view(store, roster, request)
        self.assertEqual(simple["metrics"]["passed_deadline_miss"]["firms"], 6)
        self.assertEqual(simple["metrics"]["passed_deadline_miss"]["denominator"], 600)
        self.assertAlmostEqual(
            simple["metrics"]["passed_deadline_miss"]["point_estimate"], 0.3
        )
        self.assertTrue(simple["metrics"]["classification_disagreement"]["suppressed"])
        outside = self.signed(
            self.root / "outside.json",
            {"firm": "firm6", "cohort": c},
            keys["firm6"],
            peers.REQUEST,
        )
        with self.assertRaisesRegex(InputError, "own signed"):
            peers.view(store, roster, outside)
        doc["firms"].pop("firm1")
        roster.write_text(json.dumps(doc))
        self.assertEqual(simple, peers.view(store, roster, request))
        agg["weeks"][0]["week"] = "2026-10-12"
        extra = self.signed(
            self.root / "extra.json",
            {"firm": "firm0", "protocol": "payments-v1", "aggregate": agg},
            keys["firm0"],
            peers.SUBMISSION,
        )
        other = peers.submit(store, roster, extra)["cohort"]
        with self.assertRaisesRegex(InputError, "overlapping"):
            peers.seal(store, roster, other)
