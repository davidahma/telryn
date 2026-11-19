"""Freeze a stratified weekly QA sample and estimate serious cases outside ordinary QA."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import math
import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from pathlib import Path

from .contracts import (
    InputError,
    canonical,
    fingerprint,
    integer,
    mapping,
    read_json,
    sequence,
    string,
    versioned,
)

STRATA = ("flagged", "unflagged")
DIMENSIONS = ("channel", "agent_type", "product")
CASE_COLUMNS = ("case_id", "revision", "evidence_ref", "screen_flag")
HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
SAMPLING = "hmac-sha256-without-replacement-v1"
EXACT_LIMIT = 1000


def _field(value: object, label: str, *, optional: bool = False) -> str:
    if optional and value == "":
        return ""
    value = string(value, label)
    if value != value.strip() or not value.isprintable():
        raise InputError(f"{label} must be single-line text without outer whitespace")
    return value


def _week(value: object) -> str:
    if type(value) is date:
        return value.isoformat()
    value = _field(value, "week")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise InputError("week must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise InputError("week must use YYYY-MM-DD")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not HEX_DIGEST.fullmatch(value):
        raise InputError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _csv(
    raw: bytes, columns: tuple[str, ...], label: str, *, optional: tuple = ()
) -> dict[str, dict]:
    reader = csv.DictReader(
        io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True
    )
    header = reader.fieldnames or []
    if len(header) != len(set(header)) or not set(columns) <= set(header) <= set(
        columns + optional
    ):
        raise InputError(
            f"{label} CSV must have exactly these columns: {', '.join(columns)}"
        )
    rows = {}
    for row in reader:
        if None in row or any(row[key] is None for key in header):
            raise InputError(
                f"{label} CSV line {reader.line_num} has the wrong field count"
            )
        for key in columns:
            _field(row[key], f"{label} CSV line {reader.line_num} {key}")
        case = row["case_id"]
        if case in rows:
            raise InputError(
                f"{label} CSV has a duplicate case_id on line {reader.line_num}"
            )
        if "screen_flag" in row:
            flag = row["screen_flag"].lower()
            if flag not in ("true", "false", "1", "0"):
                raise InputError(
                    f"{label} CSV line {reader.line_num} needs a boolean screen_flag"
                )
            row["screen_flag"] = flag in ("true", "1")
        rows[case] = row
    return rows


def _stratum(row: dict, dimensions: tuple | list = ()) -> str:
    side = "flagged" if row["screen_flag"] else "unflagged"
    return canonical([side, *(row[k] for k in dimensions)]) if dimensions else side


def _dimensions(value: object) -> list[str]:
    dims = sequence(value, "group_by")
    if any(d not in DIMENSIONS for d in dims) or len(dims) != len(set(dims)):
        raise InputError(
            "group_by must contain distinct channel, agent_type or product fields"
        )
    return dims


def cell_label(key: str, info: dict) -> str:
    return info.get("label", key.capitalize())


def cell_flag(key: str, info: dict) -> bool:
    return info.get("screen_flag", key == "flagged")


@dataclass
class Frame:
    plan: dict
    week: str
    meta: dict
    population: dict
    reviewed: dict
    pools: dict
    cells: dict
    dimensions: list


def _cell_descriptor(row: dict, dims: list) -> dict:
    if not dims:
        return {}
    groups = {k: row[k] for k in dims}
    label = " · ".join(
        [_stratum(row).capitalize(), *(f"{k}={v}" for k, v in groups.items())]
    )
    return {"label": label, "screen_flag": row["screen_flag"], "groups": groups}


def _sample_size(target: object, size: int, label: str) -> int:
    if isinstance(target, str):
        if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,4})?%", target):
            raise InputError(
                f"{label} needs a nonnegative whole count or a percentage such as 5%"
            )
        rate = Fraction(target[:-1]) / 100
        if rate > 1:
            raise InputError(f"{label} percentage must be between 0% and 100%")
        return math.ceil(rate * size)
    return min(size, integer(target, label))


def load_frame(plan_path: Path) -> Frame:
    """Read and reconcile the eligible population without touching the draw ledger."""
    plan = versioned(
        tomllib.loads(plan_path.read_text(encoding="utf-8")), "weekly plan"
    )
    meta = {
        key: _field(plan.get(key), key)
        for key in ("rubric", "rubric_question", "screen_version")
    }
    for key in ("finding_label", "scope_note"):
        if key in plan:
            meta[key] = _field(plan[key], key)
    week = _week(plan.get("week"))
    path = plan_path.parent / string(plan.get("population"), "population")
    raw = path.read_bytes()
    raw_digest = hashlib.sha256(raw).hexdigest()
    receipt_digest = None
    receipt = {}
    generated_screen = meta["screen_version"].startswith(
        ("local-model:", "policy-events:")
    )
    if "screen_receipt" in plan:
        receipt = read_json(
            plan_path.parent / string(plan["screen_receipt"], "screen_receipt"),
            "screen receipt",
        )
        if (
            receipt.get("screen_version") != meta["screen_version"]
            or receipt.get("population_sha256") != raw_digest
        ):
            raise InputError(
                "screen receipt does not match the weekly population or screen version"
            )
        receipt_digest = fingerprint(receipt)
    elif generated_screen:
        raise InputError("a built-in screen weekly plan requires its screen_receipt")
    dims = _dimensions(plan.get("group_by", []))
    population = _csv(
        raw, CASE_COLUMNS + tuple(dims), "population", optional=DIMENSIONS
    )
    coverage = path.with_name(path.name + ".coverage.json")
    if "coverage_receipt" in plan:
        coverage = plan_path.parent / string(
            plan["coverage_receipt"], "coverage_receipt"
        )
    if "coverage_receipt" in plan or coverage.exists():
        from .integrations_coverage import load_coverage

        meta["coverage"] = load_coverage(coverage, population, week)
    if generated_screen or "coverage_sha256" in receipt:
        expected = fingerprint(meta["coverage"]) if "coverage" in meta else None
        if receipt.get("coverage_sha256") != expected:
            raise InputError(
                "screen receipt does not match the source coverage or completion cutoffs"
            )
    population = {
        i: {k: r[k] for k in (*CASE_COLUMNS, *dims)} for i, r in population.items()
    }
    if meta["screen_version"] == "none" and any(
        row["screen_flag"] for row in population.values()
    ):
        raise InputError(
            "weekly runs without a screen must set every screen_flag to false"
        )
    reviewed = _csv(
        (plan_path.parent / string(plan.get("reviewed"), "reviewed")).read_bytes(),
        ("case_id",),
        "reviewed",
    )
    if not reviewed.keys() <= population.keys():
        raise InputError("ordinary reviewed IDs must belong to the weekly population")

    pools = {} if dims else {s: [] for s in STRATA}
    cells = {} if dims else {s: {} for s in STRATA}
    for ident, row in population.items():
        if ident in reviewed:
            continue
        cell = _stratum(row, dims)
        pools.setdefault(cell, []).append(ident)
        cells.setdefault(cell, _cell_descriptor(row, dims))
    # A stable ordering makes identical budgets independent of the export's row order.
    cells = {
        key: {**cells[key], "population": len(pools[key])} for key in sorted(cells)
    }
    meta.update(population_sha256=raw_digest, screen_receipt_digest=receipt_digest)
    return Frame(plan, week, meta, population, reviewed, pools, cells, dims)


def prepare(plan_path: Path) -> dict:
    """Commit the sampling frame and frozen design, then issue the assignments."""
    from .audit_trust import committed_draw
    from .planning import allocate, design

    frame = load_frame(plan_path)
    plan, population, reviewed = frame.plan, frame.population, frame.reviewed
    meta = frame.meta.copy()
    if "planning" in plan:
        planned = design(frame.cells, mapping(plan["planning"], "planning"))
        if not planned["coverage_complete"]:
            raise InputError(
                "planned minutes cannot cover every nonempty stratum; increase the budget or combine groups"
            )
        targets = {row["key"]: row["sample"] for row in planned["allocations"]}
        meta["planning"] = planned
    else:
        targets = {}
        for side in STRATA:
            members = {
                k: info
                for k, info in frame.cells.items()
                if cell_flag(k, info) == (side == "flagged")
            }
            size = sum(g["population"] for g in members.values())
            target = _sample_size(plan.get(f"{side}_sample"), size, f"{side}_sample")
            if members:
                targets.update(allocate(members, target))
    sample_targets = (
        targets
        if frame.dimensions or "planning" in plan
        else {s: plan[f"{s}_sample"] for s in STRATA}
    )
    frame_id = fingerprint([sorted(population), sorted(reviewed)])
    design_id = fingerprint(
        [
            frame.week,
            plan["rubric"],
            plan["rubric_question"],
            plan["screen_version"],
            frame.dimensions,
            meta.get("coverage"),
            meta.get("screen_receipt_digest"),
            plan.get("planning"),
            sample_targets,
            targets,
            sorted(population.values(), key=lambda row: row["case_id"]),
        ]
    )
    seed, commitment = committed_draw(frame.week, frame_id, design_id)
    key = bytes.fromhex(seed)
    coverage = meta.get("coverage", {})
    cutoffs = coverage.get("completed_at", {})
    if "completed_at" in coverage:
        # The lead needs the cutoff for each assignment. Retain other case IDs only as hashes.
        meta["coverage_sha256"] = fingerprint(coverage)
        meta["coverage"] = {k: v for k, v in coverage.items() if k != "completed_at"}

    def rank(case: str) -> tuple[bytes, str]:
        return hmac.digest(key, case.encode("utf-8"), "sha256"), case

    strata, cases = {}, []
    for cell, info in frame.cells.items():
        chosen = sorted(frame.pools[cell], key=rank)[: targets[cell]]
        for case in chosen:
            row = population[case]
            cases.append({**row, "completed_at": cutoffs[case]} if cutoffs else row)
        strata[cell] = {**info, "sample": len(chosen)}
    return {
        "schema": 1,
        "week": frame.week,
        **meta,
        "sampling": SAMPLING,
        "sample_targets": sample_targets,
        "group_by": frame.dimensions,
        "seed": seed,
        "draw_commitment": commitment,
        "population_count": len(population),
        "reviewed_count": len(reviewed),
        "population_id_hashes": sorted(map(fingerprint, population)),
        "reviewed_id_hashes": sorted(map(fingerprint, reviewed)),
        "strata": strata,
        "cases": sorted(cases, key=lambda r: r["case_id"]),
        "grades": {},
    }


def _membership(state: dict, key: str, expected: int) -> set[str]:
    entries = sequence(state.get(key), key)
    if len(entries) != expected:
        raise InputError(f"weekly {key} does not match its population count")
    for item in entries:
        _digest(item, key)
    result = set(entries)
    if len(result) != expected or entries != sorted(result):
        raise InputError(f"weekly {key} must be unique and in canonical order")
    return result


def validate_state(state: dict) -> tuple[dict, dict]:
    """Check the frozen sampling frame and grade semantics without computing estimates."""
    versioned(state, "weekly audit")
    _week(state.get("week"))
    for key in ("rubric", "rubric_question", "screen_version"):
        _field(state.get(key), key)
    for key in ("finding_label", "scope_note"):
        if key in state:
            _field(state[key], key)
    if state.get("sampling") != SAMPLING:
        raise InputError("unsupported weekly sampling algorithm")
    _digest(state.get("seed"), "weekly sampling seed")
    _digest(state.get("population_sha256"), "weekly population fingerprint")
    if state.get("screen_receipt_digest") is not None or state[
        "screen_version"
    ].startswith(("local-model:", "policy-events:")):
        _digest(state.get("screen_receipt_digest"), "screen receipt fingerprint")
    population = _membership(
        state,
        "population_id_hashes",
        integer(state.get("population_count"), "population_count"),
    )
    reviewed = _membership(
        state,
        "reviewed_id_hashes",
        integer(state.get("reviewed_count"), "reviewed_count"),
    )
    if not reviewed <= population:
        raise InputError("weekly ordinary reviews are outside the population")

    strata = mapping(state.get("strata"), "weekly strata")
    dims = _dimensions(state.get("group_by", []))
    if not dims and set(strata) != set(STRATA):
        raise InputError("weekly audit has invalid strata")
    cases = {}
    sample_sizes: Counter = Counter()
    for case in sequence(state.get("cases"), "weekly sample"):
        mapping(case, "weekly sample case")
        for key in ("case_id", "revision", "evidence_ref"):
            _field(case.get(key), key)
        case_id = case["case_id"]
        hashed = fingerprint(case_id)
        if case_id in cases or hashed not in population or hashed in reviewed:
            raise InputError(
                "weekly case is duplicate, reviewed, or outside the population"
            )
        if type(case.get("screen_flag")) is not bool:
            raise InputError("weekly case requires a boolean screen_flag")
        cases[case_id] = case
        for dim in dims:
            _field(case.get(dim), dim)
        sample_sizes[_stratum(case, dims)] += 1
    if not sample_sizes.keys() <= strata.keys():
        raise InputError("sample contains an unregistered stratum")
    total = 0
    for s, info in strata.items():
        mapping(info, f"weekly {s} stratum")
        if dims:
            groups = mapping(info.get("groups"), "weekly stratum groups")
            if set(groups) != set(dims) or type(info.get("screen_flag")) is not bool:
                raise InputError("invalid weekly stratum dimensions")
            for dim in dims:
                _field(groups[dim], dim)
            detail = {**groups, "screen_flag": info["screen_flag"]}
            if (
                s != _stratum(detail, dims)
                or info.get("label") != _cell_descriptor(detail, dims)["label"]
            ):
                raise InputError(
                    "weekly stratum identifier differs from its dimensions"
                )
        size = integer(info.get("population"), f"{s} population")
        sample = integer(info.get("sample"), f"{s} sample")
        if sample_sizes[s] != sample or sample > size:
            raise InputError("weekly sample does not match the frozen stratum")
        if "sample_targets" in state:
            targets = mapping(state["sample_targets"], "weekly sample targets")
            if (
                set(targets) != set(strata)
                or _sample_size(targets[s], size, f"{s}_sample") != sample
            ):
                raise InputError(
                    "weekly sample does not match its declared sampling target"
                )
        total += size
    if total != len(population) - len(reviewed):
        raise InputError("weekly strata do not reconcile with ordinary reviews")
    if state["screen_version"] == "none" and any(
        cell_flag(k, i) and i["population"] for k, i in strata.items()
    ):
        raise InputError("weekly runs without a screen cannot contain flagged cases")
    grades = mapping(state.get("grades"), "weekly grades")
    if not grades.keys() <= cases.keys():
        raise InputError("weekly grade is outside the frozen sample")
    for record in grades.values():
        mapping(record, "weekly grade")
        if "serious" not in record or (
            record["serious"] is not None and type(record["serious"]) is not bool
        ):
            raise InputError(
                "weekly grade requires a boolean serious answer or an inconclusive null"
            )
        serious = record["serious"]
        options = (
            ("new", "already_known", "unknown")
            if serious
            else (("none",) if serious is False else ("unknown",))
        )
        if record.get("disposition") not in options:
            raise InputError("weekly case disposition conflicts with serious answer")
        for field in ("owner", "action"):
            _field(
                record.get(field),
                f"grade {field}",
                optional=record["disposition"] != "new",
            )
        integer(record.get("minutes"), "grade minutes")
    return cases, grades


def _large_tail_accepts(size: int, n: int, k: int, x: int, divisor: int) -> bool:
    """Conservatively compare a hypergeometric lower tail with 1/divisor.

    Scale each mass relative to a mode. Enclose the neighboring masses by
    directed integer rounding. Once a lower mass reaches zero, every remaining
    mass on that side is at most the last upper mass (tails are monotone).
    Accept ambiguous comparisons, which only moves an interval endpoint out.
    """
    left, right = max(0, n + k - size), min(n, k)
    if x < left:
        return False
    if x >= right:
        return True
    mode = min(right, max(left, (n + 1) * (k + 1) // (size + 2)))
    scale = 1 << (64 + 2 * size.bit_length())
    total_low = scale
    tail_high = scale if mode <= x else 0
    for step, endpoint in ((-1, left), (1, right)):
        count = mode
        low = high = scale
        while count != endpoint:
            if step < 0:
                numerator = count * (size - k - n + count)
                denominator = (k - count + 1) * (n - count + 1)
            else:
                numerator = (k - count) * (n - count)
                denominator = (count + 1) * (size - k - n + count + 1)
            count += step
            low = low * numerator // denominator
            high = (high * numerator + denominator - 1) // denominator
            total_low += low
            if count <= x:
                tail_high += high
            if low == 0:
                remaining = (
                    min(count - 1, x) - left + 1 if step < 0 else min(right, x) - count
                )
                tail_high += high * max(0, remaining)
                break
    return divisor * tail_high >= total_low


def _upper_limit(size: int, sample: int, found: int, tail_divisor: int) -> int:
    """Largest serious population whose lower-tail probability is at least 1/divisor."""
    large = min(sample, size - sample) > EXACT_LIMIT
    total_ways = None if large else math.comb(size, sample)

    def accepts(serious: int) -> bool:
        n, k, x = sample, serious, found
        if n > size - n:
            # Count nonserious items among the unobserved complement instead.
            n, k, x = size - n, size - k, size - n - k + x
        if large:
            return _large_tail_accepts(size, n, k, x, tail_divisor)
        left, right = max(0, n + k - size), min(n, k)
        if x < left:
            return False
        if x >= right:
            return True
        term = math.comb(k, left) * math.comb(size - k, n - left)
        cumulative = term
        # Adjacent hypergeometric masses share binomial coefficients.
        for i in range(left, x):
            if cumulative * tail_divisor >= total_ways:
                return True
            term = term * (k - i) * (n - i) // ((i + 1) * (size - k - n + i + 1))
            cumulative += term
        return cumulative * tail_divisor >= total_ways

    low, high = found, size - sample + found
    while low < high:
        middle = (low + high + 1) // 2
        if accepts(middle):
            low = middle
        else:
            high = middle - 1
    return low


def _interval(size: int, sample: int, found: int, strata: int) -> tuple[int, int]:
    if size == sample:
        return found, found
    divisor = 40 * strata  # Two-sided 95% simultaneous coverage by Bonferroni.
    return (
        size - _upper_limit(size, sample, sample - found, divisor),
        _upper_limit(size, sample, found, divisor),
    )


def summarize(state: dict) -> dict:
    """Bound residual totals across all possible completions of the sampled grades."""
    cases, grades = validate_state(state)
    confirmed = Counter()
    found_by_stratum: Counter = Counter()
    determinate: Counter = Counter()
    dims = state.get("group_by", [])
    insufficient = minutes = 0
    for case, grade in grades.items():
        minutes += grade["minutes"]
        cell = _stratum(cases[case], dims)
        if grade["serious"] is not None:
            determinate[cell] += 1
        if grade["serious"] is None:
            insufficient += 1
        elif grade["serious"]:
            confirmed[grade["disposition"]] += 1
            found_by_stratum[cell] += 1
    graded = len(grades) - insufficient
    result = {
        "population": state["population_count"],
        "reviewed": state["reviewed_count"],
        "unreviewed": state["population_count"] - state["reviewed_count"],
        "assigned": len(cases),
        "graded": graded,
        "insufficient": insufficient,
        "confirmed_serious": confirmed.total(),
        "new": confirmed["new"],
        "already_known": confirmed["already_known"],
        "unknown": confirmed["unknown"],
        "review_minutes": minutes,
        "estimate": None,
        "interval": None,
        "confidence": 0.95,
        "estimate_reason": None,
        "estimate_range": None,
        "range_kind": "complete",
        "stratum_results": {},
        "coverage": state.get("coverage"),
        "draw_commitment": state.get("draw_commitment"),
        "agreement": None,
        "precision": None,
        "recall": None,
    }
    if any(s["population"] and not s["sample"] for s in state["strata"].values()):
        result["range_kind"] = "uncovered"
        result["estimate_reason"] = "A nonempty stratum had zero probability of review."
    elif graded != len(cases):
        result["estimate_reason"] = (
            "Some selected cases still lack a determinate human grade."
        )

    uncertain = sum(0 < s["sample"] < s["population"] for s in state["strata"].values())
    estimated = Counter()
    weighted_low = weighted_high = Fraction(0)
    lower = upper = 0
    populations = Counter()
    for group, info in state["strata"].items():
        size, sample = info["population"], info["sample"]
        found = found_by_stratum[group]
        observation = {
            "label": cell_label(group, info),
            "confirmed": found,
            "graded": determinate[group],
        }
        if size and not sample:
            result["stratum_results"][group] = {
                **observation,
                "range_kind": "uncovered",
                "estimate_range": None,
                "interval": [0, size],
            }
            continue
        possible = found + sample - determinate[group]
        low = Fraction(size * found, sample) if sample else Fraction(0)
        high = Fraction(size * possible, sample) if sample else Fraction(0)
        weighted_low += low
        weighted_high += high
        flag = cell_flag(group, info)
        estimated[flag] += low
        populations[flag] += size
        lo, hi = _interval(size, sample, found, uncertain)
        if possible != found:
            _, hi = _interval(size, sample, possible, uncertain)
        result["stratum_results"][group] = {
            **observation,
            "range_kind": "incomplete" if possible != found else "complete",
            "estimate_range": [float(low), float(high)],
            "interval": [lo, hi],
        }
        lower, upper = lower + lo, upper + hi
    if result["range_kind"] == "uncovered":
        return result
    result["estimate_range"] = [float(weighted_low), float(weighted_high)]
    result["interval"] = [lower, upper]
    if graded != len(cases):
        result["range_kind"] = "incomplete"
        return result
    serious = weighted_low
    result["estimate"] = float(serious)
    if state["screen_version"] == "none":
        return result
    flagged, unflagged = populations[True], populations[False]
    if flagged:
        result["precision"] = float(estimated[True] / flagged)
    if serious:
        result["recall"] = float(estimated[True] / serious)
    if result["unreviewed"]:
        correct = estimated[True] + unflagged - estimated[False]
        result["agreement"] = float(correct / result["unreviewed"])
    return result
