"""Suggest one change to a priority queue and verify a separate evaluation batch."""

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from pathlib import Path

from .artifacts import new_directory
from .contracts import (
    InputError,
    canonical,
    fingerprint,
    read_json,
    string,
    timestamp,
    unique_ids,
    validate_batch,
)
from .weekly import HEX_DIGEST, summarize
from .weekly_report import read_state


def _sql(flag: bool) -> str:
    # Reserve at most one seat. The remaining slots retain descending priority.
    return f"""WITH reserved AS (
    SELECT id FROM requests
    WHERE json_extract(inputs, '$.screen_flag') = {int(flag)}
    ORDER BY json_extract(inputs, '$.priority') DESC, id
    LIMIT 1
)
SELECT id FROM requests
ORDER BY id IN (SELECT id FROM reserved) DESC, json_extract(inputs, '$.priority') DESC, id
LIMIT :capacity
"""


def propose(report: Path, out: Path) -> dict:
    state = read_state(report)
    result = summarize(state)
    grades = state["grades"]
    if result["estimate"] is None or result["unknown"]:
        raise InputError(
            "complete grading and resolve serious-case dispositions before proposing a rule"
        )
    counts = []
    for flag in (False, True):
        ids = [r["case_id"] for r in state["cases"] if r["screen_flag"] == flag]
        if not ids:
            raise InputError("a proposal needs graded cases from both screen strata")
        counts.append(sum(grades[i]["disposition"] == "new" for i in ids))
    sizes = [state["strata"][key]["sample"] for key in ("unflagged", "flagged")]
    rates = [Fraction(count, n) for count, n in zip(counts, sizes)]
    if rates[0] == rates[1]:
        raise InputError(
            "both strata had the same observed newly discovered serious-case rate"
        )
    flag = rates[1] > rates[0]
    body = {
        "schema": 1,
        "week": state["week"],
        "rubric": state["rubric"],
        "rubric_question": state["rubric_question"],
        "screen_version": state["screen_version"],
        "priority_flag": flag,
        "observed_new": counts,
        "graded": sizes,
        "population_id_hashes": state["population_id_hashes"],
        "source_report": fingerprint(state),
    }
    sql = _sql(flag)
    body["sql_sha256"] = hashlib.sha256(sql.encode()).hexdigest()
    body["proposal_id"] = fingerprint(body)
    with new_directory(out) as staging:
        (staging / "candidate.sql").write_text(sql, encoding="utf-8")
        (staging / "proposal.json").write_text(canonical(body) + "\n", encoding="utf-8")
    return body


def held_out(path: Path, plan: dict, export: dict) -> tuple[dict, dict]:
    p = read_json(path, "weekly proposal")
    proposal_id = p.pop("proposal_id", None)
    if proposal_id != fingerprint(p) or type(p.get("priority_flag")) is not bool:
        raise InputError("weekly proposal checksum or format mismatch")
    sql = _sql(p["priority_flag"]).encode()
    if hashlib.sha256(sql).hexdigest() != p.get("sql_sha256"):
        raise InputError("proposed rule differs from its frozen SQL")
    for key in ("week", "rubric", "rubric_question", "screen_version", "source_report"):
        string(p.get(key), f"proposal {key}")
    policy = plan.get("serious", {})
    if (
        policy.get("rubric") != p.get("rubric")
        or policy.get("rubric_question") != p.get("rubric_question")
        or policy.get("max_lost") != 0
    ):
        raise InputError(
            "weekly proposal requires the same serious rubric and zero allowed serious losses"
        )
    if plan["minimum_additional_exceptions"] < 1:
        raise InputError("a proposal must capture at least one additional serious case")
    earliest = datetime.combine(
        date.fromisoformat(p["week"]) + timedelta(days=7), time.min, timezone.utc
    )
    tuned = unique_ids(p.get("population_id_hashes"), "proposal tuning IDs")
    if not tuned or any(not HEX_DIGEST.fullmatch(key) for key in tuned):
        raise InputError(
            "weekly proposal requires the fingerprints of the tuning population"
        )
    tuned = set(tuned)
    previous_cases = tuned.copy()
    for name in plan["required_shifts"]:
        batch = export["shifts"].get(name)
        if batch is None:
            raise InputError("proposal evaluation requires all held-out batches")
        validate_batch(name, batch)
        if timestamp(batch.get("cutoff"), "held-out cutoff")[0] < earliest:
            raise InputError("proposal evaluation must use a later, held-out batch")
        if plan["shifts"][name]["capacity"] != batch.get("capacity"):
            raise InputError(
                "proposal evaluation must preserve the historical review capacity"
            )
        membership = batch.get("membership") or {}
        if not isinstance(membership.get("ids"), list) or any(
            fingerprint(i) in tuned for i in membership["ids"]
        ):
            raise InputError(
                "proposal evaluation requires independent membership disjoint from the tuning week"
            )
        previous_cases.update(map(fingerprint, membership["ids"]))
        for row in (batch.get("requests") or {}).values():
            inputs = row.get("inputs") or {}
            flag = inputs.get("screen_flag")
            if (
                inputs.get("screen_version") != p["screen_version"]
                or type(flag) not in (int, bool)
                or flag not in (0, 1)
                or type(inputs.get("priority")) not in (int, float)
            ):
                raise InputError(
                    "held-out rows need numeric priority, binary screen_flag and the frozen screen_version"
                )
    rule = {
        "engine": "sqlite",
        "file": "candidate.sql",
        "version": f"weekly-{proposal_id}",
        "sql_sha256": p["sql_sha256"],
        "tie_break": "id-asc",
        "required_inputs": ["priority", "screen_flag", "screen_version"],
    }
    return rule, {"id": proposal_id, "prior_id_hashes": sorted(previous_cases)}
