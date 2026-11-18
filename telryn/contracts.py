"""Read and validate the comparison and export formats."""

from __future__ import annotations

import hashlib
import json
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any


class InputError(ValueError):
    """A supplied file or selector violates its public contract."""


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise InputError(f"{label} must be an integer ≥ {minimum}")
    return value


def string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label} must be a nonempty string")
    return value


def mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise InputError(f"{label} must be an object")
    return value


def sequence(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise InputError(f"{label} must be a list")
    return value


def unique_ids(value: Any, label: str) -> list[str]:
    values = [string(item, label) for item in sequence(value, label)]
    if len(set(values)) != len(values):
        raise InputError(f"{label} contains duplicate IDs")
    return values


def timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(string(value, label))
    except ValueError as exc:
        raise InputError(f"{label} must be an ISO 8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise InputError(f"{label} must include a UTC offset")
    return parsed


def versioned(data: Any, label: str) -> dict:
    validated = mapping(data, label)
    if type(validated.get("schema")) is not int or validated["schema"] != 1:
        raise InputError(f"unsupported {label} schema (expected 1)")
    return validated


def read_json(path: Path, label: str) -> dict:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InputError(f"duplicate JSON key {key!r} in {label}")
            result[key] = value
        return result

    return versioned(
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys),
        label,
    )


def load_plan(path: Path) -> dict:
    plan = versioned(tomllib.loads(path.read_text(encoding="utf-8")), "comparison")
    for field in ("export", "rubric", "rubric_question", "workload_method"):
        string(plan.get(field), field)
    if "grades" in plan:
        string(plan["grades"], "grades")
    required = unique_ids(plan.get("required_shifts"), "required_shifts")
    if not required:
        raise InputError("required_shifts must name at least one held-out shift")
    tuned = unique_ids(plan.get("tuning_shifts", []), "tuning_shifts")
    if set(required).intersection(tuned):
        raise InputError("tuning shifts cannot qualify as held-out shifts")
    for field in (
        "minimum_additional_exceptions",
        "grading_budget",
        "cross_checks_per_side",
    ):
        integer(plan.get(field), field)
    window = integer(plan.get("evidence_window_hours"), "evidence_window_hours")
    if window > 24 * 365:
        raise InputError("evidence_window_hours must be at most 8760")
    shifts = mapping(plan.get("shifts"), "shifts")
    if set(shifts) != set(required):
        raise InputError("shifts must provide limits for exactly the required shifts")
    for shift, policy in shifts.items():
        mapping(policy, f"policy for {shift}")
        integer(policy.get("capacity"), f"{shift} capacity")
        integer(policy.get("staff_minutes"), f"{shift} staff_minutes")
    for side in ("live", "candidate"):
        rule = mapping(plan.get(side), side)
        for field in ("file", "function", "version", "tie_break"):
            string(rule.get(field), f"{side}.{field}")
    try:
        canonical(plan)
    except (TypeError, ValueError) as exc:
        raise InputError("comparison values must have a JSON representation") from exc
    return plan


def load_export(path: Path) -> dict:
    data = read_json(path, "export")
    mapping(data.get("shifts"), "export shifts")
    try:
        canonical(data)
    except ValueError as exc:
        raise InputError("export contains a non-finite number") from exc
    return data


def validate_batch(shift: str, batch: dict) -> None:
    mapping(batch, shift)
    timestamp(batch.get("cutoff"), f"{shift} cutoff")
    if batch.get("membership") is not None:
        membership = mapping(batch["membership"], "membership")
        string(membership.get("source"), "membership source")
        unique_ids(membership.get("ids"), "membership IDs")
    fields = mapping(batch.get("requests"), "requests")
    for case, row in fields.items():
        string(case, "request ID")
        mapping(row, case)
        if row.get("revision") is not None:
            string(row["revision"], f"{case} revision")
        if row.get("known_at") is not None:
            timestamp(row["known_at"], f"{case} known_at")
        if row.get("inputs") is not None:
            mapping(row["inputs"], f"{case} inputs")
        refs = unique_ids(row.get("evidence", []), f"{case} evidence")
        if any(any(ord(ch) < 32 for ch in ref) for ref in refs):
            raise InputError(f"{case} evidence references must be single-line text")
        if row.get("minutes") is not None:
            time = mapping(row["minutes"], f"{case} minutes")
            lower = integer(time.get("lower"), f"{case} lower minutes")
            high = time.get("upper")
            if high is not None:
                integer(high, f"{case} upper minutes", minimum=lower)
            if time.get("basis") not in ("observed", "estimated"):
                raise InputError(f"{case} minutes basis must be observed or estimated")
            if time["basis"] == "observed" and lower != high:
                raise InputError(f"{case} observed minutes must have equal bounds")
    for field in ("actual", "mandatory"):
        if batch.get(field) is not None:
            unique_ids(batch[field], f"{shift} {field}")
    if batch.get("capacity") is not None:
        integer(batch["capacity"], f"{shift} recorded capacity")
    for field in ("live_version", "tie_break"):
        if batch.get(field) is not None:
            string(batch[field], f"{shift} {field}")
    if batch.get("overrides") is not None:
        for override in sequence(batch["overrides"], f"{shift} overrides"):
            mapping(override, f"{shift} override")
            if override.get("kind") not in ("include", "swap", "unresolved"):
                raise InputError(f"{shift} override kind is unsupported")
            if override["kind"] == "unresolved":
                string(override.get("reason"), "unresolved override reason")
                continue
            string(override.get("request"), "override request")
            if override["kind"] == "swap":
                string(override.get("replace"), "swap incumbent")
