"""Record a completed shadow trial supplied by the queue owner."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .contracts import (
    REPORT_SCHEMA,
    InputError,
    canonical,
    fingerprint,
    integer,
    mapping,
    read_json,
    sequence,
    string,
    timestamp,
)
from .decision import SHADOW, workload


def known_fields(data: dict, allowed: set[str], label: str) -> None:
    unknown = sorted(set(data).difference(allowed))
    if unknown:
        raise InputError(f"unexpected {label} fields: {', '.join(unknown)}")


def record(state: dict, path: Path, recommendation: dict) -> bool:
    data = read_json(path, "shadow observations")
    trial_id = string(data.get("trial_id"), "trial ID")
    if data.get("comparison_id") != state["comparison_id"]:
        raise InputError("shadow observations belong to a different comparison")
    trials = state.get("shadow_trials", {})
    if trial_id in trials:
        if canonical(trials[trial_id]) != canonical(data):
            raise InputError(
                "a different shadow observation already uses this trial ID"
            )
        return False
    if state["schema"] != REPORT_SCHEMA or recommendation["recommendation"] != SHADOW:
        raise InputError(
            "record-shadow requires a comparison that supports a shadow trial"
        )
    known_fields(
        data,
        {
            "schema",
            "comparison_id",
            "decision_revision",
            "trial_id",
            "observed_at",
            "lead_decision",
            "batches",
        },
        "observation",
    )
    observed_at = timestamp(data.get("observed_at"), "observation timestamp")
    owner = mapping(data.get("lead_decision"), "lead decision")
    known_fields(owner, {"action", "note"}, "lead decision")
    if owner.get("action") not in ("adopt", "revise", "hold", "continue"):
        raise InputError("lead decision must be adopt, revise, hold, or continue")
    string(owner.get("note"), "lead decision note")
    batches = mapping(data.get("batches"), "shadow batches")
    if not batches:
        raise InputError("provide at least one observed batch")
    proposal = state.get("weekly_proposal")
    previous = set()
    if (proposal or "decision_revision" in data) and data.get(
        "decision_revision"
    ) != recommendation["decision_revision"]:
        raise InputError(
            "shadow observations must identify the qualifying decision_revision from the start of the trial"
        )
    if proposal:
        previous.update(proposal["prior_id_hashes"])
        previous.update(
            fingerprint(pick["id"])
            for trial in trials.values()
            for batch in trial["batches"].values()
            for pick in batch["picks"]
        )
        latest = max(
            timestamp(shift["cutoff"], "historical cutoff")
            for shift in state["shifts"].values()
        )
        if observed_at <= latest:
            raise InputError(
                "weekly proposal shadow work must be observed after every held-out cutoff"
            )
    observed_ids: set[str] = set()
    for name, batch in batches.items():
        string(name, "shadow batch name")
        mapping(batch, f"{name} observation")
        known_fields(batch, {"reference_shift", "picks"}, "batch")
        historical = string(batch.get("reference_shift"), "reference shift")
        source = state["shifts"].get(historical)
        if source is None or source["status"] != "Replayable":
            raise InputError(f"{name} must reference a replayable historical shift")
        if observed_at < timestamp(source["cutoff"], "historical cutoff"):
            raise InputError(f"{name} observation predates its historical cutoff")
        picks = sequence(batch.get("picks"), f"{name} ordered candidate picks")
        for pick in picks:
            mapping(pick, "shadow pick")
            known_fields(pick, {"id", "cohort", "minutes"}, "pick")
            case = string(pick.get("id"), "observed case ID")
            if case in observed_ids:
                raise InputError(f"duplicate observed case ID: {case}")
            observed_ids.add(case)
            string(pick.get("cohort"), f"{case} cohort")
            if pick.get("minutes") is not None:
                integer(pick["minutes"], f"{case} recorded minutes")
            if proposal and (
                fingerprint(case) in previous or pick.get("minutes") is None
            ):
                raise InputError(
                    "weekly proposal shadows need new cases with measured review minutes"
                )
        if proposal and not any(pick["minutes"] for pick in picks):
            raise InputError(
                "weekly proposal shadows need observed review work for each batch"
            )
    state.setdefault("shadow_trials", {})[trial_id] = data
    state.setdefault("shadow_decision_revisions", {})[trial_id] = recommendation[
        "decision_revision"
    ]
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
            "decision_revision": state.get("shadow_decision_revisions", {}).get(trial),
            "lead_decision": data["lead_decision"],
            "batches": shifts,
        }
    return result
