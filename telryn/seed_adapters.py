"""Sandbox ticket submission and read-only register adapters for the release gate."""

import csv
import hashlib
import io
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .contracts import InputError, fingerprint, read_json, timestamp
from .integrations_http import Client, setting
from .seed import REVISIONS

MAX_EXPORT = 32 * 1024 * 1024


def _manifest(setting, request):
    value = os.getenv(setting)
    if not value:
        raise InputError(f"set {setting} to the approved test deployment manifest")
    path = Path(value).resolve(strict=True)
    doc = read_json(path, "test deployment manifest")
    if (
        doc.get("environment") != "test"
        or doc.get("scope") != request["scope"]
        or request.get("environment") != "test"
        or set(doc["scope"]) != set(REVISIONS)
    ):
        raise InputError("test deployment manifest differs from the selected suite")
    return path.parent, doc


def _csv_register(request):
    folder, doc = _manifest("TELRYN_SEED_REGISTER_MANIFEST", request)
    identifying = request["operation"] == "identify" and request["role"] == "register"
    if not identifying and request["operation"] != "read_register":
        raise InputError("CSV register is read-only")
    path = (folder / doc["file"]).resolve(strict=True)
    if (
        not path.is_relative_to(folder)
        or not path.is_file()
        or path.stat().st_size > MAX_EXPORT
    ):
        raise InputError("use a local register snapshot up to 32 MiB")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != doc["sha256"]:
        raise InputError("register snapshot differs from the manifest digest")
    fields = ["marker", "conversation_id", "entry_id", "recorded_at"]
    rows = csv.DictReader(
        io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True
    )
    if rows.fieldnames != fields:
        raise InputError("invalid test register CSV header")
    entries = []
    as_of = timestamp(doc["as_of"], "register observation time")
    through = doc["complete_through"]
    if through is not None and timestamp(through, "register coverage") > as_of:
        raise InputError("register coverage extends past its observation time")
    markers = set() if identifying else set(request["markers"])
    for row in rows:
        if None in row or not all(row.values()):
            raise InputError("invalid test register CSV row")
        if row["marker"] in markers:
            entries.append(row)
    if identifying:
        return {"role": "register"}
    return {
        "source_ref": doc["source_ref"],
        "as_of": doc["as_of"],
        "complete_through": doc["complete_through"],
        "entries": entries,
    }


def _zendesk(request):
    folder, doc = _manifest("TELRYN_SEED_ZENDESK_MANIFEST", request)
    client = Client("zendesk")
    scope = doc["scope"]
    integration = doc["integration"]
    if (
        scope["workspace"] != client.scope
        or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", integration)
        or type(doc.get("account_id")) is not int
        or type(doc.get("test_requester_id")) is not int
    ):
        raise InputError("invalid pinned Zendesk sandbox identity")
    # A deployment-owned ZIS configuration exists only in the approved sandbox.
    # The entitlement to create sandboxes on a production account is insufficient.
    deployment = Client("zendesk")
    deployment.headers["Authorization"] = "Bearer " + setting("TELRYN_SEED_ZIS_TOKEN")
    inventory, _ = deployment.request(
        f"/api/services/zis/integrations/{integration}/configs?filter%5Bscope%5D=telryn-release"
    )
    configs = inventory["configs"]
    if len(configs) != 1 or inventory["meta"]["has_more"] is not False:
        raise InputError("expected one pinned sandbox configuration")
    config = configs[0]
    if (
        config.get("zendesk_account_id") != doc["account_id"]
        or config.get("integration") != integration
        or config.get("scope") != "telryn-release"
        or config.get("config")
        != {
            "environment": "test",
            "scope": scope,
            "test_requester_id": doc["test_requester_id"],
        }
    ):
        raise InputError("remote Zendesk test deployment or revision does not match")
    if request["operation"] == "identify" and request["role"] == "agent":
        return {"role": "agent"}
    if request["operation"] != "submit":
        raise InputError("Zendesk sandbox adapter serves synthetic submissions only")
    marker = request["marker"]
    if not re.fullmatch("[0-9a-f]{64}", marker):
        raise InputError("invalid seed marker")
    # Reserve each marker before remote I/O. A lost response stays pending until
    # the sandbox operator reconciles it. Never create a second ticket on retry.
    with closing(sqlite3.connect(folder / "zendesk-seed-receipts.sqlite3")) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, suite TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS sends (marker TEXT PRIMARY KEY, ticket TEXT, received TEXT)"
            )
            db.execute(
                "INSERT OR IGNORE INTO runs VALUES (?, ?)",
                (request["run_id"], request["suite_digest"]),
            )
            if (
                db.execute(
                    "SELECT suite FROM runs WHERE id=?", (request["run_id"],)
                ).fetchone()[0]
                != request["suite_digest"]
            ):
                raise InputError("run ID already bound to another suite")
            row = db.execute(
                "SELECT ticket,received FROM sends WHERE marker=?", (marker,)
            ).fetchone()
            if row:
                if row[0] is None:
                    raise InputError(
                        "prior sandbox submission pending reconciliation; inspect its external_id"
                    )
                return {
                    "marker": marker,
                    "conversation_id": row[0],
                    "received_at": row[1],
                }
            db.execute("INSERT INTO sends(marker) VALUES (?)", (marker,))
        client.headers["Idempotency-Key"] = marker
        created, _ = client.request(
            "/api/v2/tickets.json",
            method="POST",
            body={
                "ticket": {
                    "requester_id": doc["test_requester_id"],
                    "submitter_id": doc["test_requester_id"],
                    "external_id": marker,
                    "subject": "Synthetic QA release check",
                    "comment": {"body": request["phrase"], "public": True},
                    "tags": ["telryn_seed"],
                }
            },
        )
        ticket = created["ticket"]
        if (
            ticket.get("external_id") != marker
            or type(ticket.get("id")) is not int
            or ticket.get("requester_id") != doc["test_requester_id"]
            or ticket.get("submitter_id") != doc["test_requester_id"]
        ):
            raise InputError(
                "Zendesk response does not identify the submitted synthetic ticket"
            )
        timestamp(ticket["created_at"], "ticket creation timestamp")
        with db:
            db.execute(
                "UPDATE sends SET ticket=?,received=? WHERE marker=?",
                (str(ticket["id"]), ticket["created_at"], marker),
            )
        return {
            "marker": marker,
            "conversation_id": str(ticket["id"]),
            "received_at": ticket["created_at"],
        }


def exchange(adapter, request):
    try:
        value = (
            _csv_register(request) if adapter == "csv-register" else _zendesk(request)
        )
    except (KeyError, TypeError, UnicodeError, csv.Error, sqlite3.Error) as exc:
        raise InputError(
            "invalid test adapter configuration, receipt or source"
        ) from exc
    return {
        "schema": 1,
        "environment": "test",
        "scope": request["scope"],
        "request_digest": fingerprint(request),
        **value,
    }
