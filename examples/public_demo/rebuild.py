"""Rebuild the CC BY 4.0 metadata projection from the pinned ConvoKit release."""

import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path

ARCHIVE_SHA256 = "03fe5f7a8e8f3d8a7c4cca083a7fc85433ca163d0ba8672469eab37e9139e9a2"


def rebuild(archive: Path, output: Path) -> None:
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != ARCHIVE_SHA256:
            raise ValueError("source archive differs from the attributed release")
    with zipfile.ZipFile(archive) as source:
        metadata = json.loads(source.read("ubuntu-chat-logs/conversations.json"))
    with output.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["source_id", "gpt4o_friction", "human_friction"])
        for ident, row in sorted(metadata.items()):
            human = {"Yes": 1, "No": 0}[row["conversational_friction_present_human"]]
            screen = row["conversational_friction_present_gpt4o"]
            if type(screen) is not bool:
                raise ValueError(f"missing model flag on {ident}")
            writer.writerow([ident, int(screen), human])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rebuild(args.archive, args.output)
