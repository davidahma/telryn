# Score complaint conduct in Braintrust or Langfuse

Use Telryn in a pre-release experiment to check whether an agent recognises a complaint under the bank’s policy and whether the register shows on-time action. Prepare one independently graded dataset item per frozen conversation. Add the evaluator to a job that already has approved access to the source. Telryn itself makes no model or platform calls from these scorers. The experiment runner may retain the dataset, output and metrics. Use synthetic fixtures or the bank’s approved evaluation environment. [Braintrust scorers](https://www.braintrust.dev/docs/evaluate/write-scorers) · [Langfuse experiment evaluators](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)

The input is trusted experiment data. Use the conversation’s `case_id`, `revision`, `evidence_ref`, `channel`, optional `region`, `completed_at` and ordered `passages` (each with `at` and `text`). The complaint register supplies matched `register_entries` containing `entry_id` and `recorded_at`, the observation `as_of` and its `complete_through` coverage timestamp or null. The dataset owner supplies the independent expected result. `complaint` takes true, false or null. A confirmed complaint also requires the approved one-based `trigger_passage` and `trigger_event` named by the selected pack’s registration clause. Keep these two trigger fields null for other grades.

The candidate returns a complaint prediction and citation. The scorer checks the revision, passage and exact quoted words, then compares the prediction with the independent answer. The approved trigger starts the deadline clock. A demonstrated missed deadline, a missing prediction and unresolved register coverage stay distinct. The source record’s timestamp determines whether the entry was on time.

```python
from pathlib import Path

from telryn import packs
from telryn.conduct_scorers import ComplaintScorers

scorers = ComplaintScorers(
    packs.load("firm-example@1.1.0", Path.cwd()),
)

# Use scores=[scorers.braintrust] in Braintrust's Eval call.
# Use evaluators=[scorers.langfuse] in Langfuse's run_experiment call.
```

Install the corresponding SDK in the experiment job. Checked SDKs were Braintrust 0.52.0 and Langfuse 5.2.0. This candidate output uses the same evidence shape on both platforms.

```json
{
  "complaint": true,
  "citation": {
    "evidence_ref": "support/demo-case/v3",
    "revision": "v3",
    "passage": 2,
    "quote": "Please review the second charge."
  }
}
```

Metrics include complaint-rubric agreement and registration by deadline. Each binary decision has a companion `_determinate` score; unresolved evidence omits the binary decision. A valid cited prediction is also emitted separately as `telryn_complaint_prediction` for the native Langfuse importer. Pair it with your QA tool’s own overall pass/fail evaluator. Rubric agreement and registration timeliness use different denominators. Scores carry the policy and dataset digests, a decision status and the validated passage index where available. The adapter omits the text of the cited passage. Preserve the private inputs and raw evaluator result from `scorers.evaluate(input, output, expected)` so the bank can reproduce it. [Native score intake](native-scores.md)

## Include the agent release exercise

Run `telryn seed` in the designated test tenant and retain its receipt through a trusted job. Pass the pinned local suite as `ComplaintScorers(pack, seed_suite=Path("suite.json"))` and add the receipt as `seed_receipt` on the evaluator’s trusted input. The scorer recalculates all deadlines and registration outcomes against the pinned suite. A receipt supplied in the candidate output carries no authority. Receipt consistency is a separate check from the provenance of the source and runner. Keep the issuer-side artifacts for that provenance. `scorers.evaluate_seed(receipt)` supports a job that runs only the release check. [Agent release gate](agent-release.md)
