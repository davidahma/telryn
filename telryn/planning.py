"""Budget a probability sample before opening any conversations."""

from __future__ import annotations

import heapq
import math
from collections import Counter
from pathlib import Path

from .contracts import InputError, canonical, integer, mapping
from .weekly import _interval, cell_flag, cell_label, load_frame


def _rate(value, field):
    if type(value) not in (float, int) or not 0 <= value <= 1:
        raise InputError(f"{field} must be a probability between 0 and 1")
    return float(value)


def allocate(cells: dict, slots: int, rates: dict | None = None) -> dict:
    """Minimize anticipated total-estimate variance with equal review costs."""
    targets = dict.fromkeys(cells, 0)
    nonempty = [k for k, info in cells.items() if info["population"]]
    if slots < len(nonempty):
        for k in sorted(nonempty, key=lambda k: (-cells[k]["population"], k))[:slots]:
            targets[k] = 1
        return targets
    weights = {}
    queue = []
    for key in nonempty:
        n = cells[key]["population"]
        rate = 0.5 if rates is None else rates[key]
        # A prior rate of zero still admits a rare finding. Smooth only the
        # allocation weights; the forecast uses the supplied rate unchanged.
        rate = (n * rate + 0.5) / (n + 1)
        weights[key] = n * n * rate * (1 - rate)
        targets[key] = 1
        if n > 1:
            heapq.heappush(queue, (-weights[key] / 2, key))
    for _ in range(slots - len(nonempty)):
        if not queue:
            break
        _, key = heapq.heappop(queue)
        targets[key] += 1
        n = targets[key]
        if n < cells[key]["population"]:
            heapq.heappush(queue, (-weights[key] / (n * (n + 1)), key))
    return targets


def _outcomes(N: int, n: int, K: int):
    """Weight possible observed counts under SRS from a frame with K positives.

    Center the hypergeometric recurrence at its mode to avoid overflow.
    For wide supports, deterministic midpoint quadrature limits the number of
    expensive interval inversions.
    """
    left, right = max(0, n + K - N), min(n, K)
    mode = min(right, max(left, (n + 1) * (K + 1) // (N + 2)))
    mass = {mode: 1.0}
    weight = 1.0
    for k in range(mode, left, -1):
        weight *= k * (N - K - n + k) / ((K - k + 1) * (n - k + 1))
        mass[k - 1] = weight
        if weight < 1e-16:
            break
    weight = 1.0
    for k in range(mode, right):
        weight *= (K - k) * (n - k) / ((k + 1) * (N - K - n + k + 1))
        mass[k + 1] = weight
        if weight < 1e-16:
            break
    total = sum(mass.values())
    if len(mass) <= 17:
        return {k: w / total for k, w in mass.items()}
    points = 17
    quantiles = Counter()
    cumulative = 0.0
    index = 0
    for k in sorted(mass):
        cumulative += mass[k]
        while index < points and cumulative >= (index + 0.5) * total / points:
            quantiles[k] += 1 / points
            index += 1
    return quantiles


def design(cells: dict, config: dict) -> dict:
    minutes = integer(config.get("minutes"), "planning minutes")
    cost = integer(config.get("minutes_per_grade"), "minutes per grade", minimum=1)
    rate = _rate(config.get("expected_rate"), "expected rate")
    flagged = _rate(config.get("flagged_rate", rate), "flagged rate")
    unflagged = _rate(config.get("unflagged_rate", rate), "unflagged rate")
    rates = {k: flagged if cell_flag(k, v) else unflagged for k, v in cells.items()}
    slots = minutes // cost
    targets = allocate(cells, slots, rates)
    assigned = sum(targets.values())
    covered = all(targets[k] or not v["population"] for k, v in cells.items())
    uncertain = sum(0 < targets[k] < c["population"] for k, c in cells.items())
    result = {
        "budget_minutes": minutes,
        "minutes_per_grade": cost,
        "slots": slots,
        "assigned": assigned,
        "unused_minutes": minutes - assigned * cost,
        "coverage_complete": covered,
        "expected_interval_width": None,
        "scenario_interval": None,
        "zero_findings_upper": None,
        "method": "Anticipated 95% interval width at supplied finite-population rates; 17-point quadrature for wide observation supports.",
        "allocations": [],
    }
    width = 0.0
    lower = upper = zero_upper = 0
    for key, info in cells.items():
        N, n = info["population"], targets[key]
        result["allocations"].append(
            {
                "key": key,
                "label": cell_label(key, info),
                "population": N,
                "sample": n,
                "expected_rate": rates[key],
            }
        )
        if not covered or N == 0:
            continue
        K = math.floor(N * rates[key] + 0.5)
        outcomes = _outcomes(N, n, K)
        center = math.floor(n * K / N + 0.5)
        limits = {
            y: _interval(N, n, y, uncertain) for y in outcomes.keys() | {0, center}
        }
        width += sum(w * (limits[y][1] - limits[y][0]) for y, w in outcomes.items())
        lower += limits[center][0]
        upper += limits[center][1]
        zero_upper += limits[0][1]
    if covered:
        result.update(
            expected_interval_width=round(width, 2),
            scenario_interval=[lower, upper],
            zero_findings_upper=zero_upper,
        )
    return result


def preview(
    plan_path: Path,
    *,
    minutes=None,
    minutes_per_grade=None,
    expected_rate=None,
    flagged_rate=None,
    unflagged_rate=None,
) -> dict:
    frame = load_frame(plan_path)
    config = mapping(frame.plan.get("planning", {}), "planning").copy()
    config.update(
        {
            key: value
            for key, value in {
                "minutes": minutes,
                "minutes_per_grade": minutes_per_grade,
                "expected_rate": expected_rate,
                "flagged_rate": flagged_rate,
                "unflagged_rate": unflagged_rate,
            }.items()
            if value is not None
        }
    )
    return design(frame.cells, config)


def register_arguments(commands):
    command = commands.add_parser(
        "plan-week",
        help="estimate weekly audit precision under a grading budget, without drawing cases",
    )
    command.add_argument("plan", type=Path)
    command.add_argument("--minutes", type=int)
    command.add_argument("--minutes-per-grade", type=int)
    command.add_argument(
        "--expected-rate",
        type=float,
        help="anticipated fraction of cases meeting the rubric",
    )
    command.add_argument("--flagged-rate", type=float)
    command.add_argument("--unflagged-rate", type=float)
    command.add_argument("--format", choices=("json", "text"), default="text")


def execute(args):
    result = preview(
        args.plan,
        minutes=args.minutes,
        minutes_per_grade=args.minutes_per_grade,
        expected_rate=args.expected_rate,
        flagged_rate=args.flagged_rate,
        unflagged_rate=args.unflagged_rate,
    )
    if args.format == "json":
        print(canonical(result))
    else:
        print(
            f"{result['assigned']} reviews from {result['budget_minutes']} minutes at {result['minutes_per_grade']} minutes each."
        )
        for group in result["allocations"]:
            print(
                f"  {group['label']}: {group['sample']} of {group['population']} cases"
            )
        if result["coverage_complete"]:
            print(
                f"Anticipated 95% interval width: {result['expected_interval_width']} cases."
            )
            print(
                f"95% upper bound if no sampled cases meet the rubric: {result['zero_findings_upper']}."
            )
        else:
            print(
                "Grading budget leaves a nonempty group unsampled. Combine groups or increase review time."
            )
    return 0
