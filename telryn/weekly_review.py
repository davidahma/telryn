"""Record independent weekly grades and retain the evidence behind corrections."""

import getpass
from datetime import datetime, timezone
from unicodedata import normalize

from .contracts import InputError, fingerprint, integer


def identity(value: str | None, label: str = "reviewer", *, default=False) -> str:
    if default and value is None:
        value = getpass.getuser()
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or not value.isprintable()
        or len(value) > 120
    ):
        raise InputError(
            f"{label} must be a single named identity (up to 120 characters)"
        )
    return value


def _same(a: str, b: str) -> bool:
    return normalize("NFC", a).casefold() == normalize("NFC", b).casefold()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _labels(grade: dict) -> tuple:
    return tuple(
        grade.get(key)
        for key in ("serious", "disposition", "category", "vulnerability", "redress")
    )


def _check_grade(grade: dict) -> None:
    serious = grade["serious"]
    allowed = (
        ("new", "already_known", "unknown")
        if serious is True
        else (("none",) if serious is False else ("unknown",))
    )
    if grade["disposition"] not in allowed or (
        grade["disposition"] == "new" and (not grade["owner"] or not grade["action"])
    ):
        raise InputError(
            "weekly grade requires a consistent disposition, owner and action"
        )
    if any(
        not isinstance(grade[f], str) or (grade[f] and not grade[f].isprintable())
        for f in ("owner", "action")
    ):
        raise InputError("owner and action must be single-line printable text")


def record_primary(state: dict, case: str, grade: dict, reviewer: str) -> bool:
    from . import control

    _check_grade(grade)
    if "control" in state:
        control.check_grade(state["control"], grade)
    ledger = state.setdefault("case_reviews", {})
    details = ledger.get(case)
    current = details["primary"] if details else None
    history = state.get("earlier_grades", {}).get(case, [])
    if details and not _same(details["primary_by"], reviewer):
        raise InputError(
            f"case {case} already has a first reviewer; submit an independent second review"
        )
    if grade == current or grade in history:
        return False
    if current is not None:
        resolved_grade = current["serious"] is None and grade["serious"] is not None
        resolved_handling = (
            current["serious"] is True
            and grade["serious"] is True
            and current["disposition"] == "unknown"
            and grade["disposition"] in ("new", "already_known")
        )
        if (
            details["checks"]
            or (
                "control" in state
                and control.downgrade(state["control"], current, grade)
            )
            or not (resolved_grade or resolved_handling)
            or grade["minutes"] < current["minutes"]
        ):
            raise InputError(
                f"conflicting return for {case}; submit a second review or adjudicate the open dispute"
            )
        state.setdefault("earlier_grades", {})[case] = [*history, current]
    else:
        details = {"primary_by": reviewer, "checks": []}
        ledger[case] = details
    details["primary"] = grade
    details["at"] = _now()
    state["grades"][case] = grade
    return True


def record_second(state: dict, case: str, grade: dict, reviewer: str) -> bool:
    from . import control

    _check_grade(grade)
    if "control" in state:
        control.check_grade(state["control"], grade)
    details = state.get("case_reviews", {}).get(case)
    if not details:
        raise InputError(f"case {case} needs a first grade before a second review")
    primary_by = details["primary_by"]
    if _same(reviewer, primary_by):
        raise InputError(
            "an independent second reviewer must differ from the first reviewer"
        )
    for check in details["checks"]:
        if _same(reviewer, check["second_by"]):
            if grade == check["second"]:
                return False
            raise InputError("conflicting return from the same second reviewer")
        if check.get("decision") and _same(reviewer, check["decision"]["reviewer"]):
            raise InputError(
                "a former adjudicator cannot provide an independent grade for the same case"
            )
    last = details["checks"][-1] if details["checks"] else None
    if last and last["disputed"] and not last.get("decision"):
        raise InputError("resolve the existing dispute before opening another review")
    if last and last.get("decision"):
        primary_by = last["decision"]["reviewer"]
    elif last:
        primary_by = last["primary_by"]
    primary = state["grades"][case]
    check = {
        "primary": primary,
        "primary_by": primary_by,
        "second": grade,
        "second_by": reviewer,
        "at": _now(),
        "disputed": _labels(primary) != _labels(grade),
    }
    if "control" in state:
        check["downgrade_requested"] = control.downgrade(
            state["control"], primary, grade
        )
    check["id"] = fingerprint(
        [state["publication_id"], case, len(details["checks"]), check]
    )
    details["checks"].append(check)
    effective = primary
    if check["disputed"]:
        effective = {
            "serious": True if primary["serious"] is grade["serious"] is True else None,
            "disposition": "unknown",
            "owner": "",
            "action": "",
        }
        if "control" in state:
            if (
                primary["serious"] is grade["serious"]
                and primary["disposition"] == grade["disposition"]
            ):
                effective.update(
                    {
                        key: primary[key]
                        for key in ("serious", "disposition", "owner", "action")
                    }
                )
            for key in control.DIMS:
                effective[key] = (
                    primary[key] if primary[key] == grade[key] else "unknown"
                )
            effective["classification_reason"] = (
                "Independent grades differ. See both signed votes."
            )
    state["grades"][case] = {
        **effective,
        "minutes": primary["minutes"] + grade["minutes"],
    }
    return True


def decide(
    state: dict,
    case: str,
    dispute_id: str,
    reviewer: str,
    choice: str,
    reason: str,
    minutes: int,
) -> bool:
    if (
        choice not in ("primary", "second")
        or not isinstance(reason, str)
        or not reason.strip()
        or not reason.isprintable()
    ):
        raise InputError(
            "adjudication needs a primary or second choice and a single-line reason"
        )
    integer(minutes, "adjudication minutes")
    details = state.get("case_reviews", {}).get(case, {})
    checks = details.get("checks", [])
    target = next((c for c in checks if c["id"] == dispute_id), None)
    if not target or not target["disputed"]:
        raise InputError("no matching independent-review dispute for this case and ID")
    decision = {
        "reviewer": reviewer,
        "choice": choice,
        "reason": reason,
        "minutes": minutes,
    }
    if prior := target.get("decision"):
        if _same(prior["reviewer"], reviewer) and all(
            prior[key] == decision[key] for key in ("choice", "reason", "minutes")
        ):
            return False
        raise InputError("dispute already adjudicated; original decision retained")
    if any(
        _same(reviewer, name)
        for name in [details["primary_by"], *[c["second_by"] for c in checks]]
    ):
        raise InputError("adjudicator must be independent of the case's graders")
    if _same(reviewer, target["primary_by"]):
        raise InputError("adjudicator must differ from both reviewers")
    target["decision"] = {**decision, "at": _now()}
    combined = state["grades"][case]["minutes"] + minutes
    state["grades"][case] = {**target[choice], "minutes": combined}
    return True


def effective_grades(state: dict) -> dict:
    """Interpret older open handling disputes without rewriting signed evidence."""
    grades = state["grades"].copy()
    for case, record in state.get("case_reviews", {}).items():
        checks = record["checks"]
        if not checks or grades[case]["serious"] is not None:
            continue
        last = checks[-1]
        if (
            last["disputed"]
            and not last.get("decision")
            and last["primary"]["serious"] is last["second"]["serious"] is True
        ):
            grades[case] = {**grades[case], "serious": True}
    return grades


def review_status(state: dict) -> dict:
    agreed = compared = unresolved = paired = 0
    pending = []
    for case, details in state.get("case_reviews", {}).items():
        checks = details["checks"]
        if checks:
            # First independent pair only, so repeated challenges don't overweight a case.
            first = checks[0]
            paired += 1
            a, b = first["primary"]["serious"], first["second"]["serious"]
            if a is not None and b is not None:
                compared += 1
                agreed += a == b
            last = checks[-1]
            if last["disputed"] and not last.get("decision"):
                unresolved += 1
                pending.append(
                    {
                        "case_id": case,
                        "dispute_id": last["id"],
                        **{
                            k: last[k]
                            for k in ("primary", "second", "primary_by", "second_by")
                        },
                    }
                )
    return {
        "paired": paired,
        "compared": compared,
        "agreed": agreed,
        "disagreement": compared - agreed,
        "agreement": agreed / compared if compared else None,
        "open": unresolved,
        "open_disputes": sorted(pending, key=lambda x: x["case_id"]),
    }


def workload(state: dict) -> dict:
    """Count primary handoffs and distinct follow-up queues in the saved revision."""
    grades = state["grades"]
    disputes = {item["case_id"] for item in review_status(state)["open_disputes"]}
    return {
        "assigned": len(state["cases"]),
        "primary_outstanding": len(state["cases"]) - len(grades),
        "insufficient_undisputed": sum(
            g["serious"] is None and case not in disputes for case, g in grades.items()
        ),
        "open_disputes": len(disputes),
        "handling_unknown": sum(
            g["serious"] is True
            and g["disposition"] == "unknown"
            and case not in disputes
            for case, g in grades.items()
        ),
    }


def follow_up(state: dict) -> list[dict]:
    """Identify returned cases that need more evidence and the permitted review route."""
    records = state.get("case_reviews", {})
    items = []
    for case in state["cases"]:
        ident = case["case_id"]
        grade = state["grades"].get(ident)
        if not grade or grade["disposition"] != "unknown":
            continue
        record = records.get(ident, {})
        checks = record.get("checks", [])
        if checks and checks[-1]["disputed"] and not checks[-1].get("decision"):
            continue  # The open disagreement already belongs to the adjudication queue.
        item = {
            "case_id": ident,
            "revision": case["revision"],
            "evidence_ref": case["evidence_ref"],
            "reason": "insufficient_evidence"
            if grade["serious"] is None
            else "prior_handling_unknown",
            "primary_reviewer": record.get("primary_by"),
            "route": "independent_review" if checks else "primary_amendment",
        }
        if "completed_at" in case:
            item["completed_at"] = case["completed_at"]
        items.append(item)
    return sorted(items, key=lambda item: item["case_id"])
