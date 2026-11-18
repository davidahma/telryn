"""Independent SQLite worksheet for the bundled Tuesday decision."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ANSWERS = ("meets", "outside", "insufficient")


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("timestamp needs a UTC offset")
    return result


def worklist(
    gain: list[str], loss: list[str], archived: dict, rows: dict, cutoff: datetime
):
    initial, excluded = {}, set()
    for case in gain + loss:
        record = archived["grades"].get(case)
        if record is None:
            continue
        refs = set(record["evidence"])
        within_window = (
            cutoff <= instant(record["evidence_at"]) <= cutoff + timedelta(hours=24)
        )
        if not (record["shared_sources"] and within_window):
            if refs.intersection(rows[case]["evidence"]):
                excluded.add(case)
        elif (
            refs
            and refs == set(rows[case]["evidence"])
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
    if base in ("meets", "outside"):
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
    batch = export["shifts"]["tuesday-am"]
    capacity, staffing, hurdle, budget = 100, 480, 1, 10
    rows = batch["requests"]
    if not batch.get("membership") or set(batch["membership"]["ids"]) != set(rows):
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
        for row in rows.values():
            if not row["revision"] or instant(row["known_at"]) > cutoff:
                raise ValueError("cutoff revision unavailable")
            for name in ("refund_cents", "conflict_priority"):
                if type(row["inputs"][name]) is not int:
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
        if any(item is None or item["upper"] is None for item in estimates)
        else sum(item["upper"] for item in estimates)
    )
    mandatory_misses = sorted(set(batch["mandatory"]) - set(proposal))
    operational_hold = bool(mandatory_misses) or low_time > staffing

    initial, excluded, controls = worklist(gain, loss, archived, rows, cutoff)
    low, high = endpoints(gain, loss, initial)
    unknown = [
        case
        for case in loss + gain
        if case not in initial and case not in excluded and rows[case]["evidence"]
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
    return json.loads(path.read_text(encoding="utf-8"))


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
        parser.exit(2, f"baseline: {exc}\n")


if __name__ == "__main__":
    main()
