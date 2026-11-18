"""Reconcile a historical shift before comparing its selections."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

from .contracts import InputError, timestamp, unique_ids, validate_batch


class MissingInput(KeyError):
    """A selector indexed an unavailable field on a cutoff row."""

    def __init__(self, case: str, field: str):
        super().__init__(field)
        self.case = case
        self.field = field


class CutoffInputs(dict):
    def __missing__(self, field: str):
        raise MissingInput(self["id"], field)


def blocked(reason: str) -> dict:
    return {"status": "Not replayable", "reason": reason}


class Selector:
    """Load trusted source from the exact bytes recorded in the comparison."""

    def __init__(self, base: Path, rule: dict):
        path = base / rule["file"]
        source = path.read_bytes()
        self.digest = hashlib.sha256(source).hexdigest()
        self.label = f"{rule['version']} ({rule['function']})"
        namespace = {"__file__": str(path), "__name__": "_telryn_selector"}
        try:
            exec(compile(source, path.name, "exec"), namespace)
        except (Exception, SystemExit) as exc:
            raise InputError(f"could not load selector {self.label}: {exc}") from exc
        self.select = namespace.get(rule["function"])
        if not callable(self.select):
            raise InputError(f"{self.label} does not name a callable selector")

    def run(self, rows: dict, capacity: int) -> list[str]:
        inputs = [
            CutoffInputs(id=case, **deepcopy(row["inputs"]))
            for case, row in sorted(rows.items())
        ]
        try:
            picks = self.select(inputs, capacity)
        except MissingInput:
            raise
        except (Exception, SystemExit) as exc:
            raise InputError(f"selector {self.label} failed: {exc}") from exc
        picks = unique_ids(picks, f"selector {self.label} output")
        foreign = sorted(set(picks).difference(rows))
        if foreign:
            raise InputError(
                f"selector {self.label} returned foreign IDs: {', '.join(foreign)}"
            )
        return picks


def apply_overrides(
    picks: list[str], overrides: list[dict], capacity: int
) -> list[str]:
    """Apply recorded includes and reproducible conditional swaps in recorded order."""
    selected = list(picks)
    protected: set[str] = set()
    selection_limit = max(capacity, len(picks))
    for override in overrides:
        case = override["request"]
        if override["kind"] == "include":
            protected.add(case)
            if case not in selected:
                selected.append(case)
                displaced = next(
                    (old for old in reversed(selected) if old not in protected), None
                )
                if len(selected) > selection_limit and displaced is not None:
                    selected.remove(displaced)
        else:
            previous = override["replace"]
            if (
                previous in selected
                and previous not in protected
                and case not in selected
            ):
                selected[selected.index(previous)] = case
    return selected


def replay_shift(
    name: str,
    batch: dict | None,
    policy: dict,
    plan: dict,
    live: Selector,
    candidate: Selector,
) -> dict:
    if batch is None:
        return blocked("Required shift is missing from the export.")
    try:
        validate_batch(name, batch)
    except InputError as exc:
        return blocked(str(exc))
    membership = batch.get("membership")
    if membership is None:
        return blocked("An independent cutoff membership record is missing.")
    rows = batch["requests"]
    missing = sorted(set(membership["ids"]).difference(rows))
    extra = sorted(set(rows).difference(membership["ids"]))
    if missing or extra:
        return blocked(
            f"Cutoff membership differs from the extract. "
            f"Missing: {', '.join(missing) or 'none'}. Extra: {', '.join(extra) or 'none'}."
        )
    cutoff = timestamp(batch["cutoff"], "cutoff")
    for case, row in sorted(rows.items()):
        if any(row.get(field) is None for field in ("known_at", "revision", "inputs")):
            return blocked(f"{case} lacks a material cutoff revision or input.")
        if timestamp(row["known_at"], f"{case} known_at") > cutoff:
            return blocked(f"{case} revision is later than the cutoff.")
        if "id" in row["inputs"]:
            return blocked(f"{case} cutoff inputs use reserved field 'id'.")
    if any(
        batch.get(field) is None
        for field in (
            "capacity",
            "live_version",
            "tie_break",
            "actual",
            "mandatory",
            "overrides",
        )
    ):
        return blocked(
            "Recorded live rule, capacity, picks, obligations, or override history is missing."
        )
    if (
        batch["live_version"] != plan["live"]["version"]
        or batch["tie_break"] != plan["live"]["tie_break"]
    ):
        return blocked(
            "The supplied live rule version or tie-break differs from the record."
        )
    if set(batch["actual"]).union(batch["mandatory"]).difference(rows):
        return blocked(
            "Recorded picks or mandatory reviews refer to ineligible requests."
        )
    for override in batch["overrides"]:
        if override["kind"] == "unresolved":
            return blocked(f"An override could change selection: {override['reason']}")
        if override["request"] not in rows or (
            override["kind"] == "swap" and override["replace"] not in rows
        ):
            return blocked("Override history refers to an ineligible request.")
    try:
        live_picks = apply_overrides(
            live.run(rows, batch["capacity"]), batch["overrides"], batch["capacity"]
        )
    except MissingInput as exc:
        return blocked(f"{exc.case} lacks live ranking input {exc.field!r}.")
    if len(live_picks) > batch["capacity"] or live_picks != batch["actual"]:
        for index in range(max(len(live_picks), len(batch["actual"]))):
            expected = (
                batch["actual"][index] if index < len(batch["actual"]) else "(end)"
            )
            got = live_picks[index] if index < len(live_picks) else "(end)"
            if expected != got:
                return blocked(
                    f"Live replay first diverges at slot {index + 1}: recorded {expected}, replayed {got}."
                )
        return blocked("Recorded selection exceeds the historical slot capacity.")
    try:
        raw_picks = candidate.run(rows, policy["capacity"])
    except MissingInput as exc:
        return blocked(f"{exc.case} lacks candidate ranking input {exc.field!r}.")
    candidate_picks = apply_overrides(raw_picks, batch["overrides"], policy["capacity"])
    return {
        "status": "Replayable",
        "name": name,
        "cutoff": batch["cutoff"],
        "membership_source": membership["source"],
        "live": live_picks,
        "candidate": candidate_picks,
        "raw_candidate_count": len(raw_picks),
        "capacity": policy["capacity"],
        "staff_minutes": policy["staff_minutes"],
        "mandatory": batch["mandatory"],
        "rows": {
            case: {
                "revision": rows[case]["revision"],
                "evidence": rows[case].get("evidence", []),
                "minutes": rows[case].get("minutes"),
            }
            for case in sorted(set(live_picks).union(candidate_picks))
        },
    }
