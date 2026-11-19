"""Independent SQLite worksheet for the bundled Tuesday decision."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

if __package__:
    from .app import instant
else:  # Also allow running this worksheet directly as a script.
    from app import instant

ROOT = Path(__file__).resolve().parent
ANSWERS = ("meets", "outside", "insufficient")


def identifiers(values: list) -> set[str]:
    if not isinstance(values, list) or any(
        not isinstance(item, str) or not item.strip() for item in values
    ):
        raise ValueError("expected a list of nonempty identifiers")
    if len(values) != len(set(values)):
        raise ValueError("duplicate identifiers")
    return set(values)


def worklist(
    gain: list[str],
    loss: list[str],
    archived: dict,
    rows: dict,
    cutoff: tuple[datetime, str],
):
    initial, excluded = {}, set()
    for case in gain + loss:
        record = archived["grades"].get(case)
        if record is None:
            continue
        if type(record["shared_sources"]) is not bool or record["grade"] not in ANSWERS:
            raise ValueError(
                f"{case} has an invalid archived grade or evidence provenance"
            )
        refs = identifiers(record["evidence"])
        cutoff_time, remainder = cutoff
        window_end = (cutoff_time + timedelta(hours=24), remainder)
        within_window = cutoff <= instant(record["evidence_at"]) <= window_end
        if not (record["shared_sources"] and within_window):
            if refs.intersection(rows[case].get("evidence", [])):
                excluded.add(case)
        elif (
            refs
            and refs == set(rows[case].get("evidence", []))
            and record["grade"] in ("meets", "outside")
        ):
            initial[case] = record["grade"]
    controls = []
    for cases, preferred in ((loss, "outside"), (gain, "meets")):
        known = sorted(case for case in cases if case in initial)
        if known:
            controls.append(
                next((case for case in known if initial[case] == preferred), known[0])
            )
    return initial, excluded, controls


def reconcile(
    initial: dict,
    controls: list[str],
    eligible: set[str],
    rows: dict,
    ledger: list[dict],
) -> tuple[dict, int, list[str], dict]:
    """Apply an analyst-maintained case ledger, independent of Telryn packet IDs."""
    received = {}
    prior_decisions = {}
    all_ids = {}
    effort = {
        key: {"events": 0, "minutes": 0, "untimed": 0}
        for key in ("grading", "cross_checks", "adjudication")
    }
    for entry in ledger:
        case, event_id, grade = entry["case"], entry["id"], entry["grade"]
        if (
            case not in eligible
            or grade not in ANSWERS
            or not isinstance(event_id, str)
            or not event_id
        ):
            raise ValueError(f"invalid review for {case}")
        if entry["evidence"] != rows[case]["evidence"]:
            raise ValueError(f"review for {case} references different evidence")
        key = (case, event_id)
        if key in all_ids:
            if all_ids[key] != entry:
                raise ValueError(f"conflicting ledger ID for {case}")
            continue
        minutes = entry.get("minutes")
        if minutes is not None and (type(minutes) is not int or minutes < 0):
            raise ValueError(f"invalid review time for {case}")
        all_ids[key] = entry
        current = received.setdefault(case, {})
        action = entry["kind"]
        if action == "review":
            current[event_id] = grade
            category = "cross_checks" if case in controls else "grading"
        elif action == "adjudication":
            check = current_decision(initial, prior_decisions, case, current)
            if (
                check != "disputed"
                or sorted(entry["reviews"]) != sorted(current)
                or not entry["reason"].strip()
            ):
                raise ValueError(
                    f"adjudication for {case} does not match the open dispute"
                )
            prior_decisions[case] = grade
            current.clear()
            category = "adjudication"
        else:
            raise ValueError(f"unrecognized ledger event {action}")
        effort[category]["events"] += 1
        effort[category]["minutes"] += minutes or 0
        effort[category]["untimed"] += minutes is None

    accepted = dict(initial)
    disputed = []
    pending = set(controls)
    for case, reviews in received.items():
        outcome = current_decision(initial, prior_decisions, case, reviews)
        if outcome in ("meets", "outside"):
            accepted[case] = outcome
            pending.discard(case)
        else:
            accepted.pop(case, None)
            if outcome == "disputed":
                disputed.append(case)
    return accepted, len(pending), sorted(disputed), effort


def current_decision(initial: dict, adjudicated: dict, case: str, reviews: dict) -> str:
    base = adjudicated.get(case, initial.get(case))
    answers = list(reviews.values())
    if base is not None:
        answers.append(base)
    if len(set(answers)) > 1:
        return "disputed"
    return answers[0] if answers else "insufficient"


def endpoints(gain: list[str], loss: list[str], accepted: dict) -> tuple[int, int]:
    # Evaluate the two endpoint assignments independently of Telryn.
    pessimistic = sum(accepted.get(case) == "meets" for case in gain) - sum(
        accepted.get(case) != "outside" for case in loss
    )
    optimistic = sum(accepted.get(case) != "outside" for case in gain) - sum(
        accepted.get(case) == "meets" for case in loss
    )
    return pessimistic, optimistic


def analyze(export: dict, archived: dict, ledger: list[dict] = ()) -> dict:
    for data in (export, archived):
        if (
            not isinstance(data, dict)
            or type(data.get("schema")) is not int
            or data["schema"] != 1
        ):
            raise ValueError("the worksheet requires version 1 cutoff and grade files")
    if archived.get("rubric") != "order-discrepancy-v1" or not isinstance(
        archived.get("grades"), dict
    ):
        raise ValueError(
            "the worksheet requires the order-discrepancy-v1 grade archive"
        )
    batch = export["shifts"]["tuesday-am"]
    capacity, staffing, hurdle, budget = 100, 480, 1, 10
    rows = batch["requests"]
    membership = batch.get("membership")
    if (
        not isinstance(membership, dict)
        or not isinstance(membership.get("source"), str)
        or not membership["source"].strip()
        or identifiers(membership.get("ids")) != set(rows)
    ):
        return {
            "batch": "Not replayable",
            "reason": "Cutoff membership differs or is missing",
        }
    if (
        batch["capacity"],
        batch["live_version"],
        batch["tie_break"],
        batch["overrides"],
    ) != (capacity, "refund-amount-v1", "id-asc-v1", []):
        return {
            "batch": "Not replayable",
            "reason": "This recipe requires the bundled Tuesday policy",
        }
    try:
        cutoff = instant(batch["cutoff"])
        if identifiers(batch["mandatory"]).difference(rows):
            raise ValueError("mandatory review refers to an ineligible request")
        for row in rows.values():
            if (
                not isinstance(row["revision"], str)
                or not row["revision"].strip()
                or instant(row["known_at"]) > cutoff
            ):
                raise ValueError("cutoff revision unavailable")
            identifiers(row.get("evidence", []))
            if "id" in row["inputs"]:
                raise ValueError("id is reserved on cutoff rows")
            time = row.get("minutes")
            if time is not None:
                lo, hi, basis = time["lower"], time.get("upper"), time["basis"]
                if (
                    type(lo) is not int
                    or lo < 0
                    or (hi is not None and (type(hi) is not int or hi < lo))
                    or basis not in ("estimated", "observed")
                    or (basis == "observed" and lo != hi)
                ):
                    raise ValueError("invalid workload bounds")
            for name in ("refund_cents", "conflict_priority"):
                score = row["inputs"][name]
                if type(score) is not int or not -(2**63) <= score < 2**63:
                    raise ValueError("invalid ranking input")
    except (KeyError, TypeError, ValueError):
        return {"batch": "Not replayable", "reason": "Cutoff input unavailable"}

    with closing(sqlite3.connect(":memory:")) as sql:
        sql.execute("CREATE TABLE requests (id TEXT, amount INTEGER, conflict INTEGER)")
        sql.executemany(
            "INSERT INTO requests VALUES (?, ?, ?)",
            (
                (key, row["inputs"]["refund_cents"], row["inputs"]["conflict_priority"])
                for key, row in rows.items()
            ),
        )
        original = [
            item[0]
            for item in sql.execute(
                "SELECT id FROM requests ORDER BY amount DESC, id LIMIT ?", (capacity,)
            )
        ]
        if original != batch["actual"]:
            return {"batch": "Not replayable", "reason": "Recorded live picks differ"}
        proposal = [
            item[0]
            for item in sql.execute(
                "SELECT id FROM requests ORDER BY conflict DESC, id LIMIT ?",
                (capacity,),
            )
        ]
    gain, loss = (
        sorted(set(proposal) - set(original)),
        sorted(set(original) - set(proposal)),
    )

    estimates = [rows[case].get("minutes") for case in proposal]
    low_time = sum(item["lower"] for item in estimates if item is not None)
    high_time = (
        None
        if any(item is None or item.get("upper") is None for item in estimates)
        else sum(item["upper"] for item in estimates)
    )
    mandatory_misses = sorted(set(batch["mandatory"]) - set(proposal))
    operational_hold = bool(mandatory_misses) or low_time > staffing

    initial, excluded, controls = worklist(gain, loss, archived, rows, cutoff)
    low, high = endpoints(gain, loss, initial)
    unknown = [
        case
        for case in loss + gain
        if case not in initial and case not in excluded and rows[case].get("evidence")
    ]
    requested = controls + (unknown if low < hurdle <= high else [])
    eligibility = set() if operational_hold else set(requested[:budget])
    if operational_hold:
        controls = []
    accepted, pending, disputed, effort = reconcile(
        initial, controls, eligibility, rows, ledger
    )

    pessimistic, optimistic = endpoints(gain, loss, accepted)
    if operational_hold or (optimistic < hurdle and pending == 0):
        decision = "Hold"
    elif (
        high_time is not None
        and high_time <= staffing
        and pessimistic >= hurdle
        and pending == 0
    ):
        decision = "Try in shadow"
    else:
        decision = "Inconclusive"
    return {
        "batch": "Replayable",
        "gain": gain,
        "loss": loss,
        "bounds": [pessimistic, optimistic],
        "workload": [low_time, high_time],
        "staff_minutes": staffing,
        "mandatory_misses": mandatory_misses,
        "hurdle": hurdle,
        "recommendation": decision,
        "unknown": sorted(set(gain + loss) - accepted.keys()),
        "cross_checks": controls,
        "grading_budget": budget,
        "pending_cross_checks": pending,
        "disputed": disputed,
        "effort": effort,
    }


def read(path: Path):
    def unique_keys(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate JSON key {key!r}")
            obj[key] = value
        return obj

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, default=ROOT / "cutoff.json")
    parser.add_argument("--grades", type=Path, default=ROOT / "known-grades.json")
    parser.add_argument(
        "--ledger",
        type=Path,
        help="analyst's ordered JSON review and adjudication events",
    )
    args = parser.parse_args()
    try:
        ledger = read(args.ledger) if args.ledger else []
        print(
            json.dumps(analyze(read(args.export), read(args.grades), ledger), indent=2)
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"baseline: {str(exc)!r}\n")


if __name__ == "__main__":
    main()
