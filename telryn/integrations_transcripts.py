"""Fetch readable message text for the exact case revisions in a population."""

import csv
import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .contracts import InputError, canonical
from .integrations_coverage import FIELDS, companion, projection, publish_with_receipt
from .integrations_http import query
from .integrations_sources import (
    boolean,
    cursor_next,
    instant,
    object_at,
    records,
    source_revision,
    time_key,
    vendor_id,
    zendesk_pages,
)


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li"):
            self.parts.append("\n")


def check_revision(row, key, expected, *, crm=False):
    if (
        vendor_id(object_at(row, "Id" if crm else "id")) != key
        or source_revision(object_at(row, "LastModifiedDate" if crm else "updated_at"))
        != expected
    ):
        raise InputError(
            "source record differs from the population revision; recover the original snapshot"
        )


def _message(role, value, *, html=False):
    if not isinstance(value, str) or not value.strip():
        raise InputError("missing message text; use an archived text transcript")
    if html:
        parser = _PlainText()
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts).strip()
        if not value:
            raise InputError("no readable message content")
    return {"role": role, "text": value}


def _zendesk(client, key, revision):
    path = f"/api/v2/tickets/{key}.json"

    def ticket():
        data, _ = client.request(path)
        row = object_at(data, "ticket")
        check_revision(row, key, revision)
        return row

    requester = vendor_id(object_at(ticket(), "requester_id"))
    comments = []
    seen = set()
    for comment in zendesk_pages(
        client, f"/api/v2/tickets/{key}/comments.json", "comments"
    ):
        ident = vendor_id(object_at(comment, "id"))
        if ident in seen or time_key(object_at(comment, "created_at")) > time_key(
            revision
        ):
            raise InputError("Zendesk comment sequence differs from the frozen case")
        seen.add(ident)
        author = vendor_id(object_at(comment, "author_id"))
        comments.append(
            (
                author,
                time_key(comment["created_at"]),
                comment.get("plain_body") or object_at(comment, "body"),
            )
        )
    # A ticket can have many end-user participants. Resolve roles instead of assuming
    # every non-requester is staff. A changed directory entry cannot prove its former role.
    others = sorted({author for author, _, _ in comments} - {requester})
    directory = {}
    for i in range(0, len(others), 100):
        batch = others[i : i + 100]
        data, _ = client.request(
            query("/api/v2/users/show_many.json", ids=",".join(batch))
        )
        for user in records(data, "users"):
            author = vendor_id(object_at(user, "id"))
            if author in directory or author not in batch:
                raise InputError("unexpected Zendesk comment author lookup result")
            directory[author] = user
    messages = []
    for author, posted, body in comments:
        role = "customer" if author == requester else "unknown"
        user = directory.get(author)
        if user and user.get("updated_at") and time_key(user["updated_at"]) <= posted:
            role = {"agent": "agent", "admin": "agent", "end-user": "customer"}.get(
                user.get("role"), "unknown"
            )
        messages.append(_message(role, body))
    ticket()
    return messages


def _freshdesk(client, key, revision):
    def ticket():
        row, _ = client.request(f"/api/v2/tickets/{key}")
        check_revision(row, key, revision)
        return row

    row = ticket()
    requester = row.get("requester_id")
    responder = row.get("responder_id")
    messages = [
        _message(
            "unknown",
            row.get("description_text") or object_at(row, "description"),
            html=not row.get("description_text"),
        )
    ]
    path = f"/api/v2/tickets/{key}/conversations"
    seen = set()
    for page in range(1, 100001):
        rows, headers = client.request(query(path, page=page, per_page=100))
        for item in records(rows, None):
            ident = vendor_id(object_at(item, "id"))
            if (
                vendor_id(object_at(item, "ticket_id")) != key
                or ident in seen
                or time_key(object_at(item, "updated_at")) > time_key(revision)
                or (
                    item.get("last_edited_at") is not None
                    and time_key(item["last_edited_at"]) > time_key(revision)
                )
            ):
                raise InputError("Freshdesk conversation differs from the frozen case")
            seen.add(ident)
            author = item.get("user_id")
            role = "unknown"
            if type(author) is int and requester != responder:
                if author == requester:
                    role = "customer"
                elif author == responder:
                    role = "agent"
            messages.append(
                _message(
                    role,
                    item.get("body_text") or object_at(item, "body"),
                    html=not item.get("body_text"),
                )
            )
        link = headers.get("Link")
        if not link:
            break
        match = re.fullmatch(r'\s*<([^<>]+)>\s*;\s*rel="?next"?\s*', link, re.I)
        target = urlsplit(match[1]) if match else None
        if (
            not rows
            or target is None
            or target.scheme != "https"
            or target.netloc != client.host
            or target.path != path
            or parse_qs(target.query).get("page") != [str(page + 1)]
        ):
            raise InputError("invalid Freshdesk conversation pagination")
    else:
        raise InputError("too many conversation pages")
    ticket()
    return messages


def _intercom(client, key, revision):
    row, _ = client.request(f"/conversations/{key}?display_as=plaintext")
    check_revision(row, key, revision)
    parts = object_at(row, "conversation_parts")
    items = records(parts, "conversation_parts")
    # The pinned API silently caps unpaged results at 500 even when total_count is also 500.
    if object_at(parts, "total_count") != len(items) or len(items) >= 500:
        raise InputError(
            "Intercom may truncate this conversation; supply the complete archived transcript"
        )
    seen = set()
    for item in items:
        ident = vendor_id(object_at(item, "id"))
        if ident in seen or not time_key(object_at(item, "created_at")) <= time_key(
            object_at(item, "updated_at")
        ) <= time_key(revision):
            raise InputError("Intercom conversation part differs from the frozen case")
        seen.add(ident)
    messages = []
    for item in [object_at(row, "source"), *items]:
        if not isinstance(item, dict) or item.get("redacted") is True:
            raise InputError(
                "Intercom message text is unavailable for this case revision"
            )
        body = item.get("body")
        if not body:
            if item.get("part_type") in ("comment", "note") or item is row["source"]:
                raise InputError("missing Intercom text, use an authorized text export")
            continue
        kind = object_at(object_at(item, "author"), "type")
        role = {
            "admin": "agent",
            "bot": "agent",
            "user": "customer",
            "lead": "customer",
            "contact": "customer",
        }.get(kind, "unknown")
        messages.append(_message(role, body))
    return messages


def _crm_query(client, soql):
    endpoint = "/services/data/v61.0/query"
    path = query(endpoint, q=soql)
    seen = set()
    expected = None
    count = 0
    while True:
        data, _ = client.request(path)
        total = object_at(data, "totalSize")
        if (
            type(total) is not int
            or total < 0
            or (expected is not None and total != expected)
        ):
            raise InputError("Salesforce transcript query changed")
        expected = total
        items = records(data, "records")
        count += len(items)
        yield from items
        if boolean(object_at(data, "done")):
            if count != expected:
                raise InputError("partial Salesforce transcript query")
            return
        path = cursor_next(object_at(data, "nextRecordsUrl"), seen)
        if not items or not re.fullmatch(
            re.escape(endpoint) + r"/[A-Za-z0-9_-]+", path
        ):
            raise InputError("invalid Salesforce transcript cursor")


def _salesforce(client, key, revision):
    if not re.fullmatch(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?", key):
        raise InputError("invalid Salesforce Case ID")

    def parent():
        rows = list(
            _crm_query(
                client,
                f"SELECT Id,CreatedDate,LastModifiedDate,Description FROM Case WHERE Id='{key}'",
            )
        )
        if len(rows) != 1:
            raise InputError("Salesforce case no longer available")
        check_revision(rows[0], key, revision, crm=True)
        return rows[0]

    case = parent()
    turns = []
    if case.get("Description"):
        turns.append(
            (instant(case["CreatedDate"]), "", _message("unknown", case["Description"]))
        )
    for table, fields in (
        ("CaseComment", "CommentBody"),
        ("EmailMessage", "Incoming,Status,TextBody,HtmlBody"),
    ):
        seen = set()
        sent_or_received = " AND Status != '5'" if table == "EmailMessage" else ""
        for row in _crm_query(
            client,
            f"SELECT Id,ParentId,CreatedDate,LastModifiedDate,{fields} FROM {table} WHERE ParentId='{key}'{sent_or_received} ORDER BY CreatedDate,Id",
        ):
            ident = vendor_id(object_at(row, "Id"))
            if (
                ident in seen
                or object_at(row, "ParentId") != key
                or time_key(object_at(row, "LastModifiedDate")) > time_key(revision)
            ):
                raise InputError(
                    "Salesforce child message differs from the frozen case"
                )
            seen.add(ident)
            if table == "CaseComment":
                msg = _message("unknown", object_at(row, "CommentBody"))
            else:
                if object_at(row, "Status") not in ("0", "1", "2", "3", "4"):
                    raise InputError("unexpected Salesforce email delivery status")
                role = "unknown" if boolean(object_at(row, "Incoming")) else "agent"
                msg = _message(
                    role,
                    row.get("TextBody") or object_at(row, "HtmlBody"),
                    html=not row.get("TextBody"),
                )
            turns.append(
                (
                    instant(object_at(row, "CreatedDate")),
                    ident,
                    msg,
                )
            )
    parent()
    return [msg for _, _, msg in sorted(turns, key=lambda t: (t[0], t[1]))]


SOURCES = {
    "zendesk": _zendesk,
    "intercom": _intercom,
    "freshdesk": _freshdesk,
    "salesforce": _salesforce,
}


def register_arguments(actions):
    parser = actions.add_parser(
        "transcripts", help="fetch messages for the exact revisions in a population"
    )
    parser.add_argument("vendor", choices=tuple(SOURCES))
    parser.add_argument("population", type=Path)
    parser.add_argument("--out", required=True, type=Path)


def fetch(client, population, out):
    if out.suffix.lower() != ".jsonl":
        raise InputError("use a .jsonl transcript output")
    if client.provider == "intercom":
        client.intercom_identity()
    prefix = f"{client.provider}:{client.scope}:"
    seen = set()
    with population.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        dims = projection(reader.fieldnames, FIELDS)
        rows = list(reader)
    if any(None in r or any(not r[k] for k in (*FIELDS, *dims)) for r in rows):
        raise InputError("invalid population row")
    coverage = companion(population, rows)
    cutoffs = coverage.get("completed_at", {}) if coverage else {}
    for row in rows:
        if cutoffs and time_key(row["revision"]) != time_key(cutoffs[row["case_id"]]):
            raise InputError(
                "current snapshot differs from completion; supply archived JSONL with the case's completion as_of"
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() or out.is_symlink():
        raise InputError("transcript output already exists")
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        path = Path(tmp) / "transcripts"
        with path.open("x", encoding="utf-8") as target:
            for row in rows:
                case = row["case_id"]
                if (
                    not isinstance(case, str)
                    or not case.startswith(prefix)
                    or case in seen
                ):
                    raise InputError("foreign or duplicate transcript case")
                seen.add(case)
                key = vendor_id(case[len(prefix) :])
                messages = SOURCES[client.provider](client, key, row["revision"])
                if not messages:
                    raise InputError("empty transcript")
                record = {k: row[k] for k in (*FIELDS[:3], *dims)}
                if cutoffs:
                    record["as_of"] = cutoffs[case]
                target.write(canonical({**record, "messages": messages}) + "\n")
            target.flush()
            os.fsync(target.fileno())
        publish_with_receipt(out, path, coverage)
    return len(seen)
