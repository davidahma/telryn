"""Replay the frozen 320-chat comparison, or score a fresh output from telryn screen."""

import argparse
import gzip
import hashlib
import json
import random
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from telryn import screen, transcripts
from telryn.contracts import fingerprint

from .materialize import HERE, cases

PROTOCOL = json.loads((HERE / "protocol.json").read_text())
FROZEN_FILES = ("pairs.tsv", "policy.txt", "keyword.sql", "protocol.json")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tiebreak(key):
    return sha((PROTOCOL["tie_seed"] + "/" + key).encode())


def keyword_scores(inputs):
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(
            "CREATE TABLE conversations(case_id TEXT PRIMARY KEY, customer TEXT, staff TEXT, tie TEXT)"
        )
        db.executemany(
            "INSERT INTO conversations VALUES(?,?,?,?)",
            (
                (
                    key,
                    " ".join(
                        m["text"] for m in row["messages"] if m["role"] == "customer"
                    ).lower(),
                    " ".join(
                        m["text"] for m in row["messages"] if m["role"] == "agent"
                    ).lower(),
                    tiebreak(key),
                )
                for key, row in inputs.items()
            ),
        )
        return dict(db.execute((HERE / "keyword.sql").read_text()))


def checked_citations(inputs, raw, receipt):
    policy = (HERE / "policy.txt").read_text().strip()
    prompt, facts = transcripts.recipe(PROTOCOL["target"], policy)
    if not (
        receipt["mechanism"] == "cited-transcript-events"
        and receipt["policy"] == PROTOCOL["target"]
        and receipt["model"] == PROTOCOL["model"]
        and receipt["model_digest"] == PROTOCOL["expected_model_digest"]
        and receipt["cases"] == len(inputs)
        and receipt["policy_fingerprint"] == fingerprint(policy)
        and receipt["prompt_fingerprint"] == fingerprint(prompt)
        and receipt["transcripts_fingerprint"]
        == fingerprint([fingerprint(v) for v in inputs.values()])
        and receipt["citations_sha256"] == sha(raw)
    ):
        raise ValueError(
            "screen receipt does not match this study's corpus, weights, prompt and citations"
        )
    rows = {}
    for line in raw.splitlines():
        record = json.loads(line)
        key = record["case_id"]
        if key in rows or key not in inputs:
            raise ValueError("duplicate or unexpected screen case")
        source = inputs[key]
        if any(record[k] != source[k] for k in screen.FIELDS):
            raise ValueError("screen case does not match the frozen chat revision")
        passages = {p["n"]: p for p in transcripts.passages(source["messages"], [])}
        choices = transcripts.citation_choices(facts, passages.values())
        for fact in screen.SIGNALS:
            entries = record["citations"][fact]
            if entries and fact not in facts:
                raise ValueError("unexpected extracted fact")
            values = set()
            for entry in entries:
                p = passages[entry["n"]]
                if (
                    entry["value"] not in ("yes", "no")
                    or entry["n"] not in choices[fact]
                    or any(entry[k] != v for k, v in p.items())
                ):
                    raise ValueError(
                        "citation does not match the visible source and speaker"
                    )
                values.add(entry["value"])
            expected = next(iter(values)) if len(values) == 1 else "unknown"
            if record["signals"][fact] != expected:
                raise ValueError("fact differs from its cited answers")
        rows[key] = record
    if rows.keys() != inputs.keys():
        raise ValueError("incomplete screen results")
    event_rows = [
        {**{k: v[k] for k in screen.FIELDS}, **v["signals"]} for v in rows.values()
    ]
    if fingerprint(event_rows) != receipt["events_fingerprint"]:
        raise ValueError("event table differs from the screening receipt")
    return rows


def ranked_selections(scores, keys, slots):
    ranked = sorted(keys, key=lambda k: (-scores.get(k, 0), tiebreak(k)))
    return set(ranked[:slots])


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def measures(selected, labels):
    found = sum(labels[k]["positive"] for k in selected)
    return {
        "selected": len(selected),
        "found": found,
        "precision": ratio(found, len(selected)),
        "recall": ratio(found, sum(label["positive"] for label in labels.values())),
    }


def percentile(sorted_samples, q):
    if not sorted_samples:
        return None
    index = q * (len(sorted_samples) - 1)
    lo = int(index)
    hi = min(lo + 1, len(sorted_samples) - 1)
    return sorted_samples[lo] + (index - lo) * (sorted_samples[hi] - sorted_samples[lo])


def intervals(scores, labels, slots, replicates):
    """Resample paired contexts within family; rerank each replicate at the fixed budget."""
    pairs = defaultdict(lambda: defaultdict(list))
    for key, label in labels.items():
        pairs[label["family"]][label["pair"]].append(key)
    families = [list(family.values()) for family in pairs.values()]
    samples = defaultdict(list)
    rng = random.Random(PROTOCOL["bootstrap_seed"])
    ranks = {
        method: sorted(labels, key=lambda k: (-priority.get(k, 0), tiebreak(k)))
        for method, priority in scores.items()
    }
    for _ in range(replicates):
        multiplicities = dict.fromkeys(labels, 0)
        for family in families:
            for a, b in rng.choices(family, k=len(family)):
                multiplicities[a] += 1
                multiplicities[b] += 1
        positives = sum(multiplicities[k] for k in labels if labels[k]["positive"])
        by_method = {}
        for method, priority in scores.items():
            budget_found = full_found = full_review = used = 0
            for key in ranks[method]:
                count = multiplicities[key]
                positive = labels[key]["positive"]
                if priority.get(key, 0) > 0:
                    full_review += count
                    full_found += count * positive
                taken = min(count, slots - used)
                budget_found += taken * positive
                used += taken
            by_method[method] = budget_found
            if full_review:
                samples[method, "screen", "precision"].append(full_found / full_review)
            samples[method, "screen", "recall"].append(full_found / positives)
            samples[method, "budget", "precision"].append(budget_found / slots)
            samples[method, "budget", "recall"].append(budget_found / positives)
        samples["paired", "budget", "found_difference"].append(
            by_method["telryn"] - by_method["sql"]
        )
    result = {}
    for metric, values in samples.items():
        values.sort()
        result[metric] = [percentile(values, 0.025), percentile(values, 0.975)]
    return result


def summarize(raw, receipt, replicates=None):
    inputs, labels = cases()
    priorities = keyword_scores(inputs)
    checked = checked_citations(inputs, raw, receipt)
    slots = (
        PROTOCOL["minutes_per_method"] - PROTOCOL["shared_setup_minutes_per_method"]
    ) // PROTOCOL["minutes_per_review"]
    scores = {
        "telryn": {
            k: int(screen.decide(r["signals"], PROTOCOL["target"]))
            for k, r in checked.items()
        },
        "sql": priorities,
    }
    confidence = intervals(
        scores, labels, slots, replicates or PROTOCOL["bootstrap_replicates"]
    )
    truth = {k for k, row in labels.items() if row["positive"]}
    report = {
        "synthetic": True,
        "cases": len(inputs),
        "positive": len(truth),
        "methods": {},
    }
    members = {}
    for method, priorities in scores.items():
        members[method] = {
            "screen": {k for k, v in priorities.items() if v > 0},
            "budget": ranked_selections(priorities, labels, slots),
        }
        report["methods"][method] = {}
        for stage, selected in members[method].items():
            result = measures(selected, labels)
            for measure in ("precision", "recall"):
                result[measure + "_95"] = confidence.get((method, stage, measure))
            report["methods"][method][stage] = result
    for stage in ("screen", "budget"):
        chosen = {m: members[m][stage] & truth for m in members}
        report[stage + "_positive_overlap"] = {
            "both": len(chosen["telryn"] & chosen["sql"]),
            "telryn_only": sorted(chosen["telryn"] - chosen["sql"]),
            "sql_only": sorted(chosen["sql"] - chosen["telryn"]),
            "neither": len(truth - chosen["sql"] - chosen["telryn"]),
        }
    report["budget_difference_found_95"] = confidence[
        "paired", "budget", "found_difference"
    ]
    report["families"] = {}
    for family in sorted({r["family"] for r in labels.values()}):
        positive = {k for k in truth if labels[k]["family"] == family}
        report["families"][family] = {
            "positive": len(positive),
            **{
                m: len(positive & selections["screen"])
                for m, selections in members.items()
            },
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--screen",
        type=Path,
        help="new completed transcript-screen output; defaults to checked-in run",
    )
    args = parser.parse_args()
    if args.screen:
        raw = (args.screen / "citations.jsonl").read_bytes()
        receipt = json.loads((args.screen / "receipt.json").read_text())
    else:
        evidence = json.loads((HERE / "run.json").read_text())
        for name in FROZEN_FILES:
            if sha((HERE / name).read_bytes()) != evidence["frozen_sha256"][name]:
                raise ValueError(f"study input changed: {name}")
        receipt = evidence["screen_receipt"]
        raw = gzip.decompress((HERE / "citations.jsonl.gz").read_bytes())
    print(json.dumps(summarize(raw, receipt), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
