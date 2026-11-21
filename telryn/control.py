"""Firm-defined complaint classification and the weekly committee statement."""

from collections import Counter
from datetime import date, datetime
from functools import cache
from html import escape
from statistics import median

from .contracts import InputError, integer, mapping, sequence, versioned
from .weekly_review import effective_grades

FIELDS = ("category", "vulnerability", "redress", "classification_reason")
DIMS = FIELDS[:3]
CHANNEL = {
    "name",
    "owner",
    "coverage",
    "ordinary_qa",
    "register_link",
    "retention",
    "review_date",
}


def _text(value, label):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2000
        or value.strip() != value
        or not value.isprintable()
    ):
        raise InputError(f"{label} needs single-line text of up to 2000 characters")
    return value


def _date(value):
    try:
        if date.fromisoformat(value).isoformat() == value:
            return
    except (ValueError, TypeError):
        pass
    raise InputError("review date must use YYYY-MM-DD")


def categories(value):
    labels = [
        _text(label, "category").casefold() for label in sequence(value, "categories")
    ]
    if (
        not labels
        or len(labels) != len(set(labels))
        or {"none", "unknown"}.intersection(labels)
    ):
        raise InputError(
            "supply distinct categories in ascending order, excluding none and unknown"
        )


def profile(value):
    cfg = versioned(value, "complaint control")
    if set(cfg) - {"population_basis", "schema", "synthetic"} != {
        "scope",
        "categories",
        "channels",
        "gap_owner",
        "gap_review_date",
        "onboarding_minutes",
    }:
        raise InputError(
            "control requires scope, categories, channels, gap_owner, gap_review_date and onboarding_minutes"
        )
    if type(cfg.get("synthetic", False)) is not bool:
        raise InputError("synthetic must be a boolean")
    if cfg.get("population_basis", "queue") not in ("queue", "shortlist_union"):
        raise InputError("population_basis must be queue or shortlist_union")
    for name in ("scope", "gap_owner"):
        _text(cfg[name], name)
    _date(cfg["gap_review_date"])
    integer(cfg["onboarding_minutes"], "onboarding minutes")
    categories(cfg["categories"])
    seen = set()
    for row in sequence(cfg["channels"], "channels"):
        if set(mapping(row, "channel")) != CHANNEL:
            raise InputError(
                "each channel needs name, owner, coverage, ordinary_qa, register_link, retention and review_date"
            )
        for key in CHANNEL:
            _text(row[key], key)
        _date(row["review_date"])
        if (
            row["coverage"] not in ("covered", "partial", "uncovered")
            or row["name"].casefold() in seen
        ):
            raise InputError(
                "channels require distinct names and covered, partial or uncovered coverage"
            )
        seen.add(row["name"].casefold())
    if not seen:
        raise InputError("enter a channel inventory")
    return cfg


def check_grade(cfg, grade):
    serious = grade["serious"]
    categories = (
        {"unknown", *cfg["categories"]}
        if serious
        else ({"none"} if serious is False else {"unknown"})
    )
    if grade.get("category") not in categories:
        raise InputError(
            "enter a firm category or unknown for a complaint, none for no, unknown for insufficient"
        )
    if grade.get("vulnerability") not in ("yes", "no", "unknown") or grade.get(
        "redress"
    ) not in ("none", "pending", "offered", "completed", "unknown"):
        raise InputError(
            "enter vulnerability (yes/no/unknown) and redress (none/pending/offered/completed/unknown)"
        )
    _text(grade.get("classification_reason"), "classification reason")


def downgrade(cfg, previous, current):
    ordered = ["none", *cfg["categories"]]
    before, after = previous["category"], current["category"]
    return before in ordered and (
        after == "unknown" or ordered.index(before) > ordered.index(after)
    )


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def summary(state):
    from . import complaints, weekly

    cfg = state["control"]
    grades = effective_grades(state)
    ledger = complaints.reconcile(state) if "complaint_records" in state else {}
    matched = {r["case_id"]: r for r in ledger.get("cases", [])}
    positives, determinate = Counter(), Counter()
    missing, registered, still_missing, unresolved = set(), set(), set(), []
    work = {cid for group in ledger.get("worklist", {}).values() for cid in group}
    overdue_now, newly_due = set(), set()
    latency = []
    for case in state["cases"]:
        cid = case["case_id"]
        row = matched.get(cid, {})
        grade = grades.get(cid, {})
        serious = grade.get("serious")
        status = row.get("registration_at_cutoff", "unresolved")
        key = weekly._stratum(case, state.get("group_by", []))
        if serious is False or status != "unresolved":
            determinate[key] += 1
        ident = (
            ("episode", row["episode_id"]) if row.get("episode_id") else ("case", cid)
        )
        if status == "missing":
            positives[key] += 1
            missing.add(ident)
            if row["registration_as_of"] == "registered":
                registered.add(ident)
            if row["registration_as_of"] == "missing":
                still_missing.add(ident)
        if row.get("registration_overdue_as_of"):
            overdue_now.add(ident)
            if status == "not_due":
                newly_due.add(ident)
        if serious is not False and (
            status == "unresolved"
            or cid in work
            or (status == "missing" and row["registration_as_of"] != "registered")
        ):
            closed = case.get("completed_at")
            age = (
                max(0, (_time(ledger["as_of"]) - _time(closed)).days)
                if closed and ledger
                else None
            )
            owner = grade.get("owner") or cfg["gap_owner"]
            if row.get("owner_as_of") == "unresolved":
                owner = cfg["gap_owner"]
            elif row.get("actions"):
                owner = row["actions"][-1]["owner"]
            unresolved.append(
                {
                    "case_id": cid,
                    "owner": owner,
                    "review_date": cfg["gap_review_date"],
                    "age_days_since_close": age,
                }
            )
        entries = row.get("register_entries", ())
        if (
            serious is True
            and case.get("completed_at")
            and row.get("registration_as_of") == "registered"
            and len(entries) == 1
        ):
            latency.append(
                (
                    _time(entries[0]["recorded_at"]) - _time(case["completed_at"])
                ).total_seconds()
                / 3600
            )
    strata = state["strata"]
    uncertain = sum(0 < s["sample"] < s["population"] for s in strata.values())
    bounds, groups = weekly._count_evidence(
        strata, positives, determinate, cache(weekly._interval), uncertain
    )
    registration_counts = {}
    for status, identities in (
        ("confirmed_unregistered", missing),
        ("since_registered", registered),
        ("still_missing", still_missing),
        ("overdue_at_followup", overdue_now),
        ("newly_due_since_cutoff", newly_due - missing),
    ):
        linked = sum(kind == "episode" for kind, _ in identities)
        registration_counts[status + "_episodes"] = linked
        registration_counts[status + "_unlinked_conversations"] = (
            len(identities) - linked
        )
    return {
        "scope": cfg["scope"],
        "population_basis": state.get("score_audit", {}).get(
            "scope", cfg.get("population_basis", "queue")
        ),
        "cutoff": ledger.get("cutoff"),
        "as_of": ledger.get("as_of"),
        "assigned": len(state["cases"]),
        "graded": sum(g["serious"] is not None for g in grades.values()),
        "queue": sum(cell["population"] for cell in state["strata"].values()),
        **registration_counts,
        "unregistered_conversations": {**bounds, "strata": groups},
        "classification_unresolved": [
            c for c, g in grades.items() if any(g.get(k) == "unknown" for k in DIMS)
        ],
        "open_disputes": [
            c
            for c, r in state.get("case_reviews", {}).items()
            if r["checks"]
            and r["checks"][-1]["disputed"]
            and not r["checks"][-1].get("decision")
        ],
        "unresolved": unresolved,
        "onboarding_minutes": cfg["onboarding_minutes"],
        "close_to_register_hours": {
            "count": len(latency),
            "median": median(latency) if latency else None,
        },
        "policy_reviews": len(ledger.get("policy_reviews", []))
        + len(state.get("pack_reviews", [])),
    }


def statement_lines(s):
    universe = {
        "shortlist_union": "conversations in the combined shortlists",
        "all_scored": "scored conversations including prior human reviews",
        "unreviewed_scored": "scored conversations outside ordinary QA",
        "queue": "conversations outside ordinary QA",
    }[s["population_basis"]]
    lines = [
        f"Reviewers graded {s['graded']:,} of {s['queue']:,} {universe} ({s['assigned']:,} selected)."
    ]
    if s["cutoff"]:

        def count(key, noun):
            n = s[key]
            return f"{n:,} {noun}" + ("" if n == 1 else "s")

        lines.append(
            f"At cutoff, the register lacked an overdue entry for {count('confirmed_unregistered_episodes', 'verified complaint episode')}. "
            f"By follow-up, it held an entry for {s['since_registered_episodes']:,} of those episodes and still lacked one for {s['still_missing_episodes']:,}. "
            f"The current backlog includes {count('overdue_at_followup_episodes', 'verified episode')}, including {s['newly_due_since_cutoff_episodes']:,} whose deadlines passed after cutoff."
        )
        if (
            s["confirmed_unregistered_unlinked_conversations"]
            or s["overdue_at_followup_unlinked_conversations"]
        ):
            lines[-1] += (
                f" Among conversations with unverified episode identity, the register lacked an overdue entry for {s['confirmed_unregistered_unlinked_conversations']:,} at cutoff. "
                f"By follow-up it held an entry for {s['since_registered_unlinked_conversations']:,} of these and still lacked one for {s['still_missing_unlinked_conversations']:,}. "
                f"The current backlog also includes {count('overdue_at_followup_unlinked_conversations', 'unlinked conversation')}, including {s['newly_due_since_cutoff_unlinked_conversations']:,} whose deadlines passed after cutoff. "
                "These conversations may refer to the same complaint."
            )
        lo, hi = s["unregistered_conversations"]["interval"]
        lines.append(
            f"95% range for {universe} with an overdue, unregistered complaint at the cutoff: {lo:,}–{hi:,}."
        )
    else:
        lines.append(
            "Attach dated register evidence to establish which complaints were unregistered at the cutoff."
        )
    return lines


def html(state, s):
    cfg = state["control"]

    def link(target, text):
        return f'<a href="#{target}">{escape(text)}</a>'

    def table(headers, rows):
        head = "".join(f"<th>{escape(h)}</th>" for h in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
            for row in rows
        )
        return f'<div class="table" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    def channel(label):
        names = [
            c["name"]
            if label == "covered"
            else f"{c['name']} (owner {c['owner']}, review {c['review_date']})"
            for c in cfg["channels"]
            if c["coverage"] == label
        ]
        return ", ".join(names) or "none listed"

    lines = statement_lines(s)
    intro = [link("control-sample", lines[0]), link("control-register", lines[1])]
    if len(lines) > 2:
        intro.append(link("control-bounds", lines[2]))
    intro += [
        link(
            "control-inventory",
            f"Covered channels: {channel('covered')}. Partial coverage: {channel('partial')}. Channels not covered: {channel('uncovered')}.",
        )
    ]
    if s["unresolved"]:
        intro.append(
            link(
                "control-work",
                f"{len(s['unresolved'])} conversations need follow-up. {cfg['gap_owner']} will review open work on {cfg['gap_review_date']}.",
            )
        )
    if s["policy_reviews"]:
        intro.append(
            link(
                "control-register",
                "A policy change requires a review of earlier findings.",
            )
        )
    inventory = table(
        [
            "Channel",
            "Access",
            "Owner",
            "Ordinary QA",
            "Register link",
            "Retention",
            "Review date",
        ],
        (
            [
                c[k]
                for k in (
                    "name",
                    "coverage",
                    "owner",
                    "ordinary_qa",
                    "register_link",
                    "retention",
                    "review_date",
                )
            ]
            for c in cfg["channels"]
        ),
    )
    grades = effective_grades(state)
    classifications = table(
        [
            "Conversation",
            "Complaint under policy",
            "Category",
            "Vulnerability",
            "Redress",
            "Classification basis",
        ],
        (
            [
                cid,
                {True: "Yes", False: "No", None: "Insufficient"}[g["serious"]],
                *[g[k] for k in FIELDS],
            ]
            for cid, g in sorted(grades.items())
        ),
    )
    gaps = table(
        [
            "Conversation",
            "Follow-up owner",
            "Review date",
            "Days since close at observation",
        ],
        (
            [
                g["case_id"],
                g["owner"],
                g["review_date"],
                g["age_days_since_close"]
                if g["age_days_since_close"] is not None
                else "Unknown",
            ]
            for g in s["unresolved"]
        ),
    )
    bounds = table(
        ["Sampling group", "Confirmed conversations", "Determinate", "95% range"],
        (
            [r["label"], r["confirmed"], r["graded"], "–".join(map(str, r["interval"]))]
            for r in s["unregistered_conversations"]["strata"].values()
        ),
    )
    latency = s["close_to_register_hours"]
    timing = (
        f"{latency['median']:g} hours (median across {latency['count']} conversations with both timestamps)"
        if latency["count"]
        else "unavailable"
    )
    detail = (
        f'<section id="control-inventory"><h2>Channel inventory</h2>{inventory}<p>Team-reported onboarding effort: {s["onboarding_minutes"]} minutes.</p></section>'
        f"<section><h2>Grading by policy</h2>{classifications}</section>"
        f'<section id="control-bounds"><h2>Overdue, unregistered conversations</h2>{bounds}<p>The range applies to conversations in the frozen queue. A linked complaint can involve several conversations. Unknown registration or grading outcomes count both ways in the range. Channels outside the queue have no estimate.</p></section>'
        f"<section id='control-work'><h2>Open work</h2><p>{len(s['unresolved'])} conversations need registration, evidence or a first grade. Close to registration: {timing}. A negative time means registration preceded closure.</p>{gaps}"
        f"<p>Review owner for outstanding classification questions and disputes: {escape(cfg['gap_owner'])}. Review date: {escape(cfg['gap_review_date'])}. "
        f"{len(s['classification_unresolved'])} grades have an unknown dimension. {len(s['open_disputes'])} disputes await a decision.</p></section>"
    )
    title = (
        "Synthetic complaints rehearsal"
        if cfg.get("synthetic")
        else "Weekly complaints control statement"
    )
    lead = f"<h1>{title}</h1><p><strong>{escape(s['scope'])}</strong></p><p>{' '.join(intro)}</p>"
    return lead, detail
