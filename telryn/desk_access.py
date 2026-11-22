"""Private request audit for the hosted desk."""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from . import audit_trust
from .artifacts import sync_directory


READ_ROUTES = {
    "": "desk",
    "desk.js": "script",
    "rubrics": "rubrics",
    "audits": "audit-list",
    "grades.csv": "grading-packet",
    "outstanding.csv": "outstanding-packet",
    "report.html": "weekly-report",
    "manifest.json": "records-manifest",
    "review": "review-worklist",
}
WRITE_ROUTES = {
    "plan": "plan-preview",
    "audits": "issue-audit",
    "records": "attach-records",
    "links": "link-suggestions",
    "policy-preview": "policy-preview",
    "policy-review": "policy-review",
    "bank": "bank-statement",
    "grades": "primary-grades",
    "second-review": "second-review",
    "adjudicate": "adjudication",
}


def route_name(method, parts):
    if len(parts) == 1:
        if method == "GET" and parts[0] in ("", "desk.js", "rubrics", "audits"):
            return READ_ROUTES[parts[0]]
        if method == "POST" and parts[0] in ("plan", "audits"):
            return WRITE_ROUTES[parts[0]]
    if len(parts) == 2:
        if method == "GET" and parts[1] not in ("", "desk.js", "rubrics", "audits"):
            return READ_ROUTES.get(parts[1], "unknown")
        if method == "POST" and parts[1] not in ("plan", "audits"):
            return WRITE_ROUTES.get(parts[1], "unknown")
    return "unknown"


class AccessError(RuntimeError):
    """A private response cannot be logged to the trust store."""


def record(
    subject,
    role,
    route,
    method,
    status,
    case_ids=(),
    *,
    publication=None,
    revision=None,
):
    try:
        root = audit_trust._root()
        path = root / "desk-access.sqlite3"
        try:
            fd = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            pass
        else:
            os.close(fd)
            sync_directory(root)
        audit_trust._private(path)
        with closing(sqlite3.connect(path, timeout=5)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """CREATE TABLE IF NOT EXISTS access_events (
                       id INTEGER PRIMARY KEY, utc TEXT NOT NULL, subject TEXT NOT NULL,
                       role TEXT NOT NULL, route TEXT NOT NULL, method TEXT NOT NULL,
                       status INTEGER, publication_id TEXT, revision INTEGER)"""
                )
                columns = {
                    column[1]
                    for column in db.execute("PRAGMA table_info(access_events)")
                }
                if "publication_id" not in columns:
                    db.execute(
                        "ALTER TABLE access_events ADD COLUMN publication_id TEXT"
                    )
                if "revision" not in columns:
                    db.execute("ALTER TABLE access_events ADD COLUMN revision INTEGER")
                db.execute(
                    """CREATE TABLE IF NOT EXISTS access_cases (
                       event_id INTEGER NOT NULL REFERENCES access_events(id),
                       case_id TEXT NOT NULL, PRIMARY KEY (event_id, case_id))"""
                )
                event = db.execute(
                    "INSERT INTO access_events(utc, subject, role, route, method, status, publication_id, revision) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        datetime.now(UTC).isoformat(timespec="microseconds"),
                        subject,
                        role,
                        route,
                        method,
                        status,
                        publication,
                        revision,
                    ),
                ).lastrowid
                db.executemany(
                    "INSERT INTO access_cases(event_id, case_id) VALUES (?,?)",
                    ((event, ident) for ident in sorted(set(case_ids))),
                )
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise AccessError("Hosted access log unavailable") from exc
