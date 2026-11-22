"""Local committee, vendor and opted-in benchmark outputs from verified weekly reports."""

from datetime import date, timedelta
from html import escape
from pathlib import Path
import re

from . import control, score_audit, weekly_report
from .artifacts import new_directory
from .contracts import InputError, canonical, fingerprint, timestamp


def quarter_weeks(quarter):
    if not re.fullmatch(r"[0-9]{4}-Q[1-4]", quarter):
        raise InputError("use a calendar quarter such as 2026-Q4")
    year, number = int(quarter[:4]), int(quarter[-1])
    if not 1 <= year < 9999:
        raise InputError("quarter year out of range")
    first = date(year, 3 * number - 2, 1)
    monday = first + timedelta(days=(-first.weekday()) % 7)
    weeks = []
    while (monday.month - 1) // 3 + 1 == number:
        weeks.append(monday.isoformat())
        monday += timedelta(days=7)
    return weeks


def _delta(current, previous):
    if current is None or previous is None:
        return None
    return [current[0] - previous[1], current[1] - previous[0]]


def _scope(state):
    score = state.get("score_audit", {})
    profile = state["control"]
    return {
        "queue": profile["scope"],
        "rubric": state["rubric"],
        "question": state["rubric_question"],
        "basis": score.get("scope", profile.get("population_basis", "queue")),
        "scorecard": score.get("scorecard"),
        "version": score.get("version"),
        "channels": sorted((c["name"], c["coverage"]) for c in profile["channels"]),
        "synthetic": profile.get("synthetic", False),
    }


def _deadline_context(state):
    """Describe what supplied the dates and compare like observation horizons."""
    snapshot = state["complaint_records"]["snapshot"]
    evidence = snapshot.get("episodes", {})
    clock = evidence.get("policy")
    fixed = sorted(
        {
            c["policy_ref"]
            for c in snapshot["cases"].values()
            if c["deadline"] is not None
        }
    )
    cutoff, fraction = timestamp(snapshot["cutoff"], "complaint cutoff")
    start, _ = timestamp(state["week"] + "T00:00:00Z", "week start")
    display = (
        f"{clock['id']}@{clock['version']}" if clock else "Issuer-supplied deadlines"
    )
    if clock and fixed:
        display += " and issuer-supplied deadlines"
    context = fingerprint([clock, fixed, str(cutoff - start), fraction])
    return display, context, bool(fixed or not clock)


def compile_series(paths, quarter, *, require_scores):
    weeks = quarter_weeks(quarter)
    rows = {}
    clocks = {}
    scope = None
    for path in paths:
        state = weekly_report.read_state(path)
        if "control" not in state or "complaint_records" not in state:
            raise InputError("supply weekly reports with control and complaint records")
        if state.get("pack_reviews") or state["complaint_records"].get(
            "policy_reviews"
        ):
            raise InputError(
                "policy re-review required before including this week in a new statement"
            )
        week = state["week"]
        if week not in weeks or week in rows:
            raise InputError("week outside the quarter or duplicate weekly report")
        current = _scope(state)
        if scope is not None and current != scope:
            raise InputError(
                "queue, channels, population, evidence type, scorecard or rubric changed; issue separate statements"
            )
        scope = current
        row = control.committee_week(state)
        row["deadline_basis"], clocks[week], row["manual_deadlines"] = (
            _deadline_context(state)
        )
        row["deadline_comparison"] = "No adjacent prior week"
        if require_scores and "score_audit" not in state:
            raise InputError(
                "vendor calibration requires an independent score audit for every week"
            )
        if "score_audit" in state:
            scores = score_audit.summary(state)
            row["metrics"] = {k: scores[k] for k in score_audit.MEASURES}
            bounds = scores["classification_disagreement"]["interval"]
            row["agreement_rate_bounds"] = (
                [1 - bounds[1], 1 - bounds[0]] if bounds is not None else None
            )
        rows[week] = row
    if not rows:
        raise InputError("supply at least one verified report")
    for week in rows:
        prior = (date.fromisoformat(week) - timedelta(days=7)).isoformat()
        if prior in rows:
            if clocks[week] != clocks[prior]:
                note = "Different clock policy, deadline source or cutoff lag"
            elif rows[week]["manual_deadlines"]:
                note = "Confirm comparability of issuer-supplied dates"
            else:
                note = "Same clock policy and cutoff lag"
            rows[week]["deadline_comparison"] = note
        if "metrics" in rows[week] and prior in rows:
            rows[week]["disagreement_change_bounds"] = _delta(
                rows[week]["metrics"]["classification_disagreement"]["interval"],
                rows[prior]["metrics"]["classification_disagreement"]["interval"],
            )
    return {
        "schema": 1,
        "quarter": quarter,
        "scope": scope,
        "weeks": [rows[w] for w in weeks if w in rows],
        "missing_weeks": [w for w in weeks if w not in rows],
    }


def _range(values, *, percent=True):
    if values is None:
        return "unresolved"

    def fmt(v):
        return f"{v:.1%}" if percent else str(v)

    return fmt(values[0]) if values[0] == values[1] else " to ".join(map(fmt, values))


def html(series, kind):
    title = (
        "Quarterly committee and board pack"
        if kind == "board"
        else "Weekly vendor calibration"
    )
    scope = series["scope"]
    headings = ["Week", "Eligible", "Determinate grades / assigned"]
    scored = scope["scorecard"] is not None
    if scored:
        headings += [
            "Classified population",
            "Grade agreement",
            "Grade disagreement",
            "Change in disagreement (percentage points)",
            "QA passes",
            "Passed with missed deadline",
        ]
    if kind == "board":
        headings += [
            "Unregistered conversations at cutoff (range)",
            "Open follow-up cases",
            "Follow-up through",
        ]
    headings += ["Deadline basis", "Deadline comparison with prior week"]
    body = []
    for w in series["weeks"]:
        values = [w["week"], w["eligible"], f"{w['graded']} / {w['assigned']}"]
        if scored:
            d, m = (w["metrics"][key] for key in score_audit.MEASURES)
            change = w.get("disagreement_change_bounds")
            change_text = (
                "no comparable prior week"
                if change is None
                else f"{change[0] * 100:+.1f}"
                if change[0] == change[1]
                else " to ".join(f"{x * 100:+.1f}" for x in change)
            )
            values += [
                d["denominator"],
                _range(w["agreement_rate_bounds"]),
                _range(d["interval"]),
                change_text,
                m["denominator"],
                _range(m["interval"]),
            ]
        if kind == "board":
            values += [
                _range(w["unregistered_bounds"], percent=False),
                w["unresolved_cases"],
                w["as_of"],
            ]
        values += [w["deadline_basis"], w["deadline_comparison"]]
        body.append(
            "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in values) + "</tr>"
        )
    label = (
        "Synthetic evidence" if scope["synthetic"] else "Team-verified weekly evidence"
    )

    def e(v):
        return escape(str(v))

    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>{title}</title><style>body{{font:1rem/1.55 system-ui;margin:3vw;color:#182d36}}table{{border-collapse:collapse}}th,td{{padding:.65rem;text-align:left;border-bottom:1px solid #aaa}}</style><main><h1>{title}</h1><p>{e(series["quarter"])} · {e(scope["queue"])} · {label}</p><p>Population {e(scope["basis"])}. Rubric {e(scope["rubric"])}. Scorecard {e(scope["scorecard"] or "none")} {e(scope["version"] or "")}.</p><table><thead><tr>{"".join('<th scope="col">' + e(h) + "</th>" for h in headings)}</tr></thead><tbody>{"".join(body)}</tbody></table><p>Missing audit weeks (Monday start): {e(", ".join(series["missing_weeks"]) or "none")}.</p><p>Rates use each week's eligible population. Bounds include unresolved evidence and 95% sampling uncertainty where sampling occurred. Censuses have no sampling error. Changes compare adjacent weeks using the endpoints of their weekly bounds. They are descriptive, without a joint confidence guarantee. Review changes in the mix of cases, policy and source coverage before attributing a trend to the vendor.</p></main></html>"""


def _shareable(metric, floor):
    yes = metric["confirmed"]
    no = metric["determinate"] - yes
    unknown = metric["assigned"] - metric["determinate"]
    return metric["determinate"] >= floor and all(
        v == 0 or v >= floor for v in (yes, no, unknown)
    )


def benchmark(series, vendor, configuration, floor):
    for label in (vendor, configuration):
        if not isinstance(label, str) or not re.fullmatch(
            "[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", label
        ):
            raise InputError(
                "supply approved short vendor and configuration identifiers"
            )
    if type(floor) is not int or floor < 5:
        raise InputError("minimum disclosure cell must be at least five")
    weeks = []
    for w in series["weeks"]:
        metrics = {}
        for key, m in w["metrics"].items():
            metrics[key] = (
                {
                    "denominator": m["denominator"],
                    "assigned": m["assigned"],
                    "rate": m["estimate"],
                    "rate_bounds": m["interval"],
                }
                if _shareable(m, floor)
                else {"suppressed": True}
            )
        weeks.append({"week": w["week"], "metrics": metrics})
    return {
        "schema": 1,
        "quarter": series["quarter"],
        "vendor": vendor,
        "configuration": configuration,
        "synthetic": series["scope"]["synthetic"],
        "basis": series["scope"]["basis"],
        "minimum_cell": floor,
        "weeks": weeks,
    }


def register_arguments(commands):
    parser = commands.add_parser(
        "oversight", help="prepare board, vendor or opt-in benchmark reports"
    )
    choices = parser.add_subparsers(dest="kind", required=True)
    for name in ("board", "vendor", "benchmark"):
        sub = choices.add_parser(name)
        sub.add_argument("--quarter", required=True)
        sub.add_argument("--out", type=Path, required=True)
        sub.add_argument("reports", type=Path, nargs="+")
        if name == "benchmark":
            sub.add_argument("--opt-in", action="store_true", required=True)
            sub.add_argument("--vendor", required=True)
            sub.add_argument("--configuration", required=True)
            sub.add_argument("--min-cell", type=int, default=10)


def execute(args):
    series = compile_series(
        args.reports, args.quarter, require_scores=args.kind != "board"
    )
    if args.kind == "benchmark":
        filename = "aggregate.json"
        payload = (
            canonical(benchmark(series, args.vendor, args.configuration, args.min_cell))
            + "\n"
        )
    else:
        filename = "report.html"
        payload = html(series, args.kind)
    with new_directory(args.out) as staging:
        (staging / filename).write_text(payload, encoding="utf-8")
    print(args.out / filename)
    return 0
