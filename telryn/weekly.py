"""Freeze a stratified weekly QA sample and estimate serious cases outside ordinary QA."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import math
import re
import secrets
import tomllib
from collections import Counter
from datetime import date
from fractions import Fraction
from pathlib import Path

from .contracts import (
    InputError,
    fingerprint,
    integer,
    mapping,
    read_json,
    sequence,
    string,
    versioned,
)

STRATA = ("flagged", "unflagged")
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


def _csv(raw: bytes, columns: tuple[str, ...], label: str) -> dict[str, dict]:
    reader = csv.DictReader(
        io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True
    )
    if sorted(reader.fieldnames or ()) != sorted(columns):
        raise InputError(
            f"{label} CSV must have exactly these columns: {', '.join(columns)}"
        )
    rows = {}
    for row in reader:
        if None in row or any(row[key] is None for key in columns):
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


def _stratum(row: dict) -> str:
    return "flagged" if row["screen_flag"] else "unflagged"


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


def prepare(plan_path: Path) -> dict:
    """Draw once from both parts of the eligible, ordinarily unreviewed population."""
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
    raw = (plan_path.parent / string(plan.get("population"), "population")).read_bytes()
    raw_digest = hashlib.sha256(raw).hexdigest()
    receipt_digest = None
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
    elif meta["screen_version"].startswith("local-model:"):
        raise InputError("a local model weekly plan requires its screen_receipt")
    population = _csv(raw, CASE_COLUMNS, "population")
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

    seed = secrets.token_hex(32)
    key = bytes.fromhex(seed)

    # Separate selection from display. Sorting the final packet by case ID hides rank.
    def rank(case: str) -> tuple[bytes, str]:
        return hmac.digest(key, case.encode("utf-8"), "sha256"), case

    eligible = {s: [] for s in STRATA}
    for case, row in population.items():
        if case not in reviewed:
            eligible[_stratum(row)].append(case)
    strata, cases = {}, []
    for s, pool in eligible.items():
        count = _sample_size(plan.get(f"{s}_sample"), len(pool), f"{s}_sample")
        chosen = sorted(pool, key=rank)[:count]
        cases.extend(population[case] for case in chosen)
        strata[s] = {"population": len(pool), "sample": len(chosen)}
    return {
        "schema": 1,
        "week": week,
        **meta,
        "sampling": SAMPLING,
        "sample_targets": {s: plan[f"{s}_sample"] for s in STRATA},
        "seed": seed,
        "population_sha256": raw_digest,
        "screen_receipt_digest": receipt_digest,
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
    ].startswith("local-model:"):
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
    if set(strata) != set(STRATA):
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
        sample_sizes[_stratum(case)] += 1
    total = 0
    for s, info in strata.items():
        mapping(info, f"weekly {s} stratum")
        size = integer(info.get("population"), f"{s} population")
        sample = integer(info.get("sample"), f"{s} sample")
        if sample_sizes[s] != sample or sample > size:
            raise InputError("weekly sample does not match the frozen stratum")
        if "sample_targets" in state:
            targets = mapping(state["sample_targets"], "weekly sample targets")
            if (
                set(targets) != set(STRATA)
                or _sample_size(targets[s], size, f"{s}_sample") != sample
            ):
                raise InputError(
                    "weekly sample does not match its declared sampling target"
                )
        total += size
    if total != len(population) - len(reviewed):
        raise InputError("weekly strata do not reconcile with ordinary reviews")
    if state["screen_version"] == "none" and strata["flagged"]["population"]:
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


def _upper_limit(size: int, sample: int, found: int, tail_divisor: int) -> int:
    """Largest serious population whose lower-tail probability is at least 1/divisor."""
    total_ways = math.comb(size, sample)

    def accepts(serious: int) -> bool:
        n, k, x = sample, serious, found
        if n > size - n:
            # Count nonserious items among the unobserved complement instead.
            n, k, x = size - n, size - k, size - n - k + x
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
    if min(sample, size - sample) <= EXACT_LIMIT:
        return (
            size - _upper_limit(size, sample, sample - found, divisor),
            _upper_limit(size, sample, found, divisor),
        )
    # Hoeffding–Serfling with finite-population correction. Round outwards and
    # intersect with the logical bounds set by the adjudicated sample.
    radius = math.ceil(
        math.sqrt(size * (size - sample + 1) * math.log(divisor) / (2 * sample))
    )
    floor, remainder = divmod(size * found, sample)
    return max(found, floor - radius), min(
        size - sample + found, floor + bool(remainder) + radius
    )


def summarize(state: dict) -> dict:
    """Count confirmed findings; infer totals only with fully graded probability samples."""
    cases, grades = validate_state(state)
    confirmed = Counter()
    found_by_stratum: Counter = Counter()
    insufficient = minutes = 0
    for case, grade in grades.items():
        minutes += grade["minutes"]
        if grade["serious"] is None:
            insufficient += 1
        elif grade["serious"]:
            confirmed[grade["disposition"]] += 1
            found_by_stratum[_stratum(cases[case])] += 1
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
        "agreement": None,
        "precision": None,
        "recall": None,
    }
    if any(s["population"] and not s["sample"] for s in state["strata"].values()):
        result["estimate_reason"] = "A nonempty stratum had zero probability of review."
        return result
    if graded != len(cases):
        result["estimate_reason"] = (
            "Some selected cases still lack a determinate human grade."
        )
        return result

    uncertain = sum(s["sample"] < s["population"] for s in state["strata"].values())
    estimated = {}
    lower = upper = 0
    for s, info in state["strata"].items():
        size, sample = info["population"], info["sample"]
        found = found_by_stratum[s]
        estimated[s] = Fraction(size * found, sample) if sample else Fraction(0)
        lo, hi = _interval(size, sample, found, uncertain)
        lower, upper = lower + lo, upper + hi
    serious = estimated["flagged"] + estimated["unflagged"]
    result["estimate"] = float(serious)
    result["interval"] = [lower, upper]
    if state["screen_version"] == "none":
        return result
    flagged = state["strata"]["flagged"]["population"]
    unflagged = state["strata"]["unflagged"]["population"]
    if flagged:
        result["precision"] = float(estimated["flagged"] / flagged)
    if serious:
        result["recall"] = float(estimated["flagged"] / serious)
    if result["unreviewed"]:
        correct = estimated["flagged"] + unflagged - estimated["unflagged"]
        result["agreement"] = float(correct / result["unreviewed"])
    return result
