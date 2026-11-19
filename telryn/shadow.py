"""Record a completed shadow trial supplied by the queue owner."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .contracts import (
    InputError,
    integer,
    mapping,
    read_json,
    sequence,
    string,
    timestamp,
)
from .decision import SHADOW, workload


def record(state: dict, path: Path, recommendation: str) -> bool:
    data = read_json(path, "shadow observations")
    trial_id = string(data.get("trial_id"), "trial ID")
    if data.get("comparison_id") != state["comparison_id"]:
        raise InputError("shadow observations belong to a different comparison")
    trials = state.setdefault("shadow_trials", {})
    if trial_id in trials:
        if trials[trial_id] != data:
            raise InputError(
                "a different shadow observation already uses this trial ID"
            )
        return False
    if recommendation != SHADOW:
        raise InputError(
            "record-shadow requires a comparison that supports a shadow trial"
        )
    timestamp(data.get("observed_at"), "observation timestamp")
    owner = mapping(data.get("lead_decision"), "lead decision")
    if owner.get("action") not in ("adopt", "revise", "hold", "continue"):
        raise InputError("lead decision must be adopt, revise, hold, or continue")
    string(owner.get("note"), "lead decision note")
    batches = mapping(data.get("batches"), "shadow batches")
    if not batches:
        raise InputError("provide at least one observed batch")
    observed_ids: set[str] = set()
    for name, batch in batches.items():
        mapping(batch, f"{name} observation")
        historical = string(batch.get("reference_shift"), "reference shift")
        source = state["shifts"].get(historical)
        if source is None or source["status"] != "Replayable":
            raise InputError(f"{name} must reference a replayable historical shift")
        for pick in sequence(batch.get("picks"), f"{name} ordered candidate picks"):
            mapping(pick, "shadow pick")
            case = string(pick.get("id"), "observed case ID")
            if case in observed_ids:
                raise InputError(f"duplicate observed case ID: {case}")
            observed_ids.add(case)
            string(pick.get("cohort"), f"{case} cohort")
            if pick.get("minutes") is not None:
                integer(pick["minutes"], f"{case} recorded minutes")
    trials[trial_id] = data
    return True


def summarize(state: dict) -> dict:
    result = {}
    for trial, data in state.get("shadow_trials", {}).items():
        shifts = {}
        for name, batch in data["batches"].items():
            reference = batch["reference_shift"]
            source = state["shifts"][reference]
            historical = source["candidate"]
            projected = workload(source["rows"], historical)
            expected_mix = Counter(
                source["rows"][case].get("cohort") or "Unspecified"
                for case in historical
            )
            observed_mix = Counter(pick["cohort"] for pick in batch["picks"])
            minutes = [pick.get("minutes") for pick in batch["picks"]]
            measured = sum(value for value in minutes if value is not None)
            missing = minutes.count(None)
            cap = source["staff_minutes"]
            exceeds = measured > cap
            if exceeds:
                staffing = "Above historical staffing limit"
            elif missing:
                staffing = "Recorded time incomplete"
            elif projected["upper"] is not None and measured > projected["upper"]:
                staffing = "Within staffing limit, above historical projection"
            elif measured < projected["lower"]:
                staffing = "Below historical projection"
            else:
                staffing = "Within historical projection"
            shifts[name] = {
                "reference_shift": reference,
                "capacity": source["capacity"],
                "projected_picks": len(historical),
                "staff_minutes": cap,
                "projected_minutes": [projected["lower"], projected["upper"]],
                "observed_picks": len(batch["picks"]),
                "ordered_observed_ids": [pick["id"] for pick in batch["picks"]],
                "recorded_minutes": measured,
                "unmeasured_picks": missing,
                "staffing": staffing,
                "mix": {
                    cohort: {
                        "projected": expected_mix[cohort],
                        "observed": observed_mix[cohort],
                    }
                    for cohort in sorted(expected_mix.keys() | observed_mix.keys())
                },
            }
        result[trial] = {
            "observed_at": data["observed_at"],
            "lead_decision": data["lead_decision"],
            "batches": shifts,
        }
    return result
