"""Create a recipient-safe weekly statement and replay it against bank-held evidence."""

import hashlib
import json
import re
from collections import Counter
from html import escape
from pathlib import Path

from . import complaints, policy, score_audit, weekly, weekly_report
from .artifacts import new_directory
from .contracts import InputError, canonical, integer, timestamp

MARKER = '<script type="application/json" id="bank-statement">'
METRICS = {
    "classification_disagreement": "Disagreement with the human complaint classification",
    "passed_deadline_miss": "QA-passed conversations with a registration miss",
}
COLUMNS = (
    "denominator",
    "assigned",
    "determinate",
    "confirmed",
    "estimate",
    "interval",
)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _digest_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _public_label(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or not value.isprintable()
        or len(value) > 180
    ):
        raise InputError(
            "bank report labels must be printable text of 1–180 characters"
        )
    return value


def _replay_state(raw, schema):
    envelope = weekly_report._envelope(raw)
    state = envelope["state"]
    if schema == 1:
        # Reproduce only the original v1 receipt's historical arithmetic.
        # New statements with an undated calendar must leave deadlines unknown.
        clocks = (
            state.get("complaint_records", {})
            .get("snapshot", {})
            .get("episodes", {})
            .get("policy")
        )
        if clocks and "holiday_calendar" not in clocks:
            clocks["holiday_calendar"] = {
                "id": "legacy-unbounded",
                "version": "historical",
                "source": "original receipt",
                "valid_from": "0001-01-01",
                "valid_through": "9999-12-31",
            }
    return state, envelope["auth"]["revision"]


def _statement(raw, declarations, schema=2):
    state, revision = _replay_state(raw, schema)
    summary = weekly_report.summarize(state)
    qa = summary.get("score_audit")
    if not qa or "complaint_records" not in state:
        raise InputError(
            "a bank report needs a score audit with attached complaint records"
        )
    records = complaints.reconcile(state)
    passed_misses = {
        row["case_id"]
        for row in qa["cases"]
        if row["decision"] == "pass" and row["deadline_miss"] is True
    }
    follow_up = Counter(
        c["owner_as_of"] for c in records["cases"] if c["case_id"] in passed_misses
    )
    return {
        "schema": schema,
        "week": state["week"],
        "synthetic": state.get("control", {}).get("synthetic", False),
        "declared_by_issuer": declarations,
        "report_revision": revision,
        **(
            {
                "calendar": policy.calendar_label(records)
                if "holiday_calendar" in records
                else "Manual deadline supplied by the issuer"
            }
            if schema == 2
            else {}
        ),
        "source_report_sha256": _digest(raw),
        "human_rubric": state["rubric"],
        "scorecard": qa["scorecard"],
        "scorecard_version": qa["version"],
        "score_scope": qa["scope"],
        "score_cutoff": qa["cutoff"],
        "register_cutoff": records["cutoff"],
        "follow_up_through": records["as_of"],
        "register_complete_through": records["sources"]["register"]["complete_through"],
        "work_complete_through": records["sources"]["actions"]["complete_through"],
        "source_sha256": {
            "register": records["sources"]["register"]["sha256"],
            "actions": records["sources"]["actions"]["sha256"],
            "scores": qa["sha256"],
        },
        "coverage": {k: v for k, v in qa["coverage"].items() if type(v) is int},
        "metrics": {name: {k: qa[name][k] for k in COLUMNS} for name in METRICS},
        "sampled_owner_status_after_miss": dict(sorted(follow_up.items())),
    }


def _percent(value):
    return "undetermined" if value is None else f"{value:.1%}"


def _html(s):
    dec = s["declared_by_issuer"]

    def show(value):
        return escape(str(value)) if value is not None else "unknown"

    def row(label, value):
        return f"<tr><th scope='row'>{escape(label)}</th><td>{show(value)}</td></tr>"

    rows = "".join(
        row(k, v)
        for k, v in (
            ("Queue in scope", dec["queue"]),
            ("Sample selected by", dec["sample_owner"]),
            ("Register supplied by", dec["register_owner"]),
            ("Excluded queues (issuer declaration)", dec["excluded_queues"]),
            ("Scored population", s["score_scope"]),
            ("Human policy rubric", s["human_rubric"]),
            ("Scorecard", f"{s['scorecard']} · {s['scorecard_version']}"),
            ("Scores frozen at", s["score_cutoff"]),
            ("Registration checked at", s["register_cutoff"]),
            ("Follow-up through", s["follow_up_through"]),
            ("Register coverage through", s["register_complete_through"]),
            ("Owner history coverage through", s["work_complete_through"]),
        )
    )
    if s["schema"] == 2:
        rows += row("Calendar used for deadlines", s["calendar"])
    metrics = []
    for key, label in METRICS.items():
        m = s["metrics"][key]
        limits = m["interval"]
        interval = (
            "undefined"
            if not limits or m["denominator"] == 0
            else "–".join(_percent(x) for x in limits)
        )
        metrics.append(
            f"<tr><th scope='row'>{escape(label)}</th><td>{m['denominator']}</td><td>{m['assigned']}</td>"
            f"<td>{m['confirmed']}</td><td>{m['assigned'] - m['determinate']}</td>"
            + (
                f"<td>{_percent(m['estimate'])}</td><td>{interval}</td>"
                if s["schema"] == 1
                else f"<td>{escape(score_audit.rate_summary(m))}</td>"
            )
            + "</tr>"
        )
    counts = ", ".join(
        f"{count} {show(status)}"
        for status, count in s["sampled_owner_status_after_miss"].items()
    )
    coverage = " · ".join(
        f"{show(label.replace('_', ' '))} {count}"
        for label, count in s["coverage"].items()
    )
    label = "Synthetic rehearsal" if s["synthetic"] else "Weekly statement"
    title = "Which QA passes missed a complaint deadline?"
    metrics_header = "<th>Estimated rate</th><th>Range</th>"
    if s["schema"] == 2:
        title = escape(
            score_audit.deadline_headline(s["metrics"]["passed_deadline_miss"])
        )
        metrics_header = "<th>Population rate</th>"
    data = (
        canonical(s)
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("&", r"\u0026")
    )
    return (
        f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<title>Complaints audit · {show(s["week"])}</title>
<style>body{{max-width:58rem;margin:2rem auto;padding:0 1rem;font:16px/1.48 system-ui;color:#172f34}}h1,h2{{line-height:1.15}}h1{{font-size:2rem}}h2{{font-size:1.25rem}}table{{border-collapse:collapse;width:100%;margin-bottom:1rem}}td,th{{padding:.3rem .5rem;text-align:left;border-bottom:1px solid #b4c6c9}}th{{font-weight:600}}.wide{{overflow:auto}}@media print{{body{{font-size:10pt;margin:0}}}}</style><main>
<p>{label} · week of {show(s["week"])} · revision {s["report_revision"]}</p><h1>{title}</h1>
<div class="wide"><table><thead><tr><th>Measure</th><th>Eligible</th><th>Sampled</th><th>Confirmed</th><th>Unresolved in sample</th>{metrics_header}</tr></thead><tbody>{"".join(metrics)}</tbody></table></div>
<p>Confirmed and unresolved counts describe the sample. Each estimated rate and its range apply to the eligible population for that row. The private audit retains the sampling design and case evidence.</p>
<h2>What owners did</h2><p>Latest recorded work status among the sampled QA passes with a confirmed registration miss by cutoff: {counts or "no such cases"}.</p>
<h2>Sources and coverage</h2><table>{rows}</table><p>{coverage}</p>
<p>For a disputed case, compare this file with the SHA-256 supplied by the issuer through an authenticated channel. Replay it with the bank-held weekly report and compare the original exports. Keep the team's signing key in its private store.</p></main>
{MARKER}{data}</script></html>\n"""
    ).encode("utf-8")


def write(report, folder, *, queue, sample_owner, register_owner, excluded_queues):
    declarations = {
        key: _public_label(value)
        for key, value in {
            "queue": queue,
            "sample_owner": sample_owner,
            "register_owner": register_owner,
            "excluded_queues": excluded_queues,
        }.items()
    }
    raw = weekly_report.verified_html(report)
    state = weekly_report._envelope(raw)["state"]
    if state.get("pack_reviews") or state.get("complaint_records", {}).get(
        "policy_reviews"
    ):
        raise InputError(
            "policy re-review required before issuing a new bank statement"
        )
    rendered = _html(_statement(raw, declarations))
    with new_directory(folder) as staging:
        (staging / "report.html").write_bytes(rendered)
    return _digest(rendered)


def write_specimen(report, folder):
    return write(
        report,
        folder,
        queue="Synthetic payments support",
        sample_owner="Complaints QA lead (staged)",
        register_owner="Complaints operations (staged)",
        excluded_queues="All other queues. The rehearsal uses the authored payments export.",
    )


def _check_originals(state, sources):
    """Repeat the joins against the bank's copies of the immutable exports."""
    snapshot = state["complaint_records"]["snapshot"]
    sampled = state["cases"]
    register_keys, case_ids = complaints.ledger_keys(
        snapshot, (c["case_id"] for c in sampled)
    )
    for name, columns, keys in (
        ("register", complaints.REGISTER, register_keys),
        ("actions", complaints.ACTIONS, case_ids),
    ):
        if path := sources.get(name):
            path = path.resolve(strict=True)
            frozen = snapshot[name]
            source = {k: frozen[k] for k in ("source_ref", "complete_through")}
            projected = complaints._ledger(
                path.parent,
                dict(source, file=path.name),
                columns,
                keys,
                snapshot["as_of"],
            )
            if projected != frozen:
                raise InputError(f"{name} rows do not reproduce the private audit")
    if path := sources.get("scores"):
        raw = path.read_bytes()
        if _digest(raw) != state["score_audit"]["sha256"]:
            raise InputError("scores differ from the frozen export")
        audit = state["score_audit"]
        if "native" in audit:
            from . import score_import

            original = score_import.read(
                raw,
                audit["native"],
                {c["case_id"]: c["revision"] for c in sampled},
                audit["scorecard"],
                audit["version"],
            )
        else:
            original = weekly._csv(raw, score_audit.COLUMNS, "bank-held scores")
        for case in sampled:
            record = original.get(case["case_id"])
            if (
                record is None
                or record["revision"] != case["revision"]
                or any(record[k] != v for k, v in case["qa_score"].items())
                or (record["scorecard"], record["scorecard_version"])
                != (state["score_audit"]["scorecard"], state["score_audit"]["version"])
            ):
                raise InputError("score records do not reproduce the private audit")


def _checked_statement(raw):
    _, marker, tail = raw.partition(MARKER.encode())
    payload, close, _ = tail.partition(b"</script>")
    if not marker or not close:
        raise InputError("bank statement is absent")
    invalid = "invalid bank statement"
    try:
        s = json.loads(payload)
        if (
            not isinstance(s, dict)
            or type(s.get("schema")) is not int
            or s["schema"] not in (1, 2)
        ):
            raise InputError("unsupported bank statement version")
        if set(s) != {
            "schema",
            "week",
            "synthetic",
            "declared_by_issuer",
            "report_revision",
            "source_report_sha256",
            "scorecard",
            "scorecard_version",
            "human_rubric",
            "score_scope",
            "score_cutoff",
            "register_cutoff",
            "follow_up_through",
            "register_complete_through",
            "work_complete_through",
            "source_sha256",
            "coverage",
            "metrics",
            "sampled_owner_status_after_miss",
        } | ({"calendar"} if s["schema"] == 2 else set()):
            raise InputError(invalid)
        if s["schema"] == 2:
            # The calendar description joins two independently valid 1024-character
            # policy labels with their date range.
            policy.label(s["calendar"], "calendar description", limit=2081)
        if set(s["declared_by_issuer"]) != {
            "queue",
            "sample_owner",
            "register_owner",
            "excluded_queues",
        }:
            raise InputError(invalid)
        for label in s["declared_by_issuer"].values():
            _public_label(label)
        if set(s["source_sha256"]) != {"register", "actions", "scores"} or any(
            not re.fullmatch("[0-9a-f]{64}", d)
            for d in [s["source_report_sha256"], *s["source_sha256"].values()]
        ):
            raise InputError(invalid)
        weekly._week(s["week"])
        integer(s["report_revision"], "report revision", minimum=1)
        for key in ("scorecard", "scorecard_version", "human_rubric"):
            weekly._field(s[key], key)
        if type(s["synthetic"]) is not bool or s["score_scope"] not in {
            "all_scored",
            "unreviewed_scored",
        }:
            raise InputError(invalid)
        for key in ("score_cutoff", "register_cutoff", "follow_up_through"):
            timestamp(s[key], key)
        for key in ("work_complete_through", "register_complete_through"):
            if s[key] is not None:
                timestamp(s[key], key)
        if set(s["metrics"]) != set(METRICS):
            raise InputError(invalid)
        for m in s["metrics"].values():
            counts = [
                integer(m[k], k)
                for k in ("confirmed", "determinate", "assigned", "denominator")
            ]
            if set(m) != set(COLUMNS) or counts != sorted(counts):
                raise InputError(invalid)
            interval = m["interval"]
            if s["schema"] == 2 and (interval is None) != (m["denominator"] == 0):
                raise InputError(invalid)
            if interval is not None and (
                not isinstance(interval, list)
                or len(interval) != 2
                or any(type(x) not in (float, int) or not 0 <= x <= 1 for x in interval)
                or interval[0] > interval[1]
            ):
                raise InputError(invalid)
            if m["estimate"] is not None and (
                type(m["estimate"]) not in (int, float) or not 0 <= m["estimate"] <= 1
            ):
                raise InputError(invalid)
        coverage = s["coverage"]
        if set(coverage) != {
            "source_population",
            "scored",
            "unscored",
            "ordinary_reviewed",
            "excluded_previously_reviewed",
            "eligible_previously_reviewed",
            "eligible_scored",
            "eligible_passes",
            "eligible_classified",
        }:
            raise InputError(invalid)
        for key, count in coverage.items():
            integer(count, key)
        if (
            coverage["scored"] + coverage["unscored"] != coverage["source_population"]
            or coverage["eligible_scored"] + coverage["excluded_previously_reviewed"]
            != coverage["scored"]
            or coverage["eligible_passes"]
            != s["metrics"]["passed_deadline_miss"]["denominator"]
            or coverage["eligible_classified"]
            != s["metrics"]["classification_disagreement"]["denominator"]
        ):
            raise InputError(invalid)
        ownership = s["sampled_owner_status_after_miss"]
        if set(ownership) - {
            "unobserved",
            "requested",
            "accepted",
            "completed",
            "unresolved",
        } or (
            sum(integer(n, "owner count") for n in ownership.values())
            != s["metrics"]["passed_deadline_miss"]["confirmed"]
        ):
            raise InputError(invalid)
        if _html(s) != raw:
            raise InputError("displayed bank report differs from its statement")
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise InputError(invalid) from exc
    return s


def verify(path, expected, *, source_report=None, case_id=None, sources=None):
    if path.is_dir():
        path = path / "report.html"
    if not re.fullmatch("[0-9a-fA-F]{64}", expected):
        raise InputError(
            "supply the 64-character SHA-256 received through an authenticated channel"
        )
    raw = path.read_bytes()
    if _digest(raw) != expected.lower():
        raise InputError("bank report differs from the supplied SHA-256")
    s = _checked_statement(raw)
    if case_id and source_report is None:
        raise InputError("--case requires the private --source-report")
    result = {"bank_report_sha256": _digest(raw), "verified_sources": []}
    for name, file in (sources or {}).items():
        if file is not None:
            if _digest_file(file) != s["source_sha256"][name]:
                raise InputError(f"{name} export differs from the frozen digest")
            result["verified_sources"].append(name)
    if source_report is not None:
        evidence = source_report.read_bytes()
        if _digest(evidence) != s["source_report_sha256"]:
            raise InputError("private report differs from the frozen source revision")
        if _html(_statement(evidence, s["declared_by_issuer"], s["schema"])) != raw:
            raise InputError("replayed bank statement differs from the supplied report")
        state, _ = _replay_state(evidence, s["schema"])
        _check_originals(state, sources or {})
        result["replayed_week"] = s["week"]
        if case_id:
            records = complaints.reconcile(state)
            selected = next(
                (c for c in records["cases"] if c["case_id"] == case_id), None
            )
            if selected is None:
                raise InputError("case is outside the issued sample")
            audit = weekly_report.summarize(state)["score_audit"]
            scored = next(c for c in audit["cases"] if c["case_id"] == case_id)
            result["case"] = {"qa": scored, "complaint": selected}
    return result


def register_arguments(commands):
    parser = commands.add_parser(
        "bank-report", help="export or verify a bank-facing complaints statement"
    )
    sub = parser.add_subparsers(dest="bank_action", required=True)
    issue = sub.add_parser(
        "export", help="create a statement from a verified private score audit"
    )
    issue.add_argument("report", type=Path)
    issue.add_argument("--out", type=Path, required=True)
    for flag in ("queue", "sample-owner", "register-owner", "excluded-queues"):
        issue.add_argument(
            "--" + flag, required=True, help="recipient-approved display text"
        )
    check = sub.add_parser(
        "verify",
        help="check an independently received digest and replay bank-held evidence",
    )
    check.add_argument("statement", type=Path)
    check.add_argument("--sha256", required=True)
    check.add_argument("--source-report", type=Path)
    check.add_argument("--case", dest="case_id")
    for name in ("register", "actions", "scores"):
        check.add_argument("--" + name, type=Path)


def execute(args):
    if args.bank_action == "export":
        digest = write(
            args.report,
            args.out,
            queue=args.queue,
            sample_owner=args.sample_owner,
            register_owner=args.register_owner,
            excluded_queues=args.excluded_queues,
        )
        print(canonical({"report": str(args.out / "report.html"), "sha256": digest}))
    else:
        print(
            canonical(
                verify(
                    args.statement,
                    args.sha256,
                    source_report=args.source_report,
                    case_id=args.case_id,
                    sources={
                        k: getattr(args, k) for k in ("register", "actions", "scores")
                    },
                )
            )
        )
    return 0
