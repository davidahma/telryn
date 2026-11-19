"""Check ordinary cutoff files before anyone translates a ranking rule."""

from __future__ import annotations

import csv
import json
import math
import os
import tempfile
import tomllib
from collections import Counter
from pathlib import Path

from .contracts import InputError, mapping, string, timestamp, unique_ids, versioned
from .replay import cutoff_issue

CAPTURE = {
    "eligible": "At the next cutoff, retain the complete eligible ID list from its own source, including cases nobody reviewed.",
    "picks": "Record the final selected IDs with their exact slot order after manual substitutions.",
    "cutoff_inputs": "Retain one cutoff row per eligible ID with available-at time, revision ID, and the scores the rules use.",
    "policy": "Record cutoff time, slot count, live rule version, tie-break, mandatory reviews, and the ordered override log.",
}


class MissingSource(InputError):
    def __init__(self, source: str, message: str):
        super().__init__(message)
        self.source = source


def file_rows(path: Path, columns: list[str]) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        names = reader.fieldnames or []
        if len(set(names)) != len(names):
            raise InputError("the CSV contains duplicate header names")
        missing = sorted(set(columns).difference(names))
        if missing:
            raise InputError(f"missing CSV column(s) {', '.join(missing)}")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise InputError(f"wrong number of cells at CSV line {reader.line_num}")
            rows.append(row)
        return rows


def whole(value: str, label: str) -> int:
    try:
        return int(value, 10)
    except ValueError as exc:
        raise InputError(f"{label} needs a whole number") from exc


def score(value: str, label: str) -> int | float:
    try:
        return int(value)
    except ValueError:
        try:
            number = float(value)
        except ValueError as exc:
            raise InputError(f"{label} needs a finite number") from exc
        if not math.isfinite(number):
            raise InputError(f"{label} needs a finite number")
        return number


def convert(base: Path, spec: dict, columns: dict, inputs: dict) -> dict:
    def column(name: str) -> str:
        return columns.get(name, name)

    def path_for(kind: str) -> Path:
        value = spec.get(kind)
        if not isinstance(value, str) or not value.strip():
            raise MissingSource(kind, f"no {kind} CSV was supplied")
        path = (base / value).resolve()
        if not path.is_file():
            raise MissingSource(kind, f"the {kind} CSV is unavailable")
        return path

    def read(kind: str, path: Path, required: list[str]) -> list[dict]:
        try:
            return file_rows(path, required)
        except (OSError, ValueError, csv.Error) as exc:
            raise MissingSource(kind, f"{kind}: {exc}") from exc

    id_header = column("id")
    eligible_path = path_for("eligible")
    picks_path = path_for("picks")
    inputs_path = path_for("cutoff_inputs")
    if eligible_path.samefile(picks_path) or eligible_path.samefile(inputs_path):
        raise MissingSource(
            "eligible", "eligible membership must come from a separate source file"
        )
    eligible = read("eligible", eligible_path, [id_header])
    ids = unique_ids([row[id_header] for row in eligible], "eligible IDs")
    if not ids:
        raise MissingSource("eligible", "eligible membership is empty")
    positions = column("slot")
    picks = read("picks", picks_path, [id_header, positions])
    ranked = sorted(
        (whole(row[positions], "recorded slot"), row[id_header]) for row in picks
    )
    if [index for index, case in ranked] != list(range(1, len(ranked) + 1)):
        raise MissingSource("picks", "recorded slots must be consecutive starting at 1")
    input_rows = read(
        "cutoff_inputs",
        inputs_path,
        [id_header, column("revision"), column("known_at"), *inputs.values()],
    )
    unique_ids([row[id_header] for row in input_rows], "cutoff input IDs")
    rows = {}
    for row in input_rows:
        case = row[id_header]
        values = {}
        for field, header in inputs.items():
            values[field] = score(row[header], f"{case} input {field}")
        try:
            timestamp(row[column("known_at")], f"{case} cutoff availability")
            string(row[column("revision")], f"{case} revision")
        except InputError as exc:
            raise MissingSource("cutoff_inputs", str(exc)) from exc
        reference = row.get(column("evidence"), "").strip()
        cohort = row.get(column("cohort"), "").strip()
        item = {
            "revision": row[column("revision")].strip() or None,
            "known_at": row[column("known_at")].strip() or None,
            "inputs": values,
            "evidence": [reference] if reference else [],
        }
        if cohort:
            item["cohort"] = cohort
        lower = row.get(column("lower_minutes"), "").strip()
        upper = row.get(column("upper_minutes"), "").strip()
        basis = row.get(column("time_basis"), "").strip()
        if lower or upper or basis:
            item["minutes"] = {
                "lower": whole(lower, f"{case} lower minutes"),
                "upper": whole(upper, f"{case} upper minutes") if upper else None,
                "basis": basis,
            }
        rows[case] = item
    missing = sorted(set(ids).difference(rows))
    if missing:
        raise MissingSource(
            "cutoff_inputs", f"eligible cases lack cutoff rows: {', '.join(missing)}"
        )
    capacity = spec.get("capacity")
    if type(capacity) is int and len(ranked) > capacity:
        raise MissingSource("picks", "recorded picks exceed recorded capacity")
    return {
        **{
            field: spec.get(field)
            for field in (
                "cutoff",
                "capacity",
                "live_version",
                "tie_break",
                "mandatory",
                "overrides",
            )
        },
        "membership": {"source": spec.get("membership_source"), "ids": ids},
        "actual": [case for _, case in ranked],
        "requests": rows,
    }


def inspect(path: Path) -> tuple[dict, dict]:
    config = versioned(tomllib.loads(path.read_text(encoding="utf-8")), "intake")
    shifts = mapping(config.get("shifts"), "intake shifts")
    if not shifts:
        raise InputError("intake must name at least one shift")
    columns = mapping(config.get("columns", {}), "intake columns")
    inputs = mapping(config.get("inputs"), "intake ranking inputs")
    for values in (columns, inputs):
        for name, header in values.items():
            string(name, "logical field")
            string(header, f"column for {name}")
    if "id" in inputs:
        raise InputError("id is reserved for the request ID")
    batches, results = {}, {}
    for name, spec in shifts.items():
        mapping(spec, f"{name} intake")
        try:
            batch = convert(path.parent, spec, columns, inputs)
            issue = cutoff_issue(name, batch)
            if issue:
                raise MissingSource("policy", issue)
        except (OSError, InputError) as exc:
            kind = exc.source if isinstance(exc, MissingSource) else "cutoff_inputs"
            results[name] = {
                "status": "Not ready",
                "source": kind,
                "reason": str(exc),
                "capture": CAPTURE[kind],
            }
        else:
            batches[name] = batch
            results[name] = {
                "status": "Ready for replay",
                "eligible": len(batch["requests"]),
                "selected": len(batch["actual"]),
            }
            if len(batch["actual"]) == len(batch["requests"]):
                results[name]["note"] = (
                    "Every listed case was selected. Check that the independent population includes any unreviewed cases."
                )
    counts = Counter(
        case for batch in batches.values() for case in batch["membership"]["ids"]
    )
    for name, batch in batches.items():
        if any(counts[case] > 1 for case in batch["membership"]["ids"]):
            results[name] = {
                "status": "Not ready",
                "source": "eligible",
                "reason": "eligible IDs overlap across shifts",
                "capture": CAPTURE["eligible"],
            }
    ready = all(item["status"] == "Ready for replay" for item in results.values())
    return {"schema": 1, "ready": ready, "shifts": results}, {
        "schema": 1,
        "shifts": batches,
    }


def write_export(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".telryn-intake-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        try:
            os.link(temp, path)
        except FileExistsError as exc:
            raise InputError(f"export already exists: {path}") from exc
    finally:
        temp.unlink(missing_ok=True)
