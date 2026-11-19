# Telryn

**See which AI conversations will lose human review before you change the audit queue.**

You have 20 reviewer-hours next week. A new risk rule fits the same number
of conversations, but sends different cases to people. Which serious mistakes
does the new rule surface? Which does it leave behind? Will the work fit?

Telryn compares two reproducible selection rules against the same historical
cutoff. It gives the lead a private HTML decision report and the reviewer a
separate grading CSV. Run it locally against your export with Python's standard library.

[![CI](https://github.com/davidahma/telryn/actions/workflows/ci.yml/badge.svg)](https://github.com/davidahma/telryn/actions/workflows/ci.yml)
[![Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[Download](https://github.com/davidahma/telryn/releases/latest) · [Support QA walkthrough](docs/support-qa.md) · [Connect a queue](docs/comparison.md)

## Try it on a synthetic support queue

Use CPython 3.12–3.15 with SQLite on Linux or macOS. Run these commands from
the unpacked release or a checkout. The package, example data, and qualification
suite work offline.

```sh
python3 -m telryn preflight examples/support-qa/intake.toml --out runs/qa-cutoff.json
python3 -m telryn compare examples/support-qa/comparison.toml --out runs/qa
```

Open `runs/qa/report.html`. Eight conversations fit the reviewer budget.
The proposed rule would put **two more defects** in front of reviewers,
but it would drop a conversation with an **unresolved serious grade**.
The grading CSV contains the frozen evidence reference and the exact
question. A reviewer confirms the serious miss and the report changes to
**Hold**, even though the net defect count stays positive.

![Telryn support QA report with a positive defect count and an unresolved serious case losing review.](docs/images/support-qa-report.png)

## Follow the decision from replay to shadow

| Step | What the team can check |
| --- | --- |
| Check exports | Find the missing eligible population, picks, cutoff scores, or policy history before translating a rule. A file of reviewed cases alone cannot establish the eligible queue. |
| Replay both rules | Verify the original ordered picks and show the cases that gain and lose review. Use a trusted local Python selector or a versioned read-only SQLite query. |
| Grade the cases that could change the decision | Use the issued evidence references and a shared rubric. Compare the best and worst supported counts, and optionally put an owner-defined cap on serious cases losing review. |
| Record a shadow trial | Import the picks and reviewer time collected by your existing system. See how workload and case mix compare with the historical projection, and record the lead's decision in the same report. |

The [support QA walkthrough](docs/support-qa.md) runs the complete flow.
After the first rule is held, a revised rule earns **Try in shadow**.
The synthetic trial then records 1,240 reviewer-minutes against a
1,200-minute budget and a lead decision to revise.

## Connect a queue

Start with scheduled, disjoint batches that reset at cutoff. Export the
independent eligible IDs, actual ordered picks, exact inputs and revisions,
live rule version, tie-break, and manual overrides. Define the mandatory
reviews, staff time, and common evidence that reviewers may use. Keep real
transcripts, reports, and returned packets in your own access-controlled systems.

For agent workflows, export the scores and signals **as they existed at
cutoff**. Telryn evaluates which completed conversations your rules send to
review. A live or shadow queue continues to run in your existing system.
Use a separate representative sample to estimate overall quality or evaluate
workforce performance. The risk-targeted audit answers a different question
about which cases receive a second look.

The [reference](docs/comparison.md) covers input formats, supported selectors,
grades and adjudication, the CLI exit codes, and the shadow trial contract.

## Read the verdict

| Verdict | Meaning |
| --- | --- |
| **Hold** | A verified safeguard failed, the count cannot meet the hurdle, or confirmed serious losses exceed the limit after required checks. |
| **Inconclusive** | One or more required shifts, workload measures, grades, or cross-checks remain unresolved. |
| **Try in shadow** | Every required shift and safeguard supports a trial, including the serious-loss bound when configured. |

`--require-shadow` returns **0** only for **Try in shadow**, **1** for a
withheld recommendation, and **2** for an input or execution error.
Regenerate v0.1.0 comparisons with the current version before using the gate.

## Refunds and verification

The [refund walkthrough](docs/refund-walkthrough.md) remains a separate
reference for grading disputed order evidence and comparing against a plain
SQLite analysis.

```sh
python3 -m telryn demo
python3 -m telryn qualify
```

The qualification suite exercises both workflows and runs from a checkout
and release archive with networking disabled. The examples establish the
mechanics on synthetic data. The [pilot protocol](docs/evaluation.md#support-qa-pilot)
shows how to compare the tool with native QA assignments plus SQL, Sheets,
or a coding agent, and measure setup and recurring effort on an authorized queue.

[Release evidence](https://github.com/davidahma/telryn/releases) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Apache-2.0](LICENSE)
