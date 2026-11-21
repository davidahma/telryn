"""Check the weekly pass audit against the two independent staged source exports."""

import csv
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from examples.complaints_pilot import __main__ as pilot
from telryn.__main__ import main


class QaPassesRehearsal(unittest.TestCase):
    def test_published_workflow_from_pass_to_action_in_both_weeks(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root / "run"
            with (
                patch.dict(
                    os.environ,
                    {
                        "TELRYN_TRUST_DIR": str(root / "team"),
                        "TELRYN_DEMO_TRUST_DIR": str(root / "demo"),
                    },
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(
                    main(
                        ["demo", "--qa-passes", "--out", str(out), "--format", "json"]
                    ),
                    0,
                )
            result = json.loads(output.getvalue())
            self.assertFalse((root / "team").exists())
            self.assertEqual(result["evidence"], "synthetic")
            self.assertTrue(Path(result["bank_report"]).is_file())
            for (
                summary,
                previous,
                expected,
                case_id,
                prediction,
                deadline,
                events,
                entry,
            ) in zip(
                result["weeks"],
                ("C101", "C201"),
                (10, 8),
                ("C102", "C204"),
                ("no", "yes"),
                ("2026-10-07T17:00:00Z", "2026-10-14T17:00:00Z"),
                (("A12", "A13"), ("A24", "A25")),
                ("R12", "R24"),
                strict=True,
            ):
                with self.subTest(week=summary["week"]):
                    report = Path(summary["report"])
                    with (
                        patch.dict(
                            os.environ, {"TELRYN_TRUST_DIR": str(root / "demo")}
                        ),
                        redirect_stdout(io.StringIO()) as readback,
                    ):
                        self.assertEqual(
                            main(["weekly", str(report), "--format", "json"]), 0
                        )
                    verified = json.loads(readback.getvalue())
                    self.assertEqual(verified["verification"], "team_hmac_verified")
                    cards = {c["case_id"]: c for c in verified["score_audit"]["cases"]}
                    register = {
                        c["case_id"]: c for c in verified["complaints"]["cases"]
                    }
                    originals = out / "source-records" / summary["week"]
                    with (originals / "scores.csv").open(newline="") as scores:
                        raw_scores = {c["case_id"]: c for c in csv.DictReader(scores)}
                    self.assertEqual(
                        (originals / "scores.csv").read_bytes(),
                        (
                            pilot.FIXTURE / "scores" / f"{summary['week']}.csv"
                        ).read_bytes(),
                    )
                    self.assertEqual(set(raw_scores), set(cards))
                    self.assertEqual(len(cards), expected)
                    self.assertIn(previous, cards)
                    self.assertEqual(summary["worked_case"], case_id)
                    self.assertEqual(summary["score"], cards[case_id])
                    self.assertEqual(summary["finding"], register[case_id])
                    for key in (
                        "decision",
                        "complaint_prediction",
                        "score_ref",
                        "scored_at",
                    ):
                        self.assertEqual(cards[case_id][key], raw_scores[case_id][key])
                    self.assertEqual(raw_scores[case_id]["decision"], "pass")
                    self.assertEqual(
                        raw_scores[case_id]["complaint_prediction"], prediction
                    )
                    self.assertIs(register[case_id]["grade"], True)
                    self.assertEqual(register[case_id]["deadline"], deadline)
                    self.assertEqual(
                        register[case_id]["registration_at_cutoff"], "missing"
                    )
                    self.assertEqual(
                        register[case_id]["registration_as_of"], "registered"
                    )
                    self.assertEqual(register[case_id]["owner_as_of"], "completed")
                    self.assertEqual(
                        [a["action_id"] for a in register[case_id]["actions"]],
                        list(events),
                    )
                    self.assertEqual(
                        [e["entry_id"] for e in register[case_id]["register_entries"]],
                        [entry],
                    )
                    with (originals / "owner-events.csv").open(newline="") as actions:
                        self.assertEqual(
                            [
                                a
                                for a in csv.DictReader(actions)
                                if a["case_id"] == case_id
                            ],
                            register[case_id]["actions"],
                        )
                    with (originals / "register.csv").open(newline="") as entries:
                        self.assertEqual(
                            [
                                e
                                for e in csv.DictReader(entries)
                                if e["entry_id"] == entry
                            ],
                            register[case_id]["register_entries"],
                        )
                    with report.with_name("grades.csv").open(newline="") as f:
                        packet = list(csv.DictReader(f))
                    self.assertEqual({r["case_id"] for r in packet}, set(raw_scores))
                    self.assertFalse(
                        set(packet[0])
                        & {
                            "decision",
                            "score_ref",
                            "scored_at",
                            "complaint_prediction",
                            "registration_at_cutoff",
                        }
                    )
                    self.assertTrue(all(r["serious"] == "" for r in packet))
