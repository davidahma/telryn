"""Freeze an external QA decision and independently check its classification and outcome."""

from collections import Counter
from functools import cache
from hashlib import sha256
from html import escape

from .contracts import InputError, fingerprint, mapping, string, timestamp

MAX_EXPORT = 24 * 1024 * 1024

COLUMNS = (
    "case_id",
    "revision",
    "scorecard",
    "scorecard_version",
    "scored_at",
    "decision",
    "complaint_prediction",
    "score_ref",
)
MEASURES = ("classification_disagreement", "passed_deadline_miss")
LABELS = {
    "classification_disagreement": "Vendor complaint classification disagreed with the human grade",
    "passed_deadline_miss": "QA pass with a missed complaint registration deadline by cutoff",
}


def load(config, folder, population, reviewed, coverage, dimensions):
    from . import weekly

    config = mapping(config, "score_audit")
    if set(config) - {"native"} != {"file", "scorecard", "version", "cutoff", "scope"}:
        raise InputError("score_audit needs file, scorecard, version, cutoff and scope")
    scope = config["scope"]
    if scope not in ("all_scored", "unreviewed_scored"):
        raise InputError("score_audit scope must be all_scored or unreviewed_scored")
    card = weekly._field(config["scorecard"], "scorecard")
    version = weekly._field(config["version"], "scorecard version")
    cutoff = timestamp(config["cutoff"], "score audit cutoff")
    closures = coverage.get("completed_at")
    if closures is None:
        raise InputError("a score audit requires source coverage with dated closures")
    if any(timestamp(t, "case closure") > cutoff for t in closures.values()):
        raise InputError("score audit cutoff precedes a source conversation closure")
    path = folder / string(config["file"], "score export")
    if not path.is_file() or path.stat().st_size > MAX_EXPORT:
        raise InputError("supply a score CSV file of at most 24 MiB")
    with path.open("rb") as stream:
        raw = stream.read(MAX_EXPORT + 1)
    if len(raw) > MAX_EXPORT:
        raise InputError("score export exceeds 24 MiB")
    if "native" in config:
        from . import score_import

        rows = score_import.read(
            raw,
            config["native"],
            {c: r["revision"] for c, r in population.items()},
            card,
            version,
        )
    else:
        rows = weekly._csv(raw, COLUMNS, "score export")
    included = set(rows)
    if scope == "unreviewed_scored":
        included.difference_update(reviewed)
    domains = {name: Counter() for name in MEASURES}
    refs = set()
    for ident, row in rows.items():
        case = population.get(ident)
        if case is None or row["revision"] != case["revision"]:
            raise InputError(
                "score export case and revision must match the frozen population"
            )
        if row["scorecard"] != card or row["scorecard_version"] != version:
            raise InputError("score export mixes scorecards or versions")
        when = timestamp(row["scored_at"], "score timestamp")
        if (
            timestamp(row.get("first_score_at", row["scored_at"]), "first score")
            < timestamp(closures[ident], "case closure")
            or when > cutoff
        ):
            raise InputError(
                "score timestamp must fall between case closure and audit cutoff"
            )
        if row["decision"] not in ("pass", "fail") or row[
            "complaint_prediction"
        ] not in ("yes", "no", "unknown"):
            raise InputError(
                "score export needs pass/fail and a yes/no/unknown complaint prediction"
            )
        if row["score_ref"] in refs:
            raise InputError(
                "score_ref must identify one score record per frozen conversation"
            )
        refs.add(row["score_ref"])
        if ident not in included:
            continue
        case["qa_score"] = {
            k: row[k]
            for k in ("scored_at", "decision", "complaint_prediction", "score_ref")
        }
        cell = weekly._stratum(case, dimensions)
        if row["decision"] == "pass":
            domains["passed_deadline_miss"][cell] += 1
        if row["complaint_prediction"] != "unknown":
            domains["classification_disagreement"][cell] += 1
    counts = {
        "source_population": len(population),
        "scored": len(rows),
        "unscored": len(population) - len(rows),
        "ordinary_reviewed": len(reviewed),
        "excluded_previously_reviewed": len(rows) - len(included),
        "eligible_previously_reviewed": len(included.intersection(reviewed)),
        "eligible_scored": len(included),
        "eligible_passes": domains["passed_deadline_miss"].total(),
        "eligible_classified": domains["classification_disagreement"].total(),
    }
    return {
        "scope": scope,
        "scorecard": card,
        "version": version,
        "cutoff": config["cutoff"],
        "sha256": sha256(raw).hexdigest(),
        **({"native": config["native"]} if "native" in config else {}),
        "coverage": counts,
        "domains": domains,
    }


def _metric(cells, rows):
    from . import weekly

    # Conditional on how many members of this domain were drawn in each original
    # stratum, those members are a uniform sample without replacement of the domain.
    strata = {key: {"population": size, "sample": 0} for key, size in cells.items()}
    determinate, found = Counter(), Counter()
    for key, value in rows:
        strata[key]["sample"] += 1
        if value is not None:
            determinate[key] += 1
            found[key] += int(value)
    n = sum(cells.values())
    uncertain = sum(0 < s["sample"] < s["population"] for s in strata.values())
    total, _ = weekly._count_evidence(
        strata, found, determinate, cache(weekly._interval), uncertain
    )

    def rates(bounds):
        return [v / n for v in bounds] if n and bounds is not None else None

    return {
        "denominator": n,
        "assigned": len(rows),
        "determinate": determinate.total(),
        "confirmed": found.total(),
        "confidence": 0.95,
        "range_kind": total["range_kind"] if n else "empty",
        "estimate": total["estimate"] / n
        if n and total["estimate"] is not None
        else None,
        "estimate_range": rates(total["estimate_range"]),
        "interval": rates(total["interval"]),
        "count_interval": total["interval"],
    }


def summary(state):
    from . import complaints, weekly
    from .weekly_review import effective_grades

    audit = state["score_audit"]
    grades = effective_grades(state)
    reconciled = (
        complaints.reconcile(state)["cases"] if "complaint_records" in state else []
    )
    evidence = {c["case_id"]: c for c in reconciled}
    labels = {k: [] for k in MEASURES}
    cases = []
    for case in state["cases"]:
        ident = case["case_id"]
        score = case["qa_score"]
        human = grades.get(ident, {}).get("serious")
        cell = weekly._stratum(case, state.get("group_by", []))
        prediction = score["complaint_prediction"]
        disagreement = None
        if prediction != "unknown":
            disagreement = None if human is None else (prediction == "yes") != human
            labels["classification_disagreement"].append((cell, disagreement))
        outcome = evidence.get(ident, {})
        status = outcome.get("registration_at_cutoff", "unresolved")
        missed = outcome.get("deadline_miss")
        if human is False:
            missed = False
        if score["decision"] == "pass":
            labels["passed_deadline_miss"].append((cell, missed))
        cases.append(
            {
                "case_id": ident,
                "revision": case["revision"],
                **score,
                "human_complaint": human,
                "classification_disagreement": disagreement,
                "registration_at_cutoff": status,
                "deadline_miss": missed,
            }
        )
    return {
        **{k: v for k, v in audit.items() if k != "domains"},
        **{name: _metric(audit["domains"][name], labels[name]) for name in MEASURES},
        "cases": cases,
    }


def sections(result):
    s = result["coverage"]
    return [
        f"Scored {s['scored']} of {s['source_population']} source conversations. "
        f"The audit covers {s['eligible_scored']} scored conversations, including "
        f"{s['eligible_previously_reviewed']} with a completed ordinary review. "
        f"{s['unscored']} had no score and {s['excluded_previously_reviewed']} scored conversations were excluded for prior review. "
        f"{s['eligible_scored'] - s['eligible_classified']} included scores had no definite complaint prediction.",
        deadline_headline(result["passed_deadline_miss"]),
        *(_metric_line(LABELS[k], result[k]) for k in MEASURES),
    ]


def format_bounds(lower, upper, format_value):
    return (
        format_value(lower)
        if lower == upper
        else f"{format_value(lower)}–{format_value(upper)}"
    )


def deadline_headline(metric):
    n = metric["denominator"]
    if n == 0:
        return "No QA passes in scope."
    if not metric["assigned"]:
        return f"No QA passes sampled from the {n:,} in scope."
    # Both the private metric and the aggregate bank receipt carry population
    # bounds as fractions of this exact integer denominator.
    lower, upper = (round(v * n) for v in metric["interval"])
    counts = format_bounds(lower, upper, lambda x: f"{x:,}")
    unresolved = metric["assigned"] - metric["determinate"]
    if metric["assigned"] == n:
        return f"{counts} of {n:,} QA passes missed the deadline" + (
            f" ({unresolved:,} unresolved)." if unresolved else "."
        )
    return f"An estimated {counts} of {n:,} QA passes missed the deadline (95% population range). {unresolved:,} sampled passes remain unresolved."


def rate_summary(metric):
    interval = metric["interval"]
    if interval is None:
        return "No eligible conversations."
    bounds = format_bounds(*interval, lambda x: f"{x:.1%}")
    if metric["assigned"] == metric["denominator"]:
        return f"Census: no sampling error. {'Rate' if interval[0] == interval[1] else 'Range from unresolved evidence'} {bounds}."
    estimate = metric["estimate"]
    point = f"Estimated rate {estimate:.1%}. " if estimate is not None else ""
    return f"{point}95% population range {bounds}."


def _metric_line(label, metric):
    return (
        f"{label}. {metric['confirmed']} confirmed among {metric['assigned']} assignments "
        f"({metric['determinate']} resolved). Population denominator {metric['denominator']}. "
        f"{rate_summary(metric)}"
    )


def html(result, *, has_records):
    parts = [
        '<section id="score-audit"><h2>Independent QA score audit</h2>',
        f"<p>{escape(result['scorecard'])} · version {escape(result['version'])} · scores through {escape(result['cutoff'])}</p>",
        *(f"<p>{escape(line)}</p>" for line in sections(result)),
        "<p>A complaint can be correctly classified and still miss its registration deadline. "
        "A deadline after cutoff has not been missed at that cutoff. Missing or disputed evidence widens the range.</p>",
    ]
    for c in result["cases"]:
        decision = escape(c["decision"].upper())
        label = {
            True: "missed deadline",
            False: "no deadline miss by cutoff",
            None: "deadline evidence unresolved",
        }[c["deadline_miss"]]
        human = {True: "yes", False: "no", None: "unresolved"}[c["human_complaint"]]
        link = (
            f' <a href="#complaint-{fingerprint(c["case_id"])}">Open case evidence for {escape(c["case_id"])}</a>'
            if has_records
            else ""
        )
        parts.append(
            f"<p><strong>{escape(c['case_id'])} · QA {decision}</strong> · {label}. Vendor complaint classification {escape(c['complaint_prediction'])}, human grade {human}. Score {escape(c['score_ref'])} at {escape(c['scored_at'])}, conversation revision {escape(c['revision'])}.{link}</p>"
        )
    parts.append("</section>")
    return "".join(parts)
