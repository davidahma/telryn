"""Join confirmed matters and propose window-bounded links from payment references."""

from collections import defaultdict
from datetime import timedelta

from . import audit_trust, policy
from .contracts import (
    InputError,
    canonical,
    fingerprint,
    integer,
    mapping,
    sequence,
    timestamp,
)


SUBJECT_DOMAIN = "complaint-subject-v1"
MATTER_DOMAIN = "complaint-matter-v1"


def instant(value):
    return timestamp(value, "contact evidence timestamp")


def _reference(key, domain, parts):
    return audit_trust.mac(key, domain, canonical(parts).encode("ascii"))


def _over_window(start, end, days):
    elapsed = end[0] - start[0]
    limit = timedelta(days=days)
    return elapsed > limit or (elapsed == limit and end[1] > start[1])


def _contact(item, key, scope, horizon):
    policy.fields(
        {k: v for k, v in item.items() if k not in ("payment_ref", "region")}
        if isinstance(item, dict)
        else item,
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
    if "region" in item:
        row["region"] = policy.label(item["region"], "region", limit=80)
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
        **(
            {
                "payment": _reference(
                    key,
                    "episode-payment-v1",
                    [subject, policy.label(item["payment_ref"], "payment_ref")],
                )
            }
            if subject and item.get("payment_ref") is not None
            else {}
        ),
    }


def suggestions(archive, policy_doc):
    """Return candidate groups for a lead to check against the payment records."""
    by_payment = defaultdict(list)
    seen = set()
    for row in sequence(
        mapping(archive, "episode evidence").get("contacts"), "episode contacts"
    ):
        if not isinstance(row, dict):
            raise InputError("episode contact must be an object")
        cid = policy.label(row.get("case_id"), "case_id")
        if cid in seen:
            raise InputError("duplicate episode contact ID")
        seen.add(cid)
        payment = row.get("payment_ref")
        if payment is None:
            continue
        policy.label(payment, "payment_ref")
        subject = row.get("subject_ref")
        matter = row.get("matter_ref")
        if matter is not None:
            policy.label(matter, "matter_ref")
        if subject is not None:
            by_payment[policy.label(subject, "subject_ref"), payment].append(
                (instant(row.get("contact_at")), cid, matter)
            )
    choices = []
    for (_, payment), rows in sorted(by_payment.items()):
        batch = []
        start = None
        for at, cid, matter in sorted(rows):
            if start is None or _over_window(start, at, policy_doc["episode_days"]):
                batch = []
                choices.append((payment, batch))
                start = at
            batch.append((cid, matter))
    return [
        {"case_ids": [cid for cid, _ in batch], "payment_ref": payment}
        for payment, batch in choices
        if len(batch) > 1
        and any(m is None for _, m in batch)
        and len({m for _, m in batch if m is not None}) <= 1
    ]


def _matter(row, horizon):
    link = row.get("confirmed_link")
    return row["matter"] or (
        link["matter"] if link and instant(link["visible_from"]) <= horizon else None
    )


def groups(contacts, window, horizon, *, legacy_windows=False):
    """A verified matter stays one episode, however long the obligation runs."""
    grouped = defaultdict(list)
    by_id = {}
    for contact in contacts:
        if instant(contact["contact_at"]) > horizon:
            continue
        subject, matter = contact["subject"], _matter(contact, horizon)
        group = grouped[subject, matter] if subject and matter else []
        start = instant(group[0]["contact_at"]) if group else None
        if (
            legacy_windows
            and start
            and _over_window(start, instant(contact["contact_at"]), window)
        ):
            group = grouped[subject, matter] = []
        group.append(contact)
        by_id[contact["case_id"]] = group
    return by_id


def load(
    contact_path,
    policy_path,
    doc,
    sampled,
    *,
    control=None,
    frozen_policy=None,
    previous=None,
):
    clock_policy = (
        frozen_policy if frozen_policy is not None else policy.load(policy_path)
    )
    archive = policy.read_input(contact_path, "episode evidence")
    policy.fields(
        {k: v for k, v in archive.items() if k != "confirmed_links"},
        ("schema", "contacts"),
        "episode evidence",
    )
    if type(archive["schema"]) is not int or archive["schema"] != 1:
        raise InputError("unsupported episode evidence schema")
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
            if completed and any(
                instant(e["at"]) > instant(completed) for e in row["events"]
            ):
                raise InputError(
                    "a cited event postdates the sampled conversation's frozen closure"
                )
        contacts[cid] = row
    # Carry signed confirmations forward, with their original witness horizon.
    # The outer receipt check rejects changes to the underlying contact rows.
    prior_groups = defaultdict(set)
    for earlier in (previous or {}).get("contacts", []):
        cid = earlier["case_id"]
        if cid in contacts and (link := earlier.get("confirmed_link")):
            contacts[cid]["confirmed_link"] = link
            prior_groups[link["matter"], link["reviewer"]].add(cid)
    proposals = {
        tuple(sorted(choice["case_ids"])): choice
        for choice in suggestions(archive, clock_policy)
    }
    for selection in sequence(archive.get("confirmed_links", []), "confirmed links"):
        policy.fields(selection, ("case_ids", "reviewer"), "confirmed link")
        ids = tuple(
            sorted(
                policy.label(cid, "linked case ID")
                for cid in sequence(selection["case_ids"], "linked case IDs")
            )
        )
        reviewer = policy.label(selection["reviewer"], "link reviewer")
        matched = [contacts[c] for c in ids if c in contacts]
        if (
            len(ids) >= 2
            and len(set(ids)) == len(ids) == len(matched)
            and all("confirmed_link" in r for r in matched)
        ):
            already = (matched[0]["confirmed_link"]["matter"], reviewer)
            if set(ids) <= prior_groups.get(already, set()):
                continue
            raise InputError(
                "follow-up changed or backfilled an issued payment confirmation"
            )
        if ids not in proposals:
            raise InputError("a confirmed link must match one proposed payment group")
        choice = proposals.pop(ids)
        rows = [contacts[cid] for cid in ids]
        existing = {matter for row in rows if (matter := _matter(row, horizon))}
        if len(existing) > 1:
            raise InputError("payment proposal spans multiple confirmed matters")
        matter = next(iter(existing), None) or _reference(
            key,
            "confirmed-payment-link-v1",
            [rows[0]["subject"], choice["payment_ref"], ids],
        )
        visible_from = max((row["contact_at"] for row in rows), key=instant)
        for row in rows:
            row.setdefault(
                "confirmed_link",
                {
                    "reviewer": reviewer,
                    "visible_from": visible_from,
                    "matter": matter,
                },
            )
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
    # Freeze potential counterparts without applying a join until a lead confirms.
    for ids in proposals:
        if eligible.intersection(ids):
            eligible.update(ids)
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
        "link_version": 2,
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
        legacy_windows=evidence.get("link_version", 1) == 1,
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
        matter = _matter(group[0], horizon) if group else None
        if group and (not group[0]["subject"] or not matter):
            match = "unresolved_episode"
        info = {
            "contacts": group,
            "keys": keys - {None},
            "match": match,
            "members": {r["case_id"] for r in group} or {cid},
        }
        if group and group[0]["subject"] and matter:
            info["episode_id"] = fingerprint([group[0]["subject"], matter, anchor])
            first = instant(group[0]["contact_at"])
            info["long_running"] = _over_window(
                first,
                instant(group[-1]["contact_at"]),
                evidence["policy"]["episode_days"],
            )
        known[anchor] = info
        result[cid] = info
    return result


def clocks(group, policy_doc, horizon, entries, exact, covered):
    contacts = {row["case_id"]: row for row in group}
    events = defaultdict(list)
    for row in group:
        for event in row["events"]:
            at = instant(event["at"])
            if at <= horizon:
                events[event["event"]].append((at, event))
    for occurrences in events.values():
        occurrences.sort(key=lambda row: (row[0], row[1]["case_id"], row[1]["passage"]))

    def place(event):
        contact = contacts[event["case_id"]]
        return {"channel": contact["channel"], "region": contact.get("region")}

    output = {}
    for name, rule in policy_doc["clauses"].items():
        triggers = events.get(rule["start_event"], ())
        start = triggers[0][1] if triggers else None
        row = {"start": start, "stop": None, "deadline": None, "status": "unresolved"}
        output[name] = row
        if not start:
            continue
        if policy.uses_business_days(rule) and any(
            key in policy_doc for key in policy.CALENDAR_MAPS
        ):
            selected, _ = policy.select_calendar(policy_doc, **place(start))
            for at, alternative in triggers[1:]:
                if at != triggers[0][0]:
                    break
                other, _ = policy.select_calendar(policy_doc, **place(alternative))
                if other != selected:
                    row["reason"] = "holiday_calendar_conflicting_triggers"
                    break
            if "reason" in row:
                continue
            if selected:
                row["calendar"] = policy.calendar_label({"holiday_calendar": selected})
            else:
                row["reason"] = "holiday_calendar_unassigned"
        due = policy.deadline(start["at"], rule, policy_doc, **place(start))
        row["deadline"] = due
        if due is None:
            row.setdefault("reason", "holiday_calendar_outside_coverage")
            continue
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
                    for at, event in events.get(rule["stop_event"], ())
                    if at >= triggers[0][0]
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
