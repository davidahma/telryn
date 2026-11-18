"""Evaluate historical counts and enforce shift-level safeguards."""

from __future__ import annotations

from collections import Counter


HOLD = "Hold"
INCONCLUSIVE = "Inconclusive"
SHADOW = "Try in shadow"


def bounds(gain: list[str], loss: list[str], grades: dict[str, str]) -> tuple[int, int]:
    gained = Counter(grades.get(case) for case in gain)
    lost = Counter(grades.get(case) for case in loss)
    difference = gained["meets"] - lost["meets"]
    return difference - lost[None], difference + gained[None]


def workload(rows: dict, picks: list[str]) -> dict:
    basis = {"observed": 0, "estimated": 0, "unknown": 0}
    lower = upper = 0
    unknown = []
    for case in picks:
        minutes = rows[case]["minutes"]
        if minutes is not None:
            lower += minutes["lower"]
        if minutes is None or minutes["upper"] is None:
            unknown.append(case)
            basis["unknown"] += 1
        else:
            upper += minutes["upper"]
            basis[minutes["basis"]] += 1
    return {
        "lower": lower,
        "upper": None if unknown else upper,
        "basis": basis,
        "unbounded": unknown,
    }


def evaluate(state: dict, grades: dict[str, str], pending_cross_checks: int) -> dict:
    plan = state["plan"]
    hurdle = plan["minimum_additional_exceptions"]
    missing, vetoes, open_items = [], [], []
    lower = upper = unknowns = 0
    shifts = {}
    for name, source in state["shifts"].items():
        if source["status"] != "Replayable":
            shifts[name] = source
            missing.append(name)
            open_items.append(f"{name}: {source['reason']}")
            continue
        live, candidate = set(source["live"]), set(source["candidate"])
        gain, loss = sorted(candidate - live), sorted(live - candidate)
        missed = sorted(set(source["mandatory"]) - candidate)
        interval = bounds(gain, loss, grades)
        time = workload(source["rows"], source["candidate"])
        limit = source["staff_minutes"]
        capacity = source["capacity"]
        if len(source["mandatory"]) > capacity:
            vetoes.append(
                f"{name}: {len(source['mandatory'])} mandatory reviews cannot fit in {capacity} slots."
            )
        if missed:
            vetoes.append(f"{name}: mandatory review missed for {', '.join(missed)}.")
        if max(len(candidate), source["raw_candidate_count"]) > capacity:
            vetoes.append(f"{name}: candidate exceeds the {capacity}-slot capacity.")
        if time["lower"] > limit:
            vetoes.append(
                f"{name}: supported minimum workload exceeds {limit} minutes."
            )
        elif time["upper"] is None or time["upper"] > limit:
            open_items.append(
                f"{name}: reviewer workload could exceed {limit} minutes."
            )
        unknown = [case for case in gain + loss if case not in grades]
        unknowns += len(unknown)
        lower += interval[0]
        upper += interval[1]
        shifts[name] = {
            "status": "Replayable",
            "gain": gain,
            "loss": loss,
            "shared": len(live & candidate),
            "live_count": len(live),
            "candidate_count": len(candidate),
            "bounds": list(interval),
            "unknown": unknown,
            "workload": time,
            "mandatory_misses": missed,
            "mandatory_count": len(source["mandatory"]),
            "capacity": capacity,
            "staff_minutes": limit,
        }
    if pending_cross_checks:
        label = (
            "cross-check still needs"
            if pending_cross_checks == 1
            else "cross-checks still need"
        )
        open_items.append(f"{pending_cross_checks} grading {label} confirmation.")
    operational_veto = bool(vetoes)
    if not missing and not pending_cross_checks and upper < hurdle:
        vetoes.append(
            f"Even the upper bound ({upper:+d}) is below the {hurdle:+d} hurdle."
        )
    if vetoes:
        recommendation = HOLD
    elif not open_items and lower >= hurdle:
        recommendation = SHADOW
    else:
        recommendation = INCONCLUSIVE
        if not missing and lower < hurdle <= upper:
            open_items.append(f"The accepted grades leave the {hurdle:+d} hurdle open.")
    return {
        "recommendation": recommendation,
        "shifts": shifts,
        "missing_shifts": missing,
        "scored_bounds": [lower, upper] if len(missing) < len(shifts) else None,
        "bounds": None if missing else [lower, upper],
        "hurdle": hurdle,
        "unknown_count": unknowns,
        "pending_cross_checks": pending_cross_checks,
        "vetoes": vetoes,
        "operational_veto": operational_veto,
        "open_items": open_items,
    }
