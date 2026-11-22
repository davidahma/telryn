"""Desk uploads hand bounded, fixed local files to the signed weekly workflow."""

import json
import tempfile
from pathlib import Path

from . import bank_report, episodes, packs, policy, weekly_report
from .contracts import InputError, decode_json, mapping, sequence, string


def _json(fields, key):
    return mapping(decode_json(string(fields.get(key), key), key), key)


def template(state):
    cutoff = state.get("score_audit", {}).get("cutoff")
    doc = {
        "schema": 1,
        "audit_id": weekly_report._audit_id(state),
        "rubric": state["rubric"],
        "scope": state.get("control", {}).get("scope", ""),
        "cutoff": cutoff,
        "as_of": cutoff,
        "cases": [
            {
                "case_id": c["case_id"],
                "revision": c["revision"],
                "deadline": None,
                "match_key": None,
                "policy_ref": state["rubric"],
            }
            for c in state["cases"]
        ],
    }
    for name in ("register", "actions"):
        doc[name] = {"file": name + ".csv", "source_ref": "", "complete_through": None}
    if "complaint_records" in state:
        doc["previous_receipt"] = state["complaint_records"]["receipt"]
    return doc


def suggestions(state, fields):
    archive = _json(fields, "contacts")
    clock = state.get("policy_pack", {}).get("clock_policy")
    if clock is None:
        clock = policy.validate(_json(fields, "clock"))
    return episodes.suggestions(archive, clock)


def attach(report, fields, user):
    manifest = _json(fields, "manifest")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for key in ("register", "actions"):
            desc = mapping(manifest.get(key), key)
            desc["file"] = key + ".csv"
            (base / desc["file"]).write_text(
                string(fields.get(key), key), encoding="utf-8", newline=""
            )
        for key, target in (
            ("contacts", "episode_evidence"),
            ("clock", "clock_policy"),
        ):
            if target in manifest and key not in fields:
                raise InputError(f"select the {key} file named by the manifest")
            if key in fields:
                obj = _json(fields, key)
                if key == "contacts":
                    if "confirmed_links" in obj:
                        raise InputError(
                            "confirm proposed episode links in the desk before attaching"
                        )
                    if selected := fields.get("confirmed_links"):
                        reviewer = string(
                            user or fields.get("link_reviewer"), "confirming lead"
                        )
                        obj["confirmed_links"] = [
                            {"case_ids": ids, "reviewer": reviewer}
                            for ids in sequence(selected, "confirmed links")
                        ]
                manifest[target] = key + ".json"
                (base / manifest[target]).write_text(json.dumps(obj), encoding="utf-8")
        (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        state = weekly_report.attach_complaints(report, base / "manifest.json")
        return {"receipt": state["complaint_records"]["receipt"]}, state


def review_policy(report, fields, *, apply):
    supplied = _json(fields, "policy")
    pack = packs.validate_local(supplied) if "clock_policy" in supplied else None
    rules = pack["clock_policy"] if pack is not None else policy.validate(supplied)

    def update(state):
        return policy.update_review(state, rules, pack)

    if apply:
        state = weekly_report._update(report, update)
    else:
        state = weekly_report.read_state(report)
        update(state)
    changes = [
        n
        for n in state.get("complaint_records", {}).get("policy_reviews", [])
        + state.get("pack_reviews", [])
        if n["policy"] == rules["id"] and n["review_version"] == rules["version"]
    ]
    return {
        "affected": bool(changes),
        "notices": changes,
        "calendar_warnings": policy.warnings_at(rules),
    }, state


def bank(report, fields):
    accepted = {"queue", "sample_owner", "register_owner", "excluded_queues"}
    if set(fields) != accepted:
        raise InputError("enter the queue, sample owner, register owner and exclusions")
    data, publication, revision = bank_report.verified_statement(report, **fields)
    return data, bank_report._digest(data), publication, revision
