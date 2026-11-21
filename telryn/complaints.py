"""Reconcile human complaint grades with dated, team-supplied register and work records."""

import csv
import hashlib
import io
from collections import defaultdict
from html import escape
from pathlib import Path

from . import episodes
from .contracts import InputError, fingerprint, integer, read_json, sequence, timestamp
from .outcomes import deadline_status
from .weekly_review import effective_grades

REGISTER = ("match_key", "entry_id", "recorded_at", "evidence_ref")
ACTIONS = ("case_id", "action_id", "status", "owner", "recorded_at", "evidence_ref")
CASE = ("case_id", "revision", "deadline", "policy_ref", "match_key")
LIMIT = 32 * 1024 * 1024
WORKLIST = {
    "awaiting_acceptance": "Still unregistered, no owner acceptance observed",
    "owner_accepted": "Still unregistered, an owner accepted the work",
    "reconcile_completed": "Still unregistered after documented owner completion",
    "unresolved": "Registration or owner evidence needs checking",
    "policy_clock_review": "Policy deadline passed with no cited completion",
}


def _fields(obj, fields, label):
    if not isinstance(obj, dict) or set(obj) != set(fields):
        raise InputError(f"{label} must have the documented fields")


def _text(value, label):
    if (
        not isinstance(value, str)
        or not value
        or not value.isprintable()
        or value.strip() != value
        or len(value) > 1024
    ):
        raise InputError(f"invalid {label} in complaint evidence")
    return value


def _time(value):
    return timestamp(_text(value, "timestamp"), "complaint evidence timestamp")


def _source_citation(source, frozen, cutoff):
    fields = ("evidence_ref", "revision", "passages", "passage", "quote")
    if isinstance(source, dict) and "contact_at" in source:
        fields = (*fields, "contact_at")
    _fields(
        source,
        fields,
        "source citation",
    )
    if any(source[k] != frozen[k] for k in ("evidence_ref", "revision")):
        raise InputError(
            "source citation must match the frozen conversation reference and revision"
        )
    passages = sequence(source["passages"], "source passages")
    number = integer(source["passage"], "one-based passage number", minimum=1)
    if number > len(passages):
        raise InputError("source citation passage is absent")
    cited = passages[number - 1]
    _fields(cited, ("at", "text"), "dated passage")
    quote = _text(source["quote"], "redacted passage quote")
    if not isinstance(cited["text"], str) or quote not in cited["text"]:
        raise InputError("source citation quote must occur in the numbered passage")
    when = _time(cited["at"])
    if "contact_at" in source and _time(source["contact_at"]) > when:
        raise InputError("source citation predates the supplied contact opening")
    if when > cutoff or (
        frozen.get("completed_at") and when > _time(frozen["completed_at"])
    ):
        raise InputError("source citation postdates the frozen closure or audit cutoff")
    return {
        **{k: source[k] for k in ("evidence_ref", "revision", "passage", "quote")},
        **({"contact_at": source["contact_at"]} if "contact_at" in source else {}),
        "at": cited["at"],
    }


def _ledger(folder, descriptor, fields, selected, as_of):
    _fields(descriptor, ("file", "source_ref", "complete_through"), "complaint source")
    ref = _text(descriptor["source_ref"], "source_ref")
    through = descriptor["complete_through"]
    if through is not None and _time(through) > _time(as_of):
        raise InputError("complaint source completeness extends beyond as_of")
    path = folder / _text(descriptor["file"], "file")
    with path.open("rb") as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise InputError("complaint evidence file exceeds 32 MiB")
    reader = csv.DictReader(
        io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True
    )
    if tuple(reader.fieldnames or ()) != fields:
        raise InputError("complaint CSV header differs from the documented schema")
    seen, rows = set(), []
    horizon = _time(as_of)
    for row in reader:
        _fields(row, fields, "complaint CSV row")
        for name in fields:
            _text(row[name], name)
        ident = row[fields[1]]
        if ident in seen or _time(row["recorded_at"]) > horizon:
            raise InputError("duplicate complaint record ID or record later than as_of")
        seen.add(ident)
        if "status" in row and row["status"] not in (
            "requested",
            "accepted",
            "completed",
        ):
            raise InputError(
                "complaint action must be requested, accepted or completed"
            )
        if row[fields[0]] in selected:
            rows.append(row)
    return {
        "source_ref": ref,
        "sha256": hashlib.sha256(data).hexdigest(),
        "complete_through": through,
        "rows": sorted(rows, key=lambda r: r[fields[1]]),
    }


def _extends(previous, current, fields, previous_keys):
    """Do not silently rewrite an event or backfill a period attested complete."""
    before = {row[fields[1]]: row for row in previous["rows"]}
    after = {row[fields[1]]: row for row in current["rows"]}
    if any(after.get(k) != v for k, v in before.items()):
        raise InputError(
            "complaint follow-up removed or changed earlier source evidence"
        )
    through = previous["complete_through"]
    if through is None:
        return
    if current["complete_through"] is None or _time(
        current["complete_through"]
    ) < _time(through):
        raise InputError("complaint source coverage regressed")
    if any(
        row[fields[0]] in previous_keys and _time(row["recorded_at"]) <= _time(through)
        for key, row in after.items()
        if key not in before
    ):
        raise InputError(
            "complaint follow-up contradicts earlier complete source coverage"
        )


def ledger_keys(doc, sampled_ids):
    keys = {item["match_key"] for item in doc["cases"].values()} - {None}
    action_ids = set(sampled_ids)
    if "episodes" in doc:
        for binding in episodes.bindings(doc, _time(doc["as_of"])).values():
            keys.update(binding["keys"])
            action_ids.update(binding["members"])
    return keys, action_ids


def attach(state, path: Path, audit_id: str):
    if path.stat().st_size > LIMIT:
        raise InputError("complaint manifest exceeds 32 MiB")
    doc = read_json(path, "complaint records")
    previous = doc.pop("previous_receipt", None)
    contacts_file = doc.pop("episode_evidence", None)
    policy_file = doc.pop("clock_policy", None)
    pack = state.get("policy_pack")
    if pack is not None:
        if contacts_file is None or policy_file is not None:
            raise InputError(
                "a pack-backed audit uses its frozen policy; supply episode_evidence and omit clock_policy"
            )
    elif (contacts_file is None) != (policy_file is None):
        raise InputError("episode_evidence and clock_policy must be supplied together")
    _fields(
        doc,
        (
            "schema",
            "audit_id",
            "rubric",
            "scope",
            "cutoff",
            "as_of",
            "register",
            "actions",
            "cases",
        ),
        "complaint manifest",
    )
    if doc["audit_id"] != audit_id or doc["rubric"] != state["rubric"]:
        raise InputError("complaint records belong to a different audit or rubric")
    _text(doc["scope"], "scope")
    if "control" in state and doc["scope"] != state["control"]["scope"]:
        raise InputError(
            "complaint register scope differs from the signed control scope"
        )
    cutoff = _time(doc["cutoff"])
    if "score_audit" in state and cutoff != _time(state["score_audit"]["cutoff"]):
        raise InputError("complaint records must use the frozen score audit cutoff")
    if _time(doc["as_of"]) < cutoff or cutoff < _time(state["week"] + "T00:00:00Z"):
        raise InputError(
            "complaint cutoff must fall after the week starts and before or at as_of"
        )
    sampled = {r["case_id"]: r for r in state["cases"]}
    if any(
        c.get("completed_at") and _time(c["completed_at"]) > cutoff
        for c in sampled.values()
    ):
        raise InputError("complaint cutoff precedes a sampled completion")
    rows = {}
    if not isinstance(doc["cases"], list):
        raise InputError("complaint cases must be an array")
    for item in doc["cases"]:
        if isinstance(item, dict) and "source_citation" in item:
            _fields(item, (*CASE, "source_citation"), "complaint case")
        else:
            _fields(item, CASE, "complaint case")
        case = _text(item["case_id"], "case_id")
        if (
            case in rows
            or case not in sampled
            or item["revision"] != sampled[case]["revision"]
        ):
            raise InputError("duplicate or unmatched frozen complaint case/revision")
        _text(item["policy_ref"], "policy_ref")
        if pack is not None and item["policy_ref"] != state["rubric"]:
            raise InputError("case policy_ref must identify the frozen policy pack")
        if item["match_key"] is not None:
            _text(item["match_key"], "match_key")
        if item["deadline"] is not None:
            if pack is not None:
                raise InputError(
                    "a pack-backed audit computes deadlines from cited events; leave deadline null"
                )
            _time(item["deadline"])
        if "source_citation" in item:
            item["source_citation"] = _source_citation(
                item["source_citation"], sampled[case], cutoff
            )
        rows[case] = item
    doc["cases"] = rows
    if contacts_file is not None:
        doc["episodes"] = episodes.load(
            path.parent / _text(contacts_file, "episode evidence file"),
            path.parent / _text(policy_file, "clock policy file")
            if pack is None
            else None,
            doc,
            sampled,
            control=state.get("control"),
            frozen_policy=pack["clock_policy"] if pack is not None else None,
        )
        for contact in doc["episodes"]["contacts"]:
            citation = rows.get(contact["case_id"], {}).get("source_citation", {})
            opened = _time(contact["contact_at"])
            if "contact_at" in citation and _time(citation["contact_at"]) != opened:
                raise InputError(
                    "source citation and episode disagree on the contact opening"
                )
            if citation and _time(citation["at"]) < opened:
                raise InputError("source citation predates the linked contact opening")
    keys, action_ids = ledger_keys(doc, sampled)
    doc["register"] = _ledger(
        path.parent, doc["register"], REGISTER, keys, doc["as_of"]
    )
    doc["actions"] = _ledger(
        path.parent, doc["actions"], ACTIONS, action_ids, doc["as_of"]
    )
    receipt = fingerprint(doc)
    existing = state.get("complaint_records")
    if existing and receipt == existing["receipt"]:
        return False
    if previous != (existing["receipt"] if existing else None):
        raise InputError(
            "supply the current previous_receipt for a complaint follow-up"
        )
    history = []
    if existing:
        old = existing["snapshot"]
        if (
            any(old[field] != doc[field] for field in ("cutoff", "scope"))
            or _time(doc["as_of"]) < _time(old["as_of"])
            or any(
                key not in rows
                or any(
                    value is not None and value != rows[key].get(field)
                    for field, value in item.items()
                )
                for key, item in old["cases"].items()
            )
        ):
            raise InputError(
                "complaint follow-up changed the frozen scope, case mapping or evidence horizon"
            )
        old_keys = {r["match_key"] for r in old["cases"].values()}
        old_ids = set(sampled)
        issued_horizon = _time(old["as_of"])
        previous_bindings = episodes.bindings(old, issued_horizon)
        if "episodes" in old:
            prior = old["episodes"]
            after = doc.get("episodes", {})
            if prior["policy"] != after.get("policy"):
                raise InputError("cannot replace the policy of an issued episode")
            for binding in previous_bindings.values():
                old_keys.update(binding["keys"])
                old_ids.update(binding["members"])
            old_collisions, new_collisions = prior["collisions"], after["collisions"]
            if any(
                new_collisions.get(k) != v for k, v in old_collisions.items()
            ) or any(
                key in old_keys
                and key not in old_collisions
                and _time(at) <= _time(old["as_of"])
                for key, at in new_collisions.items()
            ):
                raise InputError("follow-up changed historical episode alias coverage")
            by_id = {r["case_id"]: r for r in after["contacts"]}
            previously = {r["case_id"] for r in prior["contacts"]}
            if any(by_id.get(r["case_id"]) != r for r in prior["contacts"]) or any(
                _time(r["contact_at"]) <= _time(old["as_of"])
                for cid, r in by_id.items()
                if cid not in previously
            ):
                raise InputError(
                    "follow-up changed or backfilled dated episode contacts"
                )
        current_bindings = episodes.bindings(doc, issued_horizon)
        if any(
            link["match"] == "exact_key"
            and current_bindings[cid]["match"] != "exact_key"
            for cid, link in previous_bindings.items()
        ):
            raise InputError(
                "a follow-up cannot invalidate an issued register match; investigate the mapping in a fresh audit"
            )
        _extends(
            old["register"],
            doc["register"],
            REGISTER,
            old_keys,
        )
        _extends(old["actions"], doc["actions"], ACTIONS, old_ids)
        history = [*existing["history"], existing["receipt"]]
    state["complaint_records"] = {
        "receipt": receipt,
        "snapshot": doc,
        "history": history,
        **(
            {"policy_reviews": existing["policy_reviews"]}
            if existing and "policy_reviews" in existing
            else {}
        ),
    }
    return True


def _complete(source, at):
    return (
        source["complete_through"] is not None
        and _time(source["complete_through"]) >= at
    )


def _owner(rows, source, at, contact_starts):
    if not _complete(source, at):
        return "unresolved"
    order = {"unobserved": 0, "requested": 1, "accepted": 2, "completed": 3}
    status, previous_time, previous_owner = "unobserved", None, None
    for row in rows:  # sorted by recorded_at
        when, next_status = _time(row["recorded_at"]), row["status"]
        if when > at:
            break
        start = contact_starts.get(row["case_id"])
        if start is not None and when < start:
            return "unresolved"
        if order[next_status] < order[status] or (
            when == previous_time
            and (next_status != status or row["owner"] != previous_owner)
        ):
            return "unresolved"
        status, previous_time, previous_owner = next_status, when, row["owner"]
    return status


def reconcile(state):
    evidence = state["complaint_records"]
    doc = evidence["snapshot"]
    cutoff, as_of = _time(doc["cutoff"]), _time(doc["as_of"])
    by_key, by_case = defaultdict(list), defaultdict(list)
    for r in doc["register"]["rows"]:
        by_key[r["match_key"]].append(r)
    for r in doc["actions"]["rows"]:
        by_case[r["case_id"]].append(r)
    contact_starts = {
        cid: _time(spec["source_citation"]["contact_at"])
        for cid, spec in doc["cases"].items()
        if "contact_at" in spec.get("source_citation", {})
    }
    contact_starts.update(
        {
            r["case_id"]: _time(r["contact_at"])
            for r in doc.get("episodes", {}).get("contacts", [])
        }
    )
    at_bindings = episodes.bindings(doc, cutoff)
    now_bindings = episodes.bindings(doc, as_of)
    policy_doc = doc.get("episodes", {}).get("policy")
    grades = effective_grades(state)
    rows = []
    worklist = {
        group: [] for group in WORKLIST if group != "policy_clock_review" or policy_doc
    }

    def view(cid, spec, bindings, horizon):
        info = bindings.get(
            cid,
            {"keys": set(), "members": {cid}, "match": "missing_key", "contacts": []},
        )
        exact = info["match"] == "exact_key"
        entries = sorted(
            (
                r
                for key in info["keys"]
                for r in by_key[key]
                if exact and _time(r["recorded_at"]) <= horizon
            ),
            key=lambda r: (_time(r["recorded_at"]), r["entry_id"]),
        )
        actions = sorted(
            (
                r
                for member in info["members"]
                for r in by_case[member]
                if _time(r["recorded_at"]) <= horizon
            ),
            key=lambda r: (_time(r["recorded_at"]), r["action_id"]),
        )
        deadline = spec.get("deadline")
        clocks = {}
        if info["contacts"]:
            through = doc["register"]["complete_through"]
            clocks = episodes.clocks(
                info["contacts"],
                policy_doc,
                horizon,
                entries,
                exact,
                _time(through) if through else None,
            )
            registration = clocks[policy_doc["registration_clause"]]
            deadline = registration["deadline"]
            if (
                entries
                and registration["start"]
                and _time(entries[0]["recorded_at"])
                < _time(registration["start"]["at"])
            ):
                exact = False
        return info, exact, entries, actions, deadline, clocks

    for case in state["cases"]:
        cid = case["case_id"]
        spec = doc["cases"].get(cid, {})
        key = spec.get("match_key")
        _, prior_exact, visible, earlier_actions, earlier_due, earlier_clocks = view(
            cid, spec, at_bindings, cutoff
        )
        info, exact, entries, actions, deadline, clocks = view(
            cid, spec, now_bindings, as_of
        )
        match = info["match"]
        if len(entries) > 1:
            match = "multiple_entries"
        elif not exact and match == "exact_key":
            match = "entry_predates_trigger"
        grade = grades.get(cid, {}).get("serious")
        at_cutoff = deadline_status(
            grade,
            prior_exact,
            earlier_due,
            cutoff,
            [r["recorded_at"] for r in visible],
            doc["register"]["complete_through"],
        )
        # The register can cover the deadline even when it cannot prove absence
        # all the way through cutoff. Only use the mapping and clock known at cutoff.
        if "score_audit" in state:
            deadline_miss = None
            if grade is False or at_cutoff in ("on_time", "not_due"):
                deadline_miss = False
            elif at_cutoff in ("missing", "late"):
                deadline_miss = True
            elif (
                grade is True
                and prior_exact
                and earlier_due
                and not visible
                and _time(earlier_due) <= cutoff
                and _complete(doc["register"], _time(earlier_due))
            ):
                deadline_miss = True
        current = "unresolved"
        if exact and len(entries) == 1:
            current = "registered"
        elif exact and not entries and _complete(doc["register"], as_of):
            current = "missing"
        owner = _owner(actions, doc["actions"], as_of, contact_starts)
        due_now = deadline is not None and _time(deadline) <= as_of
        overdue_now = grade is True and due_now and current == "missing"
        policy_gaps = [
            name
            for name, clock in clocks.items()
            if grade is True
            and name != policy_doc["registration_clause"]
            and clock["status"] == "past_due_without_evidence"
        ]
        if policy_gaps:
            worklist["policy_clock_review"].append(cid)
        if overdue_now:
            if owner == "completed":
                follow_up = "check_registration_after_completed_work"
            elif owner == "unresolved":
                follow_up = "verify_owner_work"
            else:
                follow_up = "registration_outstanding"
        elif policy_gaps:
            follow_up = "verify_policy_clock_evidence"
        elif current == "registered":
            follow_up = "registration_observed"
        else:
            follow_up = "outside_rubric" if grade is False else "check_evidence"
        if overdue_now:
            group = {
                "unobserved": "awaiting_acceptance",
                "requested": "awaiting_acceptance",
                "accepted": "owner_accepted",
                "completed": "reconcile_completed",
                "unresolved": "unresolved",
            }[owner]
            worklist[group].append(cid)
        elif at_cutoff == "unresolved" or (
            grade is True and due_now and current == "unresolved"
        ):
            worklist["unresolved"].append(cid)
        rows.append(
            {
                "case_id": cid,
                "revision": case["revision"],
                "evidence_ref": case["evidence_ref"],
                "completed_at": case.get("completed_at"),
                "grade": grade,
                **(
                    {"source_citation": spec["source_citation"]}
                    if "source_citation" in spec
                    else {}
                ),
                **(
                    {"classification_reason": grades[cid]["classification_reason"]}
                    if "classification_reason" in grades.get(cid, {})
                    else {}
                ),
                "deadline": deadline,
                "policy_ref": spec.get("policy_ref"),
                "match_key": key,
                "match_status": match,
                "registration_at_cutoff": at_cutoff,
                **({"deadline_miss": deadline_miss} if "score_audit" in state else {}),
                "registration_as_of": current,
                "registration_overdue_as_of": overdue_now,
                "owner_at_cutoff": _owner(
                    earlier_actions, doc["actions"], cutoff, contact_starts
                ),
                "owner_as_of": owner,
                "register_entries": entries,
                "actions": actions,
                "follow_up": follow_up,
                **(
                    {
                        "episode_id": info.get("episode_id"),
                        "subject": info["contacts"][0]["subject"],
                        "contacts": [
                            {
                                k: row[k]
                                for k in (
                                    "case_id",
                                    "revision",
                                    "evidence_ref",
                                    "channel",
                                    "contact_at",
                                )
                            }
                            for row in info["contacts"]
                        ],
                        "repeat_contacts": len(info["contacts"]) - 1,
                        "clocks": clocks,
                        "policy_clauses_needing_evidence": policy_gaps,
                        "clocks_at_cutoff": earlier_clocks,
                        "policy_id": policy_doc["id"],
                        "policy_version": policy_doc["version"],
                    }
                    if info["contacts"]
                    else {}
                ),
            }
        )
    return {
        "outcome_type": "complaint_registration",
        "receipt": evidence["receipt"],
        "previous_receipts": evidence["history"],
        **{k: doc[k] for k in ("scope", "rubric", "cutoff", "as_of")},
        "sources": {
            k: {n: v for n, v in doc[k].items() if n != "rows"}
            for k in ("register", "actions")
        },
        "cases": rows,
        "worklist": worklist,
        "policy_reviews": evidence.get("policy_reviews", []),
    }


def html(state):
    if "complaint_records" not in state:
        return ""
    summary = reconcile(state)
    parts = [
        "<section><h2>Complaint registration and owner action</h2>",
        f"<p>{escape(summary['scope'])} · Policy cutoff {escape(summary['cutoff'])} · Follow-up through {escape(summary['as_of'])}</p>",
        "<p>These queues cover sampled cases still overdue at follow-up, including deadlines that passed after cutoff, and cases that still need evidence. Absence depends on the export owner's declared coverage.</p>",
    ]
    for notice in summary["policy_reviews"]:
        clauses = ", ".join(escape(name) for name in notice["changed_clauses"])
        parts.append(
            f"<p><strong>Policy re-review required</strong> · {escape(notice['policy'])} {escape(notice['review_version'])} · affected clauses {clauses}. The original findings use the issued policy.</p>"
        )
    for key, cases in summary["worklist"].items():
        label = WORKLIST[key]
        preview = ", ".join(escape(case) for case in cases[:12])
        more = " …" if len(cases) > 12 else ""
        parts.append(f"<p><strong>{label} · {len(cases)}</strong> {preview}{more}</p>")
    parts.append(
        "<p>Registration at cutoff uses the human policy grade, supplied deadline and dated register entries. A later owner action alone cannot establish who brought it forward.</p>"
    )
    for source, info in summary["sources"].items():
        parts.append(
            f"<p>{source.title()} export · {escape(info['source_ref'])} · Complete through {escape(info['complete_through'] or 'unverified')} · SHA-256 <code>{info['sha256']}</code></p>"
        )
    for case in summary["cases"]:
        title = case["registration_at_cutoff"].replace("_", " ")
        follow_up = case["follow_up"].replace("_", " ")
        parts.append(
            f"<details><summary>{escape(case['case_id'])} · {title} · {follow_up}</summary>"
        )
        anchor = (
            f' id="complaint-{fingerprint(case["case_id"])}"'
            if "score_audit" in state
            else ""
        )
        parts.append(f"<p{anchor}>Conversation · {escape(case['evidence_ref'])}</p>")
        details = (
            ("Revision", case["revision"]),
            ("Closure at recorded source cutoff", case["completed_at"]),
            ("Policy", case["policy_ref"]),
            (
                "Complaint under policy",
                {True: "Yes", False: "No", None: "Undetermined"}[case["grade"]],
            ),
            ("Deadline", case["deadline"]),
            ("Register match key", case["match_key"]),
            ("Match at follow-up", case["match_status"].replace("_", " ")),
            ("Register at follow-up", case["registration_as_of"]),
            ("Owner at cutoff", case["owner_at_cutoff"]),
            ("Owner at follow-up", case["owner_as_of"]),
        )
        parts.extend(
            f"<p>{label} · {escape(value or 'unknown')}</p>" for label, value in details
        )
        if citation := case.get("source_citation"):
            if "contact_at" in citation:
                parts.append(
                    f"<p>Contact opened · {escape(citation['contact_at'])}</p>"
                )
            parts.append(
                f"<blockquote>{escape(citation['quote'])}</blockquote><p>{escape(citation['at'])} · "
                f"{escape(citation['evidence_ref'])} ({escape(citation['revision'])}), passage {citation['passage']}</p>"
            )
        if reason := case.get("classification_reason"):
            parts.append(f"<p>Reviewer’s classification basis · {escape(reason)}</p>")
        if "contacts" in case:
            parts.append(
                f"<p>Complaint episode · {escape(case.get('episode_id') or 'unresolved')} · Policy {escape(case['policy_id'])} {escape(case['policy_version'])}</p>"
            )
            for row in case["contacts"]:
                parts.append(
                    f"<p>{escape(row['channel'])} · {escape(row['contact_at'])} · {escape(row['case_id'])} · {escape(row['evidence_ref'])} ({escape(row['revision'])})</p>"
                )
            for name, clock in case["clocks"].items():
                cutoff_status = (
                    case["clocks_at_cutoff"].get(name, {}).get("status", "unresolved")
                )
                parts.append(
                    f"<p><strong>{escape(name)}</strong> · At cutoff {escape(cutoff_status.replace('_', ' '))} · At follow-up {escape(clock['status'].replace('_', ' '))} · Due {escape(clock['deadline'] or 'unknown')}</p>"
                )
                for event in (clock["start"], clock["stop"]):
                    if event and "quote" in event:
                        parts.append(
                            f"<blockquote>{escape(event['quote'])}</blockquote><p>{escape(event['at'])} · {escape(event['evidence_ref'])} ({escape(event['revision'])}), passage {event['passage']}</p>"
                        )
        for row in case["register_entries"]:
            parts.append(
                f"<p>Register entry {escape(row['entry_id'])} · {escape(row['recorded_at'])} · {escape(row['evidence_ref'])}</p>"
            )
        for row in case["actions"]:
            parts.append(
                f"<p>{escape(row['status'].title())} · {escape(row['owner'])} · {escape(row['recorded_at'])} · {escape(row['action_id'])} · {escape(row['evidence_ref'])}</p>"
            )
        parts.append("</details>")
    parts.append("</section>")
    return "".join(parts)
