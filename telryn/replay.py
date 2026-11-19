"""Reconcile a historical shift before comparing its selections."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from .contracts import InputError, canonical, timestamp, unique_ids, validate_batch


class MissingInput(KeyError):
    """A selector indexed an unavailable field on a cutoff row."""

    def __init__(self, case: str, field: str):
        super().__init__(field)
        self.case = case
        self.field = field


class CutoffInputs(dict):
    def __missing__(self, field: str):
        raise MissingInput(self["id"], field)


class UnrepresentableInput(ValueError):
    """A cutoff value cannot retain its declared type and precision in SQLite."""


def blocked(reason: str) -> dict:
    return {"status": "Not replayable", "reason": reason}


class Selector:
    """Load trusted source from the exact bytes recorded in the comparison."""

    def __init__(self, base: Path, rule: dict):
        path = base / rule["file"]
        source = path.read_bytes()
        self.digest = hashlib.sha256(source).hexdigest()
        self.label = f"{rule['version']} ({rule['function']})"
        self.required_inputs = rule.get("required_inputs", [])
        namespace = {"__file__": str(path), "__name__": "_telryn_selector"}
        try:
            exec(compile(source, path.name, "exec"), namespace)
        except (Exception, SystemExit) as exc:
            raise InputError(f"could not load selector {self.label}: {exc}") from exc
        self.select = namespace.get(rule["function"])
        if not callable(self.select):
            raise InputError(f"{self.label} does not name a callable selector")

    def run(self, rows: dict, capacity: int) -> list[str]:
        check_inputs(rows, self.required_inputs)
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
        return validate_picks(picks, rows, self.label)


def validate_picks(picks, rows: dict, label: str) -> list[str]:
    picks = unique_ids(picks, f"selector {label} output")
    foreign = sorted(set(picks).difference(rows))
    if foreign:
        raise InputError(f"selector {label} returned foreign IDs: {', '.join(foreign)}")
    return picks


def check_inputs(rows: dict, fields: list[str]) -> None:
    for case, row in sorted(rows.items()):
        for field in fields:
            if row["inputs"].get(field) is None:
                raise MissingInput(case, field)


SQL_FUNCTIONS = {
    "abs",
    "avg",
    "coalesce",
    "count",
    "dense_rank",
    "ifnull",
    "iif",
    "json",
    "json_array_length",
    "json_extract",
    "json_type",
    "length",
    "like",
    "lower",
    "max",
    "min",
    "nullif",
    "rank",
    "replace",
    "round",
    "row_number",
    "substr",
    "sum",
    "trim",
    "upper",
}


def _sql_authorizer(action, first, second, database, trigger):
    if action == sqlite3.SQLITE_FUNCTION and second in SQL_FUNCTIONS:
        return sqlite3.SQLITE_OK
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if (
        action == sqlite3.SQLITE_READ
        and first == "requests"
        and database in (None, "main")
    ):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class SqlSelector:
    """Run a versioned read-only ranking query over detached cutoff inputs."""

    def __init__(self, base: Path, rule: dict):
        source = (base / rule["file"]).read_bytes()
        self.digest = hashlib.sha256(source).hexdigest()
        self.query = source.decode("utf-8-sig")
        self.label = f"{rule['version']} (SQLite)"
        self.required_inputs = rule["required_inputs"]

    def run(self, rows: dict, capacity: int) -> list[str]:
        check_inputs(rows, self.required_inputs)
        for case, row in rows.items():
            for field in self.required_inputs:
                if not sqlite_integers_fit(row["inputs"][field]):
                    raise UnrepresentableInput(
                        f"{case} ranking input {field!r} exceeds SQLite's signed "
                        f"64-bit integer range for {self.label}."
                    )
        try:
            with closing(sqlite3.connect(":memory:")) as db:
                db.execute(
                    "CREATE TABLE requests (id TEXT PRIMARY KEY, inputs TEXT NOT NULL)"
                )
                db.executemany(
                    "INSERT INTO requests VALUES (?, ?)",
                    [
                        (case, canonical(row["inputs"]))
                        for case, row in sorted(rows.items())
                    ],
                )
                db.execute("PRAGMA query_only = ON")
                db.set_authorizer(_sql_authorizer)
                result = db.execute(self.query, {"capacity": capacity})
                if not result.description or [
                    field[0] for field in result.description
                ] != ["id"]:
                    raise InputError(f"{self.label} must return one column named id")
                picks = [row[0] for row in result]
        except sqlite3.Error as exc:
            raise InputError(f"selector {self.label} failed: {exc}") from exc
        return validate_picks(picks, rows, self.label)


def sqlite_integers_fit(value) -> bool:
    if isinstance(value, dict):
        return all(sqlite_integers_fit(item) for item in value.values())
    if isinstance(value, list):
        return all(sqlite_integers_fit(item) for item in value)
    return type(value) is not int or -(2**63) <= value < 2**63


def load_selector(base: Path, rule: dict) -> Selector | SqlSelector:
    if rule.get("engine") == "sqlite":
        return SqlSelector(base, rule)
    return Selector(base, rule)


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


def cutoff_issue(name: str, batch: dict | None) -> str | None:
    if batch is None:
        return "Required shift is missing from the export."
    try:
        validate_batch(name, batch)
    except InputError as exc:
        return str(exc)
    membership = batch.get("membership")
    if membership is None:
        return "An independent cutoff membership record is missing."
    rows = batch["requests"]
    missing = sorted(set(membership["ids"]).difference(rows))
    extra = sorted(set(rows).difference(membership["ids"]))
    if missing or extra:
        return (
            f"Cutoff membership differs from the extract. "
            f"Missing: {', '.join(missing) or 'none'}. Extra: {', '.join(extra) or 'none'}."
        )
    cutoff = timestamp(batch["cutoff"], "cutoff")
    for case, row in sorted(rows.items()):
        if any(row.get(field) is None for field in ("known_at", "revision", "inputs")):
            return f"{case} lacks a material cutoff revision or input."
        if timestamp(row["known_at"], f"{case} known_at") > cutoff:
            return f"{case} revision is later than the cutoff."
        if "id" in row["inputs"]:
            return f"{case} cutoff inputs use reserved field 'id'."
    for field, label in (
        ("capacity", "recorded slot capacity"),
        ("live_version", "live rule version"),
        ("tie_break", "tie-break rule"),
        ("actual", "ordered recorded picks"),
        ("mandatory", "mandatory review list"),
        ("overrides", "override history for reviewer substitutions"),
    ):
        if batch.get(field) is None:
            return f"Missing {label}."
    if set(batch["actual"]).union(batch["mandatory"]).difference(rows):
        return "Recorded picks or mandatory reviews refer to ineligible requests."
    for override in batch["overrides"]:
        if override["kind"] == "unresolved":
            return f"An override could change selection: {override['reason']}"
        if override["request"] not in rows or (
            override["kind"] == "swap" and override["replace"] not in rows
        ):
            return "Override history refers to an ineligible request."
    return None


def replay_shift(
    name: str,
    batch: dict | None,
    policy: dict,
    plan: dict,
    live: Selector | SqlSelector,
    candidate: Selector | SqlSelector,
) -> dict:
    issue = cutoff_issue(name, batch)
    if issue:
        return blocked(issue)
    assert batch is not None
    membership = batch["membership"]
    rows = batch["requests"]
    if (
        batch["live_version"] != plan["live"]["version"]
        or batch["tie_break"] != plan["live"]["tie_break"]
    ):
        return blocked(
            "The supplied live rule version or tie-break differs from the record."
        )
    try:
        live_picks = apply_overrides(
            live.run(rows, batch["capacity"]), batch["overrides"], batch["capacity"]
        )
    except MissingInput as exc:
        return blocked(f"{exc.case} lacks live ranking input {exc.field!r}.")
    except UnrepresentableInput as exc:
        return blocked(str(exc))
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
    except UnrepresentableInput as exc:
        return blocked(str(exc))
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
                "cohort": rows[case].get("cohort"),
            }
            for case in sorted(set(live_picks).union(candidate_picks))
        },
    }
