"""Exercise the browser HTTP entry point without requiring a listening socket in CI."""

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.audit_support import isolate_trust
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
        isolate_trust(self, self.temp.name)
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
        content_type="application/json",
    ):
        body = (
            data
            if isinstance(data, bytes)
            else (b"" if data is None else json.dumps(data).encode())
        )
        raw = (
            f"{method} {path} HTTP/1.0\r\nHost: {host}\r\nOrigin: {origin}\r\n"
            f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n\r\n"
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

    def multipart(self, fields):
        parts = []
        settings = {
            k: v
            for k, v in fields.items()
            if k not in ("population", "reviewed", "receipt", "grades")
        }
        for key, value in {
            "settings": json.dumps(settings),
            **{k: v for k, v in fields.items() if k not in settings},
        }.items():
            parts.append(
                (
                    '--boundary\r\nContent-Disposition: form-data; name="'
                    + key
                    + '"\r\n\r\n'
                ).encode()
                + value.encode()
                + b"\r\n"
            )
        return b"".join(parts) + b"--boundary--\r\n"

    def test_combined_files_and_per_file_limits(self):
        fields = {
            **self.form,
            "population": "case_id,revision,evidence_ref,screen_flag\r\n",
            "reviewed": "case_id\r\n",
        }
        # Exercise the same transport limits at a small scale.
        with patch("telryn.desk.MAX_FILE", 128), patch("telryn.desk.MAX_BODY", 4096):
            for i in range(3):
                key = f"sample-{i}"
                fields["population"] += f"{key},r1,doc,false\r\n"
                fields["reviewed"] += key + "\r\n"
            self.assertGreater(len(fields["population"]) + len(fields["reviewed"]), 128)
            args = dict(content_type="multipart/form-data; boundary=boundary")
            head, _ = self.request(
                "POST", "/testtoken/audits", self.multipart(fields), **args
            )
            self.assertIn("200 OK", head)
            head, body = self.request(
                "POST",
                "/testtoken/audits",
                self.multipart({**fields, "reviewed": "x" * 129}),
                **args,
            )
            self.assertIn("413", head)
            self.assertIn(b"per-file limit", body)
            head, body = self.request("POST", "/testtoken/audits", b"x" * 4097, **args)
            self.assertIn("413", head)
            self.assertIn(b"complete upload", body)

    def test_plan_before_draw_and_secondary_review(self):
        fields = {
            **self.form,
            "planning": {"minutes": 10, "minutes_per_grade": 5, "expected_rate": 0.05},
        }
        head, preview = self.request("POST", "/testtoken/plan", fields)
        self.assertIn("200 OK", head)
        self.assertEqual(json.loads(preview)["assigned"], 1)
        self.assertFalse(list(self.desk.runs))
        head, body = self.request("POST", "/testtoken/audits", fields)
        self.assertIn("200 OK", head)
        path = "/testtoken/" + json.loads(body)["id"]
        packet = self.request("GET", path + "/grades.csv")[1].decode("utf-8-sig")

        def answer(serious, disposition):
            reader = csv.DictReader(io.StringIO(packet))
            row = next(reader)
            row.update(serious=serious, disposition=disposition, minutes=2)
            if serious == "yes":
                row.update(owner="Support", action="item-1")
            buf = io.StringIO()
            writer = csv.DictWriter(buf, reader.fieldnames)
            writer.writeheader()
            writer.writerow(row)
            return buf.getvalue()

        for route, name, outcome in [
            ("grades", "A", ("no", "none")),
            ("second-review", "B", ("yes", "new")),
        ]:
            head, _ = self.request(
                "POST",
                path + "/" + route,
                {"reviewer": name, "grades": answer(*outcome)},
            )
            self.assertIn("200 OK", head)
        review = json.loads(self.request("GET", path + "/review")[1])
        self.assertEqual(review["agreed"], 0)
        case = review["open_disputes"][0]
        head, _ = self.request(
            "POST",
            path + "/adjudicate",
            {
                "case_id": case["case_id"],
                "dispute_id": case["dispute_id"],
                "reviewer": "C",
                "choice": "second",
                "reason": "The case record confirms it.",
                "minutes": 1,
            },
        )
        self.assertIn("200 OK", head)
        self.assertFalse(
            json.loads(self.request("GET", path + "/review")[1])["open_disputes"]
        )
        self.assertIn(b"item-1", self.request("GET", path + "/report.html")[1])

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
