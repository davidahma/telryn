# Walk through a support QA policy change

A lead has 20 reviewer-hours to audit completed conversations from two
assistants. Both policies select eight of twelve conversations for review.
The proposed policy adds three conversations with quality defects and drops
one with an unresolved serious grade. The lead requires zero confirmed serious
cases to lose review and enough evidence to rule out more than zero.

This example is synthetic. The exported grades identify the transcript revisions at cutoff.
The sample evidence file includes the excerpt needed to return the serious grade. The queue decides what a human audits after
the conversation is complete.

## 1. Check the ordinary exports

From the unpacked release or checkout, with CPython 3.12–3.15 installed:

```sh
python3 -m telryn preflight examples/support-qa/intake.toml --out runs/qa-cutoff.json
```

The intake maps headers in three CSVs. The eligibility ledger lists all
12 IDs, the assignments list eight ordered picks, and the cutoff snapshot
contains a revision, availability time, and frozen scores per eligible ID.
Preflight reports its first gap per shift and what to capture at the next
cutoff. An explicitly mapped column must exist in the corresponding CSV.
Use scaled integers for ranking scores whose fractional precision exceeds
a round trip through a Python float. Preflight reports affected values and
writes a normalized export only when all requested shifts are ready.

Run the comparison using the bundled SQLite rankings:

```sh
python3 -m telryn compare examples/support-qa/comparison.toml --out runs/qa
```

Open `runs/qa/report.html`. The net change is **+2 additional defects sent to
review**, but the serious-loss range is **0 to 1**, so the recommendation is
**Inconclusive**. Q-005 loses review. A second rubric asks specifically
whether the assistant missed required escalation for a reported compromise.

## 2. Return the serious grade

Give the reviewer only `grading.csv`, the rubric, and authorized access to
the frozen evidence. For the synthetic case, look up
`closed-transcript/Q-005@v1` in the [sample evidence](../examples/support-qa/synthetic-evidence.json).
The assistant failed to escalate. In a copy of the packet, enter `meets` for
its one issued row and save as `runs/qa-return.csv`.

```sh
python3 -m telryn refresh runs/qa/report.html --grades runs/qa-return.csv
```

The verdict moves to **Hold**. The net defect count stays at +2.
In your own project, keep the rubric questions distinct even when the same
case and evidence are used. A reviewer who knows the serious-loss policy may
infer which side a serious-case question represents. The report flags that.

## 3. Record the result of a shadow trial

The revised query retains Q-005. It clears the serious cap and projects
960–1,160 reviewer-minutes against the 1,200-minute limit.

```sh
python3 -m telryn compare examples/support-qa/revised.toml --out runs/qa-revised --require-shadow
python3 -m telryn explain runs/qa-revised/report.html --format json
```

Copy [shadow-observations.json](../examples/support-qa/shadow-observations.json)
to `runs/qa-observed.json` and replace `COPY_FROM_EXPLAIN` with the
`comparison_id` from the JSON output. In a real trial these picks and minutes
come from the team's existing system. Then run:

```sh
python3 -m telryn record-shadow runs/qa-revised/report.html runs/qa-observed.json
```

The same HTML now shows eight observed picks, 1,240 recorded reviewer-minutes,
and more Account cases than projected. It records the lead's decision to
**revise** and the decision revision that qualified the trial at import.

## Hand the next cutoff to another analyst

Save the source headers in [mapping.toml](../examples/support-qa/mapping.toml).
For a new decision, create a separate [batch manifest](../examples/support-qa/batches.toml)
beside the new exports. Put that cutoff’s source reference, slot capacity,
rule version, tie-break, mandatory IDs, and overrides in the batch manifest.
The same mapping can handle exports whose ID headers differ by source.

```sh
python3 -m telryn preflight examples/support-qa/mapping.toml \
  --batches examples/support-qa/batches.toml --out runs/qa-repeat-cutoff.json
python3 -m telryn compare examples/support-qa/comparison.toml \
  --export runs/qa-repeat-cutoff.json --out runs/qa-repeat
```

These commands replay the bundled batch as a handoff exercise. For a real
second cutoff, use its manifest and grades. Confirm that the comparison
definition names the intended shifts and the correct live and candidate
rules. Have another analyst reconcile the recorded picks before using the
new verdict. When preflight identifies a missing source, save its capture
instruction for the next cutoff.

The [comparison reference](comparison.md) covers the SQLite input interface
and intake, grading, and shadow record formats. Use the
[pilot protocol](evaluation.md#support-qa-pilot) to compare with your current tools.
