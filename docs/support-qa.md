# Walk through a support QA policy change

A lead has 20 reviewer-hours to audit completed conversations from two
assistants. Both policies select eight of twelve conversations for review.
The proposed policy adds three conversations with quality defects and drops
one with an unresolved serious grade. The lead requires zero confirmed serious
cases to lose review and enough evidence to rule out more than zero.

This example is synthetic. All reviewed evidence is available at cutoff through
the exact transcript revision. The queue decides what a human audits after
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
cutoff. It writes a normalized export only when all requested shifts are ready.

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
**revise**. The historical recommendation remains a separate field.

The [comparison reference](comparison.md) covers the SQLite input interface
and intake, grading, and shadow record formats. Use the
[pilot protocol](evaluation.md#support-qa-pilot) to compare with your current tools.
