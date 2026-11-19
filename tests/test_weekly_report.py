"""Exercise the weekly lead/reviewer handoff, local updates and opt-in mail."""

import csv
import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from telryn.contracts import InputError
from telryn.__main__ import main
from telryn.weekly_mail import send_digest
from telryn.weekly_report import (
    COLUMNS,
    STATE_OPEN,
    adjudicate,
    digest,
    read_state,
    refresh,
    second_review,
    start,
)
from telryn.weekly_review import workload


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        trust_env = patch.dict(
            os.environ, {"TELRYN_TRUST_DIR": str(self.root / "trust")}
        )
        trust_env.start()
        self.addCleanup(trust_env.stop)
        self.plan = self.root / "weekly.toml"
        self.plan.write_text("""schema = 1
week = "2026-11-16"
rubric = "fraud-v1"
rubric_question = "=Could </script> break a frozen finding?"
screen_version = "demo-v1"
population = "population.csv"
reviewed = "reviewed.csv"
flagged_sample = 10
unflagged_sample = 2
""")
        (self.root / "population.csv").write_text(
            "case_id,revision,evidence_ref,screen_flag\n"
            "already-reviewed,v1,internal-0,1\n=private-a,v1,@private-ref,1\n"
            "private-b,v1,internal-b,0\nprivate-c,v1,internal-c,0\n"
        )
        (self.root / "reviewed.csv").write_text("case_id\nalready-reviewed\n")
        self.run = self.root / "run"
        self.report = start(self.plan, self.run)
        with (self.run / "grades.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.rows = list(csv.DictReader(stream))
        self.rows.sort(key=lambda row: row["case_id"])

    def packet(self, rows, name="return.csv"):
        path = self.root / name
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_portable_private_artifacts_with_blind_spreadsheet_safe_assignments(self):
        self.assertEqual(
            [r["case_id"] for r in self.rows], ["'=private-a", "private-b", "private-c"]
        )
        self.assertEqual(self.rows[0]["evidence_ref"], "'@private-ref")
        self.assertTrue(self.rows[0]["rubric_question"].startswith("'="))
        self.assertNotIn("screen_flag", self.rows[0])
        self.assertNotIn("seed", self.rows[0])
        self.assertTrue(all(not r["serious"] for r in self.rows))
        self.assertEqual(self.run.stat().st_mode & 0o777, 0o700)
        for name in ("report.html", "grades.csv"):
            self.assertEqual((self.run / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.report.name, "report.html")
        text = self.report.read_text()
        self.assertEqual(text.count("</script>"), 1)
        self.assertIn("&lt;/script&gt;", text)
        self.assertIn("default-src 'none'", text)
        self.assertIn("95% range", text)
        with self.assertRaises(FileExistsError):
            start(self.plan, self.run)
        self.plan.unlink()
        (self.root / "population.csv").unlink()
        self.assertEqual(read_state(self.report)["rubric"], "fraud-v1")

    def test_partial_return_effort_retry_and_later_resolution(self):
        row_a, row_b, row_c = self.rows
        observed = {
            **row_a,
            "serious": "insufficient",
            "disposition": "unknown",
            "minutes": "4",
        }
        negative = {**row_b, "serious": "no", "disposition": "none", "minutes": "2"}
        refresh(self.report, self.packet([observed, negative, row_c]))
        output = digest(self.report)
        self.assertIn("1 of 3", output)
        self.assertIn("1 returned with insufficient evidence", output)
        self.assertIn("6 review minutes", output)
        self.assertIn("95% range", output)
        resolved = {
            **row_a,
            "serious": "yes",
            "disposition": "new",
            "minutes": "7",
            "owner": "private-owner",
            "action": "private-incident/<abc>",
        }
        known = {
            **row_c,
            "serious": "yes",
            "disposition": "already_known",
            "minutes": "1",
        }
        state = refresh(self.report, self.packet([known, resolved]))
        self.assertEqual(state["earlier_grades"]["=private-a"][0]["minutes"], 4)
        self.assertEqual(state["grades"]["=private-a"]["minutes"], 7)
        before = self.report.read_bytes()
        refresh(self.report, self.packet([observed, known, negative]))
        refresh(self.report, self.packet([resolved]))
        self.assertEqual(self.report.read_bytes(), before)
        self.assertIn("private-incident/&lt;abc&gt;", self.report.read_text())
        self.assertIn("100.0%", self.report.read_text())
        output = digest(self.report)
        self.assertIn("2 confirmed serious", output)
        self.assertIn("1 newly found, 1 already known", output)
        self.assertIn("10 review minutes", output)
        self.assertNotIn("private-", output)

    def test_resume_partial_primary_returns_preserves_blinding_and_disputes(self):
        first, second, third = self.rows
        original = self.report.with_name("grades.csv").read_bytes()
        refresh(
            self.report,
            self.packet(
                [
                    {
                        **first,
                        "serious": "insufficient",
                        "disposition": "unknown",
                        "minutes": "2",
                    },
                    {**second, "serious": "no", "disposition": "none", "minutes": "3"},
                ]
            ),
            reviewer="A",
        )
        state = second_review(
            self.report,
            self.packet(
                [
                    {
                        **second,
                        "serious": "yes",
                        "disposition": "new",
                        "owner": "lead",
                        "action": "Review handoff",
                        "minutes": "2",
                    }
                ]
            ),
            reviewer="B",
        )
        self.assertEqual(
            workload(state),
            {
                "primary_outstanding": 1,
                "open_disputes": 1,
                "insufficient_undisputed": 1,
                "handling_unknown": 0,
                "assigned": 3,
            },
        )
        html = self.report.read_text().split(STATE_OPEN)[0]
        panel = html.split("Cases needing follow-up (1)")[1].split("</details>")[0]
        self.assertIn("@private-ref", panel)
        self.assertIn("Original reviewer amends primary grade", panel)
        self.assertNotIn("internal-b", panel)
        self.assertNotIn("@private-ref", digest(self.report))

        def issue(target, expected):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "weekly",
                            str(self.report),
                            "--outstanding",
                            str(target),
                            "--format",
                            "json",
                        ]
                    ),
                    expected,
                )
            return json.loads(output.getvalue())

        pending = self.run / "pending.csv"
        self.assertEqual(issue(pending, 0)["primary_outstanding"], 1)
        self.assertEqual(pending.stat().st_mode & 0o777, 0o600)
        with pending.open(encoding="utf-8-sig") as stream:
            self.assertEqual(list(csv.DictReader(stream)), [third])
        self.assertEqual(issue(pending, 2)["status"], "execution_error")
        grade = {**third, "serious": "no", "disposition": "none", "minutes": "5"}
        state = refresh(self.report, self.packet([grade]), reviewer="C")
        self.assertEqual(workload(state)["primary_outstanding"], 0)
        empty = self.run / "complete.csv"
        self.assertEqual(issue(empty, 0)["primary_outstanding"], 0)
        with empty.open(encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(tuple(reader.fieldnames), COLUMNS)
            self.assertEqual(list(reader), [])
        self.assertEqual(self.report.with_name("grades.csv").read_bytes(), original)

    def test_independent_reviewer_uses_canonically_equivalent_names(self):
        row = {**self.rows[0], "serious": "no", "disposition": "none", "minutes": "2"}
        returned = self.packet([row])
        refresh(self.report, returned, reviewer="José")
        original = self.report.read_bytes()
        refresh(self.report, returned, reviewer="Jose\u0301")
        with self.assertRaisesRegex(InputError, "independent"):
            second_review(self.report, returned, "JOSE\u0301")
        self.assertEqual(self.report.read_bytes(), original)
        other = self.packet([{**row, "serious": "yes", "disposition": "already_known"}])
        state = second_review(self.report, other, "Second reviewer")
        case = next(iter(state["grades"]))
        dispute = state["case_reviews"][case]["checks"][0]["id"]
        adjudicate(
            self.report, case, dispute, "Renée", "primary", "Checked evidence", 2
        )
        decided = self.report.read_bytes()
        adjudicate(
            self.report, case, dispute, "Rene\u0301e", "primary", "Checked evidence", 2
        )
        self.assertEqual(self.report.read_bytes(), decided)

    def test_resolving_prior_handling_preserves_all_returns(self):
        row = self.rows[1]
        insufficient = {
            **row,
            "serious": "insufficient",
            "disposition": "unknown",
            "minutes": "3",
        }
        unknown = {**row, "serious": "yes", "disposition": "unknown", "minutes": "6"}
        new = {
            **unknown,
            "disposition": "new",
            "owner": "QA lead",
            "action": "follow-up A",
            "minutes": "8",
        }
        refresh(self.report, self.packet([insufficient]))
        state = refresh(self.report, self.packet([unknown]))
        # The signed grade history remains stable while prior handling is investigated.
        case_id = row["case_id"]
        self.assertEqual(read_state(self.report), state)
        self.assertIn("1 with prior handling unknown", digest(self.report))
        existing = self.report.read_bytes()
        for invalid in (
            {**new, "owner": ""},
            {**new, "action": ""},
            {**new, "minutes": "5"},
            {**new, "serious": "no", "disposition": "none"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(InputError):
                refresh(self.report, self.packet([invalid]))
            self.assertEqual(self.report.read_bytes(), existing)
        state = refresh(self.report, self.packet([new]))
        prior = state["earlier_grades"][case_id]
        self.assertEqual(
            [(g["serious"], g["disposition"], g["minutes"]) for g in prior],
            [(None, "unknown", 3), (True, "unknown", 6)],
        )
        outcome = digest(self.report)
        self.assertIn(
            "1 newly found, 0 already known, 0 with prior handling unknown", outcome
        )
        self.assertIn("8 review minutes", outcome)
        final = self.report.read_bytes()
        for delivery in (unknown, insufficient, new):
            refresh(self.report, self.packet([delivery]))
            self.assertEqual(self.report.read_bytes(), final)
        for different in (
            {**unknown, "minutes": "7"},
            {**new, "disposition": "already_known"},
        ):
            with self.assertRaisesRegex(InputError, "conflicting"):
                refresh(self.report, self.packet([different]))
            self.assertEqual(self.report.read_bytes(), final)

    def test_unknown_handling_can_resolve_to_an_existing_control(self):
        row = {
            **self.rows[2],
            "serious": "yes",
            "disposition": "unknown",
            "minutes": "2",
        }
        refresh(self.report, self.packet([row]))
        refresh(
            self.report,
            self.packet(
                [
                    {
                        **row,
                        "disposition": "already_known",
                        "action": "existing complaint",
                    }
                ]
            ),
        )
        self.assertIn(
            "0 newly found, 1 already known, 0 with prior handling unknown",
            digest(self.report),
        )
        self.assertIn("2 review minutes", digest(self.report))

    def test_cross_run_metadata_invalid_answers_and_duplicates_rollback(self):
        row_a, row_b, _ = self.rows
        valid = {
            **row_a,
            "serious": "yes",
            "disposition": "new",
            "owner": "lead",
            "action": "work-1",
            "minutes": "2",
        }
        other_run = start(self.plan, self.root / "other")
        with (other_run.parent / "grades.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            unrelated = next(csv.DictReader(stream))
        original = self.report.read_bytes()
        for rows in (
            [valid, unrelated],
            [valid, {**row_b, "revision": "v2"}],
            [valid, valid],
            [valid, {**row_b, "serious": "yes", "disposition": "new", "minutes": "2"}],
            [valid, {**row_b, "serious": "no", "disposition": "new", "minutes": "1"}],
            [valid, {**row_b, "serious": "no", "disposition": "none", "minutes": "-1"}],
            [valid, {**row_b, "minutes": "1"}],
            [],
        ):
            with self.subTest(rows=rows), self.assertRaises(InputError):
                refresh(self.report, self.packet(rows))
            self.assertEqual(self.report.read_bytes(), original)
        refresh(self.report, self.packet([valid]))
        changed = self.report.read_bytes()
        with self.assertRaisesRegex(InputError, "conflicting"):
            refresh(self.report, self.packet([{**valid, "action": "different"}]))
        self.assertEqual(self.report.read_bytes(), changed)

    def test_concurrent_disjoint_returns_both_survive(self):
        packets = [
            self.packet(
                [{**row, "serious": "no", "disposition": "none", "minutes": "1"}],
                f"p{i}.csv",
            )
            for i, row in enumerate(self.rows)
        ]
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda packet: refresh(self.report, packet), packets))
        self.assertEqual(len(read_state(self.report)["grades"]), 3)
        self.assertIn("3 of 3", digest(self.report))

    def test_modified_or_unknown_version_report_cannot_be_refreshed(self):
        original = self.report.read_text()
        head, tail = original.split(STATE_OPEN)
        raw, ending = tail.split("</script>")
        for where in ("state", "envelope"):
            value = json.loads(raw)
            if where == "state":
                value["state"]["week"] = "2026-11-23"
            else:
                value["schema"] = 23
            self.report.write_text(
                head
                + STATE_OPEN
                + json.dumps(value).replace("<", r"\u003c")
                + "</script>"
                + ending
            )
            with self.assertRaises(InputError):
                refresh(self.report, self.packet(self.rows))
        with self.assertRaises(InputError):
            read_state(self.root / "weekly.toml")

    def test_tls_email_contains_only_aggregates_and_one_recipient(self):
        env = {
            "TELRYN_TRUST_DIR": str(self.root / "trust"),
            "TELRYN_SMTP_HOST": "mail.test",
            "TELRYN_SMTP_PORT": "4465",
            "TELRYN_SMTP_USERNAME": "operator",
            "TELRYN_SMTP_PASSWORD": "example",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("telryn.weekly_mail.smtplib.SMTP_SSL") as smtp,
        ):
            send_digest(self.report, "lead@internal.test", "audit@internal.test")
            self.assertEqual(smtp.call_args.args, ("mail.test", 4465))
            self.assertTrue(smtp.call_args.kwargs["context"].check_hostname)
            session = smtp.return_value.__enter__.return_value
            session.login.assert_called_once_with("operator", "example")
            msg = session.send_message.call_args.args[0]
            self.assertEqual(msg["To"], "lead@internal.test")
            self.assertEqual(msg.get_content(), digest(self.report))
            self.assertNotIn("private", msg.get_content())
            self.assertNotIn("fraud-v1", msg.get_content())
            self.assertNotIn("Bcc", msg)
            self.assertFalse(msg.is_multipart())

    def test_bad_mail_config_or_recipient_fails_before_connecting(self):
        good = "lead@internal.test"
        for who, config in (
            (good, {}),
            ("a@x.test,b@y.test", {"TELRYN_SMTP_HOST": "x"}),
            ("a@x.test\r\nBcc: b@y.test", {"TELRYN_SMTP_HOST": "x"}),
            (good, {"TELRYN_SMTP_HOST": "x", "TELRYN_SMTP_PORT": "0"}),
            (good, {"TELRYN_SMTP_HOST": "x", "TELRYN_SMTP_USERNAME": "operator"}),
        ):
            with (
                self.subTest(who=who, config=config),
                patch.dict(
                    os.environ,
                    {**config, "TELRYN_TRUST_DIR": str(self.root / "trust")},
                    clear=True,
                ),
                patch("telryn.weekly_mail.smtplib.SMTP_SSL") as smtp,
                self.assertRaises(InputError),
            ):
                send_digest(self.report, who, good)
            smtp.assert_not_called()
