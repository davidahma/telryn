"""Join dated contacts by a team-keyed subject, matter and bounded time window."""

from collections import defaultdict
from datetime import timedelta

from . import audit_trust, policy
from .contracts import InputError, canonical, fingerprint, integer, sequence, timestamp


SUBJECT_DOMAIN = "complaint-subject-v1"
MATTER_DOMAIN = "complaint-matter-v1"


def instant(value):
    return timestamp(value, "contact evidence timestamp")


def _reference(key, domain, parts):
    return audit_trust.mac(key, domain, canonical(parts).encode("ascii"))


def _contact(item, key, scope, horizon):
    policy.fields(
        item,
        (
            "case_id",
            "revision",
            "evidence_ref",
            "channel",
            "subject_ref",
            "matter_ref",
            "contact_at",
            "match_key",
            "passages",
            "events",
        ),
        "episode contact",
    )
    row = {
        name: policy.label(item[name], name)
        for name in ("case_id", "revision", "evidence_ref", "channel", "contact_at")
    }
    start = instant(row["contact_at"])
    if start > horizon:
        raise InputError("contact is later than the evidence horizon")
    for name in ("subject_ref", "matter_ref", "match_key"):
        if item[name] is not None:
            policy.label(item[name], name)
    subject = (
        _reference(key, SUBJECT_DOMAIN, [scope, item["subject_ref"]])
        if item["subject_ref"]
        else None
    )
    matter = (
        _reference(key, MATTER_DOMAIN, [subject, item["matter_ref"]])
        if subject and item["matter_ref"]
        else None
    )
    passages = sequence(item["passages"], "contact passages")
    for source in passages:
        policy.fields(source, ("at", "text"), "dated passage")
        if (
            not start <= instant(source["at"]) <= horizon
            or not isinstance(source["text"], str)
            or not source["text"].strip()
        ):
            raise InputError(
                "passage needs dated source text within the evidence horizon"
            )
    events = []
    for event in sequence(item["events"], "cited contact events"):
        policy.fields(event, ("event", "passage", "quote"), "event citation")
        name = policy.label(event["event"], "event")
        index = integer(event["passage"], "one-based passage number", minimum=1)
        quote = policy.label(event["quote"], "redacted passage quote")
        if (
            name == "register_entry"
            or index > len(passages)
            or quote not in passages[index - 1]["text"]
        ):
            raise InputError(
                "event needs a matching source passage; register entries come from the register"
            )
        events.append(
            {
                **event,
                "at": passages[index - 1]["at"],
                **{k: row[k] for k in ("case_id", "evidence_ref", "revision")},
            }
        )
    return {
        **row,
        "subject": subject,
        "matter": matter,
        "match_key": item["match_key"],
        "events": events,
    }


def groups(contacts, window, horizon):
    """Anchor each window at its first contact. A later chain cannot extend it."""
    grouped = defaultdict(list)
    by_id = {}
    for contact in contacts:
        if instant(contact["contact_at"]) > horizon:
            continue
        subject, matter = contact["subject"], contact["matter"]
        group = grouped[subject, matter] if subject and matter else []
        start = instant(group[0]["contact_at"]) if group else None
        if start and instant(contact["contact_at"]) > (
            start[0] + timedelta(days=window),
            start[1],
        ):
            group = grouped[subject, matter] = []
        group.append(contact)
        by_id[contact["case_id"]] = group
    return by_id


def load(contact_path, policy_path, doc, sampled, *, control=None):
    clock_policy = policy.load(policy_path)
    archive = policy.read_input(contact_path, "episode evidence")
    policy.fields(archive, ("schema", "contacts"), "episode evidence")
    key = audit_trust._key()
    horizon = instant(doc["as_of"])
    contacts = {}
    for item in sequence(archive["contacts"], "episode contacts"):
        row = _contact(item, key, doc["scope"], horizon)
        cid = row["case_id"]
        if cid in contacts:
            raise InputError("duplicate episode contact ID")
        if cid in sampled:
            spec = doc["cases"].get(cid)
            if (
                not spec
                or (
                    sampled[cid].get("channel")
                    and sampled[cid]["channel"].casefold() != row["channel"].casefold()
                )
                or spec["match_key"] != row["match_key"]
                or any(
                    sampled[cid][field] != row[field]
                    for field in ("revision", "evidence_ref")
                )
            ):
                raise InputError(
                    "episode contact must match its frozen source and register mapping"
                )
            if spec["deadline"] is not None:
                raise InputError(
                    "an episode uses the firm's clock; leave the manual deadline null"
                )
            completed = sampled[cid].get("completed_at")
            if instant(row["contact_at"]) > instant(doc["cutoff"]) or (
                completed and instant(row["contact_at"]) > instant(completed)
            ):
                raise InputError("contact begins after its frozen closure")
        contacts[cid] = row
    ordered = sorted(
        contacts.values(), key=lambda r: (instant(r["contact_at"]), r["case_id"])
    )
    by_case = groups(ordered, clock_policy["episode_days"], horizon)
    selected_groups = {
        group[0]["case_id"]: group for cid, group in by_case.items() if cid in sampled
    }
    eligible = {row["case_id"] for group in selected_groups.values() for row in group}
    if not eligible:
        raise InputError("episode evidence must include a sampled contact")
    related = [row for row in ordered if row["case_id"] in eligible]
    if control:
        readable = {
            c["name"].casefold()
            for c in control["channels"]
            if c["coverage"] in ("covered", "partial")
        }
        if any(row["channel"].casefold() not in readable for row in related):
            raise InputError(
                "linked contacts must have a covered or partial channel in the signed inventory"
            )
    events = {e["event"] for row in related for e in row["events"]}
    selected_keys = {row["match_key"] for row in related} | {
        spec["match_key"] for spec in doc["cases"].values()
    }
    # Include only the first observed alias collision. An unrelated contact's
    # identity, quotes and other identifiers do not belong in the report.
    collisions = {}
    for row in ordered:
        key = row["match_key"]
        if row["case_id"] not in eligible and key and key in selected_keys:
            collisions.setdefault(key, row["contact_at"])
    dependencies = sorted(
        {clock_policy["registration_clause"]}
        | {
            name
            for name, rule in clock_policy["clauses"].items()
            if rule["start_event"] in events
        }
    )
    return {
        "policy": clock_policy,
        "contacts": related,
        "collisions": collisions,
        "dependencies": dependencies,
    }


def bindings(doc, horizon):
    """Resolve only aliases whose visible owners all belong to the same episode."""
    evidence = doc.get("episodes", {})
    by_case = groups(
        evidence.get("contacts", []),
        evidence.get("policy", {}).get("episode_days", 1),
        horizon,
    )
    anchors = {cid: group[0]["case_id"] for cid, group in by_case.items()}
    aliases = defaultdict(set)
    for cid, spec in doc["cases"].items():
        aliases[spec["match_key"]].add(anchors.get(cid, cid))
    for cid, group in by_case.items():
        # Look up each row once, independently of the number of contacts in the group.
        if cid == group[0]["case_id"]:
            for row in group:
                aliases[row["match_key"]].add(cid)
    collisions = {
        key
        for key, at in evidence.get("collisions", {}).items()
        if instant(at) <= horizon
    }
    result = {}
    known = {}
    for cid, spec in doc["cases"].items():
        group = by_case.get(cid, [])
        anchor = anchors.get(cid, cid)
        if group and anchor in known:
            result[cid] = known[anchor]
            continue
        keys = {r["match_key"] for r in group} if group else {spec["match_key"]}
        match = "exact_key"
        if None in keys:
            match = "missing_key"
        elif keys & collisions or any(len(aliases[key]) != 1 for key in keys):
            match = "shared_key"
        if group and (not group[0]["subject"] or not group[0]["matter"]):
            match = "unresolved_episode"
        info = {
            "contacts": group,
            "keys": keys - {None},
            "match": match,
            "members": {r["case_id"] for r in group} or {cid},
        }
        if group and group[0]["subject"] and group[0]["matter"]:
            info["episode_id"] = fingerprint(
                [group[0]["subject"], group[0]["matter"], anchor]
            )
        known[anchor] = info
        result[cid] = info
    return result


def clocks(group, policy_doc, horizon, entries, exact, covered):
    events = sorted(
        (
            event
            for row in group
            for event in row["events"]
            if instant(event["at"]) <= horizon
        ),
        key=lambda e: (instant(e["at"]), e["case_id"], e["passage"]),
    )
    output = {}
    for name, rule in policy_doc["clauses"].items():
        start = next(
            (event for event in events if event["event"] == rule["start_event"]), None
        )
        row = {"start": start, "stop": None, "deadline": None, "status": "unresolved"}
        output[name] = row
        if not start:
            continue
        due = policy.deadline(start["at"], rule, policy_doc)
        row["deadline"] = due
        if name == policy_doc["registration_clause"]:
            if not exact or len(entries) > 1:
                continue
            stop = entries[0] if entries else None
            at = stop["recorded_at"] if stop else None
            if at and instant(at) < instant(start["at"]):
                continue
        else:
            stop = next(
                (
                    event
                    for event in events
                    if event["event"] == rule["stop_event"]
                    and instant(event["at"]) >= instant(start["at"])
                ),
                None,
            )
            at = stop["at"] if stop else None
        row["stop"] = stop
        if at and instant(at) <= instant(due):
            row["status"] = "met"
        elif name == policy_doc["registration_clause"] and (
            not covered or covered < min(horizon, instant(due))
        ):
            continue
        elif at:
            row["status"] = "late"
        elif horizon < instant(due):
            row["status"] = "running"
        else:
            row["status"] = "past_due_without_evidence"
    return output
