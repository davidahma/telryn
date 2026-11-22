"""Replay a dated accommodation request against its register and delivery receipts."""

import csv
import hashlib
import io
from pathlib import Path

from .contracts import InputError, canonical, fingerprint, integer, read_json, timestamp

LIMIT = 32 * 1024 * 1024
LEDGER = (
    "request_id",
    "case_id",
    "accommodation",
    "event_id",
    "status",
    "actor_type",
    "actor",
    "recorded_at",
    "evidence_ref",
)
STATUSES = {
    "register": {"recorded"},
    "delivery": {"queued", "sent", "failed", "delivered"},
}


def deadline_status(
    grade, exact_match, deadline, horizon, event_times, complete_through
):
    """Classify a single required milestone using only events visible at the horizon."""
    if grade is False:
        return "outside_rubric"
    if grade is None or deadline is None or not exact_match or len(event_times) > 1:
        return "unresolved"
    due = timestamp(deadline, "deadline")
    through = (
        timestamp(complete_through, "coverage")
        if complete_through is not None
        else None
    )
    if event_times:
        if timestamp(event_times[0], "milestone time") <= due:
            return "on_time"
        return "late" if through is not None and through >= due else "unresolved"
    if due > horizon:
        return "not_due"
    return "missing" if through is not None and through >= horizon else "unresolved"


def covered_deadline_without_entry(
    exact_match, deadline, horizon, event_times, complete_through
):
    """Deadline absence can be proven before current register status is known."""
    if not exact_match or deadline is None or event_times or complete_through is None:
        return False
    due = timestamp(deadline, "deadline")
    return due <= horizon and due <= timestamp(complete_through, "coverage")


def _fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise InputError(f"{label} must have the documented fields")
    return value


def _text(value, label):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or value != value.strip()
        or not value.isprintable()
    ):
        raise InputError(f"{label} requires a printable value of 1–2048 characters")
    return value


def _time(value, label):
    return timestamp(_text(value, label), label)


def _local(folder, name):
    path = (folder / _text(name, "source file")).resolve()
    if (
        not path.is_relative_to(folder.resolve())
        or not path.is_file()
        or path.stat().st_size > LIMIT
    ):
        raise InputError(
            "supply a local file of at most 32 MiB inside the manifest directory"
        )
    return path


def _cited(passages, number, cutoff, *, speaker_roles):
    if number is None:
        return None
    idx = integer(number, "one-based passage", minimum=1)
    if not isinstance(passages, list) or idx > len(passages):
        raise InputError("cited passage is absent from the frozen source")
    hit = _fields(passages[idx - 1], ("at", "speaker_role", "text"), "cited passage")
    if hit["speaker_role"] not in speaker_roles:
        raise InputError(
            "the request must cite the customer and the agreement must cite a support responder"
        )
    if _time(hit["at"], "passage time") > cutoff:
        raise InputError("cited passage postdates the cutoff")
    return {
        "passage": idx,
        "at": hit["at"],
        "speaker_role": hit["speaker_role"],
        "quote": _text(hit["text"], "redacted passage"),
    }


def _source_ledger(folder, source, kind, case, as_of):
    _fields(source, ("file", "source_ref", "complete_through"), f"{kind} source")
    _text(source["source_ref"], "source revision")
    through = source["complete_through"]
    if through is not None and _time(through, "coverage") > as_of:
        raise InputError("source coverage extends beyond follow-up")
    data = _local(folder, source["file"]).read_bytes()
    if len(data) > LIMIT:
        raise InputError("outcome evidence exceeds 32 MiB")
    reader = csv.DictReader(
        io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True
    )
    if tuple(reader.fieldnames or ()) != LEDGER:
        raise InputError(f"{kind} CSV must use the documented header")
    seen, rows = set(), []
    for row in reader:
        _fields(row, LEDGER, "ledger row")
        for key, value in row.items():
            _text(value, key)
        if (
            row["event_id"] in seen
            or row["status"] not in STATUSES[kind]
            or row["actor_type"] not in {"human", "agent", "system"}
        ):
            raise InputError(
                f"duplicate event ID, unknown status or actor type in {kind} CSV"
            )
        seen.add(row["event_id"])
        if _time(row["recorded_at"], "event time") > as_of:
            raise InputError("ledger event postdates follow-up")
        if case["request_id"] == row["request_id"]:
            rows.append(row)
    rows.sort(key=lambda r: (_time(r["recorded_at"], "event time"), r["event_id"]))
    return {
        "source_ref": source["source_ref"],
        "complete_through": through,
        "sha256": hashlib.sha256(data).hexdigest(),
        "events": rows,
    }


def accommodation(path: Path):
    if path.stat().st_size > LIMIT:
        raise InputError("outcome manifest exceeds 32 MiB")
    doc = read_json(path, "outcome manifest")
    _fields(
        doc,
        (
            "schema",
            "outcome_type",
            "evidence",
            "scope",
            "policy_ref",
            "cutoff",
            "as_of",
            "conversation",
            "review",
            "register",
            "delivery",
        ),
        "outcome manifest",
    )
    if doc["outcome_type"] != "accommodation" or doc["evidence"] not in (
        "synthetic",
        "operational",
    ):
        raise InputError(
            "expected an accommodation manifest labeled synthetic or operational"
        )
    for k in ("scope", "policy_ref"):
        _text(doc[k], k)
    cutoff = _time(doc["cutoff"], "cutoff")
    as_of = _time(doc["as_of"], "follow-up")
    if as_of < cutoff:
        raise InputError("follow-up precedes cutoff")
    src = read_json(_local(path.parent, doc["conversation"]), "conversation")
    _fields(
        src, ("schema", "case_id", "revision", "source_ref", "passages"), "conversation"
    )
    case = _fields(
        doc["review"],
        (
            "case_id",
            "revision",
            "source_ref",
            "reviewer",
            "required",
            "request_id",
            "accommodation",
            "request_passage",
            "agreement_passage",
            "deadline",
        ),
        "outcome review",
    )
    for k in ("case_id", "revision", "source_ref"):
        if _text(case[k], k) != src[k]:
            raise InputError(
                "review does not match the conversation case, revision and reference"
            )
    _text(case["reviewer"], "reviewer")
    grade = case["required"]
    if grade is not None and type(grade) is not bool:
        raise InputError("required must be true, false or null")
    if case["request_id"] is not None:
        _text(case["request_id"], "request ID")
    _text(case["accommodation"], "accommodation code")
    if (
        case["request_passage"] is not None
        and case["request_passage"] == case["agreement_passage"]
    ):
        raise InputError(
            "cite separate passages for the customer's request and the support agreement"
        )
    request = _cited(
        src["passages"], case["request_passage"], cutoff, speaker_roles=("customer",)
    )
    agreement = _cited(
        src["passages"],
        case["agreement_passage"],
        cutoff,
        speaker_roles=("human_support", "ai_support"),
    )
    if grade is True and request is None:
        raise InputError("a required accommodation needs a cited customer request")
    due = case["deadline"]
    if due is not None and (
        not agreement
        or not request
        or _time(due, "agreed deadline") < _time(agreement["at"], "agreement time")
    ):
        raise InputError(
            "deadline needs a cited request and an agreement made by that deadline"
        )
    if (
        request
        and agreement
        and _time(agreement["at"], "agreement time")
        < _time(request["at"], "request time")
    ):
        raise InputError("agreement predates the request")
    milestones = {}
    for kind, success in (("register", "recorded"), ("delivery", "delivered")):
        source = _source_ledger(path.parent, doc[kind], kind, case, as_of)
        for key, at in (("at_cutoff", cutoff), ("at_follow_up", as_of)):
            visible = [
                r
                for r in source["events"]
                if _time(r["recorded_at"], "event time") <= at
            ]
            exact = case["request_id"] is not None and all(
                r["case_id"] == case["case_id"]
                and (
                    not request
                    or _time(r["recorded_at"], "event time")
                    >= _time(request["at"], "request time")
                )
                for r in visible
            )
            # A receipt for a different adjustment cannot fulfill this request.
            hits = [
                r["recorded_at"]
                for r in visible
                if r["status"] == success
                and r["accommodation"] == case["accommodation"]
            ]
            source[key] = deadline_status(
                grade, exact, due, at, hits, source["complete_through"]
            )
        milestones[kind] = source
    return {
        **{
            k: doc[k]
            for k in (
                "schema",
                "outcome_type",
                "evidence",
                "scope",
                "policy_ref",
                "cutoff",
                "as_of",
            )
        },
        **{
            k: case[k]
            for k in (
                "case_id",
                "revision",
                "source_ref",
                "reviewer",
                "required",
                "request_id",
                "accommodation",
                "deadline",
            )
        },
        "request": request,
        "agreement": agreement,
        "milestones": milestones,
        "manifest_digest": fingerprint(doc),
        "conversation_digest": fingerprint(src),
    }


def register_arguments(commands):
    parser = commands.add_parser(
        "outcome",
        help="check a promised accommodation or recognition of disclosed support needs",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "manifest", nargs="?", type=Path, help="local accommodation manifest"
    )
    source.add_argument(
        "--example",
        choices=("accommodation", "vulnerability"),
        help="replay bundled synthetic evidence",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")


def execute(args):
    root = Path(__file__).resolve().parent
    examples = (
        root / "_bundled" if (root / "_bundled").is_dir() else root.parent
    ) / "examples"
    path = args.manifest or examples / args.example / "manifest.json"
    if path.stat().st_size > LIMIT:
        raise InputError("outcome manifest exceeds 32 MiB")
    if read_json(path, "outcome manifest").get("outcome_type") == "vulnerability":
        from .vulnerability import assessment

        report = assessment(path)
        if args.format == "json":
            print(canonical(report))
        else:
            print(report["measure"])
            print(
                f"{report['evidence'].capitalize()} census · {report['scope']} · {report['cutoff']}"
            )
            for row in report["groups"]:
                lo, hi = row["rate_bounds"]
                rate = f"{lo:.1%}" if lo == hi else f"{lo:.1%} to {hi:.1%}"
                print(
                    f"{row['group']}: {row['confirmed_misses']} confirmed in {row['denominator']} ({rate})"
                )
            for delta in report["differences"]:
                lo, hi = delta["difference_bounds"]
                change = (
                    f"{lo * 100:+.1f}"
                    if lo == hi
                    else f"{lo * 100:+.1f} to {hi * 100:+.1f}"
                )
                print(f"{' minus '.join(delta['groups'])}: {change} percentage points")
            if hidden := report["suppressed_group_count"]:
                groups = "group" if hidden == 1 else "groups"
                print(f"Withheld {hidden} {groups} under the small-cell rule.")
        return 0
    result = accommodation(path)
    if args.format == "json":
        print(canonical(result))
    else:
        print(
            f"{result['evidence'].capitalize()} accommodation check · {result['case_id']} · {result['scope']}"
        )
        print(f"Reviewed by {result['reviewer']} under {result['policy_ref']}")
        for key in ("request", "agreement"):
            if cited := result[key]:
                print(
                    f"{key.title()} · {cited['speaker_role'].replace('_', ' ')} · {cited['quote']} ({result['source_ref']}, {result['revision']}, passage {cited['passage']}, {cited['at']})"
                )
        print(f"Agreed deadline · {result['deadline'] or 'unknown'}")
        for kind, stage in result["milestones"].items():
            print(
                f"{kind.title()} · {stage['at_cutoff'].replace('_', ' ')} at {result['cutoff']} · {stage['at_follow_up'].replace('_', ' ')} at {result['as_of']}"
            )
            for e in stage["events"]:
                print(
                    f"  {e['status']} · {e['accommodation']} · {e['actor_type']} {e['actor']} · {e['recorded_at']} · {e['evidence_ref']}"
                )
    return 0
