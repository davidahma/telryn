"""Compare archived Intercom scorecards to a lead's dated human-review ledger."""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from telryn.contracts import (
    InputError,
    canonical,
    fingerprint,
    integer,
    mapping,
    read_json,
    sequence,
    string,
    unique_ids,
)

HERE = Path(__file__).parent
SCOPE = ("workspace", "scorecard_id", "scorecard_version_id", "since", "until")
SUBMITTED = {"submitted", "passed_review", "needs_fix", "wont_fix", "fix_complete"}


def classify(record, scope):
    cards = record.get("scorecards")
    if not isinstance(cards, list):
        return "unavailable", None, None
    matches = []
    for card in cards:
        if not isinstance(card, dict) or any(
            not isinstance(card.get(k), str) or not card[k]
            for k in ("scorecard_id", "scorecard_version_id")
        ):
            return "unavailable", None, None
        if all(card[k] == scope[k] for k in ("scorecard_id", "scorecard_version_id")):
            matches.append(card)
    if len(matches) > 1:
        return "ambiguous_scorecards", None, None
    if not matches:
        return "no_matching_scorecard", None, None
    card = matches[0]
    if "review_status" not in card or "submitted_by" not in card:
        return "unavailable", None, card
    status = card.get("review_status")
    submitter = card.get("submitted_by")
    if isinstance(status, str) and status in SUBMITTED and isinstance(submitter, dict):
        if submitter.get("type") == "admin":
            who = submitter.get("admin_id")
            if isinstance(who, str) and who.strip():
                return "human_submission", who, card
        if submitter.get("type") == "ai":
            return "ai_submission", None, card
    if status in (None, "unreviewed") and submitter is None:
        assigned = card.get("reviewer")
        if isinstance(assigned, dict) and assigned.get("type") == "admin":
            return "human_assignment", None, card
        return "unreviewed", None, card
    return "unresolved", None, card


def review_history(history, scope):
    since, until = scope["since"], scope["until"]
    last_without_submission = {}
    previous_card = None
    by_cutoff = "no_snapshot_before_cutoff"
    later = changed = False
    windows = []
    for captured, record in history:
        status, who, card = classify(record, scope)
        if captured >= until:
            later = True
            if previous_card is not None and card is not None:
                # Reflect a visible difference, without guessing when an edit occurred.
                changed |= card != previous_card
            continue
        previous_card = card
        by_cutoff = status
        teammate = (card or {}).get("reviewed_teammate")
        subject = None
        if isinstance(teammate, dict) and teammate.get("type") == "admin":
            ident = teammate.get("admin_id")
            if isinstance(ident, str) and ident.strip():
                subject = ident
        if status == "human_submission":
            created = record["created_at"]
            lower = max(created, last_without_submission.get(subject, created))
            if lower >= since:
                windows.append({"from": lower, "through": captured, "submitter": who})
        elif subject is not None and status in {
            "human_assignment",
            "unreviewed",
            "ai_submission",
        }:
            last_without_submission[subject] = captured
    return {
        "last_status_before_cutoff": by_cutoff,
        "human_submission_windows": windows,
        "observed_after_cutoff": later,
        "later_scorecard_differs": changed,
    }


def setup_time(ledger):
    if "setup_trial" not in ledger:
        return None
    timing = mapping(ledger["setup_trial"], "measured setup effort")
    fields = (
        "manual_minutes",
        "export_minutes",
        "reconciliation_minutes",
        "verification_minutes",
    )
    costs = {k: integer(timing.get(k), k) for k in fields}
    threshold = integer(
        timing.get("material_minutes"), "material saving threshold", minimum=1
    )
    assisted = sum(costs[k] for k in fields[1:])
    saving = costs["manual_minutes"] - assisted
    return {
        **costs,
        "assisted_minutes": assisted,
        "difference_minutes": saving,
        "material_minutes": threshold,
        "meets_time_threshold": saving >= threshold,
    }


def compare_submissions(windows, submissions):
    """Keep the case overlap separate from evidence for each dated submission."""
    candidates = [
        {
            i
            for i, (when, who) in enumerate(submissions)
            if w["from"] <= when <= w["through"] and w["submitter"] == who
        }
        for w in windows
    ]
    compatible = set().union(*candidates)
    unambiguous = {i for match in candidates if len(match) == 1 for i in match}
    unresolved = []
    for i, (when, who) in enumerate(submissions):
        if i not in unambiguous:
            unresolved.append(
                {
                    "submitted_at": when,
                    "submitter": who,
                    "reason": "multiple_possible_submissions"
                    if i in compatible
                    else "no_matching_observation",
                }
            )
    return unresolved, any(not match for match in candidates)


def reconcile(archive_path, ledger_path):
    archive = read_json(archive_path, "Intercom scorecard archive")
    ledger = read_json(ledger_path, "completed human-review ledger")
    if archive.get("api_version") != "2.17":
        raise InputError("supply conversation captures from Intercom API 2.17")
    scope = mapping(archive.get("scope"), "archive scope")
    if set(scope) != set(SCOPE) or ledger.get("scope") != scope:
        raise InputError(
            "archive and ledger must name the same workspace, scorecard version and week"
        )
    for key in SCOPE[:3]:
        string(scope[key], key)
    since, until = (integer(scope[k], k) for k in SCOPE[3:])
    if until - since != 7 * 86400:
        raise InputError("supply one week as a half-open interval in Unix seconds")
    if (
        archive.get("evidence") not in ("synthetic", "observed")
        or ledger.get("evidence") != archive["evidence"]
    ):
        raise InputError("both sources must declare the same evidence type")
    if ledger.get("complete") is not True:
        raise InputError(
            "a comparison needs the lead's complete ledger for the stated scope"
        )
    population = set(unique_ids(archive.get("population"), "eligible conversation IDs"))
    if (
        set(unique_ids(ledger.get("population"), "ledger population")) != population
        or not population
    ):
        raise InputError(
            "archive and ledger must cover the same nonempty eligible population"
        )

    records = defaultdict(list)
    created = {}
    for entry in sequence(archive.get("captures"), "captures"):
        entry = mapping(entry, "capture")
        captured = integer(entry.get("captured_at"), "capture time")
        record = mapping(entry.get("conversation"), "conversation")
        case = string(record.get("id"), "conversation ID")
        birth = integer(record.get("created_at"), "conversation creation time")
        if (
            case not in population
            or birth >= until
            or captured < birth
            or created.setdefault(case, birth) != birth
        ):
            raise InputError(
                "capture differs from the population or conversation chronology"
            )
        records[case].append((captured, record))
    events = defaultdict(list)
    for event in sequence(ledger.get("submissions"), "dated human submissions"):
        event = mapping(event, "submission")
        case = string(event.get("case_id"), "case ID")
        when = integer(event.get("submitted_at"), "human submission time")
        who = string(event.get("submitter"), "human submitter")
        if (
            case not in population
            or not since <= when < until
            or when < created.get(case, 0)
        ):
            raise InputError(
                "human submission outside the eligible population or time window"
            )
        events[case].append((when, who))

    cases, witnessed, conflicts = {}, set(), []
    for case in sorted(population):
        history = sorted(records[case], key=lambda item: item[0])
        if len({at for at, _ in history}) != len(history):
            raise InputError("ambiguous duplicate capture time for one conversation")
        case_result = cases[case] = review_history(history, scope)
        windows = case_result["human_submission_windows"]
        dated = events.get(case, [])
        unresolved, unexpected = compare_submissions(windows, dated)
        case_result["unverified_ledger_submissions"] = unresolved
        if windows:
            witnessed.add(case)
            if dated and (unresolved or unexpected):
                conflicts.append(case)
    manual = {case for case, entries in events.items() if entries}
    return {
        "evidence": archive["evidence"],
        "scope": scope,
        "archive_fingerprint": fingerprint(archive),
        "ledger_fingerprint": fingerprint(ledger),
        "human_submission_witnessed": sorted(witnessed),
        "lead_ledger": sorted(manual),
        "both": sorted(witnessed & manual),
        "ledger_only": sorted(manual - witnessed),
        "archive_only": sorted(witnessed - manual),
        "submitter_or_time_conflicts": conflicts,
        "setup_trial": setup_time(ledger),
        "cases": cases,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args(argv)
    if bool(args.archive) != bool(args.ledger):
        parser.error(
            "supply both archived evidence files or use the synthetic rehearsal"
        )
    try:
        result = reconcile(
            args.archive or HERE / "intercom-archive.json",
            args.ledger or HERE / "intercom-ledger.json",
        )
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(canonical(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
