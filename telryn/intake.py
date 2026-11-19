"""Check ordinary cutoff files before anyone translates a ranking rule."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import tomllib
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from .artifacts import sync_directory
from .contracts import (
    InputError,
    canonical,
    exact_number,
    mapping,
    string,
    timestamp,
    unique_ids,
    validate_request,
    versioned,
)
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


@contextmanager
def source_errors(kind: str):
    try:
        yield
    except (ValueError, OSError, csv.Error) as exc:
        raise MissingSource(kind, str(exc)) from exc


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


def convert(base: Path, spec: dict, columns: dict, inputs: dict) -> dict:
    def column(name: str) -> str:
        return columns.get(name, name)

    def path_for(kind: str) -> Path:
        with source_errors(kind):
            value = spec.get(kind)
            if not isinstance(value, str) or not value.strip():
                raise InputError(f"no {kind} CSV was supplied")
            path = (base / value).resolve()
            if not path.is_file():
                raise InputError(f"the {kind} CSV is unavailable")
            return path

    id_header = column("id")
    eligible_id = columns.get("eligible_id", id_header)
    picks_id = columns.get("picks_id", id_header)
    cutoff_id = columns.get("cutoff_id", id_header)
    eligible_path = path_for("eligible")
    picks_path = path_for("picks")
    inputs_path = path_for("cutoff_inputs")
    with source_errors("eligible"):
        if eligible_path.samefile(picks_path) or eligible_path.samefile(inputs_path):
            raise InputError(
                "eligible membership must come from a separate source file"
            )
        eligible = file_rows(eligible_path, [eligible_id])
        ids = unique_ids([row[eligible_id] for row in eligible], "eligible IDs")
        if not ids:
            raise InputError("eligible membership is empty")
        string(spec.get("membership_source"), "membership source")

    with source_errors("picks"):
        positions = column("slot")
        picks = file_rows(picks_path, [picks_id, positions])
        ranked = sorted(
            (whole(row[positions], "recorded slot"), row[picks_id]) for row in picks
        )
        if [index for index, _ in ranked] != list(range(1, len(ranked) + 1)):
            raise InputError("recorded slots must be consecutive starting at 1")
        actual = unique_ids([case for _, case in ranked], "recorded picks")
        ineligible = sorted(set(actual).difference(ids))
        if ineligible:
            raise InputError(
                f"recorded picks include ineligible IDs: {', '.join(ineligible)}"
            )
        capacity = spec.get("capacity")
        if type(capacity) is int and capacity >= 0 and len(actual) > capacity:
            raise InputError("recorded picks exceed recorded capacity")

    with source_errors("policy"):
        cutoff = timestamp(spec.get("cutoff"), "shift cutoff")
        try:
            canonical(spec.get("overrides"))
        except (TypeError, ValueError) as exc:
            raise InputError(
                "override history must contain finite JSON values"
            ) from exc
    with source_errors("cutoff_inputs"):
        optional = (
            "evidence",
            "cohort",
            "lower_minutes",
            "upper_minutes",
            "time_basis",
        )
        headers = [
            cutoff_id,
            column("revision"),
            column("known_at"),
            *inputs.values(),
            *(columns[field] for field in optional if field in columns),
        ]
        input_rows = file_rows(inputs_path, headers)
        unique_ids([row[cutoff_id] for row in input_rows], "cutoff input IDs")
        rows = {}
        for row in input_rows:
            case = row[cutoff_id]
            if (
                timestamp(
                    row[column("known_at")].strip(), f"{case} cutoff availability"
                )
                > cutoff
            ):
                raise InputError(f"{case} revision is later than the cutoff.")
            revision = string(row[column("revision")].strip(), f"{case} revision")
            reference = row.get(column("evidence"), "").strip()
            item = {
                "revision": revision,
                "known_at": row[column("known_at")].strip(),
                "inputs": {
                    field: exact_number(row[header], f"{case} input {field}")
                    for field, header in inputs.items()
                },
                "evidence": [reference] if reference else [],
            }
            cohort = row.get(column("cohort"), "").strip()
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
            validate_request(case, item)
            rows[case] = item
        missing = sorted(set(ids).difference(rows))
        if missing:
            raise InputError(f"eligible cases lack cutoff rows: {', '.join(missing)}")
        extra = sorted(set(rows).difference(ids))
        if extra:
            raise InputError(f"cutoff rows include ineligible IDs: {', '.join(extra)}")
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
        "actual": actual,
        "requests": rows,
    }


def inspect(path: Path, batch_path: Path | None = None) -> tuple[dict, dict]:
    config = versioned(tomllib.loads(path.read_text(encoding="utf-8")), "intake")
    manifest = config
    source_dir = path.parent
    if batch_path is not None:
        if "shifts" in config:
            raise InputError(
                "a reusable intake mapping must omit shifts when --batches is supplied"
            )
        manifest = versioned(
            tomllib.loads(batch_path.read_text(encoding="utf-8")), "batch manifest"
        )
        if set(manifest) != {"schema", "shifts"}:
            raise InputError(
                "the batch manifest contains only schema and shifts; keep mappings in the intake file"
            )
        source_dir = batch_path.parent
    shifts = mapping(manifest.get("shifts"), "intake shifts")
    if not shifts:
        raise InputError("intake must name at least one shift")
    columns = mapping(config.get("columns", {}), "intake columns")
    inputs = mapping(config.get("inputs"), "intake ranking inputs")
    for values in (columns, inputs):
        for name, header in values.items():
            string(name, "logical field")
            string(header, f"column for {name}")
    valid_columns = {
        "id",
        "eligible_id",
        "picks_id",
        "cutoff_id",
        "slot",
        "revision",
        "known_at",
        "evidence",
        "cohort",
        "lower_minutes",
        "upper_minutes",
        "time_basis",
    }
    unknown = sorted(set(columns).difference(valid_columns))
    if unknown:
        raise InputError(f"unknown intake columns: {', '.join(unknown)}")
    if "id" in inputs:
        raise InputError("id is reserved for the request ID")
    batches, results = {}, {}
    for name, spec in shifts.items():
        mapping(spec, f"{name} intake")
        try:
            batch = convert(source_dir, spec, columns, inputs)
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
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
            sync_directory(path.parent)
        except FileExistsError as exc:
            raise InputError(f"export already exists: {path}") from exc
    finally:
        temp.unlink(missing_ok=True)
