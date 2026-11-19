"""Reconcile human complaint grades with dated, team-supplied register and work records."""

import csv
import hashlib
import io
from collections import Counter, defaultdict
from html import escape
from pathlib import Path

from .contracts import InputError, fingerprint, read_json, timestamp
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


def attach(state, path: Path, audit_id: str):
    if path.stat().st_size > LIMIT:
        raise InputError("complaint manifest exceeds 32 MiB")
    doc = read_json(path, "complaint records")
    previous = doc.pop("previous_receipt", None)
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
    cutoff = _time(doc["cutoff"])
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
        _fields(item, CASE, "complaint case")
        case = _text(item["case_id"], "case_id")
        if (
            case in rows
            or case not in sampled
            or item["revision"] != sampled[case]["revision"]
        ):
            raise InputError("duplicate or unmatched frozen complaint case/revision")
        _text(item["policy_ref"], "policy_ref")
        if item["match_key"] is not None:
            _text(item["match_key"], "match_key")
        if item["deadline"] is not None:
            _time(item["deadline"])
        rows[case] = item
    doc["cases"] = rows
    keys = {item["match_key"] for item in rows.values()} - {None}
    doc["register"] = _ledger(
        path.parent, doc["register"], REGISTER, keys, doc["as_of"]
    )
    doc["actions"] = _ledger(
        path.parent, doc["actions"], ACTIONS, sampled, doc["as_of"]
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
                    value is not None and value != rows[key][field]
                    for field, value in item.items()
                )
                for key, item in old["cases"].items()
            )
        ):
            raise InputError(
                "complaint follow-up changed the frozen scope, case mapping or evidence horizon"
            )
        _extends(
            old["register"],
            doc["register"],
            REGISTER,
            {r["match_key"] for r in old["cases"].values()},
        )
        _extends(old["actions"], doc["actions"], ACTIONS, sampled)
        history = [*existing["history"], existing["receipt"]]
    state["complaint_records"] = {
        "receipt": receipt,
        "snapshot": doc,
        "history": history,
    }
    return True


def _complete(source, at):
    return (
        source["complete_through"] is not None
        and _time(source["complete_through"]) >= at
    )


def _owner(rows, source, at):
    if not _complete(source, at):
        return "unresolved"
    order = {"unobserved": 0, "requested": 1, "accepted": 2, "completed": 3}
    status, previous_time = "unobserved", None
    for row in rows:  # sorted by recorded_at
        when, next_status = _time(row["recorded_at"]), row["status"]
        if when > at:
            break
        if order[next_status] < order[status] or (
            when == previous_time and next_status != status
        ):
            return "unresolved"
        status, previous_time = next_status, when
    return status


def _at_cutoff(grade, match, deadline, at, rows, source):
    if grade is False:
        return "outside_rubric"
    if grade is None or deadline is None:
        return "unresolved"
    due = _time(deadline)
    if not match or len(rows) > 1:
        return "unresolved"
    if rows:
        if _time(rows[0]["recorded_at"]) <= due:
            return "on_time"
        return "late" if _complete(source, due) else "unresolved"
    if due > at:
        return "not_due"
    return "missing" if _complete(source, at) else "unresolved"


def reconcile(state):
    evidence = state["complaint_records"]
    doc = evidence["snapshot"]
    cutoff, as_of = _time(doc["cutoff"]), _time(doc["as_of"])
    by_key, by_case = defaultdict(list), defaultdict(list)
    for r in doc["register"]["rows"]:
        by_key[r["match_key"]].append(r)
    for r in doc["actions"]["rows"]:
        by_case[r["case_id"]].append(r)
    counts = Counter(r["match_key"] for r in doc["cases"].values())
    grades = effective_grades(state)
    rows = []
    worklist = {group: [] for group in WORKLIST}
    for case in state["cases"]:
        cid = case["case_id"]
        spec = doc["cases"].get(cid, {})
        key = spec.get("match_key")
        exact = key is not None and counts[key] == 1
        entries = by_key.get(key, []) if exact else []
        if key is None:
            match = "missing_key"
        elif not exact:
            match = "shared_key"
        elif len(entries) > 1:
            match = "multiple_entries"
        else:
            match = "exact_key"
        actions = sorted(
            by_case.get(cid, []),
            key=lambda r: (_time(r["recorded_at"]), r["action_id"]),
        )
        visible = [r for r in entries if _time(r["recorded_at"]) <= cutoff]
        grade = grades.get(cid, {}).get("serious")
        at_cutoff = _at_cutoff(
            grade, exact, spec.get("deadline"), cutoff, visible, doc["register"]
        )
        current = "unresolved"
        if exact and len(entries) == 1:
            current = "registered"
        elif exact and not entries and _complete(doc["register"], as_of):
            current = "missing"
        owner = _owner(actions, doc["actions"], as_of)
        due_now = spec.get("deadline") is not None and _time(spec["deadline"]) <= as_of
        if grade is True and due_now and current == "missing":
            if owner == "completed":
                follow_up = "check_registration_after_completed_work"
            elif owner == "unresolved":
                follow_up = "verify_owner_work"
            else:
                follow_up = "registration_outstanding"
        elif current == "registered":
            follow_up = "registration_observed"
        else:
            follow_up = "outside_rubric" if grade is False else "check_evidence"
        if at_cutoff == "missing" and current == "missing":
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
                "deadline": spec.get("deadline"),
                "policy_ref": spec.get("policy_ref"),
                "match_key": key,
                "match_status": match,
                "registration_at_cutoff": at_cutoff,
                "registration_as_of": current,
                "owner_at_cutoff": _owner(actions, doc["actions"], cutoff),
                "owner_as_of": owner,
                "register_entries": entries,
                "actions": actions,
                "follow_up": follow_up,
            }
        )
    return {
        "receipt": evidence["receipt"],
        "previous_receipts": evidence["history"],
        **{k: doc[k] for k in ("scope", "rubric", "cutoff", "as_of")},
        "sources": {
            k: {n: v for n, v in doc[k].items() if n != "rows"}
            for k in ("register", "actions")
        },
        "cases": rows,
        "worklist": worklist,
    }


def html(state):
    if "complaint_records" not in state:
        return ""
    summary = reconcile(state)
    parts = [
        "<section><h2>Complaint registration and owner action</h2>",
        f"<p>{escape(summary['scope'])} · Policy cutoff {escape(summary['cutoff'])} · Follow-up through {escape(summary['as_of'])}</p>",
        "<p>These queues cover the sampled cases that missed their policy deadline by the cutoff or still need evidence. Absence depends on the export owner's declared coverage.</p>",
    ]
    for key, label in WORKLIST.items():
        cases = summary["worklist"][key]
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
        details = (
            ("Conversation", case["evidence_ref"]),
            ("Revision", case["revision"]),
            ("Closure at recorded source cutoff", case["completed_at"]),
            ("Policy", case["policy_ref"]),
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
