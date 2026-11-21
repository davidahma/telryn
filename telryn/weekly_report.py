"""Publish and update a private weekly report and its blinded grading packet."""

from __future__ import annotations

import csv
import hmac
import io
import json
import os
import secrets
import tempfile
from html import escape
from pathlib import Path

from . import audit_trust, complaints, control, weekly_review
from .weekly_review import review_status
from .artifacts import new_directory, sync_directory
from .contracts import InputError, canonical, fingerprint, versioned
from .grades import render_cell
from .planning import decision_lines
from .report import STYLE, stat
from .weekly import prepare, summarize, validate_state

STATE_OPEN = '<script type="application/json" id="telryn-weekly-evidence">'
FIXED = (
    "audit_id",
    "row_id",
    "case_id",
    "revision",
    "evidence_ref",
    "rubric",
    "rubric_question",
)
ANSWERS = ("serious", "disposition", "owner", "action", "minutes")
COLUMNS = FIXED + ANSWERS


def _columns(state: dict) -> tuple:
    evidence = ("completed_at",) if "coverage_sha256" in state else ()
    rubric = ("category_options",) if "control" in state else ()
    guidance = (
        ("policy_applicability", "policy_sources")
        if state.get("pack_packet_version") == 2
        else ()
    )
    dimensions = control.FIELDS if "control" in state else ()
    return FIXED + evidence + rubric + guidance + ANSWERS + dimensions


def _audit_id(state: dict) -> str:
    frozen = {
        key: value
        for key, value in state.items()
        if key
        not in (
            "grades",
            "earlier_grades",
            "case_reviews",
            "complaint_records",
            "pack_reviews",
        )
    }
    return "W-" + fingerprint(frozen)


def _packet(state: dict) -> dict:
    audit_id = _audit_id(state)
    rows = {}
    for case in state["cases"]:
        row_id = "Q-" + fingerprint([audit_id, case["case_id"]])
        fields = {
            "audit_id": audit_id,
            "row_id": row_id,
            "case_id": case["case_id"],
            "revision": case["revision"],
            "evidence_ref": case["evidence_ref"],
            "rubric": state["rubric"],
            "rubric_question": state["rubric_question"],
        }
        if "completed_at" in case:
            fields["completed_at"] = case["completed_at"]
        if "control" in state:
            fields["category_options"] = json.dumps(
                state["control"]["categories"], ensure_ascii=False
            )
        if state.get("pack_packet_version") == 2:
            pack = state["policy_pack"]
            fields["policy_applicability"] = pack["applicability"]
            fields["policy_sources"] = json.dumps(pack["sources"], ensure_ascii=False)
        rows[row_id] = (
            case["case_id"],
            {key: render_cell(value) for key, value in fields.items()},
        )
    return rows


def _estimate(result: dict, label: str = "serious failures") -> str:
    if (
        result.get("range_kind") == "incomplete"
        and result.get("estimate_range") is not None
    ):
        lo, hi = result["estimate_range"]
        return f"Estimated {label} outside ordinary QA · {lo:,.1f} to {hi:,.1f}"
    if result["estimate"] is None:
        return "Weekly estimate unavailable"
    count = f"{result['estimate']:,.1f}".removesuffix(".0")
    return f"Estimated {label} outside ordinary QA · {count}"


def _uncertainty(result: dict, label: str = "serious failures") -> str:
    if result["interval"] is None:
        return result["estimate_reason"]
    lower, upper = result["interval"]
    if result.get("range_kind") == "incomplete":
        return f"Unknown grades could change the result. Including sampling uncertainty, the 95% range is {lower:,} to {upper:,} {label}."

    if result["graded"] == result["unreviewed"]:
        return f"All {result['unreviewed']:,} conversations outside ordinary QA have a determinate grade."
    return f"The 95% sampling interval is {lower:,} to {upper:,} {label}."


def _new_finding_line(result: dict) -> str:
    outcome = result["new_findings"]
    low, high = outcome["interval"]
    if outcome["range_kind"] == "uncovered":
        return (
            "A group received no reviews. Including every possible outcome for that group, "
            f"the 95% range for findings new to the team is {low:,} to {high:,}."
        )
    if outcome["estimate"] is None:
        best, worst = outcome["estimate_range"]
        return (
            f"Estimated findings new to the team · {best:,.1f} to {worst:,.1f} depending on unresolved "
            f"grades and prior handling. Including sampling uncertainty, the 95% range is {low:,} to {high:,}."
        )
    point = f"{outcome['estimate']:,.1f}".removesuffix(".0")
    return f"Estimated findings new to the team · {point}. The 95% range is {low:,} to {high:,}."


def _screen_bounds(metric: dict) -> str:
    interval = metric["interval"]
    if interval is None:
        return "undefined (zero denominator)"
    bounds = f"{interval[0]:.1%}–{interval[1]:.1%}"
    if metric["denominator_status"] == "possibly_zero":
        bounds += " if any positives exist"
    return bounds


def _screen_table(metrics: dict | None) -> str:
    if metrics is None:
        return "<p>This audit used no screen.</p>"
    rows = []
    for key, label in (
        ("precision", "Flagged cases meeting the rubric (precision)"),
        ("recall", "Positive cases the screen flagged (recall)"),
        ("agreement", "Screen agrees with human grade"),
    ):
        metric = metrics[key]
        point = metric["estimate"]
        low, high = metric["denominator"]
        denominator = f"{low:,}" if low == high else f"{low:,}–{high:,}"
        point_text = "Unavailable" if point is None else f"{point:.1%}"
        rows.append(
            f"<tr><th>{label}</th><td>{point_text}</td><td>{_screen_bounds(metric)}</td><td>{denominator}</td></tr>"
        )
    return (
        '<div class="table" tabindex="0"><table><thead><tr><th>Measure</th><th>Weighted estimate</th>'
        "<th>95% range</th><th>Denominator (cases)</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div><p>These ranges describe this week's conversations outside ordinary QA. "
        "Recall uses the estimated number of positive cases as its denominator. "
        "Unknown grades and unsampled groups can widen the ranges.</p>"
    )


def _digest_lines(state: dict, result: dict) -> list[str]:
    # Only controlled labels may leave the private report through the aggregate email.
    if statement := result.get("control_statement"):
        channels = state["control"]["channels"]
        excluded = sum(c["coverage"] != "covered" for c in channels)
        title = (
            "Synthetic complaints rehearsal"
            if state["control"].get("synthetic")
            else "Telryn weekly complaints control"
        )
        return [
            f"{title} · {state['week']}",
            *control.statement_lines(statement),
            f"{len(statement['unresolved'])} conversations need follow-up. {excluded} channels have partial or missing coverage.",
            "Owners, review dates and evidence are in the signed local report.",
        ]
    demo = state.get("finding_label") == "conversations with friction"
    label = "conversations with friction" if demo else "serious failures"
    confirmed = "confirmed friction labels" if demo else "confirmed serious"
    workload = weekly_review.workload(state)
    lines = [
        f"Telryn weekly support QA · {state['week']}",
        _estimate(result, label),
        _uncertainty(result, label),
        _new_finding_line(result),
        f"{result['confirmed_serious']} {confirmed} in the sample. {result['new']} newly found, "
        f"{result['already_known']} already known, {result['unknown']} with prior handling unknown.",
        f"{result['graded']} of {result['assigned']} assignments have a determinate grade. "
        f"{workload['primary_outstanding']} await a first return. {workload['insufficient_undisputed']} returned "
        f"with insufficient evidence. {workload['open_disputes']} need adjudication. {result['review_minutes']} review minutes recorded.",
        f"Ordinary QA reviewed {result['reviewed']} of {result['population']} eligible conversations.",
    ]
    if metrics := result["screen_metrics"]:
        lines.append(
            f"The 95% screen ranges for this audit are precision {_screen_bounds(metrics['precision'])}, "
            f"recall {_screen_bounds(metrics['recall'])}, agreement {_screen_bounds(metrics['agreement'])}."
        )
    lines.extend(_coverage_lines(state))
    reviews = review_status(state)
    if reviews["paired"]:
        lines.append(
            f"Independent reviewers agreed on severity for {reviews['agreed']} of {reviews['compared']} definite pairs. {reviews['open']} disputes need adjudication."
        )
    if demo:
        lines.append(
            "Public demonstration using archived human labels and simulated ordinary reviews."
        )
    return lines


def digest(report_path: Path, *, allow_legacy=False) -> str:
    if allow_legacy and TRAILER not in (raw := report_path.read_bytes()):
        state = _legacy(raw)
        revision = signature = None
    else:
        state, revision, signature = verified_snapshot(report_path)
    lines = _digest_lines(state, summarize(state))
    if state.get("pack_reviews") or state.get("complaint_records", {}).get(
        "policy_reviews"
    ):
        lines.append("Policy re-review required. Open the report for affected clauses.")
    if "control" in state:
        lines.append(f"Signed report revision {revision}, HMAC {signature}.")
    if "draw_commitment" not in state:
        lines.insert(0, "Historical unsigned weekly report · provenance unverified")
    return "\n".join(lines) + "\n"


def _evidence_follow_up(state: dict) -> str:
    items = weekly_review.follow_up(state)
    if not items:
        return ""
    rows = []
    for item in items:
        cells = (
            item["case_id"],
            item["evidence_ref"],
            item["revision"],
            item.get("completed_at", "—"),
            "Insufficient evidence"
            if item["reason"] == "insufficient_evidence"
            else "Prior handling unknown",
            item["primary_reviewer"] or "Unrecorded",
            "Original reviewer amends primary grade"
            if item["route"] == "primary_amendment"
            else "New independent review",
        )
        rows.append("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in cells) + "</tr>")
    return (
        f"<details><summary>Cases needing follow-up ({len(items)})</summary>"
        '<div class="table" tabindex="0"><table><thead><tr><th>Case</th><th>Evidence reference</th>'
        "<th>Revision</th><th>Completion (UTC)</th><th>Reason</th><th>First reviewer</th><th>Next review</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div></details>"
    )


def _review_section(state: dict) -> str:
    status = review_status(state)
    if not status["paired"]:
        return "<section><h2>Independent review</h2><p>No second grades recorded.</p></section>"
    rows = []
    for case, record in state["case_reviews"].items():
        for check in record["checks"]:

            def vote(grade):
                label = {True: "Yes", False: "No", None: "Insufficient"}[
                    grade["serious"]
                ]
                summary = f"{label} ({grade['disposition']})"
                if "category" in grade:
                    summary += f", category {grade['category']}, vulnerability {grade['vulnerability']}, redress {grade['redress']}. Basis: {grade['classification_reason']}"
                return summary

            result = "Agreement"
            if check.get("decision"):
                d = check["decision"]
                result = f"{d['reviewer']} accepted the {d['choice']} answer: {d['reason']} ({d['minutes']} minutes, {d['at']})"
            elif check["disputed"]:
                result = "Open dispute " + check["id"]
            if check.get("downgrade_requested"):
                result = "Category downgrade requested. " + result
            values = (
                case,
                f"{check['primary_by']}: {vote(check['primary'])}",
                f"{check['second_by']}: {vote(check['second'])}",
                result,
            )
            rows.append(
                "<tr>"
                + "".join(f"<td>{escape(value)}</td>" for value in values)
                + "</tr>"
            )
    return (
        "<section><h2>Independent review</h2>"
        f"<p>First and second reviewers agreed on severity for {status['agreed']} of {status['compared']} determinate pairs. "
        f"{status['paired']} cases received a second grade. {status['open']} disputes await adjudication.</p>"
        '<div class="table" tabindex="0"><table><thead><tr><th>Case</th><th>First grade</th><th>Second grade</th><th>Decision</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div></section>"
    )


def _coverage_lines(state: dict) -> list[str]:
    cov = state.get("coverage")
    if not cov:
        return [
            "This audit describes the supplied population. Source exclusions and still-open counts were not supplied."
        ]
    return [
        f"Source basis: {cov['basis']}. Scanned {cov['scanned']:,} records. "
        f"{cov['eligible']:,} eligible, {cov['open']:,} excluded as open, "
        f"{cov['outside']:,} outside the interval and {cov['unknown']:,} with unknown completion.",
        f"At export, {cov['still_open']:,} scanned records were still open.",
    ]


def _report_schema(state):
    records = state.get("complaint_records", {})
    snapshot = records.get("snapshot", {})
    if any("source_citation" in case for case in snapshot.get("cases", {}).values()):
        return 5
    issued_policy = snapshot.get("episodes", {}).get("policy", {})
    if (
        "policy_pack" in state
        or "business_weekdays" in issued_policy
        or any("pack_digest" in n for n in records.get("policy_reviews", []))
    ):
        return 4
    if (
        "control" in state
        or "episodes" in records.get("snapshot", {})
        or records.get("policy_reviews")
    ):
        return 3
    return 2


def _render(state: dict, auth: dict) -> str:
    result = summarize(state)
    label = state.get("finding_label", "serious failures")
    lead = f"<h1>{escape(_estimate(result, label))}</h1>"
    control_detail = ""
    if statement := result.get("control_statement"):
        lead, control_detail = control.html(state, statement)
    grade_help = ""
    if "control" in state:
        grade_help = "<p>Use the firm’s ordered category list in <code>category_options</code>. Give a classification reason, a vulnerability answer (yes, no or unknown) and redress status (none, pending, offered, completed or unknown). A lower category requires independent review and a recorded decision.</p>"
    registration = complaints.html(state)
    if "control" in state and not registration:
        registration = "<section><h2>Registration evidence pending</h2><p>Attach a dated register and work record with the weekly command and <code>--complaint-records</code>.</p></section>"
    scope = f"<p>{escape(state['scope_note'])}</p>" if state.get("scope_note") else ""
    if pack := result.get("policy_pack"):
        scope += (
            f"<p>Policy and rubric pack <strong>{escape(pack['reference'])}</strong>. "
            f"{escape(pack['applicability'])}</p><p>Policy sources: "
            + "; ".join(escape(source) for source in pack["sources"])
            + f"</p><p>Pack SHA-256: <code>{pack['sha256']}</code></p>"
        )
        for notice in state.get("pack_reviews", []):
            changed = ", ".join(notice["changed_pack_fields"])
            scope += f"<p>Review these grades against pack {escape(notice['review_version'])}. Changed fields: {escape(changed)}.</p>"
    scope += "".join(f"<p>{escape(line)}</p>" for line in _coverage_lines(state))
    if cov := state.get("coverage"):
        scope += f"<p>Source scope: {escape(cov['scope'])}. UTC interval [{escape(cov['since'])}, {escape(cov['until'])}).</p>"
    cover = "".join(
        (
            stat("Human-confirmed findings", str(result["confirmed_serious"])),
            stat("Newly found", str(result["new"])),
            stat("Already known to another control", str(result["already_known"])),
            stat("Prior handling unknown", str(result["unknown"])),
        )
    )
    reasons = {
        "new": "Newly found",
        "already_known": "Already known",
        "unknown": "Prior handling unknown",
    }
    actions = []
    grades = weekly_review.effective_grades(state)
    for case in state["cases"]:
        grade = grades.get(case["case_id"])
        if grade is None or grade["serious"] is not True:
            continue
        cells = (
            case["case_id"],
            case["evidence_ref"],
            ("Flagged" if case["screen_flag"] else "Unflagged")
            if state["screen_version"] != "none"
            else "No screen",
            reasons[grade["disposition"]],
            grade["owner"] or "—",
            grade["action"] or "—",
        )
        actions.append(
            "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in cells) + "</tr>"
        )
    follow_up = (
        '<div class="table" tabindex="0"><table><thead><tr><th>Case</th><th>Evidence reference</th>'
        "<th>Frozen screen</th><th>Finding</th><th>Owner</th><th>Action or work item</th></tr></thead><tbody>"
        + "".join(actions)
        + "</tbody></table></div>"
        if actions
        else "<p>No human-confirmed findings so far.</p>"
    )
    sample_rows = []
    for key, group in state["strata"].items():
        stratum_label = escape(group.get("label", key.title()))
        total, sampled = group["population"], group["sample"]
        chance = f"{sampled / total:.2%}" if total else "—"
        observed = result["stratum_results"][key]
        bounds = observed["interval"]
        interval = (
            f"{bounds[0]:,}–{bounds[1]:,}" if bounds is not None else "Unavailable"
        )
        sample_rows.append(
            f"<tr><th>{stratum_label}</th><td>{total:,}</td><td>{sampled:,}</td><td>{chance}</td>"
            f"<td>{observed['confirmed']:,}</td><td>{sampled - observed['graded']:,}</td><td>{interval}</td></tr>"
        )
    payload = canonical({"schema": _report_schema(state), "auth": auth, "state": state})
    payload = (
        payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    workload = weekly_review.workload(state)
    grade_text = (
        f"{result['graded']} of {result['assigned']} assignments have a determinate grade. "
        f"{workload['primary_outstanding']} await a first return. "
        f"{workload['insufficient_undisputed']} returned with insufficient evidence. "
        f"{workload['open_disputes']} need adjudication. "
        f"Graders recorded {result['review_minutes']} minutes ({result['review_minutes'] / 60:.2f} hours)."
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'\">"
        f"<title>Weekly support QA · {escape(state['week'])} · Telryn</title><style>{STYLE}</style>"
        "</head><body><main><header>"
        f'<p class="eyebrow">Weekly support QA · {escape(state["week"])}</p>{lead}'
        f"<p><strong>{escape(_uncertainty(result, label))}</strong></p><p>{escape(state['rubric_question'])}</p>"
        f"<p>Ordinary QA reviewed {result['reviewed']:,} of {result['population']:,} eligible conversations. "
        f"This audit covers the other {result['unreviewed']:,}, including cases another control already knew about.</p>{scope}</header>"
        f'<div id="control-register">{registration}</div>{control_detail}'
        f'<section><h2>Confirmed cases</h2><div class="grid">{cover}</div>'
        f"<p>{_new_finding_line(result)}</p>{follow_up}</section>"
        f"<section><h2>Grading handoff</h2><p>{grade_text}</p>{_evidence_follow_up(state)}{grade_help}"
        "<p>Give the reviewer <code>grades.csv</code>. Keep this lead report private so the reviewer can grade each case "
        "without seeing its screen flag. Review the listed evidence revision against the question in the packet. "
        "When the packet includes <code>completed_at</code>, use the evidence available at that UTC cutoff. "
        "If only a later revision is available, return <code>insufficient</code>.</p>"
        "<p>Enter <code>yes</code>, <code>no</code> or <code>insufficient</code> under <code>serious</code>. "
        "For a serious finding, enter <code>new</code>, <code>already_known</code> or <code>unknown</code> under "
        "<code>disposition</code>. Record an owner and action for each new finding. For other answers, use "
        "<code>none</code> for no and <code>unknown</code> for insufficient. Record total review minutes for the row. "
        "Leave unanswered rows blank. A partial return keeps all other rows open. To resolve an insufficient grade or unknown prior handling, use the same row and include all minutes spent on it.</p>"
        "<p>Import the return with <code>telryn weekly report.html --grades returned.csv</code>. "
        "For the next primary reviewer, export the unanswered rows with "
        "<code>telryn weekly report.html --outstanding remaining.csv</code>.</p></section>"
        '<section id="control-sample"><h2>Sampling and screen performance</h2><p>Cases were drawn without replacement within each group. '
        "Population estimates weight each grade by the inverse of its selection probability.</p>"
        + "".join(
            f"<p>{escape(line)}</p>"
            for line in decision_lines(state.get("planning", {}))
        )
        + '<div class="table" tabindex="0"><table><thead><tr><th>Sampling group</th><th>Outside ordinary QA</th>'
        "<th>Assigned</th><th>Selection probability</th><th>Confirmed findings</th>"
        "<th>Awaiting a definite grade</th><th>95% range in group</th></tr></thead><tbody>"
        + "".join(sample_rows)
        + "</tbody></table></div>"
        f"<h3>Screen version · {escape(state['screen_version'])}</h3>{_screen_table(result['screen_metrics'])}</section>"
        f"{_review_section(state)}"
        f"<section><h2>Random sample (verifiable)</h2><p>Draw <code>{state['draw_commitment']['draw_id']}</code> "
        f"was recorded in the team's draw registry before assignment. Use <code>telryn trust draws</code> to compare designs for this week.</p></section>"
        f"<footer><p>Signed weekly report · revision {auth['revision']} · team key <code>{auth['key_id']}</code>. Verify this file in the original Telryn trust store before relying on its contents.</p><p>Rubric {escape(state['rubric'])}</p><p>Audit <code>{_audit_id(state)}</code></p></footer></main>"
        + STATE_OPEN
        + payload
        + "</script></body></html>\n"
    )


TRAILER = b"<!-- telryn-weekly-hmac-sha256:"


def _envelope(raw: bytes) -> dict:
    _, marker, tail = raw.partition(STATE_OPEN.encode())
    text, closing, _ = tail.partition(b"</script>")
    if not marker or not closing:
        raise InputError("this file has no Telryn weekly audit snapshot")
    envelope = json.loads(text)
    if not isinstance(envelope, dict):
        raise InputError("invalid weekly report envelope")
    return envelope


def _signed(state, key, revision, previous):
    auth = {
        "registry_first": True,
        "key_id": audit_trust.key_id(key),
        "revision": revision,
        "previous": previous,
    }
    data = _render(state, auth).encode("utf-8")
    signature = audit_trust.mac(key, "weekly-html-v2", data)
    return data + TRAILER + signature.encode() + b" -->\n", signature


def _verify_signed(raw, db, key):
    data, marker, tail = raw.rpartition(TRAILER)
    if not marker or len(tail) != 69 or not tail.endswith(b" -->\n"):
        raise InputError("weekly report signature missing or malformed")
    signature = tail[:-5]
    actual = audit_trust.mac(key, "weekly-html-v2", data)
    if not hmac.compare_digest(actual.encode(), signature):
        raise InputError(
            "weekly report signature does not match the team's local key or report bytes"
        )
    envelope = _envelope(data)
    if type(envelope.get("schema")) is not int or envelope["schema"] not in (
        2,
        3,
        4,
        5,
    ):
        raise InputError("unrecognized signed weekly report version")
    state = versioned(envelope.get("state"), "weekly evidence")
    auth = envelope["auth"]
    if auth["key_id"] != audit_trust.key_id(key):
        raise InputError("weekly report belongs to another team key")
    audit_trust.check_draw(db, key, state)
    ident = state["publication_id"]
    revision = auth["revision"]
    head = db.execute(
        "SELECT revision,signature FROM reports WHERE id=?", (ident,)
    ).fetchone()
    if head != (revision, actual):
        # Initial publication has no predecessor. Pre-v0.6 signed updates used
        # file-first publication and can still reconcile their immediate successor.
        if revision == 1 and auth["previous"] is None and head is None:
            db.execute("INSERT INTO reports VALUES(?,?,?)", (ident, revision, actual))
        elif (
            not auth.get("registry_first")
            and head
            and revision == head[0] + 1
            and auth["previous"] == head[1]
        ):
            db.execute(
                "UPDATE reports SET revision=?,signature=? WHERE id=?",
                (revision, actual, ident),
            )
        else:
            raise InputError(
                "stale or diverging weekly report; open the latest signed revision"
            )
    validate_state(state)
    audit_trust.bind_policy_versions(db, state)
    return state, revision, actual


def _legacy(raw):
    envelope = versioned(_envelope(raw), "historical weekly report")
    state = versioned(envelope.get("state"), "weekly evidence")
    if (
        "draw_commitment" in state
        or "publication_id" in state
        or "policy_pack" in state
        or "control" in state
        or "complaint_records" in state
        or fingerprint(state) != envelope.get("checksum")
    ):
        raise InputError("invalid historical weekly report or checksum")
    validate_state(state)
    return state


def read_state(report_path: Path, *, allow_legacy=False) -> dict:
    if allow_legacy:
        raw = report_path.read_bytes()
        if TRAILER not in raw:
            return _legacy(raw)
    return verified_snapshot(report_path)[0]


def verified_snapshot(report_path: Path) -> tuple[dict, int, str]:
    """Return the authenticated state, revision and signature for a dependent artifact."""
    with audit_trust.registry() as (db, key):
        audit_trust.recover_report(db, report_path)
        raw = report_path.read_bytes()
        if TRAILER not in raw:
            raise InputError(
                "unsigned weekly report; use --legacy for an explicitly unverified historical read"
            )
        return _verify_signed(raw, db, key)


def verified_html(path: Path) -> bytes:
    with audit_trust.registry() as (db, key):
        audit_trust.recover_report(db, path)
        raw = path.read_bytes()
        _verify_signed(raw, db, key)
    return raw


def _packet_bytes(state: dict, *, outstanding: bool = False) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_columns(state))
    writer.writeheader()
    for _, (case, fields) in sorted(_packet(state).items()):
        if not outstanding or case not in state["grades"]:
            writer.writerow(fields)
    return stream.getvalue().encode("utf-8-sig")


def verified_packet(path: Path, *, outstanding: bool = False) -> bytes:
    return _packet_bytes(read_state(path), outstanding=outstanding)


def export_outstanding(report: Path, target: Path) -> dict:
    """Issue the unanswered rows from a verified snapshot, preserving issued row IDs."""
    state, revision, _ = verified_snapshot(report)
    data = _packet_bytes(state, outstanding=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".weekly-packet-"
    ) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        os.link(stream.name, target)
        sync_directory(target.parent)
    return {
        "path": str(target),
        "publication_id": state["publication_id"],
        "revision": revision,
        "primary_outstanding": len(state["cases"]) - len(state["grades"]),
    }


def start(plan_path: Path, out_dir: Path, *, delivery_date: str | None = None) -> Path:
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f"output directory already exists: {out_dir}")
    state = prepare(plan_path, delivery_date=delivery_date)
    state["publication_id"] = secrets.token_hex(16)
    with new_directory(out_dir) as staging:
        # Bind the policy version before exposing the signed report and packet.
        with audit_trust.registry() as (db, key):
            audit_trust.check_draw(db, key, state)
            audit_trust.bind_policy_versions(db, state)
            data, _ = _signed(state, key, 1, None)
            (staging / "report.html").write_bytes(data)
            (staging / "grades.csv").write_bytes(_packet_bytes(state))
    report = out_dir / "report.html"
    # A failed directory publication has no report head. If the process stops
    # after rename, the first verified read registers the signed initial revision.
    verified_snapshot(report)
    return report


def _answer(row: dict, state: dict) -> dict | None:
    serious, disposition, owner, action, minutes = (
        row[field].strip() for field in ANSWERS
    )
    dimensions = (
        {key: row[key].strip() for key in control.FIELDS} if "control" in state else {}
    )
    if not any((serious, disposition, owner, action, minutes, *dimensions.values())):
        return None
    choices = {"yes": True, "no": False, "insufficient": None}
    if (
        serious.lower() not in choices
        or not minutes.isascii()
        or not minutes.isdecimal()
    ):
        raise InputError(
            "a weekly grade needs yes, no or insufficient and nonnegative whole minutes"
        )
    return {
        **dimensions,
        "serious": choices[serious.lower()],
        "disposition": disposition.lower(),
        "owner": owner,
        "action": action,
        "minutes": int(minutes),
    }


def _import(path: Path, state: dict, reviewer: str, record) -> bool:
    expected = _packet(state)
    seen = set()
    changed = False
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if tuple(reader.fieldnames or ()) != _columns(state):
            raise InputError(
                "returned CSV columns differ from the weekly grading packet"
            )
        for row in reader:
            row_id = row["row_id"]
            if (
                None in row
                or None in row.values()
                or row_id not in expected
                or row_id in seen
            ):
                raise InputError(
                    f"malformed, duplicate or unknown row at CSV line {reader.line_num}"
                )
            seen.add(row_id)
            case_id, fields = expected[row_id]
            if any(row[key] != value for key, value in fields.items()):
                raise InputError(
                    f"row {row_id} no longer matches its frozen audit or evidence"
                )
            grade = _answer(row, state)
            if grade is not None and record(state, case_id, grade, reviewer):
                changed = True
    if not seen:
        raise InputError("returned CSV has no rows to bind to the weekly audit")
    return changed


def _update(report_path: Path, change) -> dict:
    report_path = report_path.resolve(strict=True)
    # Registry transactions also serialize updates made through different copies.
    with audit_trust.registry() as (db, key):
        audit_trust.recover_report(db, report_path)
        state, revision, previous = _verify_signed(report_path.read_bytes(), db, key)
        if change(state):
            validate_state(state)
            audit_trust.bind_policy_versions(db, state)
            data, signature = _signed(state, key, revision + 1, previous)
            db.execute(
                "UPDATE reports SET revision=?,signature=? WHERE id=?",
                (revision + 1, signature, state["publication_id"]),
            )
            audit_trust.replace_report(db, report_path, data)
        return state


def attach_complaints(report_path: Path, records_path: Path) -> dict:
    return _update(
        report_path,
        lambda state: complaints.attach(state, records_path, _audit_id(state)),
    )


def refresh(report_path: Path, grades_path: Path, reviewer=None) -> dict:
    actor = weekly_review.identity(reviewer, default=True)
    return _update(
        report_path,
        lambda state: _import(grades_path, state, actor, weekly_review.record_primary),
    )


def second_review(report_path: Path, grades_path: Path, reviewer: str) -> dict:
    actor = weekly_review.identity(reviewer)
    return _update(
        report_path,
        lambda state: _import(grades_path, state, actor, weekly_review.record_second),
    )


def adjudicate(
    report_path: Path,
    case_id: str,
    dispute_id: str,
    reviewer: str,
    choice: str,
    reason: str,
    minutes: int,
) -> dict:
    actor = weekly_review.identity(reviewer)
    return _update(
        report_path,
        lambda state: weekly_review.decide(
            state, case_id, dispute_id, actor, choice, reason, minutes
        ),
    )


def register_review_arguments(parser, actions):
    actions.add_argument(
        "--second-review",
        type=Path,
        help="import an independently completed copy of the original packet",
    )
    actions.add_argument(
        "--adjudicate",
        metavar="CASE",
        help="resolve an independent weekly review dispute",
    )
    parser.add_argument(
        "--reviewer",
        help="operator-recorded identity of the person grading or adjudicating",
    )
    parser.add_argument("--dispute", help="dispute ID shown in the verified report")
    parser.add_argument(
        "--accept",
        choices=("primary", "second"),
        help="which independent answer to accept",
    )
    parser.add_argument("--reason", help="reason for the adjudication")
    parser.add_argument(
        "--review-minutes", type=int, help="additional minutes spent adjudicating"
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="read a historical unsigned weekly report with provenance unverified",
    )


def review_action(args):
    if args.legacy and any(
        (
            args.out,
            args.grades,
            args.second_review,
            args.adjudicate,
            args.email_to,
            args.propose,
            args.outstanding,
            args.complaint_records,
        )
    ):
        raise InputError(
            "--legacy is available for a read-only historical summary or digest"
        )
    if args.reviewer is not None and not any(
        (args.grades, args.adjudicate, args.second_review)
    ):
        raise InputError(
            "--reviewer applies only when recording a grade or an adjudication"
        )
    fields = (args.dispute, args.accept, args.reason, args.review_minutes)
    if args.adjudicate is None and any(item is not None for item in fields):
        raise InputError("dispute resolution options require --adjudicate")
    if args.second_review is not None:
        return second_review(args.source, args.second_review, args.reviewer)
    if args.adjudicate is not None:
        if any(item is None for item in fields):
            raise InputError(
                "supply --dispute, --accept, --reason and --review-minutes to adjudicate"
            )
        return adjudicate(
            args.source,
            args.adjudicate,
            args.dispute,
            args.reviewer,
            args.accept,
            args.reason,
            args.review_minutes,
        )
    return None
