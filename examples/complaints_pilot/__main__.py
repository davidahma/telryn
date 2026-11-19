"""Rehearse a blinded two-week complaints comparison on synthetic evidence."""

import argparse
import csv
import json
import sqlite3
import tempfile
import tomllib
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telryn import complaints, weekly, weekly_report, weekly_review
from telryn.contracts import InputError, canonical, fingerprint
from telryn.integrations_coverage import new_receipt, publish

FIXTURE = Path(__file__).resolve().parent


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader()
        writer.writerows(rows)


def pilot_plan(work, week, cases, protocol, *, commitment=None):
    work.mkdir()
    monday = datetime.fromisoformat(week).replace(tzinfo=timezone.utc)
    scope = (
        "Synthetic complaints rehearsal. Grading census of the two shortlists. Counts describe their union only. Full-queue prevalence is unknown. "
        f"Frozen comparison {commitment}."
        if commitment
        else "All closed conversations in the synthetic payments support queue."
    )
    rows = [
        (
            case["case_id"],
            "v1",
            "conversations/" + case["case_id"],
            "false" if commitment else case["screen_flag"],
        )
        for case in cases
    ]
    coverage = new_receipt(monday, monday + timedelta(days=7), scope, "event-ledger")
    coverage.update(
        scanned=len(cases),
        eligible=len(cases),
        dimensions=[],
        completed_at={c["case_id"]: c["closed_at"] for c in cases},
    )
    publish(work / "population.csv", rows, coverage)
    write_csv(
        work / "ordinary.csv",
        ["case_id"],
        [{"case_id": c["case_id"]} for c in cases if c["ordinary_reviewed"] == "yes"],
    )
    settings = dict(
        schema=1,
        week=week,
        rubric=protocol["policy"],
        rubric_question=protocol["question"],
        scope_note=scope,
        finding_label="conversations requiring complaint registration",
        population="population.csv",
        reviewed="ordinary.csv",
        screen_version="none" if commitment else "synthetic-complaints-screen-v1",
        flagged_sample="100%" if commitment else protocol["flagged_sample"],
        unflagged_sample="100%" if commitment else protocol["unflagged_sample"],
    )
    path = work / "plan.toml"
    path.write_text("".join(f"{k} = {json.dumps(v)}\n" for k, v in settings.items()))
    return path


def source_cutoff(week):
    monday = datetime.fromisoformat(week).replace(tzinfo=timezone.utc)
    return monday + timedelta(days=7, hours=8)


def ledger_through(name, cutoff):
    rows = read_csv(FIXTURE / (name + ".csv"))
    return list(rows[0]), [
        r for r in rows if datetime.fromisoformat(r["recorded_at"]) <= cutoff
    ]


def shortlists(work, week, cases, protocol):
    query = (FIXTURE / "native.sql").read_text()
    cutoff = source_cutoff(week)
    _, register = ledger_through("register", cutoff)
    _, owners = ledger_through("owner-events", cutoff)
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(
            "CREATE TABLE register_at_cutoff(match_key,entry_id,recorded_at,evidence_ref)"
        )
        db.executemany(
            "INSERT INTO register_at_cutoff VALUES (:match_key,:entry_id,:recorded_at,:evidence_ref)",
            register,
        )
        db.execute(
            "CREATE TABLE owners_at_cutoff(case_id, action_id,status,owner,recorded_at,evidence_ref)"
        )
        db.executemany(
            "INSERT INTO owners_at_cutoff VALUES (:case_id,:action_id,:status,:owner,:recorded_at,:evidence_ref)",
            owners,
        )
        db.execute(
            "CREATE TABLE eligible(case_id PRIMARY KEY, message, screen_flag, match_key, deadline)"
        )
        db.executemany(
            "INSERT INTO eligible VALUES (?,?,?,?,?)",
            [
                (
                    r["case_id"],
                    r["message"],
                    r["screen_flag"],
                    r["match_key"],
                    r["deadline"],
                )
                for r in cases
                if r["ordinary_reviewed"] == "no"
            ],
        )
        native = [r[0] for r in db.execute(query, {"slots": protocol["native_slots"]})]
    draw = weekly.prepare(pilot_plan(work / "frame", week, cases, protocol))
    picks = {"native": native, "telryn": [r["case_id"] for r in draw["cases"]]}
    preregistered = {
        "evidence": "synthetic",
        "week": week,
        "protocol": protocol,
        "source_fingerprint": fingerprint(cases),
        "source_cutoff": cutoff.isoformat(),
        "visible_ledger_fingerprints": {
            "register": fingerprint(register),
            "owners": fingerprint(owners),
        },
        "native_sql": query,
        "draw_commitment": draw["draw_commitment"],
        "picks": picks,
    }
    return picks, preregistered


def grade_union(report, work, answers):
    packet = read_csv(report.with_name("grades.csv"))
    for review, submit in (
        ("primary", weekly_report.refresh),
        ("second", weekly_report.second_review),
    ):
        # Each synthetic reviewer receives the same source and blank, mixed packet.
        filled = []
        for prompt in packet:
            answer = answers[prompt["case_id"]]
            filled.append(
                {
                    **prompt,
                    **{
                        k: answer[k]
                        for k in ("serious", "disposition", "owner", "action")
                    },
                    "minutes": answer[review + "_minutes"],
                }
            )
        returned = work / "return.csv"
        write_csv(returned, packet[0].keys(), filled)
        submit(report, returned, reviewer="Staged " + review)
    return packet[0]["audit_id"]


def attach_records(report, work, week, union, protocol, audit_id):
    cutoff = source_cutoff(week)
    as_of = cutoff + timedelta(days=2)
    # Each simulated vendor export ends at its observation date. In particular,
    # week one's archive never contains an action or register entry from week two.
    for name in ("register", "owner-events"):
        columns, rows = ledger_through(name, as_of)
        write_csv(work / (name + ".csv"), columns, rows)
    manifest = {
        "schema": 1,
        "audit_id": audit_id,
        "rubric": protocol["policy"],
        "cutoff": cutoff.isoformat(),
        "as_of": as_of.isoformat(),
        "scope": protocol["queue"],
        "register": {
            "file": "register.csv",
            "source_ref": f"register/{week}",
            "complete_through": as_of.isoformat(),
        },
        "actions": {
            "file": "owner-events.csv",
            "source_ref": f"actions/{week}",
            "complete_through": as_of.isoformat(),
        },
        "cases": [
            {
                "case_id": case["case_id"],
                "revision": "v1",
                "deadline": case["deadline"],
                "match_key": case["match_key"],
                "policy_ref": protocol["policy"] + "#logging-deadline",
            }
            for case in union
        ],
    }
    path = work / "records.json"
    path.write_text(canonical(manifest))
    weekly_report.attach_complaints(report, path)


def ownership(episodes_by_method):
    native = set(episodes_by_method["native"])
    telryn = set(episodes_by_method["telryn"])
    return {
        "native_only": sorted(native - telryn),
        "telryn_only": sorted(telryn - native),
        "both": sorted(telryn & native),
        "unique": len(native | telryn),
    }


def tally(state, picks, effort, protocol):
    outcomes = complaints.reconcile(state)
    cases = {r["case_id"]: r for r in outcomes["cases"]}
    cutoff = datetime.fromisoformat(outcomes["cutoff"])
    observed = {}
    index = read_csv(FIXTURE / "owner-episodes.csv")
    episodes = {}
    for row in index:
        key = (row["case_id"], row["action_id"])
        episodes.setdefault(key, set()).add(row["complaint_episode"])
    grades = weekly_review.effective_grades(state)
    pending = []
    for ident, outcome in cases.items():
        if (
            outcome["grade"] is not True
            or grades[ident]["disposition"] != "new"
            or outcome["registration_at_cutoff"] != "missing"
            or outcome["owner_at_cutoff"] != "unobserved"
        ):
            continue
        events = sorted(
            (e for e in outcome["actions"] if e["status"] in ("accepted", "completed")),
            key=lambda event: datetime.fromisoformat(event["recorded_at"]),
        )
        if not events:
            pending.append(ident)
            continue

        def elapsed(event):
            return (
                datetime.fromisoformat(event["recorded_at"]) - cutoff
            ).total_seconds() / 60

        acceptance = next((e for e in events if e["status"] == "accepted"), None)
        completed = next((e for e in events if e["status"] == "completed"), None)
        links = [episodes.get((ident, e["action_id"]), set()) for e in events]
        matched = set.union(*links)
        episode = next(iter(matched)) if all(links) and len(matched) == 1 else None
        observed[ident] = {
            "owner": events[0]["owner"],
            "complaint_episode": episode,
            "acceptance_ref": acceptance["evidence_ref"] if acceptance else None,
            "minutes_to_owner_acceptance_from_cutoff": elapsed(acceptance)
            if acceptance
            else None,
            "minutes_to_completed_action_from_cutoff": elapsed(completed)
            if completed
            else None,
            "action_ref": completed["evidence_ref"] if completed else None,
        }

    measures = {}
    cap = protocol["staff_minutes_per_arm"]
    for method, selected in picks.items():
        phases = {
            k: int(v) for k, v in effort[method].items() if k not in ("week", "method")
        }
        reviews = [state["case_reviews"][i] for i in selected]
        phases["first_review"] = sum(r["primary"]["minutes"] for r in reviews)
        phases["second_review"] = sum(
            c["second"]["minutes"] for r in reviews for c in r["checks"]
        )
        total = sum(phases.values())
        measures[method] = {
            "selected": selected,
            "owner_accepted_new": sorted(
                {
                    observed[i]["complaint_episode"]
                    for i in selected
                    if i in observed
                    and observed[i]["complaint_episode"]
                    and observed[i]["acceptance_ref"]
                }
            ),
            "staff_minutes": phases,
            "total_staff_minutes": total,
            "within_budget": total <= cap,
        }
    return {
        "staff_budget_per_arm": cap,
        "both_within_budget": all(m["within_budget"] for m in measures.values()),
        "methods": measures,
        "selected_by_both": sorted(set(picks["native"]) & set(picks["telryn"])),
        "owner_accepted": ownership(
            {arm: m["owner_accepted_new"] for arm, m in measures.items()}
        ),
        "new_awaiting_owner_acceptance": sorted(pending),
        "observed_owner_actions": observed,
        "owner_episode_index_fingerprint": fingerprint(index),
        "episode_unresolved": sorted(
            i for i, r in observed.items() if r["complaint_episode"] is None
        ),
        "completed_without_recorded_acceptance": sorted(
            i
            for i, r in observed.items()
            if r["action_ref"] and not r["acceptance_ref"]
        ),
    }


def run(out: Path):
    if out.exists() or out.is_symlink():
        raise InputError(f"output directory already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    protocol = tomllib.loads((FIXTURE / "protocol.toml").read_text())
    population = read_csv(FIXTURE / "conversations.csv")
    weeks = []
    with tempfile.TemporaryDirectory(prefix=".complaints-", dir=out.parent) as temp:
        staging = Path(temp) / "audits"
        staging.mkdir(mode=0o700)
        for week in protocol["weeks"]:
            cases = [c for c in population if c["week"] == week]
            work = Path(temp) / week
            work.mkdir()
            picks, design = shortlists(work, week, cases, protocol)
            committed = fingerprint(design)
            selected = set(picks["native"]) | set(picks["telryn"])
            union = [c for c in cases if c["case_id"] in selected]
            blind_plan = pilot_plan(
                work / "union", week, union, protocol, commitment=committed
            )
            report = weekly_report.start(blind_plan, staging / week)
            answers = {r["case_id"]: r for r in read_csv(FIXTURE / "answers.csv")}
            audit_id = grade_union(report, work, answers)
            attach_records(report, work, week, union, protocol, audit_id)
            effort = {
                r["method"]: r
                for r in read_csv(FIXTURE / "staff-minutes.csv")
                if r["week"] == week
            }
            result = tally(weekly_report.read_state(report), picks, effort, protocol)
            weeks.append(
                {
                    "week": week,
                    "frozen_selection": design,
                    "commitment": committed,
                    "report": str(out / week / "report.html"),
                    **result,
                }
            )
        staging.rename(out)
    help_minutes = weeks[1]["methods"]["telryn"]["staff_minutes"]["operator_help"]
    all_episodes = {
        arm: {i for w in weeks for i in w["methods"][arm]["owner_accepted_new"]}
        for arm in ("native", "telryn")
    }
    total = {
        arm: sum(w["methods"][arm]["total_staff_minutes"] for w in weeks)
        for arm in all_episodes
    }
    return {
        "evidence": "synthetic",
        "weeks": weeks,
        "two_week_owner_accepted": ownership(all_episodes),
        "two_week_staff_minutes": total,
        "equivalent_findings_and_materially_less_effort": (
            all(w["both_within_budget"] for w in weeks)
            and bool(all_episodes["native"])
            and all_episodes["native"] == all_episodes["telryn"]
            and total["native"] - total["telryn"]
            >= protocol["material_effort_saving_minutes"]
        ),
        "week_two_help_minutes": help_minutes,
        "within_predeclared_help_limit": help_minutes
        <= protocol["maximum_week_two_help_minutes"],
        "field_evidence": "Actual effort, earlier action, repeat demand and payment are unmeasured.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    try:
        print(canonical(run(parser.parse_args().out)))
    except (ValueError, OSError, csv.Error) as exc:
        parser.exit(2, f"{exc}\n")
