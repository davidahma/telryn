"""Exercise the browser HTTP entry point without requiring a listening socket in CI."""

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from telryn.desk import Desk, handler
from telryn.weekly import summarize
from telryn.weekly_report import read_state


class Connection:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = io.BytesIO()

    def makefile(self, *args):
        return self.request

    def sendall(self, data):
        self.response.write(data)

    def settimeout(self, value):
        pass


class DeskFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.desk = Desk(Path(self.temp.name))
        self.handle = handler(self.desk, "testtoken")
        self.form = {
            "week": "2026-11-02",
            "rubric": "fixture",
            "rubric_question": "Was escalation missed?",
            "screen_version": "none",
            "population": "case_id,revision,evidence_ref,screen_flag\na,r1,source,false\n",
            "reviewed": "case_id\n",
            "flagged_sample": 0,
            "unflagged_sample": 1,
            "receipt": "",
        }

    def request(
        self,
        method,
        path,
        data=None,
        *,
        host="127.0.0.1:8000",
        origin="http://127.0.0.1:8000",
    ):
        body = b"" if data is None else json.dumps(data).encode()
        raw = (
            f"{method} {path} HTTP/1.0\r\nHost: {host}\r\nOrigin: {origin}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode() + body
        conn = Connection(raw)
        self.handle(conn, ("127.0.0.1", 3251), SimpleNamespace(server_port=8000))
        head, body = conn.response.getvalue().split(b"\r\n\r\n", 1)
        return head.decode(), body

    def test_create_download_return_read_and_resume(self):
        head, page = self.request("GET", "/testtoken/")
        self.assertIn("200 OK", head)
        self.assertIn("nonce-", head)
        self.assertNotIn("testtoken", head)
        self.assertNotIn(b"testtoken", page)
        self.assertNotIn(b"SESSION_NONCE", page)
        presets = json.loads(self.request("GET", "/testtoken/rubrics")[1])
        self.assertEqual(len(presets), 5)
        self.assertIn("promised-refund", presets)
        head, body = self.request("POST", "/testtoken/audits", self.form)
        self.assertIn("200 OK", head)
        run = json.loads(body)["id"]
        base = "/testtoken/" + run
        head, body = self.request("GET", base + "/grades.csv")
        self.assertIn("attachment", head)
        grades = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=grades.fieldnames)
        writer.writeheader()
        row = next(grades)
        row.update(
            serious="yes", disposition="new", owner="Lead", action="CASE-1", minutes="3"
        )
        writer.writerow(row)
        head, _ = self.request("POST", base + "/grades", {"grades": out.getvalue()})
        self.assertIn("200 OK", head)
        self.assertIn(b"CASE-1", self.request("GET", base + "/report.html")[1])
        self.assertEqual(
            json.loads(self.request("GET", "/testtoken/audits")[1])[0]["id"], run
        )
        self.assertEqual(summarize(read_state(self.desk.report(run)))["estimate"], 1)

    def test_foreign_origin_host_token_and_invalid_input(self):
        self.assertIn(
            "403",
            self.request(
                "POST", "/testtoken/audits", self.form, origin="https://wrong.test"
            )[0],
        )
        self.assertIn("404", self.request("GET", "/testtoken/", host="wrong.test")[0])
        headers, body = self.request("GET", "/wrong/")
        self.assertIn("404", headers)
        self.assertNotIn("testtoken", headers)
        self.assertNotIn(b"testtoken", body)
        self.assertIn(
            "400",
            self.request("POST", "/testtoken/audits", {**self.form, "week": "../bad"})[
                0
            ],
        )
        self.assertFalse(list(self.desk.root.iterdir()))
