"""Complaint outcome checks callable from an experiment runner.

The dataset supplies the trusted rubric answer, source passages and register.
The task under evaluation supplies only its complaint judgment and citation.
"""

from copy import deepcopy

from . import complaints, packs, policy, seed
from .contracts import InputError, fingerprint, mapping, sequence, timestamp
from .outcomes import deadline_status

_NAMES = {
    "rubric": "telryn_complaint_rubric",
    "deadline": "telryn_registration_deadline",
    "seed": "telryn_seed_release",
}


class ComplaintScorers:
    """Freeze a versioned pack and optionally a test suite for a local scorer."""

    def __init__(self, pack, *, seed_suite=None):
        self.pack = packs.validate_local(deepcopy(pack))
        self.seed_suite = seed_suite
        self.pack_digest = fingerprint(self.pack)

    def evaluate(self, input, output, expected):
        """Return evidence-bound decision states without sending anything."""
        policy.fields(
            expected,
            ("complaint", "trigger_passage", "trigger_event"),
            "independent policy judgment",
        )
        truth = expected["complaint"]
        if type(truth) is not bool and truth is not None:
            raise InputError("approved complaint answer must be true, false or null")
        mapping(input, "evaluation dataset item")
        policy.fields(
            input,
            (
                "case_id",
                "revision",
                "evidence_ref",
                "channel",
                "completed_at",
                "passages",
                "register_entries",
                "as_of",
                "complete_through",
                *(key for key in ("region", "seed_receipt") if key in input),
            ),
            "evaluation dataset item",
        )
        for key in ("case_id", "revision", "evidence_ref", "channel"):
            policy.label(input[key], key)
        if "region" in input:
            policy.label(input["region"], "region")
        as_of = timestamp(input["as_of"], "observation time")
        closed = timestamp(input["completed_at"], "frozen closure time")
        if closed > as_of:
            raise InputError("the conversation must be closed by the observation")
        passages = sequence(input["passages"], "frozen dated passages")
        if not 1 <= len(passages) <= 1000:
            raise InputError("supply 1 to 1000 frozen dated passages")
        previous = None
        for passage in passages:
            policy.fields(passage, ("at", "text"), "frozen dated passage")
            when = timestamp(passage["at"], "passage date")
            if when > closed or (previous is not None and when < previous):
                raise InputError("frozen passages must be ordered and end by closure")
            if (
                not isinstance(passage["text"], str)
                or not 1 <= len(passage["text"]) <= 16384
            ):
                raise InputError(
                    "frozen passage text must contain between 1 and 16384 characters"
                )
            previous = when
        through = input["complete_through"]
        coverage = (
            timestamp(through, "register completeness") if through is not None else None
        )
        if coverage is not None and coverage > as_of:
            raise InputError("register completeness exceeds the observation time")
        rows, seen = [], set()
        for entry in sequence(
            input["register_entries"], "register entries for this case"
        ):
            policy.fields(entry, ("entry_id", "recorded_at"), "register entry")
            ident = policy.label(entry["entry_id"], "register entry ID")
            at = timestamp(entry["recorded_at"], "register entry time")
            if ident in seen or at > as_of:
                raise InputError("duplicate or future register entry")
            seen.add(ident)
            rows.append(entry["recorded_at"])

        result = {
            "schema": 1,
            "policy_ref": packs.reference(self.pack),
            "policy_sha256": self.pack_digest,
            "dataset_sha256": fingerprint(
                {
                    "input": {k: v for k, v in input.items() if k != "seed_receipt"},
                    "expected": expected,
                }
            ),
        }
        if truth is True:
            trigger = expected["trigger_passage"]
            if type(trigger) is not int or not 1 <= trigger <= len(passages):
                raise InputError(
                    "a complaint needs an approved one-based trigger passage"
                )
            result["deadline"] = self._deadline(
                input,
                passages[trigger - 1]["at"],
                rows,
                as_of,
                expected["trigger_event"],
            )
        elif (
            expected["trigger_passage"] is not None
            or expected["trigger_event"] is not None
        ):
            raise InputError("only a confirmed complaint can have a trigger passage")
        else:
            result["deadline"] = {
                "status": "not_applicable" if truth is False else "unresolved",
                "passed": None,
                "due": None,
            }
        result["rubric"] = self._rubric(input, output, truth, as_of)
        if self.seed_suite is not None:
            result["seed"] = self.evaluate_seed(input.get("seed_receipt"))
        return result

    def _deadline(self, case, start, rows, as_of, event):
        clock = self.pack["clock_policy"]
        rule = clock["clauses"][clock["registration_clause"]]
        if event != rule["start_event"]:
            raise InputError(
                "approved trigger event differs from the registration clause"
            )
        # The approved, first qualifying passage starts the clock. A candidate
        # cannot move the deadline by citing a more convenient contact.
        due = policy.deadline(
            start, rule, clock, channel=case["channel"], region=case.get("region")
        )
        exact = not any(
            timestamp(row, "register entry") < timestamp(start, "complaint trigger")
            for row in rows
        )
        status = deadline_status(
            True, exact, due, as_of, rows, case["complete_through"]
        )
        passed = {"on_time": True, "late": False, "missing": False}.get(status)
        return {"status": status, "passed": passed, "due": due}

    def _rubric(self, input, output, truth, as_of):
        if not isinstance(output, dict) or output.get("complaint") is None:
            return {
                "status": "prediction_missing",
                "passed": False if truth is not None else None,
            }
        predicted = output["complaint"]
        if type(predicted) is not bool or not isinstance(output.get("citation"), dict):
            return {
                "status": "invalid_prediction",
                "passed": False if truth is not None else None,
            }
        citation = output["citation"]
        try:
            policy.fields(
                citation,
                ("evidence_ref", "revision", "passage", "quote"),
                "predicted citation",
            )
            bound = complaints._source_citation(
                {**citation, "passages": input["passages"]}, input, as_of
            )
        except InputError:
            return {
                "status": "invalid_citation",
                "passed": False if truth is not None else None,
            }
        return {
            "status": "no_independent_grade"
            if truth is None
            else ("agree" if predicted is truth else "disagree"),
            "passed": None if truth is None else predicted is truth,
            "complaint_prediction": predicted,
            "citation_passage": bound["passage"],
        }

    def evaluate_seed(self, receipt):
        if self.seed_suite is None:
            raise InputError("configure the independently pinned test suite")
        if receipt is None:
            return {"status": "receipt_missing", "passed": False}
        try:
            checked = seed.verify_receipt(self.seed_suite, receipt)
        except (InputError, OverflowError):
            return {"status": "receipt_invalid", "passed": False}
        return {
            "status": "passed" if checked["passed"] else "held",
            "passed": checked["passed"],
            "suite_sha256": checked["suite_digest"],
            "receipt_sha256": fingerprint(checked),
        }

    def braintrust(self, input, output, expected):
        """Use as an Eval scorer after installing Braintrust in the eval job."""
        from braintrust import Score

        return [
            Score(name=name, score=value, metadata=metadata)
            for name, value, metadata in _measurements(
                self.evaluate(input, output, expected)
            )
        ]

    def langfuse(self, *, input, output, expected_output, **_):
        """Use as a Langfuse run_experiment evaluator with the Python SDK."""
        from langfuse import Evaluation

        return [
            Evaluation(name=name, value=value, data_type="BOOLEAN", metadata=metadata)
            for name, value, metadata in _measurements(
                self.evaluate(input, output, expected_output)
            )
        ]


def _measurements(result):
    for kind, name in _NAMES.items():
        if kind not in result:
            continue
        row = result[kind]
        metadata = {
            **{k: row[k] for k in row if k != "passed"},
            "policy_sha256": result["policy_sha256"],
            "dataset_sha256": result["dataset_sha256"],
        }
        # Omit the binary result when it has no denominator. The companion
        # metric makes observation gaps visible in each experiment.
        yield name + "_determinate", int(row["passed"] is not None), metadata
        if row["passed"] is not None:
            yield name, int(row["passed"]), metadata
        if kind == "rubric" and "complaint_prediction" in row:
            yield (
                "telryn_complaint_prediction",
                int(row["complaint_prediction"]),
                metadata,
            )
