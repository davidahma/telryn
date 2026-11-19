"""The local model can cite only visible passages and must cite every definite fact."""

import json
import unittest
from unittest.mock import patch

from telryn import transcripts
from telryn.contracts import InputError


class CitationChoices(unittest.TestCase):
    policy = "promised-refund"
    case = {
        "messages": [
            {"role": "customer", "text": "Where is my refund?"},
            {"role": "agent", "text": "I promise a refund."},
            {"role": "system", "text": "Refund transaction recorded."},
            {"role": "unknown", "text": "They said a refund was issued."},
        ]
    }

    def extract(self, responder):
        with (
            patch.object(transcripts, "CHUNK_BYTES", 1),
            patch.object(transcripts.screen, "_api", side_effect=responder),
        ):
            return transcripts.extract_case(
                self.case,
                "local",
                self.policy,
                *transcripts.recipe(self.policy, "Check the refund ledger."),
                [],
            )

    @staticmethod
    def result(facts):
        return {
            "model": "local",
            "done": True,
            "done_reason": "stop",
            "message": {"content": json.dumps(facts)},
        }

    def test_each_chunk_constrains_labels_and_visible_passage_ids_together(self):
        seen = []

        def respond(_, body):
            visible = json.loads(body["messages"][1]["content"])
            numbers = [p["passage"] for p in visible]
            seen.append(numbers)
            schema = body["format"]
            self.assertEqual(
                set(schema["required"]), {"refund_promised", "refund_recorded"}
            )
            for name in schema["required"]:
                fact = schema["properties"][name]
                if visible[0]["role"] in ("agent", "system"):
                    supported, missing = fact["anyOf"]
                    self.assertEqual(
                        supported["properties"]["value"]["enum"], ["yes", "no"]
                    )
                    self.assertEqual(
                        supported["properties"]["passage"]["enum"], numbers
                    )
                else:
                    missing = fact
                self.assertEqual(missing["properties"]["value"]["enum"], ["unknown"])
                self.assertEqual(missing["properties"]["passage"]["enum"], [0])
            unknown = {"value": "unknown", "passage": 0}
            return self.result(
                {
                    "refund_promised": {"value": "yes", "passage": 2}
                    if numbers == [2]
                    else unknown,
                    "refund_recorded": {"value": "yes", "passage": 3}
                    if numbers == [3]
                    else unknown,
                }
            )

        result = self.extract(respond)
        self.assertEqual(seen, [[1], [2], [3], [4]])
        self.assertEqual(result["citations"]["refund_promised"][0]["message"], 2)
        self.assertEqual(result["citations"]["refund_recorded"][0]["message"], 3)

    def test_invalid_output_from_model_is_still_rejected(self):
        for value, number in (("no", 0), ("yes", 1), ("yes", 2), ("unknown", 1)):
            with self.subTest(value=value, number=number):

                def respond(_, body):
                    return self.result(
                        {
                            name: {"value": value, "passage": number}
                            for name in body["format"]["required"]
                        }
                    )

                with self.assertRaisesRegex(InputError, "unsupported passage"):
                    self.extract(respond)
