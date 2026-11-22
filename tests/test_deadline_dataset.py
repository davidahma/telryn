"""Check the standalone archive and representative rule bounds without a network."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import tarfile
import unittest

from deadlines import build


class DeadlineDataset(unittest.TestCase):
    def test_release_contains_bounded_calendars_schemas_and_hashed_rules(self):
        with TemporaryDirectory() as temp:
            one, two = (Path(temp) / name for name in ("a", "b"))
            build.build(one)
            build.build(two)
            self.assertEqual(one.read_bytes(), two.read_bytes())
            with tarfile.open(one) as package:
                files = {
                    member.name.split("/", 1)[1]: package.extractfile(member).read()
                    for member in package.getmembers()
                }
            manifest = dict(
                (name, digest)
                for digest, name in (
                    line.split("  ", 1)
                    for line in files.pop("SHA256SUMS.txt").decode("ascii").splitlines()
                )
            )
            self.assertEqual(
                {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
                manifest,
            )
            rules = json.loads(files["rules.json"])
            self.assertEqual(rules["version"], "1.0.0")
            self.assertEqual(
                {r["jurisdiction"] for r in rules["rules"]}, {"GB", "IE", "US", "AU"}
            )
            clock = next(
                row for row in rules["rules"] if row["id"] == "us-reg-z-resolution"
            )
            self.assertEqual(
                clock["period"], {"count": 2, "unit": "complete_billing_cycles"}
            )
            self.assertEqual(
                clock["outer_limit"], {"count": 90, "unit": "calendar_days"}
            )
            self.assertEqual(clock["computation"], "evidence_required")
            self.assertEqual(
                {
                    json.loads(data)["id"]
                    for name, data in files.items()
                    if name.startswith("calendars/")
                },
                {
                    "scotland",
                    "northern-ireland",
                    "england-and-wales",
                    "ireland-public-holidays",
                    "us-federal-reserve-banks",
                },
            )
