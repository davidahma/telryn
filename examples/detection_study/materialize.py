"""Produce blinded chat inputs and a separate answer key for a fixed synthetic corpus."""

import csv
import hashlib
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent


def cases():
    """Return indexable transcripts and labels, without exposing labels to the screen."""
    with (HERE / "pairs.tsv").open(encoding="utf-8", newline="") as stream:
        pairs = list(csv.DictReader(stream, delimiter="\t"))
    examples = []
    for pair, row in enumerate(pairs, 1):
        for text, positive in (
            (row["reportable_customer"], True),
            (row["response_control"], False),
        ):
            messages = [
                {"role": "agent", "text": row["opening"]},
                {"role": "customer", "text": text},
            ]
            examples.append((messages, pair, row["family"], positive))
    random.Random(55031).shuffle(examples)
    inputs = {}
    labels = {}
    for i, (messages, pair, family, positive) in enumerate(examples, 1):
        case_id = f"D{i:03d}"
        revision = hashlib.sha256(json.dumps(messages).encode()).hexdigest()[:16]
        inputs[case_id] = {
            "case_id": case_id,
            "revision": revision,
            "evidence_ref": f"synthetic/chat/{case_id}/{revision}",
            "messages": messages,
        }
        labels[case_id] = {"pair": pair, "family": family, "positive": positive}
    return inputs, labels


def materialize(dest):
    inputs, labels = cases()
    dest.mkdir(parents=True, exist_ok=False)
    with (dest / "chats.jsonl").open("w", encoding="utf-8") as stream:
        for row in inputs.values():
            stream.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")
    (dest / "answer-key.json").write_text(json.dumps(labels, indent=2) + "\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    materialize(parser.parse_args().destination)
