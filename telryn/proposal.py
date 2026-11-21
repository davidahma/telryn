"""Suggest one change to a priority queue and verify a separate evaluation batch."""

import hashlib
import hmac
import math
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from itertools import combinations
from pathlib import Path

from . import audit_trust, lineage
from .artifacts import new_directory
from .contracts import (
    InputError,
    canonical,
    fingerprint,
    integer,
    mapping,
    read_json,
    string,
    timestamp,
    unique_ids,
    validate_batch,
)
from .weekly import DIMENSIONS, HEX_DIGEST, _stratum, cell_flag, summarize
from .weekly_report import verified_snapshot


def _candidate_sql(predicate: dict, seats: int) -> str:
    integer(seats, "reserved seats", minimum=1)
    if seats > 2**31 - 1 or not predicate:
        raise InputError("invalid reservation budget or empty segment")
    conditions = []
    for field, value in sorted(predicate.items()):
        if field == "screen_flag" and type(value) is bool:
            expression = str(int(value))
        elif (
            field in DIMENSIONS
            and isinstance(value, str)
            and value
            and value.isprintable()
        ):
            expression = "'" + value.replace("'", "''") + "'"
        else:
            raise InputError("unknown proposal segment or invalid value")
        conditions.append(f"json_extract(inputs, '$.{field}') = {expression}")
    return f"""WITH reserved AS (
    SELECT id FROM requests
    WHERE {" AND ".join(conditions)}
    ORDER BY json_extract(inputs, '$.priority') DESC, id
    LIMIT min(:capacity, {seats})
)
SELECT id FROM requests
ORDER BY id IN (SELECT id FROM reserved) DESC, json_extract(inputs, '$.priority') DESC, id
LIMIT :capacity
"""


def _search(state: dict, max_reserved: int) -> dict:
    """Compare simple screen and segment rules under simultaneous cell bounds."""
    integer(max_reserved, "maximum reserved review slots", minimum=1)
    if max_reserved > 2**31 - 1:
        raise InputError("reservation budget is too large")
    dims = state.get("group_by", [])
    new = Counter(
        _stratum(row, dims)
        for row in state["cases"]
        if state["grades"][row["case_id"]]["disposition"] == "new"
    )
    cells = {k: c for k, c in state["strata"].items() if c["population"]}
    # Hoeffding + a union bound over atomic cells protects every searched union.
    # Ignore finite population correction: the candidate concerns future cases,
    # and a census of a four-case week is weak evidence about future arrivals.
    segments = {}
    population = 0
    total_high = 0
    for key, cell in cells.items():
        N, n = cell["population"], cell["sample"]
        rate = new[key] / n
        radius = math.sqrt(math.log(40 * len(cells)) / (2 * n))
        low = N * max(0, rate - radius)
        high = N * min(1, rate + radius)
        population += N
        total_high += high
        values = {"screen_flag": cell_flag(key, cell), **cell.get("groups", {})}
        fields = sorted(values.items())
        candidates = (
            predicate
            for length in range(1, len(fields) + 1)
            for predicate in combinations(fields, length)
        )
        for pred in candidates:
            totals = segments.setdefault(pred, [0, 0.0, 0.0, 0, 0])
            totals[0] += N
            totals[1] += low
            totals[2] += high
            totals[3] += new[key]
            totals[4] += n
    best = None
    for pred, (N, low, high, found, sampled) in sorted(segments.items()):
        if N == population:
            continue
        lower = low / N
        other_upper = (total_high - high) / (population - N)
        seats = min(max_reserved, math.floor(low))
        if lower <= other_upper or seats < 1:
            continue
        score = seats * (lower - other_upper)
        # Prefer the simpler tied predicate.
        rank = (score, -len(pred))
        if best is None or rank > best[0]:
            best = (
                rank,
                {
                    "predicate": dict(pred),
                    "reserved_seats": seats,
                    "segment_lower_rate": lower,
                    "complement_upper_rate": other_upper,
                    "sampled_in_segment": sampled,
                    "new_in_segment": found,
                    "conservative_rate_contrast": lower - other_upper,
                },
            )
    if best is None:
        raise InputError(
            "weekly evidence is inconclusive: no segment has a supported advantage "
            "in newly found serious cases at the requested review budget"
        )
    return best[1]


def propose(report: Path, out: Path, max_reserved: int = 1) -> dict:
    state, revision, signature = verified_snapshot(report)
    if state.get("complaint_records", {}).get("policy_reviews"):
        raise InputError("policy re-review required before a new rule proposal")
    result = summarize(state)
    if result["estimate"] is None or result["unknown"]:
        raise InputError(
            "complete grading and resolve serious-case dispositions before proposing a rule"
        )
    selected = _search(state, max_reserved)
    body = {
        "schema": 1,
        "week": state["week"],
        "rubric": state["rubric"],
        "rubric_question": state["rubric_question"],
        "screen_version": state["screen_version"],
        **selected,
        "priority_flag": selected["predicate"].get("screen_flag"),
        "max_reserved": max_reserved,
        "search_method": "simultaneous-hoeffding-95-v1",
        "population_id_hashes": state["population_id_hashes"],
        "source_report": fingerprint(state),
        "source_publication": state["publication_id"],
        "source_revision": revision,
        "source_signature": signature,
    }
    sql = _candidate_sql(selected["predicate"], selected["reserved_seats"])
    body["sql_sha256"] = hashlib.sha256(sql.encode()).hexdigest()
    with audit_trust.registry() as (db, key):
        lineage.check_source_head(db, body)
        body["proposal_id"] = fingerprint(body)
        body["signature"] = audit_trust.mac(
            key, "weekly-candidate-v1", body["proposal_id"].encode("ascii")
        )
    with new_directory(out) as staging:
        (staging / "candidate.sql").write_text(sql, encoding="utf-8")
        (staging / "proposal.json").write_text(canonical(body) + "\n", encoding="utf-8")
    return body


def held_out(path: Path, plan: dict, export: dict) -> tuple[dict, dict]:
    p = read_json(path, "weekly proposal")
    signature = p.pop("signature", None)
    proposal_id = p.pop("proposal_id", None)
    if proposal_id != fingerprint(p):
        raise InputError("weekly proposal checksum or format mismatch")
    if "search_method" not in p:
        raise InputError(
            "unsigned historical candidate; generate a new proposal from a signed weekly audit"
        )
    with audit_trust.registry() as (db, key):
        expected = audit_trust.mac(
            key, "weekly-candidate-v1", proposal_id.encode("ascii")
        )
        if (
            not isinstance(signature, str)
            or not signature.isascii()
            or not hmac.compare_digest(expected, signature)
        ):
            raise InputError(
                "weekly proposal signature differs from the team's signed candidate"
            )
        lineage.check_source_head(db, p)
    if p["search_method"] != "simultaneous-hoeffding-95-v1":
        raise InputError("unsupported weekly proposal search")
    predicate = mapping(p.get("predicate"), "proposal predicate")
    sql = _candidate_sql(predicate, p.get("reserved_seats")).encode()
    if p["reserved_seats"] > integer(
        p.get("max_reserved"), "proposal review budget", minimum=1
    ):
        raise InputError("proposed seats exceed the reservation budget")
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
            for field in predicate.keys() - {"screen_flag"}:
                string(
                    inputs.get(field),
                    f"held-out proposal requires {field} in every row",
                )
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
        "required_inputs": [
            "priority",
            "screen_flag",
            "screen_version",
            *sorted(predicate.keys() - {"screen_flag"}),
        ],
    }
    return rule, {
        "id": proposal_id,
        "prior_id_hashes": sorted(previous_cases),
        "source_publication": p["source_publication"],
        "source_revision": p["source_revision"],
        "source_signature": p["source_signature"],
    }
