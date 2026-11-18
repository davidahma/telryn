"""Plain SQLite analysis of the bundled Tuesday comparison, without Telryn imports."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def analyze(export: dict, archived: dict) -> dict:
    batch = export["shifts"]["tuesday-am"]
    capacity, staffing, hurdle = 100, 480, 1
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
    if any(
        row.get("inputs") is None
        or row.get("revision") is None
        or not row.get("known_at")
        or row["known_at"] > batch["cutoff"]
        for row in rows.values()
    ):
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

    from datetime import datetime, timedelta

    cutoff = datetime.fromisoformat(batch["cutoff"])
    accepted = {}
    for case in gain + loss:
        record = archived["grades"].get(case)
        if (
            record
            and record["shared_sources"]
            and record["grade"] in ("meets", "outside")
            and record["evidence"]
            and set(record["evidence"]) == set(rows[case]["evidence"])
            and cutoff
            <= datetime.fromisoformat(record["evidence_at"])
            <= cutoff + timedelta(hours=24)
        ):
            accepted[case] = record["grade"]
    unknown = sorted(set(gain + loss) - accepted.keys())
    # Enumerate endpoint treatments of unknown rows directly.
    pessimistic = sum(accepted.get(case) == "meets" for case in gain) - sum(
        accepted.get(case) != "outside" for case in loss
    )
    optimistic = sum(accepted.get(case) != "outside" for case in gain) - sum(
        accepted.get(case) == "meets" for case in loss
    )
    controls = []
    for cases, favorable in ((loss, "outside"), (gain, "meets")):
        known = sorted(
            (case for case in cases if case in accepted),
            key=lambda case: (accepted[case] != favorable, case),
        )
        controls.extend(known[:1])

    estimates = [rows[case].get("minutes") for case in proposal]
    low_time = sum(item["lower"] for item in estimates if item is not None)
    high_time = (
        None
        if any(item is None or item["upper"] is None for item in estimates)
        else sum(item["upper"] for item in estimates)
    )
    mandatory_misses = sorted(set(batch["mandatory"]) - set(proposal))
    if (
        mandatory_misses
        or low_time > staffing
        or (optimistic < hurdle and not controls)
    ):
        decision = "Hold"
    elif (
        high_time is not None
        and high_time <= staffing
        and pessimistic >= hurdle
        and not controls
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
        "unknown": unknown,
        "cross_checks": controls,
        "grading_budget": 10,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, default=ROOT / "cutoff.json")
    parser.add_argument("--grades", type=Path, default=ROOT / "known-grades.json")
    args = parser.parse_args()
    print(
        json.dumps(
            analyze(
                json.loads(args.export.read_text(encoding="utf-8")),
                json.loads(args.grades.read_text(encoding="utf-8")),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
