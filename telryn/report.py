"""Render a portable lead report with a private, checksummed decision snapshot."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from . import shadow
from .contracts import InputError, canonical, fingerprint, mapping, versioned
from .decision import INCONCLUSIVE, evaluate
from .grades import grade_status, record_effort, review_set

STATE_OPEN = '<script type="application/json" id="telryn-evidence">'
STATE_CLOSE = "</script>"

STYLE = """
:root { color-scheme: light; --ink:#14263b; --muted:#475a71; --rule:#dbe4ed; --accent:#0c5e54;
  font: 16px/1.6 system-ui,sans-serif; background:#f1f5f9; color:var(--ink); }
* { box-sizing:border-box; }
body { margin:0; }
main { max-width:72rem; padding:2rem 1.25rem 4rem; margin:auto; }
header, section, footer { background:#fff; border:1px solid var(--rule); border-radius:.9rem;
  padding:clamp(1rem,3vw,2.25rem); margin-block:1rem; }
header { border-top:5px solid var(--accent); }
header.hold { --accent:#9e3030; } header.inconclusive { --accent:#79510a; }
h1,h2,h3 { line-height:1.2; margin:0 0 .65rem; }
h1 { font-size:clamp(2rem,5vw,3.1rem); color:var(--accent); }
h2 { font-size:1.45rem; } h3 { font-size:1.05rem; }
p { margin:.55rem 0; }
.eyebrow, .muted, small { color:var(--muted); }
.eyebrow { font-weight:650; }
.grid { display:grid; gap:1rem; grid-template-columns:repeat(auto-fit,minmax(min(12rem,100%),1fr)); }
.stat { background:#f2f6fa; border-radius:.6rem; padding:.85rem 1rem; }
.stat strong { display:block; font-size:1.25rem; }
ul { padding-left:1.25rem; margin:.5rem 0; }
.cases { display:flex; flex-wrap:wrap; list-style:none; padding:0; gap:.4rem; }
.cases li { padding:.12rem .48rem; background:#ecf2f7; border-radius:.22rem; font: .88rem/1.6 monospace; }
details { border-top:1px solid var(--rule); padding-top:.7rem; margin-top:1rem; }
summary { font-weight:650; cursor:pointer; }
.table { overflow-x:auto; }
table { border-collapse:collapse; width:100%; text-align:left; font-size:.9rem; }
th,td { padding:.45rem .55rem; border-bottom:1px solid var(--rule); vertical-align:top; }
code { overflow-wrap:anywhere; }
:focus-visible { outline:3px solid #0d6a9c; outline-offset:3px; }
"""


def recommendation(state: dict) -> dict:
    grades, serious, pending, status = grade_status(state)
    summary = evaluate(state, grades, pending, serious)
    legacy = state["schema"] == 1
    if legacy:
        if not summary["operational_veto"]:
            summary["recommendation"] = INCONCLUSIVE
            summary["vetoes"] = []
        summary["open_items"].insert(
            0,
            "This v0.1.0 snapshot cannot authorize shadow. Re-run compare with the current version.",
        )
    summary.update(
        {
            "comparison_id": state["comparison_id"],
            "schema": 1,
            "legacy_report": legacy,
            "shadow_trials": shadow.summarize(state),
            "excluded_grading": state.get("excluded_grading", {}),
            "adjudication": {
                row_id: {
                    "request": state["packet"][row_id]["case"],
                    "criterion": state["packet"][row_id].get("criterion", "primary"),
                    "review_set": review_set(row_id, state["grade_history"][row_id]),
                }
                for row_id, value in status.items()
                if not legacy and value == "disputed"
            },
            "packet_rows": len(state["packet"]),
            "effort": record_effort(state),
            "packet_returns": sum(
                value != "awaiting return" for value in status.values()
            ),
            "reviewer_unblinded": state.get("reviewer_unblinded", False),
        }
    )
    return summary


def load_report(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    _, opening, tail = source.partition(STATE_OPEN)
    raw, closing, _ = tail.partition(STATE_CLOSE)
    if not opening or not closing:
        raise InputError("this file has no Telryn decision snapshot")
    envelope = versioned(json.loads(raw), "report")
    state = mapping(envelope.get("state"), "report state")
    if type(state.get("schema")) is not int or state["schema"] not in (1, 2):
        raise InputError("unsupported report state schema")
    if fingerprint(state) != envelope.get("checksum"):
        raise InputError("the embedded report snapshot is damaged or modified")
    return state


def signed_range(value: list[int] | None) -> str:
    if value is None:
        return "Coverage incomplete"
    lower, upper = value
    return f"{lower:+d} to {upper:+d}"


def time_range(time: dict) -> str:
    upper = time["upper"]
    return (
        f"{time['lower']}–{upper} min"
        if upper is not None
        else f"≥ {time['lower']} min · upper unknown"
    )


def list_items(items: list[str]) -> str:
    return "".join(f"<li>{escape(item)}</li>" for item in items)


def cases_html(ids: list[str]) -> str:
    return (
        f'<ul class="cases">{list_items(ids)}</ul>'
        if ids
        else '<p class="muted">None</p>'
    )


def stat(label: str, value: str) -> str:
    return f'<div class="stat"><small>{escape(label)}</small><strong>{escape(value)}</strong></div>'


def stopping_text(result: dict) -> str:
    interval = result["bounds"]
    if interval is None:
        return "Establish replay for every required shift before applying the combined hurdle."
    lower, upper = interval
    target = result["hurdle"]
    if lower < target <= upper:
        favorable = target - lower
        unfavorable = upper - target + 1
        return (
            f"{favorable} favorable unknown {'grade' if favorable == 1 else 'grades'} would secure the count hurdle. "
            f"{unfavorable} unfavorable unknown {'grade' if unfavorable == 1 else 'grades'} would put it out of reach. "
            "A favorable grade is an exception gaining review or a non-exception losing review. "
            "These thresholds assume the existing grades stand and the safeguards are satisfied."
        )
    return "Current grades already bound the count on one side of the hurdle."


def grade_groups(cases: list[str], grades: dict) -> str:
    if not cases:
        return '<p class="muted">None</p>'
    parts = []
    for status, label in (
        ("meets", "Confirmed exceptions"),
        ("outside", "Outside rubric"),
        (None, "Unknown grade"),
    ):
        ids = [case for case in cases if grades.get(case) == status]
        if ids:
            parts.append(
                f"<p><strong>{label} · {len(ids)}</strong></p>{cases_html(ids)}"
            )
    return "".join(parts)


def shift_html(
    name: str, shift: dict, source: dict, grades: dict, excluded: dict
) -> str:
    head = f"<section><h2>{escape(name)}</h2>"
    if shift["status"] != "Replayable":
        return (
            head
            + f"<p><strong>Not replayable.</strong> {escape(shift['reason'])}</p></section>"
        )
    time = shift["workload"]
    basis = time["basis"]
    details = []
    for case in source["candidate"]:
        row = source["rows"][case]
        detail = (
            "Unknown"
            if row["minutes"] is None
            else (f"{time_range(row['minutes'])} ({row['minutes']['basis']})")
        )
        details.append(
            f"<tr><td>{escape(case)}</td><td>{escape(row['revision'])}</td>"
            f"<td>{escape(detail)}</td></tr>"
        )
    return (
        head + f'<p class="muted">Cutoff {escape(source["cutoff"])} · Membership '
        f"{escape(source['membership_source'])} · {shift['shared']} shared</p>"
        '<div class="grid">'
        + stat("Net additional exceptions", signed_range(shift["bounds"]))
        + stat(
            "Candidate queue", f"{shift['candidate_count']} / {shift['capacity']} slots"
        )
        + stat("Projected reviewer time", time_range(time))
        + stat("Scheduled time", f"{shift['staff_minutes']} min")
        + "</div>"
        + f"<p>Original queue {shift['live_count']} · Mandatory reviews {shift['mandatory_count']} · "
        f"Mandatory misses {len(shift['mandatory_misses'])}</p>"
        + (
            f"<p>Missed mandatory reviews</p>{cases_html(shift['mandatory_misses'])}"
            if shift["mandatory_misses"]
            else ""
        )
        + f'<div class="grid"><div><h3>Gain review · {len(shift["gain"])}</h3>{grade_groups(shift["gain"], grades)}'
        + f"</div><div><h3>Lose review · {len(shift['loss'])}</h3>{grade_groups(shift['loss'], grades)}</div></div>"
        + (
            "<details><summary>Excluded grading evidence</summary><ul>"
            + list_items(
                [
                    f"{case}: {excluded[case]}"
                    for case in shift["unknown"]
                    if case in excluded
                ]
            )
            + "</ul></details>"
            if any(case in excluded for case in shift["unknown"])
            else ""
        )
        + "<details><summary>Candidate workload by request</summary>"
        + "<p>"
        + escape(
            f"{basis['observed']} observed · {basis['estimated']} estimated · "
            f"{basis['unknown']} with unknown upper time"
        )
        + "</p>"
        + '<div class="table" tabindex="0"><table><thead><tr><th>Candidate request</th><th>Revision</th>'
        "<th>Time evidence</th></tr></thead><tbody>"
        + "".join(details)
        + "</tbody></table></div>"
        "</details></section>"
    )


def render_report(state: dict) -> str:
    result = recommendation(state)
    grades, _, _, status = grade_status(state)
    frozen = state["frozen"]
    title = result["recommendation"]
    severity = {
        "Hold": "hold",
        "Inconclusive": "inconclusive",
        "Try in shadow": "shadow",
    }[title]
    notes = result["vetoes"] + result["open_items"]
    if not notes:
        notes = [
            "The historical count and all required safeguards support a shadow trial."
        ]
    packet_notes = list(state["packet_notes"])
    if result["reviewer_unblinded"]:
        packet_notes.append(
            "A reviewer knew which requests switched. This limits the blinding of these grades."
        )
    packet_details = []
    for row_id, row in state["packet"].items():
        kind = "Cross-check" if row["control"] else "Open grade"
        if row.get("criterion") == "serious":
            kind += " · Serious case"
        packet_details.append(
            f"<tr><td>{escape(row['case'])}</td><td><code>{escape(row_id)}</code></td>"
            f"<td>{kind}</td><td>{escape(status[row_id])}</td></tr>"
        )
    sections = []
    for name in state["plan"]["required_shifts"]:
        sections.append(
            shift_html(
                name,
                result["shifts"][name],
                state["shifts"][name],
                grades,
                result["excluded_grading"],
            )
        )
    disputed = result["adjudication"]
    adjudication_html = ""
    if disputed:
        commands = "".join(
            f"<li>{escape(info['request'])}<br><code>python3 -m telryn refresh REPORT "
            f"--adjudicate {escape(row)} GRADE REASON --review-set {info['review_set']}</code></li>"
            for row, info in disputed.items()
        )
        adjudication_html = (
            "<h3>Adjudicate disputed grades</h3>"
            "<p>Have an independent reviewer resolve each dispute against the frozen evidence. "
            "A regular CSV return leaves the dispute open. Replace GRADE and REASON with "
            "the decision and a quoted reason. Use the review set from the current report.</p>"
            f"<ul>{commands}</ul>"
        )
    scored = result["scored_bounds"]
    partial = (
        f"<p>Subtotal from replayable shifts {signed_range(scored)}.</p>"
        if result["bounds"] is None and scored is not None
        else ""
    )
    plan = state["plan"]
    effort_entries = []
    for label, metric in result["effort"].items():
        effort_entries.append(
            f"{label.replace('_', ' ').capitalize()}: {metric['events']} recorded, "
            f"{metric['minutes']} min logged, {metric['untimed']} untimed."
        )
    serious = result["serious_losses"]
    serious_section = ""
    if serious is not None:
        upper = str(serious["upper"]) if serious["upper"] is not None else "unknown"
        hours = sum(source["staff_minutes"] for source in plan["shifts"].values()) / 60
        serious_section = (
            '<section><h2>Serious cases losing review</h2><div class="grid">'
            + stat("Possible serious losses", f"{serious['lower']} to {upper}")
            + stat("Maximum allowed", str(serious["maximum"]))
            + stat("Scheduled reviewer hours", f"{hours:g}")
            + "</div>"
            + f"<p><strong>Serious-case question</strong> {escape(plan['serious']['rubric_question'])}</p>"
            + "<h3>Confirmed serious losses</h3>"
            + cases_html(serious["confirmed"])
            + "<h3>Unresolved serious grades</h3>"
            + cases_html(serious["unknown"])
            + "</section>"
        )
    trial_section = shadow_html(result["shadow_trials"])
    payload = canonical({"schema": 1, "checksum": fingerprint(state), "state": state})
    payload = (
        payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'\">"
        f"<title>{escape(title)} · Telryn decision report</title><style>{STYLE}</style></head><body><main>"
        f'<header class="{severity}"><p class="eyebrow">Telryn · Lead’s decision report</p><h1>{escape(title)}</h1>'
        f'<ul>{list_items(notes)}</ul><div class="grid">'
        + stat("Net exceptions across required shifts", signed_range(result["bounds"]))
        + stat(
            "Combined hurdle",
            f"{result['hurdle']:+d} additional {'exception' if result['hurdle'] == 1 else 'exceptions'}",
        )
        + stat(
            "Shift coverage",
            f"{len(result['shifts']) - len(result['missing_shifts'])} / {len(result['shifts'])}",
        )
        + "</div>"
        + partial
        + f"<p>{escape(stopping_text(result))}</p>"
        '<p class="muted">Shadow trials record candidate picks in your system while the live rule continues routing. '
        "The counts describe historical review coverage.</p>"
        + f"<p><strong>Review question</strong> {escape(plan['rubric_question'])}</p></header>"
        + serious_section
        + "".join(sections)
        + "<section><h2>Grading handoff</h2>"
        + f"<p>{result['packet_rows']} packet rows · {result['packet_returns']} returned · "
        f"{result['pending_cross_checks']} pending cross-checks</p>"
        + (f"<ul>{list_items(packet_notes)}</ul>" if packet_notes else "")
        + "<p>Share the grading CSV with the reviewer. Keep this report with the decision owner. To record a return, "
        "run <code>python3 -m telryn refresh REPORT --grades RETURNED.csv</code>.</p>"
        + adjudication_html
        + f"<ul>{list_items(effort_entries)}</ul>"
        '<p class="muted">Grading, cross-checks and adjudication are separate from estimated shift workload.</p>'
        + (
            '<details><summary>Private packet status</summary><div class="table" tabindex="0"><table>'
            "<thead><tr><th>Request</th><th>Row</th><th>Purpose</th><th>Status</th></tr></thead><tbody>"
            + "".join(packet_details)
            + "</tbody></table></div></details>"
            if packet_details
            else ""
        )
        + "</section>"
        + trial_section
        + "<footer><h2>Evidence basis</h2>"
        + f"<p>Live rule {escape(plan['live']['version'])} · Candidate {escape(plan['candidate']['version'])} · "
        f"Rubric {escape(plan['rubric'])} · Workload method {escape(plan['workload_method'])}</p>"
        + f"<p>Evidence window {plan['evidence_window_hours']} hours from cutoff · "
        f"Grading budget {plan['grading_budget']} rows · Comparison <code>{escape(state['comparison_id'])}</code></p>"
        + f"<p>Cutoff export SHA-256 <code>{escape(frozen['export'])}</code></p></footer></main>"
        + STATE_OPEN
        + payload
        + STATE_CLOSE
        + "</body></html>\n"
    )


def shadow_html(trials: dict) -> str:
    if not trials:
        return ""
    parts = [
        "<section><h2>Recorded shadow trials</h2>",
        "<p>Observed results from the existing queue system. The historical recommendation above remains a separate decision.</p>",
    ]
    for trial_id, trial in trials.items():
        owner = trial["lead_decision"]
        parts.append(
            f"<h3>{escape(trial_id)}</h3><p>Observed {escape(trial['observed_at'])} · "
            f"Lead decision <strong>{escape(owner['action'])}</strong> · {escape(owner['note'])}</p>"
        )
        for name, batch in trial["batches"].items():
            lower, upper = batch["projected_minutes"]
            ref = escape(batch["reference_shift"])
            upper_text = str(upper) if upper is not None else "unknown upper limit"
            parts.append(
                f"<p><strong>{escape(name)}</strong> compared with {ref} · "
                f"{batch['observed_picks']} picked, {batch['projected_picks']} in the historical projection "
                f"({batch['capacity']} slots) · {batch['recorded_minutes']} recorded reviewer minutes "
                f"with {batch['unmeasured_picks']} unmeasured · Historical projection {lower}–{upper_text} minutes "
                f"against a {batch['staff_minutes']}-minute staffing limit · {escape(batch['staffing'])}</p>"
            )
            mix = list_items(
                [
                    f"{group}: {counts['observed']} observed, {counts['projected']} historical"
                    for group, counts in batch["mix"].items()
                ]
            )
            parts.append(
                f"<ul>{mix}</ul><details><summary>Observed picks in review order</summary>"
                f"{cases_html(batch['ordered_observed_ids'])}</details>"
            )
    return "".join(parts) + "</section>"
