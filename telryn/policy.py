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
    versioned,
)


def fields(obj, expected, label):
    if not isinstance(obj, dict) or set(obj) != set(expected):
        raise InputError(f"{label} must have the documented fields")


def label(value, name, *, limit=1024):
    if (
        not isinstance(value, str)
        or not value.isprintable()
        or value.strip() != value
        or not value
        or len(value) > limit
    ):
        raise InputError(f"invalid {name}")
    return value


def read_input(path, name):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise InputError(f"{name} exceeds 32 MiB")
    return read_json(path, name)


def load(path):
    return validate(read_input(path, "clock policy"))


CALENDARS = Path(__file__).with_name("policy_packs") / "calendars"
CALENDAR_ID = re.compile(r"[a-z][a-z0-9-]{0,63}@[0-9]{4}-[0-9]{2}-[0-9]{2}")
LOCAL_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")


def _iso_day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _default_label(policy):
    c = policy.get("holiday_calendar") if policy else None
    if not c:
        return "No dated holiday calendar"
    return f"{c['id']}@{c['version']} ({c['valid_from']} through {c['valid_through']})"


CALENDAR_MAPS = ("holiday_calendars_by_channel", "holiday_calendars_by_region")


def calendar_entries(policy):
    if not policy:
        return {}
    return {
        **(
            {"default": policy["holiday_calendar"]}
            if policy.get("holiday_calendar")
            else {}
        ),
        **{
            f"channel {key}": value
            for key, value in policy.get(CALENDAR_MAPS[0], {}).items()
        },
        **{
            f"region {key}": value
            for key, value in policy.get(CALENDAR_MAPS[1], {}).items()
        },
    }


def calendar_label(policy):
    if not policy or not any(key in policy for key in CALENDAR_MAPS):
        return _default_label(policy)
    return "; ".join(
        f"{key}: {_default_label({'holiday_calendar': calendar})}"
        for key, calendar in sorted(calendar_entries(policy).items())
    )


def calendar_warnings(policy, *, on):
    """Return advisory text relative to the audit or review's local date."""
    warnings = []
    for key, calendar in sorted(calendar_entries(policy).items()):
        limit = date.fromisoformat(calendar["valid_through"])
        remaining = (limit - on).days
        if remaining <= 90:
            status = (
                f"expires in {remaining} day{'s' if remaining != 1 else ''}"
                if remaining >= 0
                else "expired"
            )
            warnings.append(
                f"Holiday calendar {calendar['id']}@{calendar['version']} ({key}) {status}. Coverage ends {limit.isoformat()}."
            )
    return warnings


def warnings_at(policy, instant=None):
    if not policy:
        return []
    zone = ZoneInfo(policy["timezone"])
    day = (
        timestamp(instant, "calendar observation")[0].astimezone(zone).date()
        if instant is not None
        else datetime.now(zone).date()
    )
    return calendar_warnings(policy, on=day)


def select_calendar(policy, *, channel=None, region=None):
    if calendar := policy.get(CALENDAR_MAPS[1], {}).get(region):
        return calendar, calendar["holidays"]
    if calendar := policy.get(CALENDAR_MAPS[0], {}).get(channel):
        return calendar, calendar["holidays"]
    return policy.get("holiday_calendar"), policy["holidays"]


def uses_business_days(rule):
    return rule["unit"] == "business_days" or "receipt_cutoff" in rule


def _expand(reference):
    if not CALENDAR_ID.fullmatch(reference):
        raise InputError("invalid bundled holiday calendar identifier")
    source = CALENDARS / f"{reference}.json"
    if not source.is_file():
        raise InputError(
            "select one installed holiday calendar or supply its full dated contents"
        )
    calendar = read_input(source, "bundled holiday calendar")
    calendar.pop("schema")
    if reference != f"{calendar['id']}@{calendar['version']}":
        raise InputError("bundled calendar identity mismatch")
    return calendar


def _normalize_calendar(calendar):
    fields(
        calendar,
        ("id", "version", "valid_from", "valid_through", "source", "holidays"),
        "holiday calendar",
    )
    for key in ("id", "version", "source"):
        label(calendar[key], key)
    holidays = calendar["holidays"]
    first, last = calendar["valid_from"], calendar["valid_through"]
    if (
        not _iso_day(first)
        or not _iso_day(last)
        or first > last
        or not isinstance(holidays, list)
        or any(not _iso_day(day) or day < first or day > last for day in holidays)
    ):
        raise InputError(
            "calendar dates must be inside its inclusive valid_from and valid_through"
        )
    days = sorted(set(holidays))
    ref = f"{calendar['id']}@{calendar['version']}"
    if CALENDAR_ID.fullmatch(ref):
        bundled = CALENDARS / f"{ref}.json"
        if bundled.is_file() and read_input(bundled, "bundled holiday calendar") != {
            "schema": 1,
            **calendar,
            "holidays": days,
        }:
            raise InputError(
                "modified calendar reuses a bundled ID and version; assign a distinct firm calendar identity"
            )
    return {**calendar, "holidays": days}


def validate(obj, *, historical=False):
    versioned(obj, "clock policy")
    # Only packaged calendar identifiers resolve to a file. The expanded dates
    # and authority are then frozen into the signed state for offline replay.
    calendar = obj.get("holiday_calendar")
    if isinstance(calendar, str):
        if "holidays" in obj:
            raise InputError(
                "select one installed holiday calendar or supply its full dated contents"
            )
        calendar = _expand(calendar)
        obj["holidays"] = calendar.pop("holidays")
        obj["holiday_calendar"] = calendar
    for key in CALENDAR_MAPS:
        if key not in obj:
            continue
        entries = mapping(obj[key], key)
        if not 1 <= len(entries) <= 16:
            raise InputError(f"{key} requires 1–16 calendar assignments")
        for name, entry in entries.items():
            label(name, key, limit=80)
            normalized = _normalize_calendar(
                _expand(entry) if isinstance(entry, str) else entry
            )
            for field in ("id", "version"):
                label(normalized[field], field, limit=128)
            entries[name] = normalized
    if any(key in obj for key in CALENDAR_MAPS) and calendar is None:
        if obj.get("holidays"):
            raise InputError("unassigned holiday dates need a default calendar")
        obj["holidays"] = []
    fields(
        {
            key: value
            for key, value in obj.items()
            if key not in ("business_weekdays", "holiday_calendar", *CALENDAR_MAPS)
        },
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
    weekdays = obj.get("business_weekdays", [1, 2, 3, 4, 5])
    if (
        not isinstance(weekdays, list)
        or not weekdays
        or any(type(day) is not int or not 1 <= day <= 7 for day in weekdays)
        or len(set(weekdays)) != len(weekdays)
    ):
        raise InputError(
            "business_weekdays must list distinct ISO weekdays from 1 through 7"
        )
    try:
        ZoneInfo(obj["timezone"])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise InputError("policy needs an installed IANA timezone") from exc
    if not isinstance(obj["holidays"], list) or not all(
        _iso_day(d) for d in obj["holidays"]
    ):
        raise InputError("holidays must be local YYYY-MM-DD dates")
    obj["holidays"] = sorted(set(obj["holidays"]))
    if "holiday_calendar" in obj:
        fields(
            calendar,
            ("id", "version", "valid_from", "valid_through", "source"),
            "holiday calendar",
        )
        _normalize_calendar({**calendar, "holidays": obj["holidays"]})
    if any(key in obj for key in CALENDAR_MAPS):
        identities = {}
        for key, spec in calendar_entries(obj).items():
            ident = (spec["id"], spec["version"])
            data = {**spec, "holidays": obj["holidays"]} if key == "default" else spec
            if identities.setdefault(ident, data) != data:
                raise InputError(
                    "different calendar contents reuse a policy calendar identity"
                )
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
            {
                key: value
                for key, value in mapping(rule, "policy clause").items()
                if key != "receipt_cutoff"
            },
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
            not isinstance(when, str) or not LOCAL_TIME.fullmatch(when)
        ):
            raise InputError("due_time must be same or a local HH:MM")
        if "receipt_cutoff" in rule and (
            not isinstance(rule["receipt_cutoff"], str)
            or not LOCAL_TIME.fullmatch(rule["receipt_cutoff"])
        ):
            raise InputError("receipt_cutoff must be a local HH:MM")
        if not historical and uses_business_days(rule) and not calendar_entries(obj):
            raise InputError(
                "business-day clocks and receipt cutoffs need a dated holiday_calendar with valid_through"
            )
    return obj


def deadline(start, rule, policy, *, channel=None, region=None):
    """Exclude the triggering local date. Preserve wall time or apply the specified local time."""
    instant, remainder = timestamp(start, "clock start")
    zone = ZoneInfo(policy["timezone"])
    local = instant.astimezone(zone)
    day = local.date()
    count = rule["count"]
    if uses_business_days(rule):
        calendar, holiday_dates = select_calendar(
            policy, channel=channel, region=region
        )
        if (
            not calendar
            or not calendar["valid_from"]
            <= day.isoformat()
            <= calendar["valid_through"]
        ):
            return None
        last = date.fromisoformat(calendar["valid_through"])
        holidays = set(holiday_dates)
        weekdays = set(policy.get("business_weekdays", [1, 2, 3, 4, 5]))

        def business(d):
            return d.isoweekday() in weekdays and d.isoformat() not in holidays

        if cutoff := rule.get("receipt_cutoff"):
            close = time.fromisoformat(cutoff)
            if (
                not business(day)
                or local.time() > close
                or (local.time() == close and remainder.strip("0"))
            ):
                while day < last:
                    day += timedelta(days=1)
                    if business(day):
                        break
                else:
                    return None
        if rule["unit"] == "business_days":
            while count:
                if day >= last:
                    return None
                day += timedelta(days=1)
                count -= business(day)
    if rule["unit"] != "business_days":
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
    uses_business = uses_business_days(rule)
    return fingerprint(
        [
            rule,
            policy["timezone"],
            policy["episode_days"],
            policy["holidays"] if uses_business else [],
            sorted(policy.get("business_weekdays", [1, 2, 3, 4, 5]))
            if uses_business
            else [],
            policy.get("holiday_calendar") if uses_business else None,
            *(
                [calendar_entries(policy)]
                if uses_business and any(key in policy for key in CALENDAR_MAPS)
                else []
            ),
        ]
    )


def mark(state, next_policy, *, pack_digest=None):
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
    for prior in state.get("pack_reviews", []):
        if (
            prior["review_version"] == next_policy["version"]
            and prior.get("policy_digest", digest) != digest
        ):
            raise InputError("different clock contents reuse a reviewed pack version")
    for prior in records.get("policy_reviews", []):
        if prior["review_version"] == next_policy["version"] and (
            prior["policy_digest"] != digest
            or (pack_digest and prior.get("pack_digest", pack_digest) != pack_digest)
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
            if any(prior.get(k) != v for k, v in notice.items()):
                raise InputError("different policy contents reuse a review version")
            if pack_digest and "pack_digest" not in prior:
                prior["pack_digest"] = pack_digest
                return True
            return False
    if pack_digest:
        notice["pack_digest"] = pack_digest
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


def update_review(state, next_policy, candidate=None):
    from . import packs

    rubric_changed = candidate is not None and packs.review_rubric(state, candidate)
    return (
        mark(
            state,
            next_policy,
            pack_digest=fingerprint(candidate) if candidate else None,
        )
        or rubric_changed
    )


def execute(args):
    from . import report as comparison
    from . import weekly_report

    from . import packs

    supplied = read_input(args.policy, "revised policy or pack")
    candidate = packs.validate_local(supplied) if "clock_policy" in supplied else None
    next_policy = (
        candidate["clock_policy"] if candidate is not None else validate(supplied)
    )

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
        state = weekly_report._update(
            path, lambda state: update_review(state, next_policy, candidate)
        )
        checked += 1
        flags = [
            f
            for f in state.get("complaint_records", {}).get("policy_reviews", [])
            if f["policy_digest"] == fingerprint(next_policy)
        ]
        if candidate is not None:
            flags.extend(
                f
                for f in state.get("pack_reviews", [])
                if f["pack_digest"] == fingerprint(candidate)
            )
        if flags:
            combined = {"report": str(path)}
            for flag in flags:
                combined.update(flag)
            affected.append(combined)
    print(
        canonical(
            {
                "checked": checked,
                "affected": affected,
                "comparison_reports_skipped": skipped,
                "calendar_warnings": warnings_at(next_policy),
            }
        )
    )
    return 0
