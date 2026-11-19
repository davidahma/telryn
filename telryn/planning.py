"""Budget a probability sample before opening any conversations."""

from __future__ import annotations

import heapq
import math
from collections import Counter
from decimal import Decimal
from fractions import Fraction
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


def _miss_probability(sizes, samples, positives):
    """Largest P(no positive sampled) across placements of a fixed number of positives."""
    if positives > sum(sizes[key] - samples[key] for key in sizes):
        return Fraction(0)
    # An unsampled group can absorb positives without changing the miss probability.
    positives -= sum(size for key, size in sizes.items() if not samples[key])
    if positives <= 0:
        return Fraction(1)
    sampled = [(size, samples[key]) for key, size in sizes.items() if samples[key]]
    # Placing one more positive multiplies the miss probability by 1 - n/(N-k).
    # Choose the largest (N-k)/n ratios. Find a common cutoff, then resolve the
    # few integer ties/rounding differences without iterating over all positives.
    remaining = sum(N for N, n in sampled) - positives
    slots = sum(n for N, n in sampled)
    for N, n in sorted(sampled, key=lambda pair: Fraction(*pair)):
        if N * slots >= remaining * n:
            break
        remaining -= N
        slots -= n
    counts = [max(0, N - n * remaining // slots) for N, n in sampled]
    marginal = [
        (Fraction(N - k + 1, n), index)
        for index, ((N, n), k) in enumerate(zip(sampled, counts))
        if k
    ]
    heapq.heapify(marginal)
    for _ in range(sum(counts) - positives):
        _, index = heapq.heappop(marginal)
        N, n = sampled[index]
        counts[index] -= 1
        if counts[index]:
            heapq.heappush(marginal, (Fraction(N - counts[index] + 1, n), index))
    miss = Fraction(1)
    for (N, n), k in zip(sampled, counts):
        a, b = sorted((n, k))
        miss *= Fraction(math.comb(N - b, a), math.comb(N, a))
    return miss


def _detection(cells, rates, targets, config, cost, reserve):
    if not {"detection_cases", "detection_target"} <= config.keys():
        raise InputError(
            "supply both detection_cases and detection_target for a decision forecast"
        )
    positives = integer(config.get("detection_cases"), "detection_cases", minimum=1)
    target = _rate(config.get("detection_target"), "detection_target")
    if target == 0:
        raise InputError("detection_target must be greater than zero")
    sizes = {key: cell["population"] for key, cell in cells.items()}
    population = sum(sizes.values())
    result = {
        "population": population,
        "cases": positives,
        "target": target,
        "assumption": "Random sampling within each group and correct recognition of every sampled positive.",
        "placement": "least favorable across the planned groups",
        "minimum_probability": None,
        "maximum_miss_probability": None,
        "minimum_sample": None,
        "minimum_budget_minutes": None,
        "status": "threshold_exceeds_population",
    }
    if positives > population:
        return result
    miss = _miss_probability(sizes, targets, positives)
    allowed_miss = 1 - Fraction(str(target))
    # Allocations are nested as slots increase. A committed audit also needs at
    # least one case from every nonempty group.
    groups = sum(size > 0 for size in sizes.values())

    def reaches(slots):
        return (
            _miss_probability(sizes, allocate(cells, slots, rates), positives)
            <= allowed_miss
        )

    low, high = groups - 1, groups
    while high < population and not reaches(high):
        low, high = high, min(2 * high, population)
    while high - low > 1:
        middle = (low + high) // 2
        if reaches(middle):
            high = middle
        else:
            low = middle
    meets = miss <= allowed_miss
    status = "target_met" if meets else "more_minutes_needed"
    if sum(targets.values()) < groups:
        status = "incomplete_coverage"
    result.update(
        minimum_probability=float(1 - miss),
        maximum_miss_probability=float(miss),
        certain=(miss == 0),
        minimum_sample=high,
        minimum_budget_minutes=high * cost + reserve,
        status=status,
    )
    return result


def design(cells: dict, config: dict) -> dict:
    minutes = integer(config.get("minutes"), "planning minutes")
    cost = integer(config.get("minutes_per_grade"), "minutes per grade", minimum=1)
    rates = None
    if "expected_rate" in config:
        rate = _rate(config["expected_rate"], "expected rate")
        flagged = _rate(config.get("flagged_rate", rate), "flagged rate")
        unflagged = _rate(config.get("unflagged_rate", rate), "unflagged rate")
        rates = {k: flagged if cell_flag(k, v) else unflagged for k, v in cells.items()}
    elif not {"detection_cases", "detection_target"} <= config.keys() or {
        "flagged_rate",
        "unflagged_rate",
    }.intersection(config):
        raise InputError(
            "supply an expected_rate for a precision forecast or both detection_cases and detection_target for a detection-only plan"
        )
    second = integer(config.get("second_review_minutes", 0), "second-review minutes")
    adjudication = integer(
        config.get("adjudication_minutes", 0), "adjudication minutes"
    )
    reserve = second + adjudication
    if reserve > minutes:
        raise InputError(
            "second-review and adjudication reserves exceed the total minutes"
        )
    slots = (minutes - reserve) // cost
    targets = allocate(cells, slots, rates)
    assigned = sum(targets.values())
    covered = all(targets[k] or not v["population"] for k, v in cells.items())
    uncertain = sum(0 < targets[k] < c["population"] for k, c in cells.items())
    result = {
        "budget_minutes": minutes,
        "minutes_per_grade": cost,
        "slots": slots,
        "assigned": assigned,
        "unused_minutes": minutes - reserve - assigned * cost,
        "coverage_complete": covered,
        "expected_interval_width": None,
        "scenario_interval": None,
        "zero_findings_upper": None,
        "method": "Anticipated 95% interval width at supplied finite-population rates; 17-point quadrature for wide observation supports.",
        "allocations": [],
    }
    if rates is None:
        result["method"] = (
            "Detection-only design. Equal assumed within-group variance for allocation. No assumed prevalence for an interval-width forecast."
        )
    if {
        "second_review_minutes",
        "adjudication_minutes",
        "detection_cases",
    }.intersection(config):
        result["staff_minutes"] = {
            "primary": assigned * cost,
            "second_review": second,
            "adjudication": adjudication,
        }
    if "detection_cases" in config or "detection_target" in config:
        result["detection"] = _detection(cells, rates, targets, config, cost, reserve)
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
                "expected_rate": rates[key] if rates is not None else None,
            }
        )
        if not covered or N == 0:
            continue
        if rates is None:
            zero_upper += _interval(N, n, 0, uncertain)[1]
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
            expected_interval_width=round(width, 2) if rates is not None else None,
            scenario_interval=[lower, upper] if rates is not None else None,
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
    detection_cases=None,
    detection_target=None,
    second_review_minutes=None,
    adjudication_minutes=None,
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
                "detection_cases": detection_cases,
                "detection_target": detection_target,
                "second_review_minutes": second_review_minutes,
                "adjudication_minutes": adjudication_minutes,
            }.items()
            if value is not None
        }
    )
    return design(frame.cells, config)


def decision_lines(plan):
    """Operator text shared by the CLI, browser preview and signed report."""
    lines = []
    if staff := plan.get("staff_minutes"):
        lines.append(
            f"The plan allows {staff['primary']:,} minutes for first grades, "
            f"{staff['second_review']:,} for second review and {staff['adjudication']:,} for adjudication."
        )
    if not (detection := plan.get("detection")):
        return lines
    if detection["status"] == "threshold_exceeds_population":
        return lines + [
            f"The decision threshold of {detection['cases']:,} cases exceeds the {detection['population']:,} conversations outside ordinary QA."
        ]
    probability = detection["minimum_probability"]
    if not detection["certain"] and probability > 0.999:
        chance = "over 99.9%"
    elif 0 < probability < 0.001:
        chance = "under 0.1%"
    else:
        chance = f"{probability:.1%}"
    lines.append(
        f"If {detection['cases']:,} of {detection['population']:,} conversations meet the rubric, "
        f"the chance of finding at least one is {chance} in the least favorable placement across groups. "
        "This assumes random sampling and correct recognition of every sampled positive."
    )
    if detection["status"] == "incomplete_coverage":
        lines.append("This budget leaves at least one group unsampled.")
    else:
        verdict = "meets" if detection["status"] == "target_met" else "falls short of"
        percent = format((Decimal(str(detection["target"])) * 100).normalize(), "f")
        lines.append(f"This budget {verdict} the {percent}% target.")
    lines.append(
        f"Covering all groups and reaching the detection target under this allocation takes at least {detection['minimum_sample']:,} first grades and {detection['minimum_budget_minutes']:,} total staff minutes."
    )
    if detection["status"] != "target_met":
        lines.append(
            "Increase review time, plan a longer period with its own population and budget, or narrow the question before drawing."
        )
    return lines


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
    command.add_argument(
        "--detection-cases",
        type=int,
        help="smallest total number of rubric-positive cases you need to detect",
    )
    command.add_argument(
        "--detection-target",
        type=float,
        help="required chance of finding at least one, from 0 (exclusive) to 1",
    )
    command.add_argument(
        "--second-review-minutes",
        type=int,
        help="total staff minutes reserved for independent checks",
    )
    command.add_argument(
        "--adjudication-minutes",
        type=int,
        help="total staff minutes reserved for resolving disputes",
    )
    command.add_argument("--format", choices=("json", "text"), default="text")


def execute(args):
    result = preview(
        args.plan,
        minutes=args.minutes,
        minutes_per_grade=args.minutes_per_grade,
        expected_rate=args.expected_rate,
        flagged_rate=args.flagged_rate,
        unflagged_rate=args.unflagged_rate,
        detection_cases=args.detection_cases,
        detection_target=args.detection_target,
        second_review_minutes=args.second_review_minutes,
        adjudication_minutes=args.adjudication_minutes,
    )
    if args.format == "json":
        print(canonical(result))
    else:
        print(
            f"{result['assigned']} reviews from {result['budget_minutes']} minutes at {result['minutes_per_grade']} minutes each."
        )
        for line in decision_lines(result):
            print(line)
        for group in result["allocations"]:
            print(
                f"  {group['label']}: {group['sample']} of {group['population']} cases"
            )
        if result["coverage_complete"]:
            if result["expected_interval_width"] is not None:
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
