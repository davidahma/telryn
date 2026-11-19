"""Screen a batch using a local model and a projection of policy events."""

import csv
import hashlib
import http.client
import json
import re
from pathlib import Path

from .integrations_coverage import carry, check_as_of, projection
from .artifacts import new_directory
from .contracts import InputError, canonical, fingerprint

FIELDS = ("case_id", "revision", "evidence_ref")
SIGNALS = (
    "account_compromise",
    "escalation_required",
    "escalated",
    "refund_promised",
    "refund_recorded",
    "legal_threat",
    "complaint",
    "complaint_logged",
)
# The policy identifier is also the name used by the browser's starter rubric.
RUBRICS = {
    "account-compromise": (
        "Account compromise",
        "Was a reported account compromise closed without the required fraud-desk referral?",
    ),
    "missed-escalation": (
        "Missed escalation",
        "Was a support case closed without the escalation required by the approved policy?",
    ),
    "promised-refund": (
        "Refund promise",
        "Did the agent promise a refund without recording the required payment action?",
    ),
    "legal-threat": (
        "Legal escalation",
        "Did the agent close a legal threat without the escalation required by the approved policy?",
    ),
    "unlogged-complaint": (
        "Unlogged complaint",
        "Was a reportable complaint closed without an entry in the complaints register?",
    ),
}
POLICIES = {
    "account-compromise": "Decide whether these case facts indicate a missed escalation for account compromise. Return false when account_compromise is no, escalation_required is no, or escalated is yes. Return true when compromise and required are yes and escalated is no or unknown. Unknown alone is not evidence of compromise. Ignore the other fields.",
    "missed-escalation": "Flag if escalation_required is yes and escalated is no or unknown. Otherwise return false.",
    "promised-refund": "Flag if refund_promised is yes and refund_recorded is no or unknown. Otherwise return false.",
    "legal-threat": "Flag a documented legal threat with a required escalation and no recorded escalation. Return false when legal_threat is no, escalation_required is no or escalated is yes.",
    "unlogged-complaint": "Flag if complaint is yes and complaint_logged is no or unknown. Otherwise return false.",
    "serious-cases": "Flag a required escalation without a recorded escalation, a promised refund without a recorded refund, a legal threat, or a reported complaint without a complaint log. A yes must establish the triggering event before a missing or unknown follow-up can flag the case. Return false if none of these patterns appears.",
}
OPTIONS = {"temperature": 0, "seed": 1}


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise InputError("model response has duplicate keys")
        value[key] = item
    return value


def _api(path: str, data: dict | None = None) -> dict:
    # Direct numeric loopback, without proxies, DNS, redirects, or configurable model hosts.
    conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=120)
    try:
        conn.request(
            "GET" if data is None else "POST",
            path,
            body=None if data is None else canonical(data),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        if response.status != 200:
            raise InputError(f"local Ollama returned HTTP {response.status} for {path}")
        raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise InputError("local model response exceeded the size limit")
        result = json.loads(raw, object_pairs_hook=_unique_keys)
        if not isinstance(result, dict) or "error" in result:
            raise InputError("invalid local model response")
        return result
    except http.client.HTTPException as exc:
        raise InputError("local model connection failed") from exc
    finally:
        conn.close()


def _installed(model: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9._:/-]{1,160}", model) or "cloud" in model.lower():
        raise InputError("use an installed local model tag")
    listing = _api("/api/tags").get("models")
    if not isinstance(listing, list):
        raise InputError("local model inventory unavailable")
    matches = [m for m in listing if isinstance(m, dict) and m.get("name") == model]
    if len(matches) != 1 or not re.fullmatch(
        r"[0-9a-f]{64}", str(matches[0].get("digest"))
    ):
        raise InputError("use the exact installed model tag from ollama list")
    details = _api("/api/show", {"model": model})
    if (
        details.get("remote_model")
        or details.get("remote_host")
        or not details.get("model_info")
    ):
        raise InputError("screen requires locally installed model weights")
    return matches[0]["digest"]


def _signals(path: Path, *, with_dimensions=False):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        header = reader.fieldnames or ()
        dims = projection(
            header, FIELDS + SIGNALS + (("as_of",) if "as_of" in header else ())
        )
        rows = list(reader)
    seen = set()
    for row in rows:
        if None in row or any(
            not row.get(k) or not row[k].isprintable() or row[k] != row[k].strip()
            for k in (*FIELDS, *dims)
        ):
            raise InputError("screen row has missing or invalid case metadata")
        if row["case_id"] in seen:
            raise InputError("duplicate screen case ID")
        seen.add(row["case_id"])
        if any(row.get(key) not in ("yes", "no", "unknown") for key in SIGNALS):
            raise InputError("each policy event must be yes, no or unknown")
    return (rows, dims) if with_dimensions else rows


def decide(events, policy):
    """Apply the named human-readable policy to explicit or extracted facts."""
    missing_escalation = (
        events["escalation_required"] == "yes" and events["escalated"] != "yes"
    )
    missing_refund = (
        events["refund_promised"] == "yes" and events["refund_recorded"] != "yes"
    )
    missing_complaint = (
        events["complaint"] == "yes" and events["complaint_logged"] != "yes"
    )
    if policy == "account-compromise":
        return events["account_compromise"] == "yes" and missing_escalation
    if policy == "missed-escalation":
        return missing_escalation
    if policy == "legal-threat":
        return events["legal_threat"] == "yes" and missing_escalation
    if policy == "promised-refund":
        return missing_refund
    if policy == "unlogged-complaint":
        return missing_complaint
    if policy == "serious-cases":
        return (
            missing_escalation
            or missing_refund
            or missing_complaint
            or events["legal_threat"] == "yes"
        )
    raise InputError("unknown screening policy")


def population_file(staging, rows, policy, dims):
    path = staging / "population.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=(*FIELDS, "screen_flag", *dims))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **{k: row[k] for k in (*FIELDS, *dims)},
                    "screen_flag": str(decide(row, policy)).lower(),
                }
            )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def screen(path: Path, out: Path, model: str | None, policy: str, **options) -> dict:
    """Read a prepared fact CSV directly or extract cited facts from a conversation JSONL."""
    if path.suffix.lower() == ".jsonl":
        from .transcripts import extract

        return extract(path, out, model, policy, **options)
    if path.suffix.lower() != ".csv":
        raise InputError("supply transcript .jsonl or structured event .csv")
    if options.get("policy_text") or options.get("redact") or options.get("checkpoint"):
        raise InputError(
            "policy text, redaction and checkpoints apply to transcript extraction"
        )
    rows, dims = _signals(path, with_dimensions=True)
    # The recipe and its version identify the deterministic truth table.
    recipe = fingerprint(["policy-events-v1", policy, POLICIES[policy]])
    with new_directory(out) as staging:
        coverage = carry(staging, path, rows)
        check_as_of(rows, coverage)
        receipt = {
            "schema": 1,
            "screen_version": "policy-events:" + recipe,
            "policy": policy,
            "mechanism": "deterministic-event-policy",
            "coverage_sha256": fingerprint(coverage) if coverage else None,
            "events_fingerprint": fingerprint(rows),
            "cases": len(rows),
            "population_sha256": population_file(staging, rows, policy, dims),
        }
        (staging / "receipt.json").write_text(
            canonical(receipt) + "\n", encoding="utf-8"
        )
    return receipt


def register_arguments(commands):
    parser = commands.add_parser(
        "screen",
        help="extract cited events from transcripts or apply a policy to structured events",
    )
    parser.add_argument(
        "source", type=Path, help="transcript JSONL or policy-event CSV"
    )
    parser.add_argument(
        "--model",
        help="installed Ollama tag for transcript extraction; CSV policy evaluation is deterministic",
    )
    parser.add_argument("--policy", required=True, choices=tuple(POLICIES))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--workers", type=int, default=2, help="1–16 local transcript workers"
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="private checkpoint directory (default alongside output)",
    )
    parser.add_argument(
        "--redact",
        type=Path,
        help="optional file of additional literal identifiers to redact, one per line",
    )
    parser.add_argument(
        "--policy-text",
        type=Path,
        help="customer-approved text defining which escalations are required",
    )


def execute(args):
    print(
        canonical(
            screen(
                args.source,
                args.out,
                args.model,
                args.policy,
                workers=args.workers,
                checkpoint=args.checkpoint,
                redact=args.redact,
                policy_text=args.policy_text,
            )
        )
    )
    return 0
