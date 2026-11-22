"""Replay the published local model run and check the study's sampling unit."""

import gzip
import json
import unittest

from examples.detection_study import evaluate
from examples.detection_study.materialize import HERE, cases
from telryn.contracts import fingerprint


class DetectionStudy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs, cls.labels = cases()
        cls.evidence = json.loads((HERE / "run.json").read_text())
        cls.raw = gzip.decompress((HERE / "citations.jsonl.gz").read_bytes())
        cls.receipt = cls.evidence["screen_receipt"]

    def test_corpus_has_160_balanced_pairs_and_no_key_in_visible_input(self):
        self.assertEqual(len(self.inputs), 320)
        responses = set()
        pairs = {}
        for key, row in self.inputs.items():
            self.assertEqual(
                set(row), {"case_id", "revision", "evidence_ref", "messages"}
            )
            self.assertIn(row["messages"][1]["role"], {"customer"})
            responses.add(row["messages"][1]["text"])
            pair = self.labels[key]["pair"]
            pairs.setdefault(pair, []).append(key)
        self.assertEqual(len(responses), 320)
        self.assertEqual(len(pairs), 160)
        for a, b in pairs.values():
            self.assertNotEqual(self.labels[a]["positive"], self.labels[b]["positive"])
            self.assertEqual(
                self.inputs[a]["messages"][0], self.inputs[b]["messages"][0]
            )

    def test_replay_joins_all_citations_and_matches_the_published_counts(self):
        for filename in evaluate.FROZEN_FILES:
            self.assertEqual(
                evaluate.sha((HERE / filename).read_bytes()),
                self.evidence["frozen_sha256"][filename],
            )
        result = evaluate.summarize(self.raw, self.receipt, replicates=12)
        self.assertEqual(result["positive"], 160)
        reference = json.loads((HERE / "results.json").read_text())
        for stage in ("budget", "screen"):
            self.assertEqual(
                result[stage + "_positive_overlap"],
                reference[stage + "_positive_overlap"],
            )
            for method in ("sql", "telryn"):
                for stat in ("found", "selected", "precision", "recall"):
                    self.assertEqual(
                        result["methods"][method][stage][stat],
                        reference["methods"][method][stage][stat],
                    )
                if stage == "budget":
                    self.assertEqual(result["methods"][method][stage]["selected"], 120)

    def test_changed_source_and_misattributed_passage_are_rejected(self):
        receipt = dict(self.receipt, transcripts_fingerprint=fingerprint("wrong input"))
        with self.assertRaisesRegex(ValueError, "receipt"):
            evaluate.checked_citations(self.inputs, self.raw, receipt)
        lines = self.raw.splitlines()
        for i, line in enumerate(lines):
            row = json.loads(line)
            entries = row["citations"]["complaint"]
            if entries:
                entries[0]["quote"] = "different speaker's text"
                lines[i] = json.dumps(row).encode()
                break
        modified = b"\n".join(lines) + b"\n"
        with self.assertRaisesRegex(ValueError, "citation does not match"):
            evaluate.checked_citations(
                self.inputs,
                modified,
                dict(self.receipt, citations_sha256=evaluate.sha(modified)),
            )

    def test_budget_selection_uses_ranked_flags_then_fills_unused_slots(self):
        priorities = {"A": 1, "B": 2}
        keys = ["A", "B", "C", "D"]
        self.assertEqual(evaluate.ranked_selections(priorities, keys, 1), {"B"})
        selected = evaluate.ranked_selections(priorities, keys, 3)
        self.assertTrue({"A", "B"}.issubset(selected))
        self.assertIn(min(["C", "D"], key=evaluate.tiebreak), selected)
