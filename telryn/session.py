"""Build a comparison and publish or refresh its artifacts as one operation."""

from __future__ import annotations

import fcntl
import os
import shutil
import sys
import tempfile
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

from .contracts import InputError, fingerprint, load_export, load_plan
from .grades import add_packet, adjudicate, import_packet, initial_grades, write_packet
from .replay import Selector, blocked, replay_shift
from .report import load_report, render_report


def compare(plan_path: Path, export_path: Path | None = None) -> dict:
    plan = load_plan(plan_path)
    base = plan_path.parent
    source = load_export(
        export_path if export_path is not None else base / plan["export"]
    )
    with redirect_stdout(sys.stderr):
        live = Selector(base, plan["live"])
        candidate = Selector(base, plan["candidate"])
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
    for name, ids in replayed.items():
        overlap = sorted(case for case in ids if counts[case] > 1)
        if overlap:
            shifts[name] = blocked(
                f"Eligible request IDs overlap required shifts: {', '.join(overlap)}."
            )
    grades, grade_digest, excluded = initial_grades(
        base / plan["grades"] if "grades" in plan else None, plan, shifts
    )
    state = {
        "schema": 2,
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
    add_packet(state)
    state["reviewer_unblinded"] = False
    state["comparison_id"] = "T-" + fingerprint(state)
    return state


def _write_report(fd: int, state: dict) -> None:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(render_report(state))
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
        _sync_directory(staging)
        staging.rename(out)
        _sync_directory(out.parent)
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
    path = path.resolve(strict=True)
    lock = path.with_name(f".{path.name}.lock")
    # Retain the inode so waiting processes always lock the same file.
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX)
        state = load_report(path)
        if state["schema"] != 2:
            raise InputError(
                "re-run compare for this v0.1.0 report before importing or adjudicating grades"
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
        if changed:
            report_fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}-", dir=path.parent
            )
            temp = Path(temp_name)
            try:
                _write_report(report_fd, state)
                temp.replace(path)
                _sync_directory(path.parent)
            finally:
                temp.unlink(missing_ok=True)
        return state
