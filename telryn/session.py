"""Build a comparison and publish or refresh its artifacts as one operation."""

from __future__ import annotations

import fcntl
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path

from . import audit_trust, lineage, shadow
from .artifacts import sync_directory
from .contracts import (
    REPORT_SCHEMA,
    InputError,
    fingerprint,
    load_export,
    load_plan,
    mapping,
    unique_ids,
)
from .grades import add_packet, adjudicate, import_packet, initial_grades, write_packet
from .replay import SqlSelector, blocked, load_selector, replay_shift
from .report import load_report, recommendation, render_report


def compare(
    plan_path: Path,
    export_path: Path | None = None,
    *,
    proposal_path: Path | None = None,
) -> dict:
    plan = load_plan(plan_path)
    base = plan_path.parent
    source = load_export(
        export_path if export_path is not None else base / plan["export"]
    )
    candidate_base = base
    proposal = None
    if proposal_path is not None:
        from .proposal import held_out

        plan["candidate"], proposal = held_out(proposal_path, plan, source)
        candidate_base = proposal_path.parent
        # A weekly proposal must capture additional cases under the weekly rubric.
        # The broader comparison's primary rubric could reward a minor defect.
        policy = plan["serious"]
        for key in ("rubric", "rubric_question"):
            plan[key] = policy[key]
        plan.pop("grades", None)
        if "grades" in policy:
            plan["grades"] = policy["grades"]
    with redirect_stdout(sys.stderr):
        live = load_selector(base, plan["live"])
        candidate = load_selector(candidate_base, plan["candidate"])
        if (
            proposal_path is not None
            and candidate.digest != plan["candidate"]["sql_sha256"]
        ):
            raise InputError("the proposal SQL changed while loading its selector")
        shifts = {
            name: replay_shift(
                name,
                source["shifts"].get(name),
                plan["shifts"][name],
                plan,
                live,
                candidate,
            )
            for name in plan["required_shifts"]
        }
    replayed = {
        name: source["shifts"][name]["membership"]["ids"]
        for name, shift in shifts.items()
        if shift["status"] == "Replayable"
    }
    counts = Counter(case for ids in replayed.values() for case in ids)
    tuning_cases = set()
    for name in plan.get("tuning_shifts", []):
        if name not in source["shifts"]:
            if proposal is not None:
                raise InputError(
                    f"weekly proposal requires tuning membership for {name}"
                )
            continue
        batch = mapping(source["shifts"][name], f"tuning shift {name}")
        membership = mapping(batch.get("membership"), f"{name} tuning membership")
        tuning_cases.update(unique_ids(membership.get("ids"), f"{name} tuning IDs"))
    if proposal is not None and plan.get("tuning_shifts"):
        proposal["declared_tuning_id_hashes"] = sorted(map(fingerprint, tuning_cases))
    for name, ids in replayed.items():
        overlap = sorted(case for case in ids if counts[case] > 1)
        if overlap:
            shifts[name] = blocked(
                f"Eligible request IDs overlap required shifts: {', '.join(overlap)}."
            )
        elif overlap := sorted(tuning_cases.intersection(ids)):
            shifts[name] = blocked(
                f"Held-out requests also appear in a tuning shift: {', '.join(overlap)}."
            )
    grades, grade_digest, excluded = initial_grades(
        base / plan["grades"] if "grades" in plan else None, plan, shifts
    )
    state = {
        "schema": REPORT_SCHEMA,
        "plan": plan,
        "shifts": shifts,
        "initial_grades": grades,
        "excluded_grading": excluded,
        "frozen": {
            "export": fingerprint(source),
            "grades": grade_digest,
            "live": live.digest,
            "candidate": candidate.digest,
        },
    }
    if proposal is not None:
        state["weekly_proposal"] = proposal
        state["serious_initial_grades"] = dict(grades)
        state["excluded_serious_grading"] = excluded
        state["frozen"]["serious_grades"] = grade_digest
    elif policy := plan.get("serious"):
        serious_plan = {**plan, "rubric": policy["rubric"]}
        archived = base / policy["grades"] if "grades" in policy else None
        serious_grades, digest, serious_excluded = initial_grades(
            archived, serious_plan, shifts
        )
        # Both rubric questions use the same evidence bundle at the same cutoff.
        excluded.update(serious_excluded)
        for case in excluded:
            grades.pop(case, None)
            serious_grades.pop(case, None)
        state["serious_initial_grades"] = serious_grades
        state["excluded_serious_grading"] = excluded
        state["frozen"]["serious_grades"] = digest
    if isinstance(live, SqlSelector) or isinstance(candidate, SqlSelector):
        state["frozen"]["sqlite_version"] = sqlite3.sqlite_version
    add_packet(state)
    state["reviewer_unblinded"] = False
    state["comparison_id"] = "T-" + fingerprint(state)
    if proposal is not None:
        lineage.sign_initial(state)
    return state


def _write_report(fd: int, state: dict) -> None:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(render_report(state))
        stream.flush()
        os.fsync(stream.fileno())


def publish(out: Path, state: dict) -> Path:
    if out.exists() or out.is_symlink():
        raise InputError(f"output directory already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".telryn-", dir=out.parent))
    try:
        _write_report(
            os.open(
                staging / "report.html", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            ),
            state,
        )
        packet = staging / "grading.csv"
        write_packet(packet, state)
        with packet.open("rb") as stream:
            os.fsync(stream.fileno())
        sync_directory(staging)
        with lineage.current(state):
            staging.rename(out)
            sync_directory(out.parent)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return out / "report.html"


def refresh(
    path: Path,
    grades: Path | None,
    resolution: tuple[str, str, str] | None,
    minutes: int | None = None,
    reviewer_unblinded: bool = False,
    review_id: str | None = None,
    expected_review_set: str | None = None,
) -> dict:
    def update(state: dict) -> bool:
        if state["schema"] != REPORT_SCHEMA:
            raise InputError(
                "re-run compare and issue a fresh packet before updating this older report"
            )
        if grades is not None:
            changed = import_packet(grades, state, review_id)
        else:
            if resolution is None:
                raise InputError("refresh requires returned grades or an adjudication")
            if expected_review_set is None:
                raise InputError(
                    "--adjudicate requires --review-set from the current report"
                )
            changed = adjudicate(state, *resolution, minutes, expected_review_set)
        if reviewer_unblinded and not state.get("reviewer_unblinded", False):
            state["reviewer_unblinded"] = True
            changed = True
        return changed

    return _update_report(path, update)


def record_shadow(path: Path, observations: Path) -> dict:
    def update(state: dict) -> bool:
        return shadow.record(state, observations, recommendation(state))

    return _update_report(path, update)


def _update_report(path: Path, update: Callable[[dict], bool]) -> dict:
    path = path.resolve(strict=True)
    lock = path.with_name(f".{path.name}.lock")
    # Retain the inode so waiting processes always lock the same file.
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX)
        state = load_report(path, recover=True)
        with lineage.current(state, require_source=False) as store:
            if update(state):
                lineage.advance(state, store)
                if store is not None:
                    audit_trust.replace_report(
                        store[0], path, render_report(state).encode("utf-8")
                    )
                    return state
                report_fd, temp_name = tempfile.mkstemp(
                    prefix=f".{path.name}-", dir=path.parent
                )
                temp = Path(temp_name)
                try:
                    _write_report(report_fd, state)
                    temp.replace(path)
                    sync_directory(path.parent)
                finally:
                    temp.unlink(missing_ok=True)
        return state
