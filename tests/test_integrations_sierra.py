"""Validate a pinned customer S3 delivery through the Sierra CLI entry point."""

import csv
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from telryn.__main__ import main
from telryn.integrations_sierra import MAX_BYTES


class SierraDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name) / "population.csv"
        self.args = [
            "integrations",
            "pull-sierra",
            "--bucket",
            "qa-example",
            "--key",
            "weekly/closed.csv",
            "--version-id",
            "release+v1",
            "--bucket-owner",
            "012345678901",
            "--expected-count",
            "2",
            "--workspace",
            "uk",
            "--flag-column",
            "flag",
            "--out",
            str(self.out),
        ]
        self.raw = b"conversation_id,revision,evidence_ref,flag\na,v1,trace-a,true\nb,v2,trace-b,false\n"
        self.head = {"VersionId": "release+v1", "ContentLength": len(self.raw)}
        self.get = self.head.copy()
        self.download = None

    def s3(self, command, **options):
        self.assertEqual(command[:2], ["aws", "s3api"])
        for flag in (
            "--bucket=qa-example",
            "--key=weekly/closed.csv",
            "--version-id=release+v1",
            "--expected-bucket-owner=012345678901",
        ):
            self.assertIn(flag, command)
        self.assertEqual(options["env"]["AWS_IGNORE_CONFIGURED_ENDPOINT_URLS"], "true")
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertNotIn("shell", options)
        if command[2] == "head-object":
            metadata = self.head
        else:
            self.assertEqual(command[2], "get-object")
            self.download = Path(command[-1])
            self.download.write_bytes(self.raw)
            metadata = self.get
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(metadata).encode()
        )

    def run_cli(self, args=(), code=0):
        std, err = io.StringIO(), io.StringIO()
        with redirect_stdout(std), redirect_stderr(err):
            actual = main(self.args + list(args))
        self.assertEqual(actual, code, err.getvalue())
        return std.getvalue() if code == 0 else err.getvalue()

    def test_reads_exact_delivery_and_exports_scoped_population(self):
        with patch(
            "telryn.integrations_sierra.subprocess.run", side_effect=self.s3
        ) as request:
            digest = hashlib.sha256(self.raw).hexdigest()
            self.assertIn(digest, self.run_cli(["--sha256", digest]))
            self.assertIn("already exists", self.run_cli(code=2))
            self.assertEqual(request.call_count, 2)
        self.assertFalse(self.download.exists())
        with self.out.open(newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(
            [row["case_id"] for row in rows], ["sierra:uk:a", "sierra:uk:b"]
        )
        self.assertEqual([row["screen_flag"] for row in rows], ["true", "false"])
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o600)

    def test_changed_version_count_schema_size_or_checksum_never_publishes(self):
        for variant in (
            "head-version",
            "get-version",
            "length",
            "oversize",
            "count",
            "checksum",
            "private-column",
        ):
            with self.subTest(variant=variant):
                self.set_inputs(variant)
                extra = {
                    "count": ["--expected-count", "3"],
                    "checksum": ["--sha256", "0" * 64],
                }.get(variant, [])
                with patch(
                    "telryn.integrations_sierra.subprocess.run", side_effect=self.s3
                ):
                    self.run_cli(extra, code=2)
                self.assertFalse(self.out.exists())
                if self.download:
                    self.assertFalse(self.download.exists())

    def set_inputs(self, variant):
        data = b"conversation_id,revision,evidence_ref,flag\na,v1,trace-a,true\nb,v2,trace-b,false\n"
        self.raw = (
            data
            if variant != "private-column"
            else data.replace(b",flag\n", b",flag,body\n")
        )
        self.head = {"VersionId": "release+v1", "ContentLength": len(self.raw)}
        self.get = self.head.copy()
        if variant == "head-version":
            self.head["VersionId"] = "other"
        elif variant == "get-version":
            self.get["VersionId"] = "other"
        elif variant == "length":
            self.get["ContentLength"] += 2
        elif variant == "oversize":
            self.head["ContentLength"] = MAX_BYTES + 1

    def test_invalid_s3_identifiers_fail_before_connecting(self):
        for args in (
            ["--version-id", "null"],
            ["--bucket-owner", "other"],
            ["--expected-count", "-1"],
            ["--key", "file:///etc/hosts"],
            ["--key", "https://untrusted.test/data"],
        ):
            with (
                self.subTest(args=args),
                patch("telryn.integrations_sierra.subprocess.run") as request,
            ):
                self.run_cli(args, code=2)
                request.assert_not_called()

    def test_cloud_failure_omits_vendor_output(self):
        exc = subprocess.CalledProcessError(
            1, ["aws"], stderr=b"private vendor response"
        )
        with patch("telryn.integrations_sierra.subprocess.run", side_effect=exc):
            error = self.run_cli(code=2)
        self.assertIn("head-object failed", error)
        self.assertNotIn("private vendor", error)
        self.assertFalse(self.out.exists())
