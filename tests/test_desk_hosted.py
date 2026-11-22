"""Verify role boundaries and authenticated reviewer attribution at the HTTP entry point."""

import csv
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from telryn.contracts import InputError
from telryn.desk import Desk, handler
from telryn.desk_hosted import Proxy
from telryn.weekly_report import read_state
from tests.audit_support import isolate_trust
from tests.test_desk import Connection


class HostedDesk(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        isolate_trust(self, self.root)
        self.desk = Desk(self.root / "audits")
        self.key = "a" * 64
        self.path = self.root / "proxy-key"
        self.path.write_text(self.key)
        self.path.chmod(0o600)
        self.proxy = Proxy.configure("https://desk.bank.test", self.path)
        self.handle = handler(self.desk, "unused", self.proxy)
        self.form = {
            "week": "2026-11-02",
            "rubric": "fixture",
            "rubric_question": "Was a complaint missed?",
            "screen_version": "none",
            "population": "case_id,revision,evidence_ref,screen_flag\na,1,private-evidence,false\n",
            "reviewed": "case_id\n",
            "flagged_sample": 0,
            "unflagged_sample": 1,
        }

    def request(
        self, method, path, fields=None, role="lead", subject="staff001", header=None
    ):
        payload = json.dumps(fields or {}).encode()
        headers = {
            "Host": "desk.bank.test",
            "Origin": self.proxy.origin,
            "X-Telryn-Proxy": self.key,
            "X-Telryn-Subject": subject,
            "X-Telryn-Role": role,
            **(header or {}),
        }
        lines = [
            f"{method} {path} HTTP/1.0",
            *(f"{k}: {v}" for k, v in headers.items()),
            "Content-Type: application/json",
            f"Content-Length: {len(payload)}",
            "",
            "",
        ]
        conn = Connection("\r\n".join(lines).encode() + payload)
        self.handle(conn, ("127.0.0.1", 12345), SimpleNamespace(server_port=8000))
        head, raw = conn.response.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split(maxsplit=2)[1]), raw

    def test_proxy_and_role_boundaries(self):
        for bad in (
            {"X-Telryn-Proxy": ""},
            {"X-Telryn-Role": "unknown"},
            {"X-Telryn-Subject": "A,B"},
            {"Host": "other.test"},
        ):
            self.assertEqual(self.request("GET", "/", header=bad)[0], 404)
        self.assertEqual(
            self.request(
                "POST",
                "/audits",
                self.form,
                header={"Origin": "https://elsewhere.test"},
            )[0],
            403,
        )
        self.assertEqual(
            self.request("POST", "/audits", self.form, role="reviewer")[0], 403
        )
        status, body = self.request("POST", "/audits", self.form)
        self.assertEqual(status, 200, body)
        ident = json.loads(body)["id"]
        path = "/" + ident + "/"
        status, body = self.request("GET", path + "grades.csv", role="reviewer")
        self.assertEqual(status, 200)
        row = next(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        row.update(
            serious="yes",
            disposition="new",
            owner="Alice",
            action="private-owner-task",
            minutes="3",
        )
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=row.keys())
        writer.writeheader()
        writer.writerow(row)
        data = {"reviewer": "someone-else", "grades": output.getvalue()}
        self.assertEqual(
            self.request(
                "POST", path + "grades", data, role="reviewer", subject="actual-id"
            )[0],
            200,
        )
        state = read_state(self.desk.report(ident))
        bound = state["case_reviews"]["a"]["primary_by"]
        self.assertIn("actual-id", bound)
        self.assertNotIn("someone-else", bound)
        self.assertEqual(
            self.request(
                "POST", path + "grades", data, role="reviewer", subject="Actual-Id"
            )[0],
            400,
        )
        self.assertEqual(
            self.request(
                "POST",
                path + "second-review",
                data,
                role="reviewer",
                subject="actual-id",
            )[0],
            400,
        )
        status, body = self.request("GET", path + "review", role="reviewer")
        self.assertEqual(status, 200)
        self.assertNotIn("private-owner-task", body.decode())
        self.assertNotIn("agreed", json.loads(body))
        for endpoint in ("report.html", "manifest.json"):
            self.assertEqual(
                self.request("GET", path + endpoint, role="reviewer")[0], 403
            )
        for endpoint in (
            "records",
            "links",
            "adjudicate",
            "policy-preview",
            "policy-review",
            "bank",
        ):
            self.assertEqual(
                self.request("POST", path + endpoint, role="reviewer")[0], 403
            )
        self.assertEqual(self.request("GET", path + "report.html")[0], 200)
        status, body = self.request("GET", "/", role="reviewer")
        self.assertEqual(status, 200)
        self.assertIn(b'data-role="reviewer"', body)
        self.assertNotIn(self.key.encode(), body)

    def test_private_log_distinguishes_viewers_packets_and_unauthorized_requests(self):
        status, body = self.request("POST", "/audits", self.form)
        self.assertEqual(status, 200)
        route = "/" + json.loads(body)["id"]
        self.assertEqual(
            self.request("GET", route + "/report.html", role="reviewer")[0], 403
        )
        requests = [
            ("report.html", "lead", "Compliance02"),
            ("grades.csv", "reviewer", "Reviewer03"),
            ("manifest.json", "lead", "Compliance04"),
        ]
        with ThreadPoolExecutor(max_workers=3) as pool:

            def access(request):
                suffix, role, user = request
                return self.request(
                    "GET", route + "/" + suffix, role=role, subject=user
                )[0]

            self.assertEqual(list(pool.map(access, requests)), [200, 200, 200])
        self.assertEqual(
            self.request("GET", route + "/review", role="reviewer")[0], 200
        )
        self.assertEqual(
            self.request("GET", route + "/report.html/opaque?token=private-url")[0], 404
        )
        self.assertEqual(
            self.request(
                "GET",
                route + "/report.html",
                subject="unverified",
                header={"X-Telryn-Proxy": "wrong"},
            )[0],
            404,
        )
        log = self.root / "audit-trust/desk-access.sqlite3"
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(log)) as db:
            vals = db.execute("""
                SELECT subject,role,route,status,case_id
                FROM access_events LEFT JOIN access_cases ON id=event_id ORDER BY route,subject
            """).fetchall()
            self.assertIn(("Compliance02", "lead", "weekly-report", 200, "a"), vals)
            self.assertIn(("Reviewer03", "reviewer", "grading-packet", 200, "a"), vals)
            self.assertIn(("Compliance04", "lead", "records-manifest", 200, "a"), vals)
            self.assertIn(("staff001", "reviewer", "weekly-report", 403, None), vals)
            self.assertIn(("staff001", "reviewer", "review-worklist", 200, None), vals)
            self.assertIn(("staff001", "lead", "unknown", 404, None), vals)
            for (at,) in db.execute("SELECT utc FROM access_events"):
                self.assertEqual(
                    datetime.fromisoformat(at).utcoffset().total_seconds(), 0
                )
        raw = log.read_bytes()
        for value in ("unverified", "private-evidence", "private-url", self.key):
            self.assertNotIn(value.encode(), raw)

    def test_log_migrates_and_distinguishes_case_id_reused_across_audits(self):
        trust = self.root / "audit-trust"
        trust.mkdir(mode=0o700)
        path = trust / "desk-access.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                """CREATE TABLE access_events (id INTEGER PRIMARY KEY, utc TEXT NOT NULL,
                subject TEXT NOT NULL, role TEXT NOT NULL, route TEXT NOT NULL,
                method TEXT NOT NULL, status INTEGER)"""
            )
            db.execute(
                "INSERT INTO access_events VALUES(1,?,?,?,?,?,?)",
                (
                    "2026-11-02+00:00",
                    "prior-reviewer",
                    "reviewer",
                    "grading-packet",
                    "GET",
                    200,
                ),
            )
        path.chmod(0o600)
        ids = []
        for week in ("2026-11-02", "2026-11-09"):
            status, body = self.request("POST", "/audits", self.form | {"week": week})
            self.assertEqual(status, 200, body)
            run = json.loads(body)["id"]
            ids.append(read_state(self.desk.report(run))["publication_id"])
            self.assertEqual(self.request("GET", "/" + run + "/report.html")[0], 200)
        self.assertNotEqual(*ids)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT publication_id, revision FROM access_events WHERE id=1"
                ).fetchone(),
                (None, None),
            )
            self.assertEqual(
                db.execute("""
                    SELECT case_id, publication_id, revision
                    FROM access_events JOIN access_cases ON access_events.id=access_cases.event_id
                    WHERE route='weekly-report' ORDER BY access_events.id
                """).fetchall(),
                [("a", ident, 1) for ident in ids],
            )

    def test_unavailable_log_blocks_protected_delivery_and_changes(self):
        status, body = self.request("POST", "/audits", self.form)
        self.assertEqual(status, 200)
        path = "/" + json.loads(body)["id"] + "/report.html"
        log = self.root / "audit-trust/desk-access.sqlite3"
        log.chmod(0o0644)
        self.assertEqual(
            self.request("GET", path), (503, b"Hosted access log unavailable")
        )
        self.assertEqual(self.request("POST", "/audits", self.form)[0], 503)
        self.assertEqual(len(self.desk.runs), 1)

    def test_long_sso_subjects_remain_distinct_in_reviewer_ledger(self):
        from telryn.desk_hosted import reviewer_id
        from telryn.weekly_review import identity, _same

        prefix = "A" * 127
        first = identity(reviewer_id(prefix + "B"))
        second = identity(reviewer_id(prefix + "b"))
        self.assertLessEqual(len(first), 120)
        self.assertFalse(_same(first, second))

    def test_ambiguous_upload_rejected_before_signing(self):
        from telryn import desk_evidence
        from telryn.desk import _form

        with self.assertRaisesRegex(InputError, "duplicate JSON key"):
            _form("application/json", b'{"week":1,"week":2}')
        with self.assertRaisesRegex(InputError, "duplicate JSON key"):
            desk_evidence.attach(
                self.root / "unused",
                {"manifest": '{"register":{"file":"a","file":"b"}}'},
                None,
            )

    def test_private_secret_and_strict_https_origin(self):
        for origin in (
            "http://desk.bank.test",
            "https://desk.bank.test/a",
            "https://user@desk.bank.test",
            "https://desk.bank.test?q=x",
        ):
            with self.assertRaises(InputError):
                Proxy.configure(origin, self.path)
        self.path.chmod(0o644)
        with self.assertRaises(InputError):
            Proxy.configure(self.proxy.origin, self.path)
