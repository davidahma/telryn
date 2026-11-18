"""Render a portable lead report with a private, checksummed decision snapshot."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .contracts import InputError, canonical, fingerprint, versioned
from .decision import evaluate
from .grades import grade_status, record_effort

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
    grades, pending, status = grade_status(state)
    summary = evaluate(state, grades, pending)
    summary.update(
        {
            "comparison_id": state["comparison_id"],
            "schema": 1,
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
    state = versioned(envelope.get("state"), "report state")
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


def shift_html(name: str, shift: dict, source: dict, grades: dict) -> str:
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
        + f'<div class="grid"><div><h3>Gain review · {len(shift["gain"])}</h3>{cases_html(shift["gain"])}'
        + f"</div><div><h3>Lose review · {len(shift['loss'])}</h3>{cases_html(shift['loss'])}</div></div>"
        + f"<details><summary>Open grades · {len(shift['unknown'])}</summary>{cases_html(shift['unknown'])}</details>"
        + "<details><summary>Accepted grades and workload by request</summary>"
        + "<p>"
        + escape(
            f"{basis['observed']} observed · {basis['estimated']} estimated · "
            f"{basis['unknown']} with unknown upper time"
        )
        + "</p>"
        + "<p>Gaining review: "
        + escape(_grade_counts(shift["gain"], grades))
        + "</p>"
        + "<p>Losing review: "
        + escape(_grade_counts(shift["loss"], grades))
        + "</p>"
        + '<div class="table" tabindex="0"><table><thead><tr><th>Candidate request</th><th>Revision</th>'
        "<th>Time evidence</th></tr></thead><tbody>"
        + "".join(details)
        + "</tbody></table></div>"
        "</details></section>"
    )


def _grade_counts(cases: list[str], grades: dict) -> str:
    answers = [grades.get(case) for case in cases]
    return (
        f"{answers.count('meets')} meet the rubric · {answers.count('outside')} outside · "
        f"{answers.count(None)} unknown"
    )


def render_report(state: dict) -> str:
    result = recommendation(state)
    grades, _, status = grade_status(state)
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
        packet_details.append(
            f"<tr><td>{escape(row['case'])}</td><td><code>{escape(row_id)}</code></td>"
            f"<td>{kind}</td><td>{escape(status[row_id])}</td></tr>"
        )
    sections = []
    for name in state["plan"]["required_shifts"]:
        sections.append(
            shift_html(name, result["shifts"][name], state["shifts"][name], grades)
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
        "The counts describe historical review coverage.</p></header>"
        + "".join(sections)
        + "<section><h2>Grading handoff</h2>"
        + f"<p>{result['packet_rows']} packet rows · {result['packet_returns']} returned · "
        f"{result['pending_cross_checks']} pending cross-checks</p>"
        + (f"<ul>{list_items(packet_notes)}</ul>" if packet_notes else "")
        + "<p>Share the grading CSV with the reviewer. Keep this report with the decision owner. To record a return, "
        "run <code>python3 -m telryn refresh REPORT --grades RETURNED.csv</code>.</p>"
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
        + "</section><footer><h2>Evidence basis</h2>"
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
