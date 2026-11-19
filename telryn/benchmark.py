"""Read an operator's matched pilot record against a saved comparison."""

from __future__ import annotations

from datetime import date, timezone
from pathlib import Path

from .contracts import (
    REPORT_SCHEMA,
    InputError,
    integer,
    mapping,
    read_json,
    string,
    timestamp,
    unique_ids,
)
from .report import recommendation

MATCH_CHECKS = (
    "same_starting_exports",
    "same_policy_change",
    "same_eligible_population",
    "same_rubrics_and_evidence",
    "same_staffing_and_grading_budget",
)
PHASES = ("analyst_minutes", "reviewer_minutes", "adjudication_minutes", "lead_minutes")


def recorded_effort(arm: dict, label: str) -> dict:
    arm = mapping(arm, label)
    effort = {
        name: integer(arm.get(name), f"{label}.{name}")
        for name in ("setup_minutes", *PHASES)
    }
    return {**effort, "recurring_minutes": sum(effort[name] for name in PHASES)}


def flag(value, label: str) -> bool:
    if type(value) is not bool:
        raise InputError(f"{label} must be true or false")
    return value


def assess(state: dict, path: Path) -> dict:
    record = read_json(path, "pilot record")
    if record.get("comparison_id") != state["comparison_id"]:
        raise InputError("pilot record belongs to a different comparison")
    if state["schema"] != REPORT_SCHEMA:
        raise InputError("regenerate this legacy report before benchmarking")
    try:
        decision_date = date.fromisoformat(
            string(record.get("decision_date"), "decision date")
        )
    except ValueError as exc:
        raise InputError("decision_date must be an ISO calendar date") from exc
    for shift in state["shifts"].values():
        if shift["status"] == "Replayable":
            cutoff, _ = timestamp(shift["cutoff"], "historical cutoff")
            if decision_date < cutoff.astimezone(timezone.utc).date():
                raise InputError("decision_date predates a historical cutoff in UTC")
    evidence = record.get("evidence")
    if evidence not in ("synthetic", "observed"):
        raise InputError("evidence must be synthetic or observed")
    comparator = string(record.get("comparator"), "comparator name")
    material = integer(
        record.get("material_minutes_saved"), "material_minutes_saved", minimum=1
    )
    cadence = record.get("policy_changes_last_year")
    if cadence is not None:
        cadence = integer(cadence, "policy_changes_last_year")
    factors = mapping(record.get("matched"), "matched starting conditions")
    gaps = [
        name for name in MATCH_CHECKS if not flag(factors.get(name), f"matched.{name}")
    ]
    lead = mapping(record.get("lead_review"), "lead review")
    equivalent = flag(lead.get("equally_supported"), "lead_review.equally_supported")
    misses = unique_ids(
        lead.get("baseline_missed_serious_cases", []), "baseline missed serious cases"
    )
    baseline = recorded_effort(record.get("baseline"), "baseline")
    telryn = recorded_effort(record.get("telryn"), "telryn")

    result = recommendation(state)
    if record.get("decision_revision") != result["decision_revision"]:
        raise InputError(
            "pilot record does not match the saved decision revision; verify the evidence before updating it"
        )
    effort = result["effort"]
    phase_minimums = {
        "reviewer_minutes": effort["grading"]["minutes"]
        + effort["cross_checks"]["minutes"],
        "adjudication_minutes": effort["adjudication"]["minutes"],
    }
    for phase, minimum in phase_minimums.items():
        if telryn[phase] < minimum:
            raise InputError(
                f"telryn.{phase} is below the {minimum} minutes recorded in this report"
            )
    serious = result["serious_losses"]
    confirmed = set(serious["confirmed"]) if serious else set()
    unsupported = sorted(set(misses) - confirmed)
    if unsupported:
        raise InputError(
            f"serious misses are not confirmed losing review in this report: {', '.join(unsupported)}"
        )
    readiness = not result["missing_shifts"] and not result["pending_cross_checks"]
    comparable = readiness and not gaps and equivalent
    recurring_saved = baseline["recurring_minutes"] - telryn["recurring_minutes"]
    incremental_setup = telryn["setup_minutes"] - baseline["setup_minutes"]
    payback = None
    if comparable and recurring_saved > 0:
        payback = (max(0, incremental_setup) + recurring_saved - 1) // recurring_saved
    return {
        "schema": 1,
        "evidence": evidence,
        "decision_date": decision_date.isoformat(),
        "comparison_id": state["comparison_id"],
        "decision_revision": result["decision_revision"],
        "comparator": comparator,
        "baseline": baseline,
        "telryn": telryn,
        "unmatched_conditions": gaps,
        "replay_and_cross_checks_complete": readiness,
        "lead_reports_equivalent_support": equivalent,
        "timing_comparable": comparable,
        "material_minutes_saved": material,
        "recurring_minutes_saved": recurring_saved if comparable else None,
        "material_time_win": comparable and recurring_saved >= material,
        "serious_misses_confirmed_by_lead": sorted(misses)
        if readiness and not gaps
        else [],
        "incremental_setup_minutes": incremental_setup,
        "changes_to_recover_setup": payback,
        "policy_changes_last_year": cadence,
        "setup_recovered_at_recorded_annual_cadence": (
            payback <= cadence if payback is not None and cadence is not None else None
        ),
    }
