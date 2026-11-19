"""Run a local public-data audit and compare it with the full human-labeled set."""

import argparse
import csv
import hashlib
import json
import tempfile
from pathlib import Path

from telryn.weekly import summarize
from telryn.weekly_report import refresh, start

from .rebuild import ARCHIVE_SHA256


def run(out: Path, census: bool = False) -> dict:
    with (
        Path(__file__)
        .with_name("annotations.csv")
        .open(encoding="utf-8", newline="") as stream
    ):
        data = {f"ubuntu-{row['source_id']}": row for row in csv.DictReader(stream)}
    # Independent of both sets of labels. The source has no record of ordinary QA.
    reviewed = set(
        sorted(data, key=lambda key: hashlib.sha256(key.encode()).digest())[:40]
    )
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with (base / "population.csv").open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.writer(stream)
            writer.writerow(["case_id", "revision", "evidence_ref", "screen_flag"])
            for case, row in data.items():
                writer.writerow(
                    [
                        case,
                        ARCHIVE_SHA256,
                        f"ubuntu-chat-logs/{case[7:]}",
                        row["gpt4o_friction"],
                    ]
                )
        (base / "reviewed.csv").write_text(
            "case_id\n" + "\n".join(sorted(reviewed)) + "\n"
        )
        plan = base / "week.toml"
        plan.write_text(
            'schema = 1\nweek = "2025-10-13"\nrubric = "ubuntu-friction-v1"\n'
            'rubric_question = "Did the human annotator identify conversational friction?"\n'
            'finding_label = "conversations with friction"\n'
            'scope_note = "Public Ubuntu support corpus. The 40 ordinary reviews and batch date are simulated. Here a positive grade means the researchers annotated conversational friction. Prior handling and reviewer time are unavailable. Source: Sarkar, Srikanth, Hudson, Rudinger, Bonial and Resnik (2025), Ubuntu Chat Logs Corpus, formatted for ConvoKit by Axel Bax. Binary labels adapted under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Dataset: https://www.convokit.cornell.edu/documentation/chatlogs.html"\n'
            'screen_version = "convokit-ubuntu-gpt4o-released-labels"\n'
            'population = "population.csv"\nreviewed = "reviewed.csv"\n'
            f"flagged_sample = {200 if census else 32}\nunflagged_sample = 200\n"
        )
        report = start(plan, out)
        packet = out / "grades.csv"
        with (
            packet.open(encoding="utf-8-sig", newline="") as src,
            (base / "return.csv").open("w", encoding="utf-8", newline="") as dst,
        ):
            reader = csv.DictReader(src)
            writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                serious = data[row["case_id"]]["human_friction"] == "1"
                row.update(
                    serious="yes" if serious else "no",
                    disposition="unknown" if serious else "none",
                    minutes="0",
                )
                writer.writerow(row)
        result = summarize(
            refresh(report, base / "return.csv", reviewer="Published human annotations")
        )
    truth = sum(
        int(row["human_friction"]) for case, row in data.items() if case not in reviewed
    )
    return {
        "public_conversations": len(data),
        "simulated_ordinary_reviews": len(reviewed),
        "residual_human_friction_total": truth,
        "weekly": result,
        "report": str(report),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--census", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.out, args.census), indent=2))
