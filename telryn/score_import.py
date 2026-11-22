"""Read native frozen QA exports without changing the bytes a bank can replay."""

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .contracts import (
    InputError,
    decode_json,
    fingerprint,
    mapping,
    sequence,
    timestamp,
)

FORMATS = {"zendesk-qa", "maestroqa", "langfuse-v2"}


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or not value.isprintable():
        raise InputError(f"native score needs a printable {field}")
    return value


def _percent(value, name):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise InputError(f"{name} must be a percentage from 0 to 100") from exc
    if not result.is_finite() or not 0 <= result <= 100:
        raise InputError(f"{name} must be a percentage from 0 to 100")
    return result


def settings(raw):
    cfg = mapping(raw, "native score settings")
    fmt = cfg.get("format")
    if (
        not isinstance(fmt, str)
        or fmt not in FORMATS
        or cfg.get("attest_revisions") is not True
    ):
        raise InputError(
            "select a supported native format and confirm the scorecard version and frozen conversation revisions"
        )
    required = {"format", "attest_revisions"}
    if fmt != "langfuse-v2":
        required.add("pass_percent")
        _percent(cfg.get("pass_percent"), "pass threshold")
    elif "complaint_evaluator" in cfg:
        required.add("complaint_evaluator")
        _text(cfg["complaint_evaluator"], "complaint evaluator name")
    if "case_prefix" in cfg:
        required.add("case_prefix")
        _text(cfg["case_prefix"], "case ID prefix")
    if set(cfg) != required:
        raise InputError("unexpected or missing native score import setting")
    return cfg


def _pages(raw, rows, meta, count):
    parsed = decode_json(raw, "native score export")
    pages = parsed if isinstance(parsed, list) else [parsed]
    result = []
    for page in pages:
        page = mapping(page, "native export page")
        result.extend(sequence(page.get(rows), "native exported records"))
    totals = set()
    numbers = set()
    for page in pages:
        info = mapping(page.get(meta), "native pagination")
        n = info.get("page")
        total = info.get(count)
        if (
            type(n) is not int
            or type(total) is not int
            or n < 1
            or n in numbers
            or total < 0
        ):
            raise InputError("native export has an invalid or repeated page")
        numbers.add(n)
        totals.add(total)
    if numbers != set(range(1, len(pages) + 1)) or totals != {len(result)}:
        raise InputError(
            "native export is incomplete; supply all pages of the same frozen selection"
        )
    return result


def _zendesk(raw, card, threshold):
    conversations = _pages(raw, "conversations", "pagination", "total")
    seen = set()
    workspaces = set()
    for conv in conversations:
        conv = mapping(conv, "Zendesk QA conversation")
        cid = _text(conv.get("externalId"), "externalId")
        workspaces.add(_text(conv.get("workspaceId"), "workspace ID"))
        if len(workspaces) > 1 or cid in seen:
            raise InputError(
                "supply one Zendesk QA workspace and connection with unique external IDs"
            )
        seen.add(cid)
        for review in sequence(conv.get("reviews"), "Zendesk QA reviews"):
            review = mapping(review, "Zendesk QA review")
            if mapping(review.get("scorecard"), "Zendesk scorecard").get("id") != card:
                raise InputError("Zendesk export includes a different scorecard ID")
            created = review.get("createdISO") or review.get("created")
            scored = review.get("updatedISO") or review.get("updated")
            if timestamp(scored, "review update") < timestamp(
                created, "review creation"
            ):
                raise InputError("review update predates creation")
            yield (
                cid,
                scored,
                _percent(review.get("score"), "review score") >= threshold,
                _text(review.get("id"), "review ID"),
                "unknown",
                created,
            )


def _maestro(raw, card, threshold):
    reader = csv.DictReader(
        io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True
    )
    header = reader.fieldnames or []
    required = {"gradable_id", "rubric_id", "rubric_score", "date_graded", "grade_type"}
    if not required <= set(header) or len(set(header)) != len(header):
        raise InputError(
            "select MaestroQA Rubric_scores.csv with ticket, rubric, score, type and saved time"
        )
    for row in reader:
        if (
            None in row
            or None in row.values()
            or row["grade_type"] != "Grading"
            or row["rubric_id"] != card
        ):
            raise InputError(
                "select only complete production grades for one MaestroQA rubric ID"
            )
        cid = _text(row["gradable_id"], "gradable_id")
        scored = _text(row["date_graded"], "date_graded")
        if datetime.fromisoformat(scored).tzinfo is None:
            scored += "+00:00"  # All dates in the native raw export are GMT.
        ref = "maestro-" + fingerprint([cid, card, scored])
        yield (
            cid,
            scored,
            _percent(row["rubric_score"], "rubric score") >= threshold,
            ref,
            "unknown",
            scored,
        )


def _langfuse(raw, card, complaint):
    if complaint == card:
        raise InputError("use separate overall pass and complaint evaluator names")
    results = {}
    for item in _pages(raw, "data", "meta", "totalItems"):
        item = mapping(item, "Langfuse score")
        name = _text(item.get("name"), "evaluator name")
        if (
            item.get("dataType") != "BOOLEAN"
            or item.get("source") != "EVAL"
            or item.get("observationId") is not None
            or name not in (card, complaint)
            or type(item.get("value")) not in (int, float)
            or item["value"] not in (0, 1)
        ):
            raise InputError(
                "select trace-level Langfuse v2 boolean scores from the named evaluators"
            )
        cid = _text(item.get("traceId"), "traceId")
        record = results.setdefault(cid, {})
        if name in record:
            raise InputError("multiple scores for the same trace and evaluator")
        at = item.get("updatedAt")
        timestamp(at, "evaluator last update")
        record[name] = (at, bool(item["value"]), _text(item.get("id"), "score ID"))
    for cid, record in results.items():
        if card not in record:
            raise InputError("complaint classification without an overall QA score")
        at, passed, ref = record[card]
        earliest = at
        prediction = "unknown"
        if complaint in record:
            other_at, positive, other_ref = record[complaint]
            earliest = min((at, other_at), key=lambda t: timestamp(t, "score time"))
            at = max((at, other_at), key=lambda t: timestamp(t, "score time"))
            prediction = "yes" if positive else "no"
            ref = "langfuse-" + fingerprint([ref, other_ref])
        yield cid, at, passed, ref, prediction, earliest


def read(raw, cfg, revisions, card, version):
    cfg = settings(cfg)
    fmt = cfg["format"]
    if fmt == "langfuse-v2":
        records = _langfuse(raw, card, cfg.get("complaint_evaluator"))
    else:
        fn = _zendesk if fmt == "zendesk-qa" else _maestro
        records = fn(raw, card, _percent(cfg["pass_percent"], "pass threshold"))
    rows = {}
    for cid, scored, passed, ref, prediction, earliest in records:
        cid = cfg.get("case_prefix", "") + cid
        if cid in rows:
            raise InputError(
                "multiple scores match a conversation; freeze one production score per revision"
            )
        timestamp(scored, "score recorded or updated")
        rows[cid] = {
            "case_id": cid,
            "revision": revisions.get(cid),
            "scorecard": card,
            "scorecard_version": version,
            "scored_at": scored,
            "first_score_at": earliest,
            "decision": "pass" if passed else "fail",
            "complaint_prediction": prediction,
            "score_ref": ref,
        }
    return rows
