"""Audit two weeks of synthetic QA decisions against human grades and register history."""

import shutil
import tempfile
import tomllib
from pathlib import Path

from telryn import bank_report, complaints, weekly, weekly_report
from telryn.contracts import InputError

from . import __main__ as pilot


def run(out: Path):
    if out.exists() or out.is_symlink():
        raise InputError(f"output directory already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    protocol = tomllib.loads((pilot.FIXTURE / "protocol.toml").read_text())
    population = pilot.read_csv(pilot.FIXTURE / "conversations.csv")
    weeks = []
    with tempfile.TemporaryDirectory(prefix=".qa-passes-", dir=out.parent) as temp:
        staging = Path(temp) / "audits"
        staging.mkdir(mode=0o700)
        for week, worked_case in zip(protocol["weeks"], ("C102", "C204"), strict=True):
            cases = [c for c in population if c["week"] == week]
            cutoff = pilot.source_cutoff(week)
            work = Path(temp) / week
            plan = pilot.pilot_plan(
                work,
                week,
                cases,
                protocol,
                score_audit={
                    "file": "scores.csv",
                    "scorecard": "payments-support",
                    "version": "1.0",
                    "cutoff": cutoff.isoformat(),
                    "scope": "all_scored",
                },
            )
            shutil.copyfile(
                pilot.FIXTURE / "scores" / f"{week}.csv", work / "scores.csv"
            )
            report = weekly_report.start(plan, staging / week)
            sampled = {
                r["case_id"] for r in pilot.read_csv(report.with_name("grades.csv"))
            }
            answers = {
                r["case_id"]: r for r in pilot.read_csv(pilot.FIXTURE / "answers.csv")
            }
            audit_id = pilot.grade_union(report, work, answers)
            manifest = pilot.records_manifest(
                work,
                week,
                [c for c in cases if c["case_id"] in sampled],
                protocol,
                audit_id,
                pilot.visible_contacts(population, cutoff),
            )
            weekly_report.attach_complaints(report, manifest)
            state = weekly_report.read_state(report)
            audit = weekly.summarize(state)["score_audit"]
            source = complaints.reconcile(state)
            score = next(row for row in audit["cases"] if row["case_id"] == worked_case)
            finding = next(
                row for row in source["cases"] if row["case_id"] == worked_case
            )
            weeks.append(
                {
                    "week": week,
                    "report": str(out / week / "report.html"),
                    "worked_case": worked_case,
                    "score": score,
                    "finding": finding,
                    "coverage": audit["coverage"],
                    "classification_disagreement": audit["classification_disagreement"],
                    "passed_deadline_miss": audit["passed_deadline_miss"],
                }
            )
            originals = staging / "source-records" / week
            originals.mkdir(parents=True)
            for name in ("scores.csv", "register.csv", "owner-events.csv"):
                shutil.copyfile(work / name, originals / name)
        bank_sha256 = bank_report.write_specimen(
            staging / weeks[0]["week"] / "report.html", staging / "bank"
        )
        staging.rename(out)
    return {
        "evidence": "synthetic",
        "weeks": weeks,
        "bank_report": str(out / "bank/report.html"),
        "bank_report_sha256": bank_sha256,
    }
