"""Local synthetic support agent and independently readable SQLite register."""

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path


def digest(value):
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def main():
    request = json.load(sys.stdin)
    revision = os.environ.get("TELRYN_DEMO_AGENT_REVISION", "demo-prompt-v2")
    if revision not in {"demo-prompt-v1", "demo-prompt-v2"}:
        raise ValueError("unknown simulated agent revision")
    scope = {
        "workspace": "local-synthetic-lab",
        "agent_revision": revision,
        "policy_revision": "demo-complaints-v1",
        "handoff_revision": "demo-register-v1",
    }
    response = {
        "schema": 1,
        "environment": "test",
        "scope": scope,
        "request_digest": digest(request),
    }
    operation = request["operation"]
    path = Path(os.environ["TELRYN_SEED_DEMO_DB"]).resolve()
    if operation == "identify":
        response["role"] = request["role"]
    elif request["scope"] != scope or request["environment"] != "test":
        raise ValueError("wrong synthetic deployment")
    elif operation == "submit":
        with sqlite3.connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, suite TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS messages (marker TEXT PRIMARY KEY, conversation TEXT UNIQUE, received TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS register (id TEXT PRIMARY KEY, marker TEXT, conversation TEXT, recorded TEXT)"
            )
            db.execute(
                "INSERT OR IGNORE INTO runs VALUES (?, ?)",
                (request["run_id"], request["suite_digest"]),
            )
            (saved,) = db.execute(
                "SELECT suite FROM runs WHERE id=?", (request["run_id"],)
            ).fetchone()
            if saved != request["suite_digest"]:
                raise ValueError("a run ID was reused for another suite")
            marker = request["marker"]
            existing = db.execute(
                "SELECT conversation, received FROM messages WHERE marker=?", (marker,)
            ).fetchone()
            if existing:
                conversation, received = existing
            else:
                conversation = "conversation-" + marker
                received = "2026-10-05T09:00:00Z"
                db.execute(
                    "INSERT INTO messages VALUES (?, ?, ?)",
                    (marker, conversation, received),
                )
                phrases = ["third time", "ombudsman", "still unhappy"]
                if revision == "demo-prompt-v2":
                    phrases.append("can't read the letters")
                if any(phrase in request["phrase"].casefold() for phrase in phrases):
                    db.execute(
                        "INSERT INTO register VALUES (?, ?, ?, ?)",
                        (
                            "record-" + marker,
                            marker,
                            conversation,
                            "2026-10-05T09:15:00Z",
                        ),
                    )
        response.update(
            marker=marker, conversation_id=conversation, received_at=received
        )
    elif operation == "read_register":
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            marks = request["markers"]
            placeholders = ",".join("?" for _ in marks)
            rows = db.execute(
                "SELECT id, marker, conversation, recorded FROM register "
                f"WHERE marker IN ({placeholders}) ORDER BY id",
                marks,
            )
            response.update(
                source_ref="synthetic-register-snapshot",
                as_of="2026-10-05T12:00:00Z",
                complete_through="2026-10-05T12:00:00Z",
                entries=[
                    dict(
                        zip(
                            ("entry_id", "marker", "conversation_id", "recorded_at"),
                            row,
                            strict=True,
                        )
                    )
                    for row in rows
                ],
            )
    else:
        raise ValueError("unsupported synthetic operation")
    print(json.dumps(response))


if __name__ == "__main__":
    main()
