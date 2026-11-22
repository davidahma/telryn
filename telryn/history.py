"""Preserve a portable, cumulative series of team-verified weekly audit reports."""

import hashlib
from pathlib import Path
import re

from . import evidence_signatures as signatures
from . import oversight, weekly_report
from .artifacts import new_directory
from .contracts import InputError, canonical, decode_json, fingerprint, versioned

PURPOSE = "telryn-quarter-history-v1"
MAX_REPORT = 64 * 1024 * 1024
MAX_QUARTERS = 40
HEX = re.compile(r"[a-f0-9]{64}")
SERIES = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _next(quarter):
    year, number = int(quarter[:4]), int(quarter[-1])
    return f"{year + 1:04d}-Q1" if number == 4 else f"{year:04d}-Q{number + 1}"


def _read(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise InputError("history requires a bounded regular evidence file")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise InputError("history evidence exceeds its file limit")
    return content


def _report(raw, state=None):
    envelope = weekly_report._envelope(raw)
    _, marker, tail = raw.rpartition(weekly_report.TRAILER)
    if (
        not marker
        or not HEX.fullmatch(tail[:-5].decode("ascii"))
        or not tail.endswith(b" -->\n")
    ):
        raise InputError("history needs an issued, signed weekly report")
    state = envelope["state"] if state is None else state
    if "control" not in state or "complaint_records" not in state:
        raise InputError(
            "the series requires reports with complaint controls and dated evidence"
        )
    scope = oversight._scope(state)
    _, clock, supplied = oversight._deadline_context(state)
    return {
        "week": state["week"],
        "revision": envelope["auth"]["revision"],
        "weekly_signature": tail[:-5].decode("ascii"),
        "sha256": _digest(raw),
        "scope": fingerprint(scope),
        "clock": clock,
        "manual_deadlines": supplied,
        "policy_review_pending": bool(
            state.get("pack_reviews")
            or state["complaint_records"].get("policy_reviews")
        ),
        "queue": scope["queue"],
        "synthetic": scope["synthetic"],
    }


def _comparability(rows, previous):
    if not previous:
        return "first quarter"
    if {r["scope"] for r in rows} != {r["scope"] for r in previous["weeks"]}:
        return "scope, rubric or score definition changed"
    if {r["clock"] for r in rows} != {r["clock"] for r in previous["weeks"]}:
        return "deadline sources, clock or cutoff timing changed"
    if any(r["policy_review_pending"] for r in [*rows, *previous["weeks"]]):
        return "policy re-review remains open"
    if any(r["manual_deadlines"] for r in [*rows, *previous["weeks"]]):
        return "confirm comparability of issuer-supplied deadlines"
    if len({(r["scope"], r["clock"]) for r in rows}) > 1:
        return "multiple audit settings within quarter"
    return "same recorded settings"


def _body(series, quarter, rows, previous, previous_digest):
    if not isinstance(series, str) or not SERIES.fullmatch(series):
        raise InputError("use an approved short series identifier")
    expected = oversight.quarter_weeks(quarter)
    if not rows:
        raise InputError("at least one signed weekly report is required")
    rows = sorted(rows, key=lambda row: row["week"])
    weeks = [r["week"] for r in rows]
    if len(weeks) != len(set(weeks)) or any(w not in expected for w in weeks):
        raise InputError("duplicate week or report outside the stated quarter")
    identities = {(r["queue"], r["synthetic"]) for r in rows}
    if len(identities) != 1:
        raise InputError(
            "history contains different queues or operational and synthetic evidence"
        )
    if previous and (
        quarter != _next(previous["quarter"])
        or series != previous["series"]
        or identities != {(r["queue"], r["synthetic"]) for r in previous["weeks"]}
    ):
        raise InputError(
            "history must extend the same queue, evidence type and immediately preceding quarter"
        )
    return {
        "series": series,
        "quarter": quarter,
        "previous_sha256": previous_digest,
        "weeks": rows,
        "missing_weeks": [w for w in expected if w not in weeks],
        "continuity": _comparability(rows, previous),
    }


def verify(folder, public_key, *, expected=None, extends=None):
    label = "history index"
    index = versioned(decode_json(_read(folder / "index.json", 4096), label), label)
    quarters = index.get("quarters")
    if (
        set(index) != {"schema", "quarters"}
        or not isinstance(quarters, list)
        or not 1 <= len(quarters) <= MAX_QUARTERS
        or any(
            not isinstance(q, str) or not re.fullmatch(r"[0-9]{4}-Q[1-4]", q)
            for q in quarters
        )
    ):
        raise InputError("invalid history index")
    previous = digest = None
    history = []
    found = set()
    files = {"index.json"}
    public = signatures.key_bytes(public_key)
    for quarter in quarters:
        manifest = f"{quarter}.json"
        files.add(manifest)
        raw = _read(folder / manifest, 1024 * 1024)
        body = signatures.verify_key(
            decode_json(raw, "signed history manifest"), public, PURPOSE
        )
        if body.get("quarter") != quarter or not isinstance(body.get("weeks"), list):
            raise InputError("history manifest does not match its quarter")
        rows = []
        expected_weeks = set(oversight.quarter_weeks(quarter))
        for row in body["weeks"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("week"), str)
                or row["week"] not in expected_weeks
            ):
                raise InputError("invalid week in signed history")
            filename = f"{row['week']}.html"
            files.add(filename)
            source = _read(folder / filename, MAX_REPORT)
            if _digest(source) != row.get("sha256") or _report(source) != row:
                raise InputError(
                    "weekly bytes or metadata differ from their signed receipt"
                )
            rows.append(row)
        if body != _body(body.get("series"), quarter, rows, previous, digest):
            raise InputError(
                "signed manifest does not preserve week and quarter continuity"
            )
        digest = _digest(raw)
        found.add(digest)
        history.append(body)
        previous = body
    if set(f.name for f in folder.iterdir()) != files:
        raise InputError("history contains unexpected files")
    if expected is not None and expected != digest:
        raise InputError("history head differs from the expected digest")
    if extends is not None and (extends not in found or extends == digest):
        raise InputError("history does not extend the recipient's earlier checkpoint")
    return {"series": history[-1]["series"], "sha256": digest, "quarters": history}


def issue(paths, out, *, series, quarter, private_key, public_key, prior=None):
    previous = previous_digest = None
    preserved = []
    if prior is not None:
        checked = verify(prior, public_key)
        preserved = checked["quarters"]
        if len(preserved) >= MAX_QUARTERS:
            raise InputError(
                "history contains the maximum supported number of quarters"
            )
        previous, previous_digest = preserved[-1], checked["sha256"]
    with new_directory(out) as staging:
        if prior is not None:
            for q in preserved:
                manifest = q["quarter"] + ".json"
                (staging / manifest).write_bytes(_read(prior / manifest, 1024 * 1024))
                for row in q["weeks"]:
                    name = row["week"] + ".html"
                    (staging / name).write_bytes(_read(prior / name, MAX_REPORT))
        rows = []
        seen = set()
        horizons = set(oversight.quarter_weeks(quarter))
        for path in paths:
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > MAX_REPORT
            ):
                raise InputError("history requires a bounded regular weekly source")
            raw = weekly_report.verified_html(path)
            if len(raw) > MAX_REPORT:
                raise InputError("history source exceeds the weekly file limit")
            row = _report(raw)
            if row["week"] not in horizons or row["week"] in seen:
                raise InputError("duplicate week or report outside the stated quarter")
            seen.add(row["week"])
            (staging / (row["week"] + ".html")).write_bytes(raw)
            rows.append(row)
        signed = signatures.sign(
            _body(series, quarter, rows, previous, previous_digest),
            private_key,
            PURPOSE,
        )
        (staging / (quarter + ".json")).write_text(
            canonical(signed) + "\n", encoding="ascii"
        )
        quarters = [q["quarter"] for q in preserved] + [quarter]
        (staging / "index.json").write_text(
            canonical({"schema": 1, "quarters": quarters}) + "\n", encoding="ascii"
        )
        return verify(staging, public_key, extends=previous_digest)


def register_arguments(commands):
    parser = commands.add_parser(
        "history", help="issue or verify private signed quarter history"
    )
    sub = parser.add_subparsers(dest="operation", required=True)
    export = sub.add_parser("export")
    export.add_argument("--series", required=True)
    export.add_argument("--quarter", required=True)
    export.add_argument("--signing-key", type=Path, required=True)
    export.add_argument("--public-key", type=Path, required=True)
    export.add_argument("--previous", type=Path)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("reports", type=Path, nargs="+")
    check = sub.add_parser("verify")
    check.add_argument("folder", type=Path)
    check.add_argument("--public-key", type=Path, required=True)
    check.add_argument("--expect")
    check.add_argument("--extends")


def execute(args):
    if args.operation == "export":
        result = issue(
            args.reports,
            args.out,
            series=args.series,
            quarter=args.quarter,
            private_key=args.signing_key,
            public_key=args.public_key,
            prior=args.previous,
        )
    else:
        result = verify(
            args.folder, args.public_key, expected=args.expect, extends=args.extends
        )
    print(canonical(result))
    return 0
