"""Screen a batch using a local model and a projection of policy events."""

import csv
import hashlib
import http.client
import json
import re
from pathlib import Path

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
FORMAT = {
    "type": "object",
    "properties": {"flag": {"type": "boolean"}},
    "required": ["flag"],
    "additionalProperties": False,
}
INSTRUCTION = "Respond using JSON with exactly one boolean flag."
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


def _signals(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if sorted(reader.fieldnames or []) != sorted(FIELDS + SIGNALS):
            raise InputError(
                "screen requires the documented metadata and policy-event columns"
            )
        rows = list(reader)
    seen = set()
    for row in rows:
        if None in row or any(
            not row.get(k) or not row[k].isprintable() or row[k] != row[k].strip()
            for k in FIELDS
        ):
            raise InputError("screen row has missing or invalid case metadata")
        if row["case_id"] in seen:
            raise InputError("duplicate screen case ID")
        seen.add(row["case_id"])
        if any(row.get(key) not in ("yes", "no", "unknown") for key in SIGNALS):
            raise InputError("each policy event must be yes, no or unknown")
    return rows


def screen(path: Path, out: Path, model: str, policy: str) -> dict:
    rows = _signals(path)
    model_digest = _installed(model)
    system = f"{POLICIES[policy]} {INSTRUCTION}"
    version = "local-model:" + fingerprint(
        [model_digest, system, FORMAT, OPTIONS, SIGNALS]
    )
    with new_directory(out) as staging:
        population = staging / "population.csv"
        with population.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=(*FIELDS, "screen_flag"))
            writer.writeheader()
            for row in rows:
                facts = {key: row[key] for key in SIGNALS}
                result = _api(
                    "/api/chat",
                    {
                        "model": model,
                        "stream": False,
                        "format": FORMAT,
                        "options": OPTIONS,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": canonical(facts)},
                        ],
                    },
                )
                message = result.get("message")
                if (
                    result.get("model") != model
                    or result.get("done") is not True
                    or result.get("done_reason") != "stop"
                    or not isinstance(message, dict)
                    or not isinstance(message.get("content"), str)
                ):
                    raise InputError("incomplete model classification")
                value = json.loads(message["content"], object_pairs_hook=_unique_keys)
                if (
                    not isinstance(value, dict)
                    or set(value) != {"flag"}
                    or type(value["flag"]) is not bool
                ):
                    raise InputError("model failed to return a boolean flag")
                writer.writerow(
                    {
                        **{key: row[key] for key in FIELDS},
                        "screen_flag": str(value["flag"]).lower(),
                    }
                )
        if _installed(model) != model_digest:
            raise InputError("model revision changed while screening")
        receipt = {
            "schema": 1,
            "screen_version": version,
            "model": model,
            "model_digest": model_digest,
            "policy": policy,
            "events_fingerprint": fingerprint(rows),
            "cases": len(rows),
            "population_sha256": hashlib.sha256(population.read_bytes()).hexdigest(),
        }
        (staging / "receipt.json").write_text(
            canonical(receipt) + "\n", encoding="utf-8"
        )
    return receipt
