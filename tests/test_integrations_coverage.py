"""Completion events, exclusions, and the source→transcript→weekly path."""

import csv
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telryn import integrations_coverage, integrations_transcripts, screen, weekly
from telryn.contracts import InputError
from tests import test_integrations as wire


class Coverage(unittest.TestCase):
    setUp = wire.IntegrationTests.setUp
    command = wire.IntegrationTests.command
    since = "2025-01-06"
    until = "2025-01-13"

    def pull(self, vendor, replies, *flags, expected=0):
        return self.command(
            [
                "pull",
                vendor,
                "--since",
                self.since,
                "--until",
                self.until,
                "--out",
                self.root / "population.csv",
                *flags,
            ],
            replies,
            expected=expected,
        )

    def read(self, file):
        with (self.root / file).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_zendesk_history_handles_prior_creation_reopening_and_admin_closure(self):
        ti = dict(
            wire.TICKET,
            created_at="2024-10-01T11:00:00Z",
            updated_at="2025-01-15T11:00:00Z",
            status="open",
        )
        others = [
            dict(ti, id=91, status="solved"),
            dict(ti, id=92, status="closed"),
            dict(ti, id=93),
        ]

        def audit(ticket, date, *pairs):
            return {
                "ticket_id": ticket,
                "created_at": date,
                "events": [
                    {
                        "type": "Change",
                        "field_name": "status",
                        "previous_value": a,
                        "value": b,
                    }
                    for a, b in pairs
                ],
            }

        def page(replies):
            return wire.response({"audits": replies, "meta": {"has_more": False}})

        replies = [
            wire.response({"results": [ti, *others], "meta": {"has_more": False}}),
            page(
                [
                    audit(90, wire.DAY, ("open", "solved")),
                    audit(90, "2025-01-08T12:00:00Z", ("solved", "open")),
                ]
            ),
            page([audit(91, wire.DAY, ("pending", "solved"))]),
            page(
                [
                    audit(92, "2025-01-01T11:00:00Z", ("open", "solved")),
                    audit(92, wire.DAY, ("solved", "closed")),
                ]
            ),
            page([audit(93, "2024-12-01T10:00:00Z", ("new", "open"))]),
        ]
        requests = self.pull("zendesk", replies)
        self.assertIn(b"updated", requests[0][1])
        self.assertIn(b"include_boundary_indicators=true", requests[1][1])
        self.assertNotIn(b"status%3Aclosed", requests[0][1])
        rows = self.read("population.csv")
        self.assertEqual(
            [r["case_id"] for r in rows], ["zendesk:demo:90", "zendesk:demo:91"]
        )
        ledger = integrations_coverage.load_coverage(
            self.root / "population.csv.coverage.json", rows, self.since
        )
        self.assertEqual(
            [
                ledger[k]
                for k in (
                    "still_open",
                    "eligible",
                    "open",
                    "unknown",
                    "outside",
                    "scanned",
                )
            ],
            [2, 2, 1, 0, 1, 4],
        )

    def test_freshdesk_older_creation_and_resolved_with_later_admin_close(self):
        old = dict(
            wire.TICKET,
            status=4,
            created_at="2024-11-01T00:00:00Z",
            updated_at="2025-02-01T00:00:00Z",
        )
        rows = [
            dict(old, id=k, status=status, stats=stats)
            for k, status, stats in [
                (1, 4, {"resolved_at": wire.DAY}),
                (2, 5, {"resolved_at": wire.DAY, "closed_at": "2025-01-14T00:00:00Z"}),
                (3, 5, {"closed_at": self.until + "T00:00:00Z"}),
                (4, 5, {}),
                (5, 2, {}),
            ]
        ]
        req = self.pull("freshdesk", [wire.response(rows)])
        self.assertIn(b"include=stats", req[0][1])
        self.assertEqual(len(self.read("population.csv")), 2)
        info = json.loads((self.root / "population.csv.coverage.json").read_text())
        self.assertEqual(
            (info["scanned"], info["unknown"], info["still_open"]), (5, 3, 1)
        )

    def test_salesforce_and_intercom_finish_times(self):
        sf = {
            "Id": "500SF123",
            "CreatedDate": "2024-01-01T00:00:00Z",
            "ClosedDate": wire.DAY,
            "IsClosed": True,
            "LastModifiedDate": wire.DAY,
            "Origin": "Email",
        }
        req = self.pull(
            "salesforce",
            [wire.response({"totalSize": 1, "done": True, "records": [sf]})],
            "--dimension",
            "channel=Origin",
        )
        self.assertIn(b"LastModifiedDate", req[0][1])
        self.assertIn(b"Origin", req[0][1])
        self.assertEqual(self.read("population.csv")[0]["channel"], "Email")
        self.assertEqual(len(self.read("population.csv")), 1)
        (self.root / "population.csv").unlink()
        (self.root / "population.csv.coverage.json").unlink()
        item = {
            "id": 415,
            "state": "closed",
            "created_at": 1704067200,
            "updated_at": 1736812800,
            "statistics": {"first_close_at": 1736251200, "last_close_at": 1736812800},
        }
        self.pull(
            "intercom",
            [
                wire.response(wire.IDENTITY),
                wire.response(
                    {"conversations": [item], "pages": {"next": None}, "total_count": 1}
                ),
            ],
        )
        self.assertEqual(len(self.read("population.csv")), 1)
        self.pull("decagon", [], expected=2)

    def test_event_export_through_native_transcript_model_and_grouped_audit(self):
        events = self.root / "events.csv"
        with events.open("w", newline="") as stream:
            w = csv.writer(stream)
            w.writerow(
                [
                    "case_id",
                    "revision",
                    "evidence_ref",
                    "screen_flag",
                    "state",
                    "completion_events",
                    "channel",
                ]
            )
            w.writerow(
                [
                    "zendesk:demo:90",
                    wire.DAY,
                    "archive-90",
                    "false",
                    "completed",
                    json.dumps([wire.DAY]),
                    "private-chat",
                ]
            )
            w.writerow(
                [
                    "zendesk:demo:91",
                    wire.DAY,
                    "archive-91",
                    "false",
                    "open",
                    "[]",
                    "phone",
                ]
            )
        self.command(
            [
                "cohort",
                events,
                "--scope",
                "Demo support ledger",
                "--since",
                self.since,
                "--until",
                self.until,
                "--out",
                self.root / "population.csv",
            ]
        )
        ticket = dict(wire.TICKET, requester_id=9)
        replies = [
            wire.response({"ticket": ticket}),
            wire.response(
                {
                    "comments": [
                        {
                            "id": 1,
                            "created_at": wire.DAY,
                            "author_id": 9,
                            "plain_body": "Please escalate my complaint.",
                        }
                    ],
                    "meta": {"has_more": False},
                }
            ),
            wire.response({"ticket": ticket}),
        ]
        source = self.root / "transcript.jsonl"
        self.command(
            ["transcripts", "zendesk", self.root / "population.csv", "--out", source],
            replies,
        )
        data = json.loads(source.read_text())
        self.assertEqual(data["channel"], "private-chat")
        self.assertEqual(data["as_of"], wire.DAY)

        def chat(path, data):
            self.assertNotIn("private-chat", str(data))
            return {
                "done": True,
                "done_reason": "stop",
                "model": "local",
                "message": {
                    "content": json.dumps(
                        {
                            "complaint": {"value": "yes", "passage": 1},
                            "complaint_logged": {"value": "unknown", "passage": 0},
                        }
                    )
                },
            }

        with (
            patch.object(screen, "_installed", return_value="a" * 64),
            patch.object(screen, "_api", side_effect=chat),
        ):
            rec = screen.screen(
                source, self.root / "screen", "local", "unlogged-complaint"
            )
        rows = self.read("screen/population.csv")
        self.assertEqual(rows[0]["channel"], "private-chat")
        self.assertEqual(rows[0]["screen_flag"], "true")
        self.assertEqual(
            rec["coverage_sha256"],
            screen.fingerprint(
                json.loads((self.root / "population.csv.coverage.json").read_text())
            ),
        )
        followup = screen.screen(
            self.root / "screen/signals.csv",
            self.root / "again",
            None,
            "unlogged-complaint",
        )
        self.assertEqual(rec["coverage_sha256"], followup["coverage_sha256"])
        self.assertEqual(self.read("again/population.csv"), rows)
        info = integrations_coverage.load_coverage(
            self.root / "screen/population.csv.coverage.json", rows, self.since
        )
        self.assertEqual(info["open"], 1)
        with self.assertRaises(InputError):
            integrations_coverage.load_coverage(
                self.root / "screen/population.csv.coverage.json",
                [dict(rows[0], channel="changed")],
                self.since,
            )
        (self.root / "reviewed.csv").write_text("case_id\n")
        plan = self.root / "week.toml"
        plan.write_text(f'''schema=1
week="{self.since}"
population="screen/population.csv"
reviewed="reviewed.csv"
rubric="demo"
rubric_question="Was a complaint missed?"
screen_version="{rec["screen_version"]}"
screen_receipt="screen/receipt.json"
group_by=["channel"]
flagged_sample=2
unflagged_sample=2
''')
        state = weekly.prepare(plan)
        self.assertEqual(state["coverage"]["open"], 1)
        self.assertEqual(state["cases"][0]["channel"], "private-chat")

    def test_later_referral_and_reopening_require_archived_completion_snapshot(self):
        events = self.root / "ledger.csv"
        reopened = "2025-01-15T11:20:00Z"
        with events.open("w", newline="") as stream:
            w = csv.writer(stream)
            w.writerow([*integrations_coverage.FIELDS, "state", "completion_events"])
            w.writerow(
                [
                    "zendesk:demo:1",
                    reopened,
                    "archive-through-first-closure",
                    "false",
                    "open",
                    json.dumps(["2025-01-08T18:20:00Z", wire.DAY]),
                ]
            )
        pop = self.root / "population.csv"
        self.command(
            [
                "cohort",
                events,
                "--scope",
                "Support history",
                "--since",
                self.since,
                "--until",
                self.until,
                "--out",
                pop,
            ]
        )
        source = self.root / "transcript.jsonl"
        self.command(["transcripts", "zendesk", pop, "--out", source], [], expected=2)
        self.assertFalse(source.exists())
        info = json.loads((self.root / "population.csv.coverage.json").read_text())
        self.assertEqual(info["completed_at"], {"zendesk:demo:1": wire.DAY})
        self.assertEqual(info["still_open"], 1)
        case = {
            k: v
            for k, v in self.read("population.csv")[0].items()
            if k != "screen_flag"
        }
        companion = self.root / "transcript.jsonl.coverage.json"
        companion.write_text(json.dumps(info))
        with patch.object(screen, "_installed", return_value="a" * 64):
            for extra in ({}, {"as_of": reopened}):
                source.write_text(
                    json.dumps(
                        {
                            **case,
                            **extra,
                            "messages": [
                                {
                                    "role": "agent",
                                    "text": "Reopened later and referred it.",
                                }
                            ],
                        }
                    )
                )
                with (
                    patch.object(screen, "_api", side_effect=AssertionError),
                    self.assertRaisesRegex(InputError, "as_of"),
                ):
                    screen.screen(
                        source, self.root / "screen", "local", "missed-escalation"
                    )
            source.write_text(
                json.dumps(
                    {
                        **case,
                        "as_of": wire.DAY,
                        "messages": [
                            {
                                "role": "agent",
                                "text": "Closing without making the required referral.",
                            }
                        ],
                    }
                )
            )
            signals = {
                "escalation_required": {"value": "yes", "passage": 1},
                "escalated": {"value": "no", "passage": 1},
            }

            def answer(path, data):
                self.assertNotIn("Reopened later", str(data))
                return {
                    "done": True,
                    "done_reason": "stop",
                    "model": "local",
                    "message": {"content": json.dumps(signals)},
                }

            with patch.object(screen, "_api", side_effect=answer):
                rec = screen.screen(
                    source, self.root / "screen", "local", "missed-escalation"
                )
            self.assertEqual(
                self.read("screen/population.csv")[0]["screen_flag"], "true"
            )
            self.assertEqual(rec["coverage_sha256"], screen.fingerprint(info))
            tampered = dict(
                info, completed_at={"zendesk:demo:1": "2025-01-08T18:20:00Z"}
            )
            companion.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(InputError, "receipt differs"):
                integrations_coverage.load_coverage(companion, [case], self.since)

    def test_zendesk_author_roles_use_directory_evidence_at_comment_time(self):
        case = dict(wire.TICKET, requester_id=9)
        comments = [
            {
                "id": i,
                "created_at": wire.DAY,
                "author_id": author,
                "plain_body": "comment",
            }
            for i, author in enumerate([9, 10, 11, 12, 13], 1)
        ]
        users = [
            {"id": 10, "role": "agent", "updated_at": wire.DAY},
            {"id": 11, "role": "admin", "updated_at": "2025-02-01T00:00:00Z"},
            {"id": 12, "role": "end-user", "updated_at": wire.DAY},
        ]
        replies = [
            {"ticket": case},
            {"comments": comments, "meta": {"has_more": False}},
            {"users": users},
            {"ticket": case},
        ]
        client = SimpleNamespace(request=lambda path: (replies.pop(0), {}))
        messages = integrations_transcripts._zendesk(client, "90", wire.DAY)
        self.assertEqual(
            [m["role"] for m in messages],
            ["customer", "agent", "unknown", "customer", "unknown"],
        )

    def test_native_transcripts_refuse_drift_and_truncation(self):
        pop = self.root / "population.csv"
        pop.write_text(
            "case_id,revision,evidence_ref,screen_flag\nintercom:demo:415,2025-01-07T12:00:00Z,archive,false\n"
        )
        row = {
            "id": "415",
            "updated_at": wire.DAY,
            "conversation_parts": {"total_count": 501, "conversation_parts": []},
        }
        cmd = ["transcripts", "intercom", pop, "--out", self.root / "transcript.jsonl"]
        for item in [row, dict(row, updated_at="2025-01-08T12:00:00Z")]:
            self.command(
                cmd, [wire.response(wire.IDENTITY), wire.response(item)], expected=2
            )
            self.assertFalse((self.root / "transcript.jsonl").exists())

    def test_salesforce_email_and_comment_and_freshdesk_description_and_replies(self):
        sf_id = "500000000000001"
        case = {
            "Id": sf_id,
            "Description": "Initial help request",
            "CreatedDate": "2024-01-01T00:00:00Z",
            "LastModifiedDate": wire.DAY,
        }
        common = {
            "ParentId": sf_id,
            "CreatedDate": wire.DAY,
            "LastModifiedDate": wire.DAY,
        }
        sources = [
            [case],
            [dict(common, Id="abc", CommentBody="Escalate this.")],
            [dict(common, Id="def", Incoming=False, TextBody="I escalated it.")],
            [case],
        ]
        client = SimpleNamespace(
            request=lambda path: (
                {"done": True, "totalSize": len(sources[0]), "records": sources.pop(0)},
                {},
            )
        )
        result = integrations_transcripts._salesforce(client, sf_id, wire.DAY)
        self.assertEqual(
            [r["text"] for r in result],
            ["Initial help request", "Escalate this.", "I escalated it."],
        )
        fd = dict(id=1, updated_at=wire.DAY, description_text="Initial complaint.")
        replies = [
            (fd, {}),
            (
                [
                    dict(
                        id=3,
                        updated_at=wire.DAY,
                        incoming=False,
                        body_text="I recorded it.",
                    )
                ],
                {},
            ),
            (fd, {}),
        ]
        client = SimpleNamespace(request=lambda path: replies.pop(0))
        self.assertEqual(
            integrations_transcripts._freshdesk(client, "1", wire.DAY)[1]["role"],
            "agent",
        )
