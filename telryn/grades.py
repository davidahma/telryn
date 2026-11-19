"""Create the blind grading packet and reconcile returns against exact evidence."""

from __future__ import annotations

import csv
import json
import re
import secrets
from datetime import timedelta
from pathlib import Path

from .contracts import (
    InputError,
    fingerprint,
    mapping,
    read_json,
    timestamp,
    unique_ids,
)
from .decision import evaluate

ANSWERS = {"meets", "outside", "insufficient"}
COLUMNS = (
    "comparison_id",
    "row_id",
    "review_revision",
    "evidence_references",
    "rubric_question",
    "grade",
    "minutes_spent",
)


def initial_grades(
    path: Path | None, plan: dict, shifts: dict
) -> tuple[dict[str, str], str | None, dict[str, str]]:
    if path is None:
        return {}, None, {}
    source = read_json(path, "grades")
    if source.get("rubric") != plan["rubric"]:
        raise InputError("the initial grade file uses a different rubric")
    accepted: dict[str, str] = {}
    excluded: dict[str, str] = {}
    records = mapping(source.get("grades"), "initial grades")
    by_case = {}
    for shift in shifts.values():
        if shift["status"] == "Replayable":
            cutoff = timestamp(shift["cutoff"], "cutoff")
            for case, row in shift["rows"].items():
                by_case[case] = (cutoff, row)
    window = timedelta(hours=plan["evidence_window_hours"])
    for case, record in records.items():
        if case not in by_case:
            continue
        mapping(record, f"grade for {case}")
        answer = record.get("grade")
        if not isinstance(answer, str) or answer not in ANSWERS:
            raise InputError(f"{case} has an invalid initial grade")
        refs = unique_ids(record.get("evidence"), f"grade evidence for {case}")
        when = timestamp(record.get("evidence_at"), f"evidence timestamp for {case}")
        shared_sources = record.get("shared_sources")
        if type(shared_sources) is not bool:
            raise InputError(f"{case} grade requires a shared_sources boolean")
        cutoff, row = by_case[case]
        admissible_time = cutoff <= when <= cutoff + window
        if set(refs).intersection(row["evidence"]):
            if not shared_sources:
                excluded[case] = (
                    "The listed evidence includes a source unavailable to both rules."
                )
            elif not admissible_time:
                excluded[case] = (
                    "The listed evidence includes a revision outside the grading window."
                )
        if (
            answer != "insufficient"
            and shared_sources
            and refs
            and set(refs) == set(row["evidence"])
            and admissible_time
        ):
            accepted[case] = answer
    return accepted, fingerprint(source), excluded


def add_packet(state: dict) -> None:
    """Freeze a budgeted packet before any reviewer sees the rows."""
    plan = state["plan"]
    accepted = state["initial_grades"]
    serious_grades = state.get("serious_initial_grades", {})
    current = evaluate(state, accepted, 0, serious_grades)
    required = plan["cross_checks_per_side"]
    hard_veto = current["operational_veto"]
    serious = current["serious_losses"]
    interval = current["bounds"]
    count_open = interval is None or interval[0] < current["hurdle"] <= interval[1]
    cap_open = (
        serious is not None
        and serious["lower"] <= serious["maximum"]
        and (serious["upper"] is None or serious["upper"] > serious["maximum"])
    )
    both_possible = (interval is None or interval[1] >= current["hurdle"]) and (
        serious is None or serious["lower"] <= serious["maximum"]
    )
    by_side = {"loss": [], "gain": []}
    for shift, scored in current["shifts"].items():
        if scored["status"] == "Replayable":
            for side in by_side:
                by_side[side].extend(
                    (case, state["shifts"][shift]["rows"][case]["evidence"])
                    for case in scored[side]
                )
    controls, serious_unknown, unknown = [], [], []
    if not hard_veto:
        criteria = [
            ("primary", "loss", accepted, state["excluded_grading"], "outside"),
            ("primary", "gain", accepted, state["excluded_grading"], "meets"),
        ]
        if serious is not None:
            criteria.append(
                (
                    "serious",
                    "loss",
                    serious_grades,
                    state.get("excluded_serious_grading", {}),
                    "outside",
                )
            )
        for rubric, side, known, excluded, preferred in criteria:
            rows = by_side[side]
            checked = sorted(
                (row for row in rows if row[0] in known),
                key=lambda row: (known[row[0]] != preferred, row[0]),
            )
            controls.extend(
                (case, refs, True, rubric) for case, refs in checked[:required]
            )
            if rubric == "serious" and cap_open and both_possible:
                serious_unknown.extend(
                    (case, refs, False, rubric)
                    for case, refs in rows
                    if refs and case not in known and case not in excluded
                )
            if rubric == "primary" and count_open and both_possible:
                unknown.extend(
                    (case, refs, False, rubric)
                    for case, refs in rows
                    if refs and case not in known and case not in excluded
                )
    # Resolving a possible safety veto has priority over the net-count hurdle.
    work = controls + serious_unknown + unknown
    budget = plan["grading_budget"]
    rng = secrets.SystemRandom()
    chosen = work[:budget]
    rng.shuffle(chosen)
    packet = {}
    for case, refs, control, criterion in chosen:
        packet["Q-" + secrets.token_hex(16)] = {
            "case": case,
            "evidence": refs,
            "control": control,
            "criterion": criterion,
            "review_revision": "V-" + secrets.token_hex(12),
        }
    reasons = []
    if any(row["criterion"] == "serious" for row in packet.values()):
        reasons.append(
            "A reviewer familiar with the serious-loss policy may infer selection side from its question."
        )
    if controls and (not both_possible or (not count_open and not cap_open)):
        reasons.append(
            "Only cross-checks were issued because a count or limit already settles the grading decision. "
            "If a check changes the range, reassess the grading plan."
        )
    if not packet:
        if hard_veto:
            reasons.append(
                "A confirmed operational veto determines the recommendation."
            )
        elif budget == 0 and work:
            reasons.append("The frozen grading budget is zero.")
        elif count_open or cap_open:
            reasons.append(
                "No admissible grading rows were available for the open decision."
            )
        else:
            reasons.append(
                "Available grades and safeguards permit a decision without a packet."
            )
    if len(work) > budget:
        reasons.append(
            f"The frozen packet can cover {budget} of {len(work)} proposed grading rows."
        )
    state["packet"] = packet
    state["cross_check_total"] = len(controls)
    state["packet_notes"] = reasons
    state["grade_history"] = {}


def render_cell(value: str) -> str:
    trimmed = value.lstrip()
    if value[:1] in ("\t", "\r", "\n") or trimmed[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def packet_fields(state: dict, row_id: str, row: dict) -> dict[str, str]:
    policy = (
        state["plan"]["serious"] if row.get("criterion") == "serious" else state["plan"]
    )
    return {
        "comparison_id": state["comparison_id"],
        "row_id": row_id,
        "review_revision": row["review_revision"],
        "evidence_references": json.dumps(
            row["evidence"], ensure_ascii=False, separators=(",", ":")
        ),
        "rubric_question": render_cell(policy["rubric_question"]),
    }


def write_packet(path: Path, state: dict) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for row_id, row in state["packet"].items():
            writer.writerow(
                {**packet_fields(state, row_id, row), "grade": "", "minutes_spent": ""}
            )


def import_packet(path: Path, state: dict, review_id: str | None = None) -> bool:
    if review_id is not None and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", review_id
    ):
        raise InputError(
            "review ID must be 1–80 letters, digits, dots, underscores, or hyphens"
        )
    changed = False
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise InputError(
                "returned CSV columns differ from the issued grading packet"
            )
        if not state["packet"]:
            raise InputError("this comparison has no grading rows; no return is needed")
        received_row = False
        for entry in reader:
            received_row = True
            row_id = entry["row_id"]
            row = state["packet"].get(row_id)
            if None in entry or row is None or entry.get("grade") is None:
                raise InputError(
                    f"malformed or unknown grading row at CSV line {reader.line_num}"
                )
            for key, expected in packet_fields(state, row_id, row).items():
                if entry.get(key) != expected:
                    raise InputError(
                        f"row {row_id} does not match this comparison or its evidence"
                    )
            answer = entry["grade"].strip().lower()
            minutes = parse_minutes(entry.get("minutes_spent"))
            if not answer:
                if minutes is not None:
                    raise InputError(f"row {row_id} has time recorded without a grade")
                continue
            if answer not in ANSWERS:
                raise InputError(
                    f"row {row_id} grade must be meets, outside, or insufficient"
                )
            excluded = (
                state.get("excluded_serious_grading", {})
                if row.get("criterion") == "serious"
                else state["excluded_grading"]
            )
            if row["case"] in excluded:
                raise InputError(f"row {row_id} contains excluded evidence")
            delivery = {"grade": answer, "minutes": minutes}
            receipt = (
                "review:" + review_id
                if review_id
                else "content:" + fingerprint(delivery)
            )
            event = {
                "kind": "return",
                "id": receipt,
                **delivery,
                "review_revision": row["review_revision"],
            }
            events = state["grade_history"].setdefault(row_id, [])
            prior = next((item for item in events if item.get("id") == receipt), None)
            if prior is not None:
                if prior != event:
                    raise InputError(
                        f"review ID {review_id!r} was already used for a different return on row {row_id}"
                    )
                continue
            events.append(event)
            changed = True
        if not received_row:
            raise InputError("returned CSV has no rows to bind to this comparison")
    return changed


def pending_reviews(events: list[dict]) -> tuple[dict | None, list[dict]]:
    """Separate the latest adjudication from the independent returns it did not see."""
    for index in range(len(events) - 1, -1, -1):
        if events[index]["kind"] == "adjudication":
            return events[index], events[index + 1 :]
    return None, events


def grade_status(state: dict) -> tuple[dict, dict, int, dict]:
    primary = dict(state["initial_grades"])
    serious = dict(state.get("serious_initial_grades", {}))
    pending = state["cross_check_total"]
    status = {}
    for row_id, row in state["packet"].items():
        case = row["case"]
        grades = serious if row.get("criterion") == "serious" else primary
        last_decision, reviews = pending_reviews(state["grade_history"].get(row_id, []))
        reference = last_decision["grade"] if last_decision else grades.get(case)
        if reference == "insufficient":
            reference = None
        considered = {event["grade"] for event in reviews}
        if reference is not None:
            considered.add(reference)
        if len(considered) > 1:
            grades.pop(case, None)
            status[row_id] = "disputed"
        elif considered == {"insufficient"} or (not considered and last_decision):
            grades.pop(case, None)
            status[row_id] = "insufficient"
        elif reviews or last_decision:
            grade = next(iter(considered))
            grades[case] = grade
            status[row_id] = "accepted"
            if row["control"]:
                pending -= 1
        else:
            status[row_id] = "awaiting return"
    return primary, serious, pending, status


def review_set(row_id: str, events: list[dict]) -> str:
    decision, remaining = pending_reviews(events)
    return fingerprint(
        [
            row_id,
            decision["id"] if decision else None,
            sorted(event["id"] for event in remaining),
        ]
    )


def parse_minutes(value: str | None) -> int | None:
    if value is None:
        raise InputError("returned grade is missing its minutes_spent column")
    value = value.strip()
    if not value:
        return None
    if not value.isascii() or not value.isdecimal() or len(value) > 6:
        raise InputError(
            "minutes spent must be a nonnegative whole number (up to six digits)"
        )
    return int(value)


def record_effort(state: dict) -> dict:
    effort = {
        name: {"events": 0, "minutes": 0, "untimed": 0}
        for name in ("grading", "cross_checks", "adjudication")
    }
    for row_id, events in state["grade_history"].items():
        for event in events:
            if event["kind"] == "adjudication":
                metric = effort["adjudication"]
            else:
                metric = effort[
                    "cross_checks" if state["packet"][row_id]["control"] else "grading"
                ]
            metric["events"] += 1
            if event["minutes"] is None:
                metric["untimed"] += 1
            else:
                metric["minutes"] += event["minutes"]
    return effort


def adjudicate(
    state: dict,
    row_id: str,
    answer: str,
    reason: str,
    minutes: int | None,
    expected_set: str,
) -> bool:
    if row_id not in state["packet"]:
        raise InputError("the adjudication row is outside this comparison")
    if answer not in ANSWERS or not reason.strip():
        raise InputError("adjudication needs a valid grade and a brief reason")
    events = state["grade_history"].get(row_id, [])
    event = {
        "kind": "adjudication",
        "grade": answer,
        "reason": reason.strip(),
        "minutes": minutes,
        "review_set": expected_set,
        "review_revision": state["packet"][row_id]["review_revision"],
    }
    event["id"] = "decision:" + fingerprint(event)
    if any(previous.get("id") == event["id"] for previous in events):
        return False
    if expected_set != review_set(row_id, events):
        raise InputError(
            "the review set changed; open the refreshed lead report before adjudicating"
        )
    if grade_status(state)[3][row_id] != "disputed":
        raise InputError("only a disputed row can be adjudicated")
    events.append(event)
    return True
