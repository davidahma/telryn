"""A standalone SQLite queue and evidence source for the synthetic tutorial."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SHIFTS = {
    "tuesday-am": ("R", "2026-04-07T09:00:00+00:00"),
    "saturday-am": ("S", "2026-04-11T09:00:00+00:00"),
}
CAPACITY = 100
RUBRIC = "order-discrepancy-v1"

SCHEMA = """
CREATE TABLE shift (
    name TEXT PRIMARY KEY, cutoff TEXT NOT NULL, capacity INTEGER NOT NULL,
    live_version TEXT NOT NULL, tie_break TEXT NOT NULL
);
CREATE TABLE membership (shift TEXT, request TEXT, PRIMARY KEY (shift, request));
CREATE TABLE revision (
    request TEXT, revision TEXT, known_at TEXT NOT NULL, effective_at TEXT NOT NULL,
    inputs TEXT NOT NULL, evidence TEXT NOT NULL, minutes TEXT,
    PRIMARY KEY (request, revision)
);
CREATE TABLE evidence (ref TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE live_selection (shift TEXT, slot INTEGER, request TEXT, PRIMARY KEY (shift, slot));
CREATE TABLE overrides (shift TEXT, sequence INTEGER, payload TEXT, PRIMARY KEY (shift, sequence));
CREATE TABLE mandatory (shift TEXT, request TEXT, PRIMARY KEY (shift, request));
CREATE TABLE archived_grade (
    request TEXT PRIMARY KEY, grade TEXT, evidence TEXT, evidence_at TEXT, shared_sources INTEGER
);
"""


def case_attributes(number):
    old_only = number == 73 or 82 <= number <= 100
    conflict = 101 <= number <= 120 or (number <= 100 and not old_only)
    refined = 101 <= number <= 108 or number <= 92
    meets = (
        number == 73
        or 82 <= number <= 90
        or 101 <= number <= 108
        or number in {113, 114, 118}
    )
    inputs = {
        "refund_cents": 13000 - 100 * number,
        "conflict_priority": (1000 if conflict else 0) - number,
        "refined_priority": (1000 if refined else 0) - number,
    }
    return inputs, meets


def seed(path: Path) -> None:
    if path.exists():
        raise ValueError(f"database already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(SCHEMA)
        for shift, (prefix, cutoff) in SHIFTS.items():
            connection.execute(
                "INSERT INTO shift VALUES (?, ?, ?, ?, ?)",
                (shift, cutoff, CAPACITY, "refund-amount-v1", "id-asc-v1"),
            )
            for number in range(1, 122):
                case = f"{prefix}-{number:03d}"
                inputs, meets = case_attributes(number)
                refs = [f"order/{case}@2", f"refund/{case}@1"]
                minutes = {
                    "lower": 4,
                    "upper": 4 if number % 4 == 0 else 5,
                    "basis": "observed" if number % 4 == 0 else "estimated",
                }
                connection.execute(
                    "INSERT INTO membership VALUES (?, ?)", (shift, case)
                )
                connection.execute(
                    "INSERT INTO revision VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        case,
                        "1",
                        cutoff,
                        cutoff,
                        json.dumps(inputs),
                        json.dumps(refs),
                        json.dumps(minutes),
                    ),
                )
                original = {"units_delivered": 2 if meets else 3}
                connection.execute(
                    "INSERT INTO evidence VALUES (?, ?)",
                    (refs[0], json.dumps(original)),
                )
                connection.execute(
                    "INSERT INTO evidence VALUES (?, ?)",
                    (refs[1], json.dumps({"units_requested": 3})),
                )
                if number in {41, 42}:
                    connection.execute(
                        "INSERT INTO mandatory VALUES (?, ?)", (shift, case)
                    )
                if not 113 <= number <= 120:
                    connection.execute(
                        "INSERT INTO archived_grade VALUES (?, ?, ?, ?, ?)",
                        (
                            case,
                            "meets" if meets else "outside",
                            json.dumps(refs),
                            cutoff,
                            1,
                        ),
                    )
            # The application records its own historical selection through SQL.
            connection.execute(
                """
                INSERT INTO live_selection
                SELECT m.shift,
                       row_number() OVER (ORDER BY json_extract(r.inputs, '$.refund_cents') DESC, m.request),
                       m.request
                FROM membership m JOIN revision r ON r.request = m.request
                WHERE m.shift = ? AND r.known_at <= ?
                ORDER BY json_extract(r.inputs, '$.refund_cents') DESC, m.request
                LIMIT ?
                """,
                (shift, cutoff, CAPACITY),
            )
        # This correction has an earlier effective date but was unavailable at Tuesday's cutoff.
        connection.execute("""
            INSERT INTO revision
            SELECT request, '2', '2026-04-08T10:00:00+00:00', '2026-04-06T10:00:00+00:00',
                   json_set(inputs, '$.refund_cents', 999999), evidence, minutes
            FROM revision WHERE request = 'R-121' AND revision = '1'
            """)


def instant(value: str) -> tuple[datetime, str]:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("source timestamps must include a UTC offset")
    match = re.fullmatch(
        r"\d{4}-?(?:\d{2}-?\d{2}|W\d{2}(?:-?\d)?)."
        r"\d{2}(?::?\d{2}){0,2}(?:[.,](\d+))?(?:Z|[+-]\d{2}(?::?\d{2}){0,2})",
        value,
        re.ASCII | re.DOTALL,
    )
    if match is None:
        raise ValueError(
            "source timestamp needs an ISO time and whole-second UTC offset"
        )
    fraction = match[1] or ""
    return parsed, fraction[6:].rstrip("0")


def utc_timestamp(value: str) -> str:
    parsed, remainder = instant(value)
    utc = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    # Fixed-width UTC through microseconds, followed by the exact submicrosecond part.
    return utc.isoformat(timespec="microseconds") + remainder


def snapshot(connection: sqlite3.Connection) -> tuple[dict, dict]:
    connection.create_function("utc_timestamp", 1, utc_timestamp, deterministic=True)
    exports = {}
    for shift, cutoff, capacity, version, tie_break in connection.execute(
        "SELECT name, cutoff, capacity, live_version, tie_break FROM shift ORDER BY utc_timestamp(cutoff), name"
    ):
        ids = [
            row[0]
            for row in connection.execute(
                "SELECT request FROM membership WHERE shift = ? ORDER BY request",
                (shift,),
            )
        ]
        requests = {}
        for case, revision, known_at, inputs, refs, time in connection.execute(
            """
            SELECT m.request, r.revision, r.known_at, r.inputs, r.evidence, r.minutes
            FROM membership m JOIN revision r ON r.request = m.request
            WHERE m.shift = ? AND r.revision = (
                SELECT revision FROM revision WHERE request = m.request AND utc_timestamp(known_at) <= utc_timestamp(?)
                ORDER BY utc_timestamp(known_at) DESC, revision DESC LIMIT 1
            ) ORDER BY m.request
            """,
            (shift, cutoff),
        ):
            requests[case] = {
                "revision": revision,
                "known_at": known_at,
                "inputs": json.loads(inputs),
                "evidence": json.loads(refs),
                "minutes": json.loads(time),
            }
        actual = [
            row[0]
            for row in connection.execute(
                "SELECT request FROM live_selection WHERE shift = ? ORDER BY slot",
                (shift,),
            )
        ]
        mandatory = [
            row[0]
            for row in connection.execute(
                "SELECT request FROM mandatory WHERE shift = ? ORDER BY request",
                (shift,),
            )
        ]
        overrides = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM overrides WHERE shift = ? ORDER BY sequence",
                (shift,),
            )
        ]
        exports[shift] = {
            "cutoff": cutoff,
            "membership": {"source": f"queue-membership/{shift}@1", "ids": ids},
            "requests": requests,
            "capacity": capacity,
            "live_version": version,
            "tie_break": tie_break,
            "actual": actual,
            "mandatory": mandatory,
            "overrides": overrides,
        }
    grades = {}
    for case, grade, evidence, when, shared in connection.execute(
        "SELECT request, grade, evidence, evidence_at, shared_sources FROM archived_grade ORDER BY request"
    ):
        if type(shared) is not int or shared not in (0, 1):
            raise ValueError(f"{case} has invalid shared-source status")
        grades[case] = {
            "grade": grade,
            "evidence": json.loads(evidence),
            "evidence_at": when,
            "shared_sources": bool(shared),
        }
    return {"schema": 1, "shifts": exports}, {
        "schema": 1,
        "rubric": RUBRIC,
        "grades": grades,
    }


def write_json(path: Path, value: dict) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def export_snapshot(connection: sqlite3.Connection, out: Path) -> None:
    # The eligibility ledger, revisions, picks and grades must share one SQLite snapshot.
    connection.execute("BEGIN")
    try:
        export, grades = snapshot(connection)
    finally:
        connection.rollback()
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".refunds-export-", dir=out.parent))
    try:
        for name, value in (("cutoff.json", export), ("known-grades.json", grades)):
            write_json(staging / name, value)
        if not out.exists() and not out.is_symlink():
            try:
                staging.rename(out)
                return
            except OSError:
                if not out.exists():
                    raise
        if (
            out.is_dir()
            and not out.is_symlink()
            and all(
                (out / child.name).is_file()
                and not (out / child.name).is_symlink()
                and (out / child.name).read_bytes() == child.read_bytes()
                for child in staging.iterdir()
            )
        ):
            return
        raise ValueError(f"choose a new export directory for a changed snapshot: {out}")
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("seed", "export", "inspect"):
        child = commands.add_parser(command)
        child.add_argument("--db", type=Path, required=True)
        if command == "export":
            child.add_argument("--out", type=Path, required=True)
        if command == "inspect":
            child.add_argument("references", nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "seed":
            seed(args.db)
            return 0
        with closing(
            sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            if args.command == "export":
                export_snapshot(connection, args.out)
            else:
                for ref in args.references:
                    found = connection.execute(
                        "SELECT body FROM evidence WHERE ref = ?", (ref,)
                    ).fetchone()
                    if found is None:
                        raise ValueError(f"no evidence stored at {ref}")
                    print(f"{ref!r}  {found[0]!r}")
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(2, f"refunds: {str(exc)!r}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
