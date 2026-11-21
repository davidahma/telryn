"""Check synthetic complaints in an explicitly selected test workspace."""

import json
import subprocess
import sys
import tempfile
from datetime import timedelta, timezone
from pathlib import Path

from .contracts import (
    InputError,
    canonical,
    fingerprint,
    integer,
    mapping,
    read_json,
    sequence,
    string,
    timestamp,
    versioned,
)

REVISIONS = ("workspace", "agent_revision", "policy_revision", "handoff_revision")


def _label(value, name):
    value = string(value, name)
    if len(value) > 256 or not value.isprintable() or value != value.strip():
        raise InputError(f"invalid {name}")
    return value


def _plan(path):
    plan = read_json(path, "seed suite")
    if (
        set(plan) != {"schema", "environment", *REVISIONS, "cases"}
        or plan["environment"] != "test"
    ):
        raise InputError(
            "use a test suite with pinned workspace, agent, policy and handoff revisions"
        )
    scope = {key: _label(plan[key], key) for key in REVISIONS}
    cases = sequence(plan["cases"], "synthetic complaints")
    if not 1 <= len(cases) <= 256:
        raise InputError("provide 1 to 256 synthetic complaints")
    seen = set()
    for row in cases:
        if set(mapping(row, "seed case")) != {"id", "phrase", "registration_minutes"}:
            raise InputError("each seed needs id, phrase and registration_minutes")
        key = _label(row["id"], "seed ID")
        phrase = string(row["phrase"], "synthetic phrase")
        if key in seen or len(phrase) > 8000 or not phrase.isprintable():
            raise InputError("duplicate seed ID or invalid synthetic phrase")
        seen.add(key)
        if integer(row["registration_minutes"], "registration_minutes") > 525600:
            raise InputError("test registration window exceeds one year")
    return plan, scope


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise InputError("duplicate field in seed adapter response")
        obj[key] = value
    return obj


def exchange(adapter, request):
    # Code paths come explicitly from the operator, never from the suite or a response.
    path = adapter.resolve(strict=True)
    cmd = [sys.executable, "-I", str(path)] if path.suffix == ".py" else [str(path)]
    try:
        with tempfile.TemporaryFile() as output:
            process = subprocess.run(
                cmd,
                input=canonical(request).encode("ascii"),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            output.seek(0)
            payload = output.read(4_000_001)
    except subprocess.TimeoutExpired as exc:
        raise InputError(
            f"test adapter timed out during {request['operation']}; retry with the same run ID"
        ) from exc
    if process.returncode or len(payload) > 4_000_000:
        raise InputError(
            f"test adapter failed during {request['operation']}; check the adapter locally"
        )
    try:
        response = versioned(
            json.loads(payload, object_pairs_hook=_unique),
            "seed adapter response",
        )
    except (ValueError, UnicodeError) as exc:
        raise InputError("test adapter returned an invalid JSON response") from exc
    if (
        response.get("environment") != "test"
        or response.get("scope") != request["scope"]
    ):
        raise InputError(
            "adapter test workspace or deployed revision differs from the suite"
        )
    if response.get("request_digest") != fingerprint(request):
        raise InputError("adapter response belongs to a different request")
    return response


def run(path, run_id, agent, register):
    plan, scope = _plan(path)
    run_id = _label(run_id, "run ID")
    base = {
        "schema": 1,
        "environment": "test",
        "scope": scope,
        "run_id": run_id,
        "suite_digest": fingerprint(plan),
    }

    def call(adapter, operation, **fields):
        return exchange(adapter, {**base, "operation": operation, **fields})

    def identify():
        for role, adapter in (("agent", agent), ("register", register)):
            if call(adapter, "identify", role=role).get("role") != role:
                raise InputError(f"expected the {role} test adapter")

    identify()
    submissions = {}
    conversations = set()
    for case in plan["cases"]:
        marker = fingerprint(
            [
                "telryn-seed-v1",
                scope["workspace"],
                base["suite_digest"],
                run_id,
                case["id"],
            ]
        )
        sent = call(
            agent, "submit", case_id=case["id"], marker=marker, phrase=case["phrase"]
        )
        conversation = _label(sent.get("conversation_id"), "test conversation")
        received = sent.get("received_at")
        clock, fraction = timestamp(received, "test agent receipt time")
        if sent.get("marker") != marker or conversation in conversations:
            raise InputError(
                "test agent receipt does not uniquely identify this synthetic complaint"
            )
        conversations.add(conversation)
        due = clock.astimezone(timezone.utc) + timedelta(
            minutes=case["registration_minutes"]
        )
        submissions[marker] = {
            "case_id": case["id"],
            "conversation_id": conversation,
            "received_at": received,
            "deadline": due.replace(tzinfo=None).isoformat(timespec="microseconds")
            + fraction
            + "Z",
        }

    snapshot = call(register, "read_register", markers=list(submissions))
    identify()
    source = _label(snapshot.get("source_ref"), "register snapshot reference")
    as_of = timestamp(snapshot.get("as_of"), "register observation time")
    if any(
        timestamp(s["received_at"], "agent receipt") > as_of
        for s in submissions.values()
    ):
        raise InputError("register observation predates an agent receipt")
    through = snapshot.get("complete_through")
    complete = timestamp(through, "register coverage") if through is not None else None
    if complete is not None and complete > as_of:
        raise InputError("register coverage extends past its observation time")
    entries = {marker: [] for marker in submissions}
    ids = set()
    for entry in sequence(snapshot.get("entries"), "test register entries"):
        entry = mapping(entry, "register entry")
        marker = _label(entry.get("marker"), "seed marker")
        ident = _label(entry.get("entry_id"), "register entry ID")
        if marker not in submissions or ident in ids:
            raise InputError("unexpected test marker or repeated register entry")
        ids.add(ident)
        if entry.get("conversation_id") != submissions[marker]["conversation_id"]:
            raise InputError("register evidence refers to a different conversation")
        recorded = timestamp(entry.get("recorded_at"), "register entry time")
        if (
            not timestamp(submissions[marker]["received_at"], "agent receipt")
            <= recorded
            <= as_of
        ):
            raise InputError(
                "register evidence falls outside the observed test interval"
            )
        entries[marker].append({"entry_id": ident, "recorded_at": entry["recorded_at"]})

    results = []
    for marker, data in submissions.items():
        due = timestamp(data["deadline"], "seed deadline")
        rows = entries[marker]
        if len(rows) > 1:
            status = "ambiguous"
        elif rows:
            status = (
                "on_time"
                if timestamp(rows[0]["recorded_at"], "register entry") <= due
                else "late"
            )
        elif as_of < due:
            status = "pending"
        else:
            status = (
                "missing"
                if complete is not None and complete >= due
                else "incomplete_register"
            )
        results.append({**data, "marker": marker, "status": status, "entries": rows})
    return {
        **base,
        "register": {
            "source_ref": source,
            "as_of": snapshot["as_of"],
            "complete_through": through,
        },
        "cases": results,
        "passed": all(case["status"] == "on_time" for case in results),
    }


def register_arguments(commands):
    cmd = commands.add_parser(
        "seed",
        help="test a changed AI agent with synthetic complaints in its test workspace",
    )
    cmd.add_argument("suite", type=Path)
    cmd.add_argument(
        "--run-id", required=True, help="unique release trial ID; reuse on retries"
    )
    cmd.add_argument("--agent-adapter", required=True, type=Path)
    cmd.add_argument("--register-adapter", required=True, type=Path)
    cmd.add_argument("--format", choices=("text", "json"), default="text")


def execute(args):
    result = run(args.suite, args.run_id, args.agent_adapter, args.register_adapter)
    if args.format == "json":
        print(canonical(result))
    else:
        for row in result["cases"]:
            print(
                f"{row['case_id']} · {row['status']} · register deadline {row['deadline']}"
            )
        print(
            "Release check passed."
            if result["passed"]
            else "Release check did not pass."
        )
    return 0 if result["passed"] else 1
