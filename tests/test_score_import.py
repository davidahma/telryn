"""Native reviewer exports keep the source bytes, selection and historical score time."""

import json
import unittest
from copy import deepcopy

from telryn.contracts import InputError
from telryn.score_import import read

TIME = "2026-10-01T15:00:00Z"


class NativeScores(unittest.TestCase):
    def scores(self, raw, fmt, **kwargs):
        cfg = {"format": fmt, "attest_revisions": True, **kwargs}
        if fmt != "langfuse-v2":
            cfg.setdefault("pass_percent", 80)
        return read(raw, cfg, {"C1": "v1", "zendesk:corp:C1": "v1"}, "main", "v3")

    def test_zendesk_native_pages(self):
        c = {
            "externalId": "C1",
            "workspaceId": "100",
            "reviews": [
                {
                    "scorecard": {"id": "main"},
                    "id": "R1",
                    "score": 80,
                    "created": TIME,
                    "updated": TIME,
                }
            ],
        }
        page = {"conversations": [c], "pagination": {"page": 1, "total": 1}}
        r = self.scores(json.dumps(page), "zendesk-qa", case_prefix="zendesk:corp:")[
            "zendesk:corp:C1"
        ]
        self.assertEqual(
            (r["decision"], r["revision"], r["score_ref"], r["complaint_prediction"]),
            ("pass", "v1", "R1", "unknown"),
        )
        for change in ("missing page", "other card", "duplicate", "undated"):
            p = deepcopy(page)
            if change == "missing page":
                p["pagination"]["total"] = 2
            elif change == "other card":
                p["conversations"][0]["reviews"][0]["scorecard"]["id"] = "other"
            elif change == "duplicate":
                p["conversations"][0]["reviews"] *= 2
            else:
                del p["conversations"][0]["reviews"][0]["updated"]
            with self.subTest(change), self.assertRaises(InputError):
                self.scores(json.dumps(p), "zendesk-qa")

    def test_maestro_uses_saved_time_and_explicit_threshold(self):
        native = b"gradable_id,date_graded,rubric_id,grade_type,rubric_score,grader\r\nC1,2026-10-01 15:00:00,main,Grading,79,Ann\r\n"
        row = self.scores(native, "maestroqa")["C1"]
        self.assertEqual(
            (row["decision"], row["complaint_prediction"], row["scored_at"]),
            ("fail", "unknown", "2026-10-01 15:00:00+00:00"),
        )
        for raw in (
            native.replace(b"Grading", b"Calibration"),
            native.replace(b"79", b"NaN"),
        ):
            with self.assertRaises(InputError):
                self.scores(raw, "maestroqa")
        with self.assertRaises(InputError):
            self.scores(native, "maestroqa", attest_revisions=False)

    def test_langfuse_separate_classifier_and_pass(self):
        score = {
            "traceId": "C1",
            "source": "EVAL",
            "name": "main",
            "id": "S1",
            "value": 1,
            "updatedAt": TIME,
            "dataType": "BOOLEAN",
        }

        def load(rows):
            return self.scores(
                json.dumps(
                    {"data": rows, "meta": {"page": 1, "totalItems": len(rows)}}
                ),
                "langfuse-v2",
                complaint_evaluator="is-complaint",
            )

        self.assertEqual(load([score])["C1"]["complaint_prediction"], "unknown")
        both = [score, {**score, "id": "C1-class", "name": "is-complaint", "value": 0}]
        self.assertEqual(load(both)["C1"]["complaint_prediction"], "no")
        for rows in (
            [{**score, "source": "ANNOTATION"}],
            [{**score, "observationId": "other"}],
            [score, score],
        ):
            with self.assertRaises(InputError):
                load(rows)
