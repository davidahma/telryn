"""Exercise a private operating record through a portable vendor attestation."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import patch

from examples.complaints_pilot import qa_passes
from telryn import attestation, evidence_signatures
from telryn.contracts import InputError, canonical, read_json

FIXTURES = Path(__file__).resolve().parents[1] / "examples/agent_release"


@skipUnless(shutil.which("ssh-keygen"), "optional OpenSSH evidence signing")
class VendorAttestation(TestCase):
    def setUp(self):
        base = Path(self.enterContext(TemporaryDirectory()))
        demo = qa_passes.run(base / "demo", isolated=True)
        self.store = base / "demo/trust"
        self.out = base / "vendor-pack"
        self.key = base / "issuer"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)],
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        profile = {
            "schema": 1,
            "vendor": "Test agent vendor",
            "customer": "Test payments bank",
            "source_queue": "Payments support",
            "bank_queue": "Synthetic payments support",
            "scorecard": "payments-support",
            "scorecard_version": "1.0",
            "evidence": "synthetic",
            "customer_controls": {
                name: [f"QA-{i}"] for i, name in enumerate(attestation.FILES, 1)
            },
        }
        path = base / "profile.json"
        path.write_text(canonical(profile), encoding="ascii")
        self.suite = base / "suite.json"
        self.suite.write_bytes((FIXTURES / "suite.json").read_bytes())
        self.env = {
            "TELRYN_TRUST_DIR": str(self.store),
            "TELRYN_SEED_DEMO_DB": str(base / "lab.db"),
            "TELRYN_DEMO_AGENT_REVISION": "demo-prompt-v2",
        }
        self.args = SimpleNamespace(
            quarter="2026-Q4",
            run_id="case-001",
            profile=path,
            suite=self.suite,
            out=self.out,
            key=self.key,
            bank=Path(demo["bank_report"]),
            reports=[Path(week["report"]) for week in demo["weeks"]],
            agent_adapter=FIXTURES / "bridge.py",
            register_adapter=FIXTURES / "bridge.py",
        )

    def test_issue_and_recipient_replay(self):
        with patch.dict(os.environ, self.env):
            self.assertEqual(attestation.issue(self.args)["test"], "passed")
        args = SimpleNamespace(
            **(
                vars(self.args)
                | {"folder": self.out, "key": self.key.with_suffix(".pub")}
            ),
        )
        with patch.dict(os.environ, TELRYN_TRUST_DIR=str(self.out / "absent")):
            receipt = attestation.verify(args)
            self.assertTrue(receipt["verified"])
            self.assertEqual(receipt["weeks"], 2)
            self.assertEqual(receipt["bank_week"], "2026-10-05")
            self.assertFalse((self.out / "absent").exists())
            extra = self.out / "extra.html"
            extra.write_text("not in the signed bundle", encoding="ascii")
            with self.assertRaisesRegex(InputError, "exactly the signed bundle"):
                attestation.verify(args)
            extra.unlink()
            bank = self.out / "bank-statement.html"
            bank.write_bytes(bank.read_bytes() + b"changed")
            with self.assertRaisesRegex(InputError, "member digest"):
                attestation.verify(args)

    def test_pins_the_recipient_profile_quarter_run_and_suite(self):
        with patch.dict(os.environ, self.env):
            attestation.issue(self.args)
        args = SimpleNamespace(
            **(
                vars(self.args)
                | {"folder": self.out, "key": self.key.with_suffix(".pub")}
            ),
        )
        args.run_id = "other-run"
        with self.assertRaisesRegex(InputError, "approved profile"):
            attestation.verify(args)
        args.run_id = self.args.run_id
        wrong = read_json(self.suite, "suite") | {"agent_revision": "another-version"}
        self.suite.write_text(canonical(wrong), encoding="ascii")
        with self.assertRaisesRegex(InputError, "another suite"):
            attestation.verify(args)
        manifest = self.out / "attestation.json"
        envelope = read_json(manifest, "manifest")
        unsigned = envelope["payload"] | {"customer": "other company"}
        with self.assertRaises(InputError):
            evidence_signatures.verify(
                envelope | {"payload": unsigned}, args.key, attestation.PURPOSE
            )

    def test_issuing_key_cannot_bypass_canonical_calibration_check(self):
        with patch.dict(os.environ, self.env):
            attestation.issue(self.args)
        page = self.out / "calibration.html"
        page.write_bytes(page.read_bytes() + b"<p>extra statement</p>")
        manifest = self.out / "attestation.json"
        payload = read_json(manifest, "manifest")["payload"]
        payload["file_sha256"]["calibration.html"] = hashlib.sha256(
            page.read_bytes()
        ).hexdigest()
        manifest.write_text(
            canonical(evidence_signatures.sign(payload, self.key, attestation.PURPOSE)),
            encoding="ascii",
        )
        check = SimpleNamespace(
            **(
                vars(self.args)
                | {"folder": self.out, "key": self.key.with_suffix(".pub")}
            ),
        )
        with self.assertRaisesRegex(InputError, "calibration page differs"):
            attestation.verify(check)

    def test_refuses_failed_seed_without_leaving_a_pack(self):
        suite = read_json(self.suite, "suite") | {"agent_revision": "demo-prompt-v1"}
        self.suite.write_text(canonical(suite), encoding="ascii")
        with patch.dict(
            os.environ, self.env | {"TELRYN_DEMO_AGENT_REVISION": "demo-prompt-v1"}
        ):
            with self.assertRaisesRegex(InputError, "test seed failed"):
                attestation.issue(self.args)
        self.assertFalse(self.out.exists())
