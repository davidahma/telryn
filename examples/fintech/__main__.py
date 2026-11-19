"""Run an offline synthetic weekly audit, evaluate its candidate, and record a shadow observation."""

import argparse
import csv
import json
import tempfile
from pathlib import Path

from telryn.contracts import InputError, canonical
from telryn.proposal import propose
from telryn.report import recommendation
from telryn.session import compare, publish, record_shadow
from telryn.weekly import summarize
from telryn.weekly_report import refresh, start

FIXTURE = Path(__file__).resolve().parent


def sample_return(packet: Path, returned: Path) -> None:
    with (FIXTURE / "staged-grades.csv").open(encoding="utf-8", newline="") as grades:
        answers = {r["case_id"]: r for r in csv.DictReader(grades)}
    with (
        packet.open(encoding="utf-8-sig", newline="") as source,
        returned.open("w", encoding="utf-8", newline="") as target,
    ):
        reader = csv.DictReader(source)
        writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            staged = answers[row["case_id"]]
            for key in ("serious", "disposition", "owner", "action", "minutes"):
                row[key] = staged[key]
            writer.writerow(row)


def run(out: Path) -> dict:
    if out.exists() or out.is_symlink():
        raise InputError(f"output directory already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".fintech-", dir=out.parent) as temp:
        work = Path(temp) / "run"
        work.mkdir(mode=0o700)
        weekly = start(FIXTURE / "week.toml", work / "weekly")
        returned = work / "synthetic-return.csv"
        sample_return(weekly.with_name("grades.csv"), returned)
        audit = refresh(weekly, returned)
        proposal = propose(weekly, work / "proposal")
        state = compare(
            FIXTURE / "held-out.toml", proposal_path=work / "proposal" / "proposal.json"
        )
        report = publish(work / "comparison", state)
        observations = json.loads(
            (FIXTURE / "shadow-template.json").read_text(encoding="utf-8")
        )
        observations["comparison_id"] = state["comparison_id"]
        shadow = work / "shadow-observations.json"
        shadow.write_text(canonical(observations) + "\n", encoding="utf-8")
        result = recommendation(record_shadow(report, shadow))
        work.rename(out)
    return {
        "evidence": "synthetic",
        "weekly": summarize(audit),
        "new_cases": sorted(
            case
            for case, grade in audit["grades"].items()
            if grade["disposition"] == "new"
        ),
        "proposal_id": proposal["proposal_id"],
        "reserved_seat": "flagged" if proposal["priority_flag"] else "unflagged",
        "held_out": result["recommendation"],
        "net_additional_serious_cases": result["bounds"],
        "shadow": result["shadow_trials"]["demo-week-three"]["lead_decision"]["action"],
        "weekly_report": str(out / "weekly" / "report.html"),
        "comparison_report": str(out / "comparison" / "report.html"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(canonical(run(args.out)))
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f"{exc}\n")
