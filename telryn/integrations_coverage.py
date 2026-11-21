"""Account for the source records included and excluded from a weekly population."""

import csv
import fcntl
import json
import os
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

from .artifacts import sync_directory
from .contracts import InputError, canonical, fingerprint, read_json
from .integrations_sources import instant, source_revision, time_key

FIELDS = ("case_id", "revision", "evidence_ref", "screen_flag")
DIMENSIONS = ("channel", "agent_type", "product")
COUNTS = ("eligible", "open", "unknown", "outside")


def dimensions(header):
    return tuple(key for key in DIMENSIONS if key in header)


def projection(header, required):
    header = tuple(header or ())
    dims = dimensions(header)
    if len(header) != len(set(header)) or set(header) != set(required) | set(dims):
        raise InputError("input columns differ from the documented schema")
    return dims


def dimension_mapping(specs):
    result = {}
    for spec in specs:
        key, sep, path = spec.partition("=")
        if (
            key not in DIMENSIONS
            or key in result
            or not sep
            or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,4}", path
            )
        ):
            raise InputError(
                "map each dimension once, for example --dimension channel=via.channel"
            )
        result[key] = path
    return {k: result[k] for k in DIMENSIONS if k in result}


def dimension_values(row, mapping):
    result = []
    for path in mapping.values():
        value = row
        for token in path.split("."):
            value = value.get(token) if isinstance(value, dict) else None
        if type(value) is int:
            value = str(value)
        if (
            not isinstance(value, str)
            or not value.strip()
            or not value.isprintable()
            or value.strip() != value
            or value.startswith(("=", "+", "-", "@"))
        ):
            raise InputError(f"missing or unsafe source dimension {path}")
        result.append(value)
    return result


def membership(rows, cutoffs=None):
    if isinstance(rows, dict):
        rows = rows.values()
    return fingerprint(
        sorted(
            [
                r["case_id"],
                r["revision"],
                r["evidence_ref"],
                {k: r[k] for k in dimensions(r)},
                *((cutoffs[r["case_id"]],) if cutoffs is not None else ()),
            ]
            for r in rows
        )
    )


def companion(source, rows):
    path = Path(str(source) + ".coverage.json")
    if not path.exists():
        return None
    receipt = read_json(path, "source coverage")
    return _validate_coverage(receipt, list(rows), None)


def carry(staging, source, rows):
    receipt = companion(source, rows)
    if receipt is not None:
        (staging / "population.csv.coverage.json").write_text(
            canonical(receipt) + "\n", encoding="utf-8"
        )
    return receipt


def new_receipt(since, until, scope, basis):
    return {
        "schema": 1,
        "basis": basis,
        **({"completed_at": {}} if basis != "legacy-created" else {}),
        "scope": scope,
        "since": since.date().isoformat(),
        "until": until.date().isoformat(),
        "scanned": 0,
        "still_open": 0,
        **dict.fromkeys(COUNTS, 0),
    }


def load_coverage(path, population_rows, week):
    receipt = read_json(Path(path), "population coverage")
    if isinstance(population_rows, dict):
        population_rows = population_rows.values()
    return _validate_coverage(receipt, list(population_rows), week)


def _validate_coverage(receipt, rows, week):
    start = date.fromisoformat(str(week or receipt.get("since")))
    until = date.fromisoformat(str(receipt.get("until")))

    dims = receipt.get("dimensions")
    if (
        not isinstance(dims, list)
        or tuple(dims) != dimensions(dims)
        or any(dimensions(row) != tuple(dims) for row in rows)
    ):
        raise InputError("population grouping fields differ from source coverage")
    cutoffs = receipt.get("completed_at")
    if receipt.get("basis") != "legacy-created":
        if not isinstance(cutoffs, dict) or set(cutoffs) != {
            r["case_id"] for r in rows
        }:
            raise InputError(
                "missing case-specific completion cutoffs in coverage receipt"
            )
        if not all(
            isinstance(t, str) and start <= instant(t).date() < until
            for t in cutoffs.values()
        ):
            raise InputError("completion cutoff outside coverage interval")
    elif cutoffs is not None:
        raise InputError("legacy creation receipt cannot set completion cutoffs")
    if (
        receipt.get("since") != start.isoformat()
        or until <= start
        or (week is not None and until != start + timedelta(days=7))
        or receipt.get("membership") != membership(rows, cutoffs)
        or receipt.get("eligible") != len(rows)
    ):
        raise InputError(
            "coverage receipt differs from the weekly dates or population revisions"
        )
    for key in (*COUNTS, "scanned", "still_open"):
        if type(receipt.get(key)) is not int or receipt[key] < 0:
            raise InputError("invalid source coverage count")
    if (
        receipt["scanned"] != sum(receipt[k] for k in COUNTS)
        or receipt["still_open"] > receipt["scanned"]
    ):
        raise InputError("source coverage counts do not reconcile")
    if receipt.get("basis") not in (
        "completion-snapshot",
        "event-ledger",
        "legacy-created",
    ):
        raise InputError("unrecognized source coverage basis")
    if not isinstance(receipt.get("scope"), str) or not receipt["scope"].strip():
        raise InputError("missing source coverage scope")
    return receipt


def publish(path, rows, receipt):
    """Install the receipt first; a complete visible population always has its receipt."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (*FIELDS, *receipt.get("dimensions", []))
    receipt["membership"] = membership(
        (dict(zip(fields, r, strict=True)) for r in rows), receipt.get("completed_at")
    )
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        population = Path(tmp) / "population"
        with population.open("x", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows(rows)
        publish_with_receipt(path, population, receipt)


def publish_with_receipt(path, artifact, receipt):
    """Publish the coverage first, so a newly visible data file always has its provenance."""
    artifact.chmod(0o600)
    with artifact.open("rb") as stream:
        os.fsync(stream.fileno())
    # All connector writers take the same directory lock through both no-clobber
    # links. An uncovered writer cannot interleave with an incoming receipt.
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        _publish_links(path, artifact, receipt)
    finally:
        os.close(directory)


def _publish_links(path, artifact, receipt):
    if path.exists() or path.is_symlink():
        raise InputError(f"output already exists: {path}")
    ledger = Path(str(path) + ".coverage.json")
    if receipt is not None:
        info = artifact.with_name("coverage.tmp")
        info.write_text(canonical(receipt) + "\n", encoding="utf-8")
        info.chmod(0o600)
        with info.open("rb") as stream:
            os.fsync(stream.fileno())
        try:
            os.link(info, ledger)
        except FileExistsError:
            if ledger.is_symlink() or ledger.read_bytes() != info.read_bytes():
                raise InputError(
                    f"unmatched coverage receipt already exists: {ledger}"
                ) from None
        sync_directory(path.parent)
    elif ledger.exists() or ledger.is_symlink():
        raise InputError("output path already has a coverage receipt")
    os.link(artifact, path)
    sync_directory(path.parent)


def from_events(path, since, until, scope):
    """Import one row per case with the archived list of all completion timestamps."""
    receipt = new_receipt(since, until, scope, "event-ledger")
    eligible, seen = [], set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        dims = projection(reader.fieldnames, (*FIELDS, "state", "completion_events"))
        receipt["dimensions"] = list(dims)
        for row in reader:
            if None in row or any(
                not v or not v.isprintable() or v.strip() != v for v in row.values()
            ):
                raise InputError("event ledger has missing or invalid columns")
            key = row["case_id"]
            if key in seen:
                raise InputError("duplicate case in event ledger")
            seen.add(key)
            state = row["state"]
            if state not in {"open", "completed", "unknown"} or row[
                "screen_flag"
            ].lower() not in {"true", "false", "0", "1"}:
                raise InputError("invalid event ledger state or screen flag")
            events = json.loads(row["completion_events"])
            if events is not None and not isinstance(events, list):
                raise InputError(
                    "completion_events must be an array of timestamps or null"
                )
            if isinstance(events, list) and not all(isinstance(t, str) for t in events):
                raise InputError("completion event requires a timestamp with timezone")
            times = [source_revision(t) for t in events or []]
            receipt["scanned"] += 1
            receipt["still_open"] += state == "open"
            qualifying = [t for t in times if since <= instant(t) < until]
            if qualifying:
                receipt["completed_at"][key] = min(qualifying, key=time_key)
                receipt["eligible"] += 1
                eligible.append(tuple(row[k] for k in (*FIELDS, *dims)))
            elif (
                events is None
                or state == "unknown"
                or (state == "completed" and not times)
            ):
                receipt["unknown"] += 1
            elif state == "open":
                receipt["open"] += 1
            else:
                receipt["outside"] += 1
    return sorted(eligible), receipt


def check_as_of(rows, receipt):
    """Every projected evidence revision must be explicitly archived as of its completion."""
    cutoffs = receipt.get("completed_at") if receipt else None
    for row in rows:
        t = row.get("as_of")
        if t is not None and (not isinstance(t, str) or not t):
            raise InputError("invalid as_of timestamp")
        if cutoffs is not None and (
            t is None or time_key(t) != time_key(cutoffs[row["case_id"]])
        ):
            raise InputError(
                "supply archived evidence with as_of matching each case's completion cutoff"
            )
        if t is not None:
            time_key(t)
