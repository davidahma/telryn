"""Publish and update a private weekly report and its blinded grading packet."""

from __future__ import annotations

import csv
import fcntl
import json
import os
import tempfile
from html import escape
from pathlib import Path

from .artifacts import new_directory, sync_directory
from .contracts import InputError, canonical, fingerprint, versioned
from .grades import render_cell
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


def _audit_id(state: dict) -> str:
    frozen = {
        key: value
        for key, value in state.items()
        if key not in ("grades", "earlier_grades")
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
        rows[row_id] = (
            case["case_id"],
            {key: render_cell(value) for key, value in fields.items()},
        )
    return rows


def _estimate(result: dict, label: str = "serious failures") -> str:
    if result["estimate"] is None:
        return "Weekly estimate unavailable"
    count = f"{result['estimate']:,.1f}".removesuffix(".0")
    return f"Estimated {label} outside ordinary QA · {count}"


def _uncertainty(result: dict, label: str = "serious failures") -> str:
    if result["interval"] is None:
        return result["estimate_reason"]
    lower, upper = result["interval"]
    if result["graded"] == result["unreviewed"]:
        return f"All {result['unreviewed']:,} conversations outside ordinary QA have a determinate grade."
    return f"The 95% sampling interval is {lower:,} to {upper:,} {label}."


def _metrics(value: float | None) -> str:
    return "Unavailable" if value is None else f"{value:.1%}"


def _digest_lines(state: dict, result: dict) -> list[str]:
    # Only controlled labels may leave the private report through the aggregate email.
    demo = state.get("finding_label") == "conversations with friction"
    label = "conversations with friction" if demo else "serious failures"
    confirmed = "confirmed friction labels" if demo else "confirmed serious"
    lines = [
        f"Telryn weekly support QA · {state['week']}",
        _estimate(result, label),
        _uncertainty(result, label),
        f"{result['confirmed_serious']} {confirmed} in the sample. {result['new']} newly found, "
        f"{result['already_known']} already known, {result['unknown']} with prior handling unknown.",
        f"{result['graded']} of {result['assigned']} assignments have a determinate grade. "
        f"{result['insufficient']} returned with insufficient evidence. {result['review_minutes']} review minutes recorded.",
        f"Ordinary QA reviewed {result['reviewed']} of {result['population']} eligible conversations.",
    ]
    if demo:
        lines.append(
            "Public demonstration using archived human labels and simulated ordinary reviews."
        )
    return lines


def digest(report_path: Path) -> str:
    state = read_state(report_path)
    return "\n".join(_digest_lines(state, summarize(state))) + "\n"


def _render(state: dict) -> str:
    result = summarize(state)
    label = state.get("finding_label", "serious failures")
    scope = f"<p>{escape(state['scope_note'])}</p>" if state.get("scope_note") else ""
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
    for case in state["cases"]:
        grade = state["grades"].get(case["case_id"])
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
    for key, stratum_label in (("flagged", "Flagged"), ("unflagged", "Unflagged")):
        group = state["strata"][key]
        total, sampled = group["population"], group["sample"]
        chance = f"{sampled / total:.2%}" if total else "—"
        sample_rows.append(
            f"<tr><th>{stratum_label}</th><td>{total:,}</td><td>{sampled:,}</td><td>{chance}</td></tr>"
        )
    scores = "".join(
        (
            stat("Screen agrees with human grade", _metrics(result["agreement"])),
            stat(
                "Flagged cases meeting the rubric (precision)",
                _metrics(result["precision"]),
            ),
            stat(
                "Positive cases the screen flagged (recall)", _metrics(result["recall"])
            ),
        )
    )
    payload = canonical({"schema": 1, "checksum": fingerprint(state), "state": state})
    payload = (
        payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    grade_text = (
        f"{result['graded']} of {result['assigned']} assignments have a determinate grade. "
        f"{result['insufficient']} returned with insufficient evidence. "
        f"Graders recorded {result['review_minutes']} minutes ({result['review_minutes'] / 60:.2f} hours)."
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'\">"
        f"<title>Weekly support QA · {escape(state['week'])} · Telryn</title><style>{STYLE}</style>"
        "</head><body><main><header>"
        f'<p class="eyebrow">Weekly support QA · {escape(state["week"])}</p><h1>{escape(_estimate(result, label))}</h1>'
        f"<p><strong>{escape(_uncertainty(result, label))}</strong></p><p>{escape(state['rubric_question'])}</p>"
        f"<p>Ordinary QA reviewed {result['reviewed']:,} of {result['population']:,} eligible conversations. "
        f"This audit covers the other {result['unreviewed']:,}, including cases another control already knew about.</p>{scope}</header>"
        f'<section><h2>Confirmed cases</h2><div class="grid">{cover}</div>{follow_up}</section>'
        f"<section><h2>Grading handoff</h2><p>{grade_text}</p>"
        "<p>Give the reviewer <code>grades.csv</code>. Keep this lead report private so the reviewer can grade each case "
        "without seeing its screen flag. Review the listed evidence revision against the question in the packet.</p>"
        "<p>Enter <code>yes</code>, <code>no</code> or <code>insufficient</code> under <code>serious</code>. "
        "For a serious finding, enter <code>new</code>, <code>already_known</code> or <code>unknown</code> under "
        "<code>disposition</code>. Record an owner and action for each new finding. For other answers, use "
        "<code>none</code> for no and <code>unknown</code> for insufficient. Record total review minutes for the row. "
        "Leave unanswered rows blank. A partial return keeps all other rows open. To resolve an insufficient grade or unknown prior handling, use the same row and include all minutes spent on it.</p>"
        "<p>Import the return with <code>python3 -m telryn weekly report.html --grades returned.csv</code>.</p></section>"
        "<section><h2>Sampling and screen performance</h2><p>Cases were drawn without replacement within each group. "
        "Population estimates weight each grade by the inverse of its selection probability.</p>"
        '<div class="table" tabindex="0"><table><thead><tr><th>Screen result</th><th>Outside ordinary QA</th>'
        "<th>Assigned</th><th>Selection probability</th></tr></thead><tbody>"
        + "".join(sample_rows)
        + "</tbody></table></div>"
        f'<h3>Screen version · {escape(state["screen_version"])}</h3><div class="grid">{scores}</div>'
        "<p>These weighted measures compare the frozen screen to human grades for the ordinary-unreviewed population. "
        "A measure requires completed sampling and a nonzero denominator.</p></section>"
        f"<footer><p>Rubric {escape(state['rubric'])}</p><p>Audit <code>{_audit_id(state)}</code></p></footer></main>"
        + STATE_OPEN
        + payload
        + "</script></body></html>\n"
    )


def read_state(report_path: Path) -> dict:
    source = report_path.read_text(encoding="utf-8")
    _, opening, tail = source.partition(STATE_OPEN)
    raw, closing, _ = tail.partition("</script>")
    if not opening or not closing:
        raise InputError("this file has no Telryn weekly audit snapshot")
    try:
        envelope = versioned(json.loads(raw), "weekly report")
    except json.JSONDecodeError as exc:
        raise InputError("the embedded weekly snapshot has invalid JSON") from exc
    state = versioned(envelope.get("state"), "weekly evidence")
    if fingerprint(state) != envelope.get("checksum"):
        raise InputError("the weekly audit snapshot is damaged or modified")
    validate_state(state)
    return state


def _write_report(fd: int, state: dict) -> None:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(_render(state))
        stream.flush()
        os.fsync(stream.fileno())


def start(plan_path: Path, out_dir: Path) -> Path:
    with new_directory(out_dir) as staging:
        state = prepare(plan_path)
        (staging / "report.html").write_text(
            _render(state), encoding="utf-8", newline="\n"
        )
        with (staging / "grades.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            # Opaque row order does not reveal which cases shared a stratum.
            for _, (_, fields) in sorted(_packet(state).items()):
                writer.writerow(fields)
    return out_dir / "report.html"


def _answer(row: dict) -> dict | None:
    serious, disposition, owner, action, minutes = (
        row[field].strip() for field in ANSWERS
    )
    if not any((serious, disposition, owner, action, minutes)):
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
        "serious": choices[serious.lower()],
        "disposition": disposition.lower(),
        "owner": owner,
        "action": action,
        "minutes": int(minutes),
    }


def _record_grade(state: dict, case_id: str, grade: dict) -> bool:
    current = state["grades"].get(case_id)
    history = state.get("earlier_grades", {}).get(case_id, [])
    # The initial schema-1 writer saved the single prior insufficient grade directly.
    if isinstance(history, dict):
        history = [history]
    if grade == current or grade in history:
        return False
    if current is not None:
        resolved_grade = current["serious"] is None and grade["serious"] is not None
        resolved_handling = (
            current["serious"] is True
            and grade["serious"] is True
            and current["disposition"] == "unknown"
            and grade["disposition"] in ("new", "already_known")
        )
        if (
            not (resolved_grade or resolved_handling)
            or grade["minutes"] < current["minutes"]
        ):
            raise InputError(
                f"conflicting return for {case_id}; existing grade preserved"
            )
        state.setdefault("earlier_grades", {})[case_id] = [*history, current]
    state["grades"][case_id] = grade
    return True


def _import(path: Path, state: dict) -> bool:
    expected = _packet(state)
    seen = set()
    changed = False
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if tuple(reader.fieldnames or ()) != COLUMNS:
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
            grade = _answer(row)
            if grade is not None and _record_grade(state, case_id, grade):
                changed = True
    if not seen:
        raise InputError("returned CSV has no rows to bind to the weekly audit")
    return changed


def refresh(report_path: Path, grades_path: Path) -> dict:
    report_path = report_path.resolve(strict=True)
    lock = report_path.with_name(f".{report_path.name}.lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        state = read_state(report_path)
        if _import(grades_path, state):
            report_fd, name = tempfile.mkstemp(
                prefix=".weekly-report-", dir=report_path.parent
            )
            temp = Path(name)
            try:
                _write_report(report_fd, state)
                temp.replace(report_path)
                sync_directory(report_path.parent)
            finally:
                temp.unlink(missing_ok=True)
        return state
