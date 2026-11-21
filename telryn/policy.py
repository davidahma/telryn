"""Calculate and track clocks defined by the firm's dated policy evidence."""

import os
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import (
    InputError,
    canonical,
    fingerprint,
    integer,
    mapping,
    read_json,
    timestamp,
)


def fields(obj, expected, label):
    if not isinstance(obj, dict) or set(obj) != set(expected):
        raise InputError(f"{label} must have the documented fields")


def label(value, name):
    if (
        not isinstance(value, str)
        or not value.isprintable()
        or value.strip() != value
        or not value
        or len(value) > 1024
    ):
        raise InputError(f"invalid {name}")
    return value


def read_input(path, name):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise InputError(f"{name} exceeds 32 MiB")
    return read_json(path, name)


def load(path):
    obj = read_input(path, "clock policy")
    fields(
        obj,
        (
            "schema",
            "id",
            "version",
            "timezone",
            "holidays",
            "episode_days",
            "registration_clause",
            "clauses",
        ),
        "clock policy",
    )
    for key in ("id", "version", "timezone", "registration_clause"):
        label(obj[key], key)
    try:
        ZoneInfo(obj["timezone"])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise InputError("policy needs an installed IANA timezone") from exc
    try:
        valid_holidays = isinstance(obj["holidays"], list) and all(
            isinstance(d, str) and date.fromisoformat(d).isoformat() == d
            for d in obj["holidays"]
        )
    except ValueError:
        valid_holidays = False
    if not valid_holidays:
        raise InputError("holidays must be local YYYY-MM-DD dates")
    obj["holidays"] = sorted(set(obj["holidays"]))
    if integer(obj["episode_days"], "episode days", minimum=1) > 3660:
        raise InputError("episode window exceeds ten years")
    clauses = mapping(obj["clauses"], "policy clauses")
    if not 1 <= len(clauses) <= 100 or obj["registration_clause"] not in clauses:
        raise InputError(
            "supply up to 100 named clauses including a registration clause"
        )
    for name, rule in clauses.items():
        label(name, "clause ID")
        fields(
            rule,
            ("start_event", "stop_event", "unit", "count", "due_time"),
            "policy clause",
        )
        for event in ("start_event", "stop_event"):
            label(rule[event], event)
        if (
            rule["start_event"] == rule["stop_event"]
            or rule["start_event"] == "register_entry"
        ):
            raise InputError("a clock needs distinct start and stop evidence")
        if (name == obj["registration_clause"]) != (
            rule["stop_event"] == "register_entry"
        ):
            raise InputError(
                "only the named registration clock can end on register entry"
            )
        if (
            rule["unit"] not in ("calendar_days", "business_days", "weeks")
            or integer(rule["count"], "clock count", minimum=1) > 3660
        ):
            raise InputError("invalid clock unit or count")
        when = rule["due_time"]
        if when != "same" and (
            not isinstance(when, str)
            or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", when)
        ):
            raise InputError("due_time must be same or a local HH:MM")
    return obj


def deadline(start, rule, policy):
    """Exclude the triggering local date. Preserve wall time or apply the specified local time."""
    instant, remainder = timestamp(start, "clock start")
    zone = ZoneInfo(policy["timezone"])
    local = instant.astimezone(zone)
    day = local.date()
    count = rule["count"]
    if rule["unit"] == "business_days":
        holidays = set(policy["holidays"])
        while count:
            day += timedelta(days=1)
            count -= day.weekday() < 5 and day.isoformat() not in holidays
    else:
        day += timedelta(days=count * (7 if rule["unit"] == "weeks" else 1))
    same_time = rule["due_time"] == "same"
    wall = datetime.combine(
        day, local.time() if same_time else time.fromisoformat(rule["due_time"])
    )
    # A nonexistent/repeated wall time needs a decision by the policy owner.
    first = wall.replace(tzinfo=zone, fold=0)
    if first.utcoffset() != wall.replace(tzinfo=zone, fold=1).utcoffset():
        raise InputError(
            "ambiguous or nonexistent policy due time at a timezone transition"
        )
    utc = first.astimezone(UTC)
    if same_time and remainder:
        return utc.isoformat(timespec="microseconds")[:-6] + remainder + "Z"
    return utc.isoformat().replace("+00:00", "Z")


def dependency(policy, name):
    rule = policy["clauses"].get(name)
    if rule is None:
        return None
    return fingerprint(
        [
            rule,
            policy["timezone"],
            policy["episode_days"],
            policy["holidays"] if rule["unit"] == "business_days" else [],
        ]
    )


def mark(state, next_policy):
    records = state.get("complaint_records", {})
    evidence = records.get("snapshot", {}).get("episodes")
    if not evidence or evidence["policy"]["id"] != next_policy["id"]:
        return False
    old = evidence["policy"]
    if old["version"] == next_policy["version"]:
        if old != next_policy:
            raise InputError("different policy contents reuse the issued version")
        return False
    clauses = set(evidence["dependencies"])
    relevant_events = {
        event["event"]
        for contact in evidence["contacts"]
        for event in contact["events"]
    }
    clauses.update(
        name
        for name, rule in next_policy["clauses"].items()
        if rule["start_event"] in relevant_events
    )
    changed = sorted(
        name
        for name in clauses
        if dependency(old, name) != dependency(next_policy, name)
    )
    if old["registration_clause"] != next_policy["registration_clause"]:
        changed = sorted(
            set(changed)
            | {old["registration_clause"], next_policy["registration_clause"]}
        )
    digest = fingerprint(next_policy)
    for prior in records.get("policy_reviews", []):
        if (
            prior["review_version"] == next_policy["version"]
            and prior["policy_digest"] != digest
        ):
            raise InputError("different policy contents reuse a review version")
    if not changed:
        return False
    notice = {
        "policy": old["id"],
        "issued_version": old["version"],
        "review_version": next_policy["version"],
        "policy_digest": digest,
        "week": state["week"],
        "changed_clauses": changed,
    }
    notices = records.setdefault("policy_reviews", [])
    for prior in notices:
        if prior["review_version"] == next_policy["version"]:
            if prior != notice:
                raise InputError("different policy contents reuse a review version")
            return False
    notices.append(notice)
    return True


def register_arguments(commands):
    cli = commands.add_parser(
        "policy-review",
        help="mark signed weekly reports that depend on a changed policy clause",
    )
    cli.add_argument("policy", type=Path)
    cli.add_argument(
        "reports",
        type=Path,
        nargs="+",
        help="weekly report files or directories containing report.html files",
    )


def _directory_reports(root):
    root = root.resolve(strict=True)

    def unreadable(error):
        raise InputError(
            f"cannot inspect policy-review directory {error.filename}"
        ) from error

    for directory, subdirs, files in os.walk(root, onerror=unreadable):
        parent = Path(directory)
        for name in subdirs:
            folder = parent / name
            if folder.is_symlink() and not folder.resolve(strict=True).is_relative_to(
                root
            ):
                raise InputError(
                    f"policy-review directory contains a linked subtree; supply its reports explicitly: {parent / name}"
                )
        if "report.html" in files:
            report = (parent / "report.html").resolve(strict=True)
            if not report.is_relative_to(root):
                raise InputError(
                    f"report resolves outside the selected directory; supply the intended report explicitly: {parent}"
                )
            yield report


def execute(args):
    from . import report as comparison
    from . import weekly_report

    next_policy = load(args.policy)
    paths = set()
    explicit = set()
    for item in args.reports:
        if item.is_dir():
            paths.update(_directory_reports(item))
        else:
            path = item.resolve(strict=True)
            explicit.add(path)
            paths.add(path)
    affected = []
    skipped = []
    checked = 0
    for path in sorted(paths):
        # A shared reports root may contain the ordinary queue comparison too.
        if path not in explicit:
            raw = path.read_bytes()
            if (
                weekly_report.STATE_OPEN.encode() not in raw
                and comparison.STATE_OPEN.encode() in raw
            ):
                comparison.load_report(path)
                skipped.append(str(path))
                continue
        state = weekly_report._update(path, lambda state: mark(state, next_policy))
        checked += 1
        for flag in state.get("complaint_records", {}).get("policy_reviews", []):
            if flag["policy_digest"] == fingerprint(next_policy):
                affected.append({"report": str(path), **flag})
    print(
        canonical(
            {
                "checked": checked,
                "affected": affected,
                "comparison_reports_skipped": skipped,
            }
        )
    )
    return 0
