"""Vendor wire contracts and the path from an HTTPS export to a native handoff."""

import csv
import http.client
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from telryn.__main__ import main
from telryn.weekly_report import read_state, start

DAY = "2025-01-07T12:00:00Z"
TICKET = {
    "id": 90,
    "created_at": DAY,
    "updated_at": DAY,
    "status": "closed",
    "tags": ["risk"],
}
IDENTITY = {"id": "500", "app": {"id_code": "demo"}}


class Wire:
    """Use urllib's real HTTP encoder, response parser, and redirect handler offline."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def connect(self, connection):
        if not self.replies:
            raise AssertionError("unexpected outbound request")
        status, data, headers = self.replies.pop(0)
        raw = json.dumps(data).encode()
        lines = [
            f"HTTP/1.1 {status} Test",
            f"Content-Length: {len(raw)}",
            "Content-Type: application/json",
        ]
        lines.extend(f"{k}: {v}" for k, v in headers.items())
        reply = "\r\n".join(lines).encode() + b"\r\n\r\n" + raw
        sent = bytearray()
        self.requests.append((connection.host, sent))

        class Socket:
            def sendall(self, data):
                sent.extend(data)

            def makefile(self, *args, **kwargs):
                return io.BytesIO(reply)

            def close(self):
                pass

        connection.sock = Socket()


def response(data, headers=None, code=200):
    return code, data, headers or {}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        config = {
            "TELRYN_TRUST_DIR": str(self.root / "audit-trust"),
            "TELRYN_ZENDESK_SUBDOMAIN": "demo",
            "TELRYN_INTERCOM_WORKSPACE": "demo",
            "TELRYN_FRESHDESK_SUBDOMAIN": "demo",
            "TELRYN_SALESFORCE_HOST": "demo.my.salesforce.com",
            "TELRYN_DECAGON_WORKSPACE": "demo",
            "TELRYN_ZENDESK_QA_SUBDOMAIN": "demo",
        }
        for provider in (
            "FRESHDESK",
            "INTERCOM",
            "SALESFORCE",
            "ZENDESK",
            "DECAGON",
            "ZENDESK_QA",
        ):
            config[f"TELRYN_{provider}_TOKEN"] = "example-only"
        stack = patch.dict(os.environ, config, clear=True)
        stack.start()
        self.addCleanup(stack.stop)

    def command(self, args, replies=(), expected=0):
        wire = Wire(replies)
        out = io.StringIO()
        with (
            patch.object(
                http.client.HTTPSConnection,
                "connect",
                lambda connection: wire.connect(connection),
            ),
            patch("urllib.request.getproxies", return_value={}),
            patch("time.sleep"),
            redirect_stdout(out),
            redirect_stderr(out),
        ):
            code = main(["integrations", *map(str, args)])
        self.assertEqual(code, expected, out.getvalue())
        self.assertFalse(wire.replies)
        self.assertNotIn("example-only", out.getvalue())
        return [(host, bytes(data)) for host, data in wire.requests]

    def pull(self, vendor, replies, *flags, expected=0):
        return self.command(
            [
                "pull",
                vendor,
                "--basis",
                "created",
                "--since",
                "2025-01-06",
                "--until",
                "2025-01-13",
                "--out",
                self.root / "population.csv",
                *flags,
            ],
            replies,
            expected,
        )

    def rows(self, name="population.csv"):
        with (self.root / name).open(newline="") as stream:
            return list(csv.DictReader(stream))

    def report(self):
        (self.root / "reviewed.csv").write_text("case_id\n")
        plan = self.root / "plan.toml"
        plan.write_text(
            'schema=1\nweek="2025-01-06"\nrubric="risk@1"\nrubric_question="Is this serious?"\nscreen_version="test-tag"\npopulation="population.csv"\nreviewed="reviewed.csv"\nflagged_sample=3\nunflagged_sample=3\n'
        )
        return start(plan, self.root / "audit")

    def test_zendesk_export_weekly_report_and_handoff(self):
        first = {
            "results": [TICKET],
            "meta": {"has_more": True, "after_cursor": "next+/="},
        }
        second = {"results": [dict(TICKET, id=92)], "meta": {"has_more": False}}
        wire = self.pull(
            "zendesk",
            [response(first), response(second)],
            "--flag-tag",
            "risk",
            "--expected-count",
            "2",
        )
        self.assertTrue(all(host == "demo.zendesk.com" for host, _ in wire))
        self.assertIn(b"page%5Bafter%5D=next%2B%2F%3D", wire[-1][1])
        self.assertEqual(
            [r["case_id"] for r in self.rows()], ["zendesk:demo:90", "zendesk:demo:92"]
        )
        self.assertEqual((self.root / "population.csv").stat().st_mode & 0o777, 0o600)
        report = self.report()
        self.assertEqual(len(read_state(report)["cases"]), 2)
        cmd = ["handoff", "zendesk", report, "--tag", "telryn_week"]
        self.assertEqual(self.command(cmd), [])
        already = dict(
            TICKET, id=92, updated_at="2025-01-15T00:00:00Z", tags=["telryn_week"]
        )
        responses = [
            response({"ticket": TICKET}),
            response({"ticket": already}),
            response({"job_status": {"id": "job-42"}}),
            response({"job_status": {"status": "working"}}),
            response(
                {
                    "job_status": {
                        "status": "completed",
                        "results": [{"id": 90, "success": True}],
                    }
                }
            ),
        ]
        responses.append(response({"ticket": dict(already, id=90)}))
        wire = self.command([*cmd, "--apply"], responses)
        batch = json.loads(wire[2][1].split(b"\r\n\r\n", 1)[1])
        self.assertEqual(
            batch,
            {
                "tickets": [
                    {
                        "id": 90,
                        "safe_update": True,
                        "updated_stamp": DAY,
                        "additional_tags": ["telryn_week"],
                    }
                ]
            },
        )
        self.command(
            [*cmd, "--apply"],
            [response({"ticket": dict(already, id=90)}), response({"ticket": already})],
        )
        self.command(
            [
                "maestroqa",
                report,
                "--source",
                "zendesk",
                "--workspace",
                "demo",
                "--out",
                self.root / "maestro.csv",
            ]
        )
        self.assertEqual(
            (self.root / "maestro.csv").read_text().splitlines(), ["90", "92"]
        )
        self.command(
            [
                "maestroqa",
                report,
                "--source",
                "zendesk",
                "--workspace",
                "other",
                "--out",
                self.root / "wrong.csv",
            ],
            expected=2,
        )

    def test_no_partial_csv_on_changed_count_and_no_cross_host_redirect(self):
        self.pull(
            "zendesk",
            [
                response(
                    {
                        "results": [TICKET],
                        "meta": {"has_more": True, "after_cursor": "abc"},
                    }
                ),
                response({}, {"Location": "https://untrusted.invalid/collect"}, 302),
            ],
            expected=2,
        )
        self.assertFalse((self.root / "population.csv").exists())
        self.pull(
            "zendesk",
            [response({"results": [TICKET], "meta": {"has_more": False}})],
            "--expected-count",
            "2",
            expected=2,
        )
        self.assertFalse((self.root / "population.csv").exists())

    def test_zendesk_qa_matches_only_scorecard_in_cohort_before_cutoff(self):
        self.pull(
            "zendesk", [response({"results": [TICKET], "meta": {"has_more": False}})]
        )
        review = {"scorecard": {"id": "c"}, "created": DAY}
        record = {
            "externalId": "90",
            "externalUrl": "https://demo.zendesk.com/agent/tickets/90",
            "workspaceId": "12",
            "reviews": [review],
        }
        cmd = [
            "reviewed-zendesk",
            self.root / "population.csv",
            "--workspace",
            "12",
            "--scorecard",
            "c",
            "--since",
            "2025-01-06",
            "--until",
            "2025-01-13",
            "--out",
            self.root / "reviewed.csv",
        ]
        outside = dict(
            record,
            externalId="92",
            externalUrl="https://demo.zendesk.com/agent/tickets/92",
        )
        wire = self.command(
            cmd,
            [
                response(
                    {
                        "conversations": [outside],
                        "pagination": {"page": 1, "total": "2"},
                    }
                ),
                response(
                    {"conversations": [record], "pagination": {"page": 2, "total": "2"}}
                ),
            ],
        )
        self.assertIn(b"/qa/api/export/workspace/12/reviews?", wire[0][1])
        self.assertEqual(self.rows("reviewed.csv"), [{"case_id": "zendesk:demo:90"}])
        (self.root / "reviewed.csv").unlink()
        changed = dict(
            record,
            reviews=[
                dict(review, created="2025-01-13T00:00:00Z"),
                dict(review, scorecard={"id": "other"}),
            ],
        )
        self.command(
            cmd,
            [
                response(
                    {"conversations": [changed], "pagination": {"page": 1, "total": 1}}
                )
            ],
        )
        self.assertEqual(self.rows("reviewed.csv"), [])
        (self.root / "reviewed.csv").unlink()
        foreign = dict(
            outside, externalUrl="https://other.zendesk.com/agent/tickets/92"
        )
        self.command(
            cmd,
            [
                response(
                    {"conversations": [foreign], "pagination": {"page": i, "total": 2}}
                )
                for i in (1, 2)
            ],
            expected=2,
        )
        self.assertFalse((self.root / "reviewed.csv").exists())

    def test_intercom_fin_workspace_and_handoff(self):
        ticket = {
            "id": "415",
            "state": "closed",
            "created_at": 1736251200,
            "updated_at": 1736251200,
            "ai_agent_participated": True,
            "tags": {"tags": [{"id": "77", "name": "risk"}]},
        }
        page = {"conversations": [ticket], "pages": {"next": None}, "total_count": 1}
        wire = self.pull(
            "intercom",
            [response(IDENTITY), response(page)],
            "--flag-tag",
            "risk",
            "--fin-only",
        )
        self.assertIn(b'"field": "ai_agent_participated"', wire[-1][1])
        self.assertEqual(self.rows()[0]["screen_flag"], "true")
        report = self.report()
        cmd = ["handoff", "intercom", report, "--tag", "88", "--apply"]
        tagged = dict(ticket, tags={"tags": [{"id": "88", "name": "weekly QA"}]})
        wire = self.command(
            cmd,
            [
                response(IDENTITY),
                response(ticket),
                response(ticket),
                response({"id": "88", "type": "tag"}),
                response(tagged),
            ],
        )
        self.assertIn(b"POST /conversations/415/tags ", wire[-2][1])
        self.assertIn(b"GET /conversations/415 ", wire[-1][1])
        self.command(cmd, [response({"app": {"id_code": "other"}})], expected=2)
        self.command(
            cmd,
            [
                response(IDENTITY),
                response(ticket),
                response(dict(ticket, updated_at=1736251210)),
            ],
            expected=2,
        )
        self.command(cmd, [response(IDENTITY), response(ticket), response(tagged)])
        self.command(
            cmd,
            [
                response(IDENTITY),
                response(ticket),
                response(ticket),
                response({"id": "88"}),
                response(ticket),
            ],
            expected=2,
        )

    def test_salesforce_cursor_and_rejection(self):
        item = {
            "Id": "500SF123",
            "CreatedDate": DAY,
            "LastModifiedDate": DAY,
            "IsClosed": True,
        }
        first = {
            "records": [item],
            "done": False,
            "totalSize": 2,
            "nextRecordsUrl": "/services/data/v61.0/query/next-1",
        }
        second = {"records": [dict(item, Id="500SF456")], "done": True, "totalSize": 2}
        wire = self.pull("salesforce", [response(first), response(second)])
        self.assertEqual(len(self.rows()), 2)
        self.assertIn(b"GET /services/data/v61.0/query/next-1 ", wire[1][1])
        (self.root / "population.csv").unlink()
        self.pull(
            "salesforce",
            [response(dict(first, nextRecordsUrl="https://untrusted.invalid/cursor"))],
            expected=2,
        )
        self.assertFalse((self.root / "population.csv").exists())

    def test_freshdesk_filters_paginates_and_limits_columns(self):
        item = dict(
            TICKET, status=5, requester_id="private", description="private transcript"
        )
        link = '<https://demo.freshdesk.com/api/v2/tickets?page=2>; rel="next"'
        wire = self.pull(
            "freshdesk",
            [response([item], {"Link": link}), response([dict(item, id=91, status=4)])],
            "--flag-tag",
            "risk",
        )
        self.assertIn(b"Authorization: Basic ", wire[0][1])
        self.assertEqual(len(self.rows()), 1)
        self.assertNotIn("private", (self.root / "population.csv").read_text())
        self.assertTrue(all(host == "demo.freshdesk.com" for host, _ in wire))

    def test_decagon_cursor_alias_and_tenant_closed_tag(self):
        item = {
            "conversation_id": "c1",
            "created_at": DAY,
            "updated_at": DAY,
            "messages": [{"text": "private"}],
            "tags": [{"name": "complete"}],
        }
        wire = self.pull(
            "decagon",
            [
                response(
                    {
                        "conversations": [item],
                        "next_page_cursor": None,
                        "next_cursor": "pg+2",
                    }
                ),
                response(
                    {
                        "conversations": [dict(item, conversation_id="c2", tags=[])],
                        "next_cursor": None,
                    }
                ),
            ],
            "--closed-tag",
            "complete",
            "--expected-count",
            "1",
        )
        query = parse_qs(urlsplit(wire[-1][1].split()[1].decode()).query)
        self.assertEqual(query["cursor"], ["pg+2"])
        self.assertEqual(self.rows()[0]["case_id"], "decagon:demo:c1")
        self.assertEqual(len(self.rows()), 1)

    def test_decagon_requires_count_and_stops_on_ambiguous_or_truncated_pages(self):
        item = {
            "conversation_id": "c1",
            "created_at": DAY,
            "updated_at": DAY,
            "tags": [{"name": "complete"}],
        }
        flags = ("--closed-tag", "complete", "--expected-count", "2")
        self.pull("decagon", [], "--closed-tag", "complete", expected=2)
        for extras in (
            {"next_cursor": "abc", "next_page_cursor": "different"},
            {"has_more": True},
            {"unrecognizedCursor": "next"},
        ):
            with self.subTest(fields=extras):
                self.pull(
                    "decagon",
                    [response({"conversations": [item], **extras})],
                    *flags,
                    expected=2,
                )
                self.assertFalse((self.root / "population.csv").exists())

    def test_submicrosecond_revisions_remain_distinct(self):
        item = dict(TICKET, updated_at="2025-01-07T13:00:00.000000050+01:00")
        self.pull(
            "zendesk", [response({"results": [item], "meta": {"has_more": False}})]
        )
        self.assertEqual(self.rows()[0]["revision"], "2025-01-07T12:00:00.00000005Z")

    def test_agent_mapping_drops_content_preserves_complete_flag_and_rejects_overwrite(
        self,
    ):
        source = self.root / "source.csv"
        source.write_text(
            "ID,Rev,URI,Flag,Messages\n888,v2,archive:001,true,private transcript\n"
        )
        args = [
            "import-agent",
            "sierra",
            source,
            "--workspace",
            "demo",
            "--id-column",
            "ID",
            "--revision-column",
            "Rev",
            "--evidence-column",
            "URI",
            "--flag-column",
            "Flag",
            "--out",
            self.root / "population.csv",
        ]
        self.command(args)
        self.command(args, expected=2)
        self.assertEqual(
            self.rows(),
            [
                {
                    "case_id": "sierra:demo:888",
                    "revision": "v2",
                    "evidence_ref": "archive:001",
                    "screen_flag": "true",
                }
            ],
        )
        (self.root / "population.csv").unlink()
        source.write_text(
            "ID,Rev,URI,Flag,Messages\n888,v2,archive:001,,private transcript\n"
        )
        self.command(args, expected=2)
        self.assertFalse((self.root / "population.csv").exists())

    def test_malformed_and_retry(self):
        self.pull(
            "zendesk",
            [
                response({}, {"Retry-After": "0"}, 429),
                response({"results": [], "meta": {"has_more": False}}),
            ],
        )
        (self.root / "population.csv").unlink()
        broken = {
            "results": [TICKET],
            "meta": {"has_more": True, "after_cursor": "same"},
        }
        self.pull(
            "zendesk",
            [response(broken), response(dict(broken, results=[dict(TICKET, id=91)]))],
            expected=2,
        )
        self.assertFalse((self.root / "population.csv").exists())
        with patch.dict(os.environ, {"TELRYN_ZENDESK_SUBDOMAIN": "demo.evil.invalid"}):
            self.pull("zendesk", [], expected=2)
