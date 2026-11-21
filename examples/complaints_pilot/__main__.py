"""Rehearse a blinded two-week complaints comparison on synthetic evidence."""

import argparse
import csv
import json
import sqlite3
import tempfile
import tomllib
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from itertools import combinations, product
from pathlib import Path

from telryn import complaints, control, weekly, weekly_report, weekly_review

from telryn.contracts import InputError, canonical, fingerprint, integer, timestamp
from telryn.integrations_coverage import new_receipt, publish

from . import signals

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
        screen_version="none" if commitment else "synthetic-complaints-screen-v2",
        flagged_sample="100%" if commitment else protocol["flagged_sample"],
        unflagged_sample="100%" if commitment else protocol["unflagged_sample"],
    )
    if commitment:
        review_date = (monday + timedelta(days=11)).date().isoformat()
        onboarding = read_csv(FIXTURE / "onboarding.csv")
        cfg = {
            "schema": 1,
            "scope": protocol["queue"],
            "population_basis": "shortlist_union",
            "synthetic": True,
            "categories": ["Service complaint", "Priority complaint"],
            "gap_owner": "Complaints case manager",
            "gap_review_date": review_date,
            "onboarding_minutes": (
                sum(int(r["telryn"]) for r in onboarding)
                if week == protocol["weeks"][0]
                else 0
            ),
            "channels": [
                {**c, "review_date": review_date}
                for c in read_csv(FIXTURE / "channels.csv")
            ],
        }
        (work / "control.json").write_text(canonical(cfg))
        settings["control"] = "control.json"
    path = work / "plan.toml"
    path.write_text("".join(f"{k} = {json.dumps(v)}\n" for k, v in settings.items()))
    return path


def source_cutoff(week):
    monday = datetime.fromisoformat(week).replace(tzinfo=timezone.utc)
    return monday + timedelta(days=7, hours=8)


def ledger_through(name, cutoff):
    with (FIXTURE / (name + ".csv")).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise InputError(f"{name} requires a CSV header")
        return reader.fieldnames, [
            r
            for r in reader
            if timestamp(r["recorded_at"], "ledger time") <= (cutoff, "")
        ]


def current_contact(case):
    return {
        **{
            k: case[k]
            for k in ("case_id", "channel", "subject_ref", "matter_ref", "match_key")
        },
        "revision": "v1",
        "evidence_ref": "conversations/" + case["case_id"],
        "contact_at": case["opened_at"],
        "passages": [{"at": case["message_at"], "text": case["message"]}],
        "events": [],
    }


def visible_contacts(population, cutoff):
    archive = json.loads((FIXTURE / "prior-contacts.json").read_text())["contacts"]
    contacts = [*archive, *(current_contact(c) for c in population)]
    history = []
    horizon = (cutoff, "")
    for contact in contacts:
        if timestamp(contact["contact_at"], "contact time") > horizon:
            continue
        passages = []
        for number, row in enumerate(contact["passages"], 1):
            if timestamp(row["at"], "passage time") <= horizon:
                if number != len(passages) + 1:
                    raise InputError(
                        "supply the transcript revision at cutoff to preserve passage numbering"
                    )
                passages.append(row)
        events = []
        for event in contact["events"]:
            number = integer(event["passage"], "event passage", minimum=1)
            if number > len(contact["passages"]):
                raise InputError("event refers to an absent source passage")
            if number <= len(passages):
                events.append(event)
        history.append({**contact, "passages": passages, "events": events})
    return history


def shortlists(work, week, cases, protocol, history):
    query = (FIXTURE / "native.sql").read_text()
    cutoff = source_cutoff(week)
    _, register = ledger_through("register", cutoff)
    _, owners = ledger_through("owner-events", cutoff)
    policy = json.loads((FIXTURE / "clock-policy.json").read_text())
    register_keys = {r["match_key"] for r in register}
    owned = {r["case_id"] for r in owners if r["status"] in ("accepted", "completed")}
    links, screened, cues, eligible = [], [], {}, []
    for case in cases:
        cid = case["case_id"]
        if not (
            timestamp(case["opened_at"], "conversation opening")
            <= timestamp(case["message_at"], "source passage time")
            <= timestamp(case["closed_at"], "conversation closure")
            <= (cutoff, "")
        ):
            raise InputError(
                f"{cid} needs a source passage between opening and closure by cutoff"
            )
        prior = signals.prior_related(case, history, policy["episode_days"])
        related = [current_contact(case), *prior]
        links.extend((cid, c["case_id"], c["match_key"]) for c in related)
        known = any(c["match_key"] in register_keys for c in related)
        flagged, cues[cid] = signals.flags(case, prior, known)
        already_owned = any(c["case_id"] in owned for c in related)
        screened.append(
            {**case, "screen_flag": str(flagged and not already_owned).lower()}
        )
        if case["ordinary_reviewed"] == "no":
            eligible.append((cid, case["message"], case["agent_note"], len(prior)))
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
        db.execute("CREATE TABLE linked(case_id, contact_id, match_key)")
        db.executemany("INSERT INTO linked VALUES (?,?,?)", links)
        db.execute(
            "CREATE TABLE eligible(case_id PRIMARY KEY, message, agent_note, prior_contacts)"
        )
        db.executemany("INSERT INTO eligible VALUES (?,?,?,?)", eligible)
        native = [r[0] for r in db.execute(query, {"slots": protocol["native_slots"]})]
    draw = weekly.prepare(pilot_plan(work / "frame", week, screened, protocol))
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
        "history_fingerprint": fingerprint(history),
        "screen_recipe_fingerprint": fingerprint(Path(signals.__file__).read_text()),
        "clock_policy_fingerprint": fingerprint(policy),
        "screen_cues": cues,
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
                        for k in (
                            "serious",
                            "disposition",
                            "owner",
                            "action",
                            *control.FIELDS,
                        )
                        if k in prompt
                    },
                    "minutes": answer[review + "_minutes"],
                }
            )
        returned = work / "return.csv"
        write_csv(returned, packet[0].keys(), filled)
        submit(report, returned, reviewer="Staged " + review)
    return packet[0]["audit_id"]


def records_manifest(work, week, union, protocol, audit_id, history):
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
                "deadline": case["deadline"] or None,
                "match_key": case["match_key"],
                "policy_ref": protocol["policy"] + "#logging-deadline",
                "source_citation": {
                    "evidence_ref": "conversations/" + case["case_id"],
                    "revision": "v1",
                    "contact_at": case["opened_at"],
                    "passages": current_contact(case)["passages"],
                    "passage": 1,
                    "quote": case["message"],
                },
            }
            for case in union
        ],
    }
    calculated = [c for c in union if not c["deadline"]]
    if calculated:
        clock_source = (FIXTURE / "clock-policy.json").read_text()
        days = json.loads(clock_source)["episode_days"]
        contacts = {}
        for case in calculated:
            for c in [
                *signals.prior_related(case, history, days),
                current_contact(case),
            ]:
                contacts[c["case_id"]] = c
        (work / "contacts.json").write_text(
            canonical({"schema": 1, "contacts": list(contacts.values())})
        )
        (work / "clock-policy.json").write_text(clock_source)
        manifest.update(
            episode_evidence="contacts.json", clock_policy="clock-policy.json"
        )
    path = work / "records.json"
    path.write_text(canonical(manifest))
    return path


def ownership(episodes_by_method):
    native = set(episodes_by_method["native"])
    telryn = set(episodes_by_method["telryn"])
    return {
        "native_only": sorted(native - telryn),
        "telryn_only": sorted(telryn - native),
        "both": sorted(telryn & native),
        "unique": len(native | telryn),
    }


def owner_findings(state):
    outcomes = complaints.reconcile(state)
    cutoff = timestamp(outcomes["cutoff"], "comparison cutoff")
    observed = {}
    index = read_csv(FIXTURE / "owner-episodes.csv")
    episodes = {}
    for row in index:
        key = (row["case_id"], row["action_id"])
        episodes.setdefault(key, set()).add(row["complaint_episode"])
    grades = weekly_review.effective_grades(state)
    pending = []
    unresolved = []
    for outcome in outcomes["cases"]:
        ident = outcome["case_id"]
        if (
            outcome["grade"] is not True
            or grades[ident]["disposition"] != "new"
            or outcome["registration_at_cutoff"] != "missing"
        ):
            continue
        if "unresolved" in (outcome["owner_at_cutoff"], outcome["owner_as_of"]):
            unresolved.append(ident)
            continue
        if outcome["owner_at_cutoff"] != "unobserved" or any(
            timestamp(e["recorded_at"], "owner event time") <= cutoff
            for e in outcome["actions"]
        ):
            continue
        if outcome["match_status"] != "exact_key":
            unresolved.append(ident)
            continue
        events = [
            e for e in outcome["actions"] if e["status"] in ("accepted", "completed")
        ]
        if not events:
            pending.append(ident)
            continue

        def elapsed(event):
            when, remainder = timestamp(event["recorded_at"], "owner event time")
            # timestamp preserves any fraction smaller than a microsecond separately.
            seconds = (when - cutoff[0]).total_seconds()
            seconds += float("0.000000" + remainder) - float("0.000000" + cutoff[1])
            return seconds / 60

        acceptance = next((e for e in events if e["status"] == "accepted"), None)
        completed = next((e for e in events if e["status"] == "completed"), None)
        links = [episodes.get((e["case_id"], e["action_id"]), set()) for e in events]
        matched = set.union(*links)
        episode = next(iter(matched)) if all(links) and len(matched) == 1 else None
        observed[ident] = {
            "owner": events[0]["owner"],
            "complaint_episode": episode,
            "acceptance_owner": acceptance["owner"] if acceptance else None,
            "completion_owner": completed["owner"] if completed else None,
            "acceptance_ref": acceptance["evidence_ref"] if acceptance else None,
            "minutes_to_owner_acceptance_from_cutoff": elapsed(acceptance)
            if acceptance
            else None,
            "minutes_to_completed_action_from_cutoff": elapsed(completed)
            if completed
            else None,
            "action_ref": completed["evidence_ref"] if completed else None,
        }

    return {
        "observed_owner_actions": observed,
        "new_awaiting_owner_acceptance": sorted(pending),
        "unresolved_owner_evidence": sorted(unresolved),
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


def accepted_episodes(findings):
    return {
        case: result["complaint_episode"]
        for case, result in findings["observed_owner_actions"].items()
        if result["complaint_episode"] and result["acceptance_ref"]
    }


def tally(state, picks, effort, protocol):
    findings = owner_findings(state)
    accepted = accepted_episodes(findings)
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
                {accepted[i] for i in selected if i in accepted}
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
        **findings,
    }


def fixture_acceptances(work, week, protocol, cases, history, answers):
    """Score the authored outcomes under the same evidence rules as the actual draw."""
    eligible = [c for c in cases if c["ordinary_reviewed"] == "no"]
    audit = "synthetic-sensitivity"
    work.mkdir()
    manifest = records_manifest(work, week, eligible, protocol, audit, history)
    staged = {
        "week": week,
        "rubric": protocol["policy"],
        "cases": [
            {
                "case_id": c["case_id"],
                "revision": "v1",
                "evidence_ref": "conversations/" + c["case_id"],
                "completed_at": c["closed_at"],
            }
            for c in eligible
        ],
        "grades": {
            c["case_id"]: {
                "serious": {"yes": True, "no": False, "insufficient": None}[
                    answers[c["case_id"]]["serious"]
                ],
                "disposition": answers[c["case_id"]]["disposition"],
            }
            for c in eligible
        },
    }
    complaints.attach(staged, manifest, audit)
    return accepted_episodes(owner_findings(staged))


def possible_selections(work, protocol, episode_by_case):
    """Enumerate alternatives without issuing assignments or crediting hypothetical time."""
    reviewed = {r["case_id"] for r in read_csv(work / "ordinary.csv")}
    pool = read_csv(work / "population.csv")
    choices = []
    for flag, target in [("true", "flagged_sample"), ("false", "unflagged_sample")]:
        members = [
            r["case_id"]
            for r in pool
            if r["case_id"] not in reviewed and r["screen_flag"] == flag
        ]
        choices.append(combinations(members, min(len(members), protocol[target])))
    return Counter(
        frozenset(episode_by_case[c] for c in (*a, *b) if c in episode_by_case)
        for a, b in product(*choices)
    )


def sensitivity(distributions, native):
    counts = Counter()
    for (a, n), (b, m) in product(*(d.items() for d in distributions)):
        total = len(a | b)
        outcome = "more" if total > native else "equal" if total == native else "fewer"
        counts[outcome] += n * m
    return {
        "possible_two_week_selections": sum(counts.values()),
        "owner_accepted_episodes_vs_fixed_sql": dict(sorted(counts.items())),
    }


def run(out: Path):
    if out.exists() or out.is_symlink():
        raise InputError(f"output directory already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    protocol = tomllib.loads((FIXTURE / "protocol.toml").read_text())
    population = read_csv(FIXTURE / "conversations.csv")
    weeks = []
    possible = []
    with tempfile.TemporaryDirectory(prefix=".complaints-", dir=out.parent) as temp:
        staging = Path(temp) / "audits"
        staging.mkdir(mode=0o700)
        for week in protocol["weeks"]:
            cases = [c for c in population if c["week"] == week]
            work = Path(temp) / week
            work.mkdir()
            history = visible_contacts(population, source_cutoff(week))
            picks, design = shortlists(work, week, cases, protocol, history)
            committed = fingerprint(design)
            selected = set(picks["native"]) | set(picks["telryn"])
            union = [c for c in cases if c["case_id"] in selected]
            blind_plan = pilot_plan(
                work / "union", week, union, protocol, commitment=committed
            )
            report = weekly_report.start(blind_plan, staging / week)
            answers = {r["case_id"]: r for r in read_csv(FIXTURE / "answers.csv")}
            audit_id = grade_union(report, work, answers)
            manifest = records_manifest(work, week, union, protocol, audit_id, history)
            weekly_report.attach_complaints(report, manifest)
            effort = {
                r["method"]: r
                for r in read_csv(FIXTURE / "staff-minutes.csv")
                if r["week"] == week
            }
            state = weekly_report.read_state(report)
            result = tally(state, picks, effort, protocol)
            result["control_statement"] = control.summary(state)
            accepted = fixture_acceptances(
                work / "sensitivity", week, protocol, cases, history, answers
            )
            possible.append(possible_selections(work / "frame", protocol, accepted))
            weeks.append(
                {
                    "week": week,
                    "frozen_selection": design,
                    "commitment": committed,
                    "report": str(out / week / "report.html"),
                    **result,
                }
            )
        result = two_week_result(weeks, possible, protocol)
        (staging / "comparison.json").write_text(
            canonical(result) + "\n", encoding="utf-8"
        )
        staging.rename(out)
    return result


def two_week_result(weeks, possible, protocol):
    help_minutes = weeks[1]["methods"]["telryn"]["staff_minutes"]["operator_help"]
    all_episodes = {
        arm: {i for w in weeks for i in w["methods"][arm]["owner_accepted_new"]}
        for arm in ("native", "telryn")
    }
    total = {
        arm: sum(w["methods"][arm]["total_staff_minutes"] for w in weeks)
        for arm in all_episodes
    }
    onboarding = read_csv(FIXTURE / "onboarding.csv")
    if any(
        sum(int(r[arm]) for r in onboarding)
        != weeks[0]["methods"][arm]["staff_minutes"]["preparation"]
        for arm in all_episodes
    ):
        raise InputError(
            "team onboarding minutes must reconcile to first-week preparation"
        )
    return {
        "evidence": "synthetic",
        "onboarding_by_team": onboarding,
        "fixture_selection_sensitivity": sensitivity(
            possible, len(all_episodes["native"])
        ),
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
