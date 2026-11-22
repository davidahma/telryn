"""Operator-mediated, opt-in peer benchmark with frozen cohorts."""

from contextlib import closing, contextmanager
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat

from . import evidence_signatures as signatures
from . import oversight, score_audit
from .artifacts import new_directory
from .contracts import InputError, canonical, fingerprint, read_json

SUBMISSION = "telryn-peer-submission-v1"
REQUEST = "telryn-peer-request-v1"
MIN_PEERS = 5
ALIAS = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}")
METRICS = score_audit.MEASURES


def _alias(value):
    if not isinstance(value, str) or not ALIAS.fullmatch(value):
        raise InputError("use approved short firm, vendor and protocol identifiers")
    return value


def _cohort(aggregate, protocol):
    return {
        "protocol": _alias(protocol),
        "vendor": _alias(aggregate["vendor"]),
        "configuration": _alias(aggregate["configuration"]),
        "quarter": aggregate["quarter"],
        "basis": aggregate["basis"],
        "synthetic": aggregate["synthetic"],
        "weeks": [row["week"] for row in aggregate["weeks"]],
    }


def _bounded_rate(value):
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def _validate(aggregate):
    keys = {
        "schema",
        "vendor",
        "configuration",
        "quarter",
        "synthetic",
        "basis",
        "minimum_cell",
        "weeks",
    }
    if (
        not isinstance(aggregate, dict)
        or set(aggregate) != keys
        or type(aggregate["schema"]) is not int
        or aggregate["schema"] != 1
        or type(aggregate["synthetic"]) is not bool
        or aggregate["basis"] not in ("all_scored", "unreviewed_scored")
        or type(aggregate["minimum_cell"]) is not int
        or aggregate["minimum_cell"] < 5
        or not isinstance(aggregate["weeks"], list)
    ):
        raise InputError("invalid or unsupported opt-in benchmark aggregate")
    if not isinstance(aggregate["quarter"], str):
        raise InputError("invalid benchmark quarter")
    expected = oversight.quarter_weeks(aggregate["quarter"])
    floor = aggregate["minimum_cell"]
    available = set()
    weeks = []
    for row in aggregate["weeks"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"week", "metrics"}
            or row["week"] not in expected
            or not isinstance(row["metrics"], dict)
            or set(row["metrics"]) != set(METRICS)
        ):
            raise InputError("invalid benchmark week or metric name")
        weeks.append(row["week"])
        for name, metric in row["metrics"].items():
            if metric == {"suppressed": True} and metric["suppressed"] is True:
                continue
            if not isinstance(metric, dict) or set(metric) != {
                "denominator",
                "assigned",
                "rate",
                "rate_bounds",
            }:
                raise InputError("invalid benchmark metric fields")
            lowhigh = metric["rate_bounds"]
            count, assigned, rate = (
                metric[field] for field in ("denominator", "assigned", "rate")
            )
            if (
                type(count) is not int
                or type(assigned) is not int
                or not floor <= assigned <= count <= 10**12
                or not isinstance(lowhigh, list)
                or len(lowhigh) != 2
                or not all(map(_bounded_rate, lowhigh))
                or lowhigh[0] > lowhigh[1]
                or (
                    rate is not None
                    and (
                        not _bounded_rate(rate) or not lowhigh[0] <= rate <= lowhigh[1]
                    )
                )
            ):
                raise InputError("invalid benchmark metric counts or rate range")
            available.add(name)
    if not weeks or weeks != sorted(set(weeks)) or not available:
        raise InputError(
            "benchmark needs unique sorted weeks and at least one shareable metric"
        )
    return aggregate


def _policy(path):
    doc = read_json(path, "collector roster")
    if (
        set(doc) != {"schema", "protocols", "firms"}
        or not isinstance(doc["protocols"], list)
        or not isinstance(doc["firms"], dict)
        or not doc["protocols"]
        or not doc["firms"]
    ):
        raise InputError(
            "collector configuration needs protocols and a firm-to-key roster"
        )
    protocols = {_alias(p) for p in doc["protocols"]}
    keys = {}
    for firm, filename in doc["firms"].items():
        _alias(firm)
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise InputError("place approved public key files alongside the roster")
        keys[firm] = signatures.key_bytes(path.parent / filename)
    if len(keys) != len(set(keys.values())):
        raise InputError("each approved firm requires a distinct public key")
    return protocols, keys


@contextmanager
def _database(folder, roster):
    # The collector operator owns the directory and sends outputs over an
    # authenticated channel, outside this reference command.
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (
        folder.is_symlink()
        or folder.stat().st_uid != os.getuid()
        or folder.stat().st_mode & 0o077
    ):
        raise InputError("collector directory must be owner-only")
    path = folder / "peer.sqlite3"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        meta = os.fstat(fd)
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_uid != os.getuid()
            or meta.st_mode & 0o077
        ):
            raise InputError("collector database must be a private regular file")
    finally:
        os.close(fd)
    try:
        with closing(sqlite3.connect(path, timeout=15)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS cohorts (id TEXT PRIMARY KEY, spec TEXT NOT NULL, sealed INTEGER NOT NULL DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS releases (window TEXT PRIMARY KEY, cohort TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS contributions (cohort TEXT NOT NULL, firm TEXT NOT NULL, statement TEXT NOT NULL, PRIMARY KEY(cohort,firm))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS firms (firm TEXT PRIMARY KEY, pubkey BLOB UNIQUE NOT NULL)"
            )
            for firm, key in roster.items():
                db.execute("INSERT OR IGNORE INTO firms VALUES (?,?)", (firm, key))
                if db.execute(
                    "SELECT pubkey FROM firms WHERE firm=?", (firm,)
                ).fetchone() != (key,):
                    raise InputError(
                        "collector roster conflicts with a previously registered identity"
                    )
            yield db
    except sqlite3.Error as exc:
        raise InputError("could not update the collector database") from exc


def _authenticated(path, roster, purpose):
    if path.stat().st_size > signatures.LIMIT:
        raise InputError("signed peer evidence exceeds 8 MiB")
    signed = read_json(path, "signed peer evidence")
    payload = signed.get("payload")
    firm = payload.get("firm") if isinstance(payload, dict) else None
    _alias(firm)
    if firm not in roster:
        raise InputError("firm is absent from the approved collector roster")
    return signatures.verify_key(signed, roster[firm], purpose)


def submit(folder, roster_path, path):
    protocols, roster = _policy(roster_path)
    signed = _authenticated(path, roster, SUBMISSION)
    if set(signed) != {"firm", "protocol", "aggregate"}:
        raise InputError("unexpected benchmark submission fields")
    agg = _validate(signed["aggregate"])
    spec = _cohort(agg, signed["protocol"])
    if spec["protocol"] not in protocols:
        raise InputError("benchmark protocol is not approved by the collector")
    cid = fingerprint(spec)
    raw = canonical(agg)
    with _database(folder, roster) as db:
        db.execute(
            "INSERT OR IGNORE INTO cohorts(id,spec) VALUES (?,?)",
            (cid, canonical(spec)),
        )
        (sealed,) = db.execute(
            "SELECT sealed FROM cohorts WHERE id=?", (cid,)
        ).fetchone()
        prior = db.execute(
            "SELECT statement FROM contributions WHERE cohort=? AND firm=?",
            (cid, signed["firm"]),
        ).fetchone()
        if prior == (raw,):
            return {"cohort": cid, "received": True}
        if sealed or prior:
            raise InputError("this cohort or the firm's contribution is already frozen")
        db.execute(
            "INSERT INTO contributions VALUES (?,?,?)", (cid, signed["firm"], raw)
        )
    return {"cohort": cid, "received": True}


def _contributions(db, cohort):
    return {
        firm: json.loads(aggregate)
        for firm, aggregate in db.execute(
            "SELECT firm,statement FROM contributions WHERE cohort=?", (cohort,)
        )
    }


def _metric_firms(contributions, name):
    return {
        firm: [w["metrics"][name] for w in aggregate["weeks"]]
        for firm, aggregate in contributions.items()
        if all(not w["metrics"][name].get("suppressed") for w in aggregate["weeks"])
    }


def seal(folder, roster_path, cid):
    _, roster = _policy(roster_path)
    with _database(folder, roster) as db:
        row = db.execute(
            "SELECT spec,sealed FROM cohorts WHERE id=?", (cid,)
        ).fetchone()
        if row is None:
            raise InputError("unknown cohort")
        if not row[1]:
            spec = json.loads(row[0])
            window = fingerprint(
                {
                    k: v
                    for k, v in spec.items()
                    if k not in ("protocol", "weeks", "basis")
                }
            )
            db.execute("INSERT OR IGNORE INTO releases VALUES (?,?)", (window, cid))
            if db.execute(
                "SELECT cohort FROM releases WHERE window=?", (window,)
            ).fetchone() != (cid,):
                raise InputError(
                    "an overlapping result is already frozen for the same configuration and quarter"
                )
            members = _contributions(db, cid)
            if any(firm not in roster for firm in members):
                raise InputError(
                    "a contributor is no longer approved; resolve roster membership before freezing"
                )
            if not any(
                len(_metric_firms(members, name)) > MIN_PEERS for name in METRICS
            ):
                raise InputError(
                    "a frozen cohort needs at least six approved firms with a shareable metric"
                )
            db.execute("UPDATE cohorts SET sealed=1 WHERE id=?", (cid,))
    return {"cohort": cid, "sealed": True}


def view(folder, roster_path, request):
    _, roster = _policy(roster_path)
    body = _authenticated(request, roster, REQUEST)
    if set(body) != {"firm", "cohort"} or not isinstance(body["cohort"], str):
        raise InputError("invalid peer view request")
    cid, requester = body["cohort"], body["firm"]
    with _database(folder, roster) as db:
        row = db.execute(
            "SELECT spec,sealed FROM cohorts WHERE id=?", (cid,)
        ).fetchone()
        contributions = _contributions(db, cid)
    if not row or not row[1] or requester not in contributions:
        raise InputError(
            "view requires a sealed cohort and your own signed contribution"
        )
    spec = json.loads(row[0])
    metrics = {}
    for name in METRICS:
        firm_rows = _metric_firms(contributions, name)
        if requester not in firm_rows or len(firm_rows) <= MIN_PEERS:
            metrics[name] = {"suppressed": True}
            continue
        rows = [m for firm in firm_rows.values() for m in firm]
        count = sum(m["denominator"] for m in rows)
        metrics[name] = {
            "firms": len(firm_rows),
            "denominator": count,
            "rate_bounds": [
                math.fsum(m["denominator"] * m["rate_bounds"][i] for m in rows) / count
                for i in (0, 1)
            ],
            "point_estimate": (
                math.fsum(m["denominator"] * m["rate"] for m in rows) / count
                if all(m["rate"] is not None for m in rows)
                else None
            ),
        }
    return {
        "schema": 1,
        "cohort": cid,
        **spec,
        "metrics": metrics,
        "interpretation": "Includes the contributing firm. Weighted endpoints are descriptive and carry no joint confidence guarantee.",
    }


def register_arguments(commands):
    parser = commands.add_parser(
        "peer-benchmark", help="sign or collect opt-in peer benchmark evidence"
    )
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("contribute", "request"):
        p = sub.add_parser(name)
        p.add_argument("--firm", required=True)
        p.add_argument("--signing-key", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        if name == "contribute":
            p.add_argument("--opt-in", action="store_true", required=True)
            p.add_argument("--protocol", required=True)
            p.add_argument("aggregate", type=Path)
        else:
            p.add_argument("cohort")
    for name in ("receive", "seal", "view"):
        p = sub.add_parser(name)
        p.add_argument("--store", type=Path, required=True)
        p.add_argument("--roster", type=Path, required=True)
        if name == "seal":
            p.add_argument("cohort")
        else:
            p.add_argument("signed", type=Path)


def execute(args):
    if args.action in ("contribute", "request"):
        firm = _alias(args.firm)
        if args.action == "contribute":
            agg = _validate(read_json(args.aggregate, "opt-in benchmark"))
            cohort = fingerprint(_cohort(agg, args.protocol))
            payload = {"firm": firm, "protocol": args.protocol, "aggregate": agg}
            purpose = SUBMISSION
        else:
            cohort = args.cohort
            if not re.fullmatch("[a-f0-9]{64}", cohort):
                raise InputError("invalid cohort receipt")
            payload, purpose = {"firm": firm, "cohort": cohort}, REQUEST
        with new_directory(args.out) as staging:
            signed = signatures.sign(payload, args.signing_key, purpose)
            (staging / "signed.json").write_text(
                canonical(signed) + "\n", encoding="ascii"
            )
        result = {"cohort": cohort, "signed": str(args.out / "signed.json")}
    elif args.action == "receive":
        result = submit(args.store, args.roster, args.signed)
    elif args.action == "seal":
        result = seal(args.store, args.roster, args.cohort)
    else:
        result = view(args.store, args.roster, args.signed)
    print(canonical(result))
    return 0
