# Telryn

**Test changes to human-review queues before you roll them out.**

Your refund team can review 100 requests per shift. A new escalation rule
still picks 100, but replaces 20. Which exceptions would lose a human review?
Would the team catch more of the right cases and finish the shift?

Telryn replays the original queue and compares it with a frozen candidate.
It produces a private HTML decision report for the lead and a blind grading
CSV for the reviewer. The report names the cases that move, checks staffing
and mandatory reviews, and shows what the available evidence supports.

[![CI](https://github.com/davidahma/telryn/actions/workflows/ci.yml/badge.svg)](https://github.com/davidahma/telryn/actions/workflows/ci.yml)
[![Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[Download](https://github.com/davidahma/telryn/releases/latest) · [Try the full workflow](docs/refund-walkthrough.md) · [Connect a queue](docs/comparison.md)

## See a decision in under a minute

Use CPython 3.12–3.15 with SQLite on Linux or macOS. Download and unpack the
[release archive](https://github.com/davidahma/telryn/releases/latest), or
clone the repository:

```sh
git clone https://github.com/davidahma/telryn.git
cd telryn
```

From the unpacked directory or checkout:

```sh
python3 -m telryn demo --out runs/demo
```

The demo uses synthetic data and runs offline with the standard library. Open
`runs/demo/report.html`. The terminal also shows why one candidate was held
and why another qualified for shadow testing. Use a fresh output directory
for each comparison.

![Lead report showing a range of −2 to +6 against a +1 hurdle, projected workload, and the cases gaining or losing review grouped by grade.](docs/images/tuesday-report.png)

## Follow the evidence

Tuesday's rules have 80 requests in common. Of the 20 losing review, **10**
are confirmed exceptions. Of the 20 gaining review, **8** are confirmed
exceptions and **8** remain ungraded. The resulting range is **−2 to +6**
additional exceptions sent to review. Three positive grades among those
eight secure the **+1** hurdle if accepted grades stand and the safeguards
pass.

| Role | What happens next |
| --- | --- |
| Analyst | Exports the cutoff snapshot, confirms the original queue replays, and runs the comparison. |
| Reviewer | Opens the issued evidence revisions, applies the rubric, and returns `meets`, `outside`, or `insufficient` in the CSV. The packet omits ranking, selection side, and cross-check labels. |
| Operations lead | Sees which cases moved, their grades, staffing, the decision range, and the exact next step. A disputed grade requires an independent adjudication tied to the reviews it considered. |

```sh
python3 -m telryn compare examples/refunds/comparison.toml --out runs/tuesday
python3 -m telryn explain runs/tuesday/report.html
```

The [walkthrough](docs/refund-walkthrough.md) shows how to inspect the SQLite
evidence, return five judgments, and refresh Tuesday to **+1 to +6**.
A separate comparison that requires Saturday still needs Saturday's evidence.

## Use it with AI agents

When an agent or model helps decide what reaches a human, Telryn can test the
**routing rule around that decision**. For example, you could compare two
thresholds for escalating an uncertain refund case, or compare two
prioritization rules for a queue of agent-proposed account changes. Export
the scores, flags, and other inputs as they were available at cutoff. Run
each proposed routing rule against that same historical batch.

The rule must be reproducible. For model-backed queues, record the model or
policy version behind each exported signal and preserve the signal values
used at the time. Telryn does the offline comparison with those frozen
inputs. Your system remains responsible for execution, review approvals,
and any live or shadow routing.

## Connect a queue

Telryn is designed for scheduled, disjoint batches. Prepare:

- The independently recorded eligible IDs and the actual ordered picks.
- Exact cutoff inputs for each eligible request, with availability timestamps
  and revision IDs. A backdated correction can still arrive after cutoff.
- The original rule and candidate as trusted local Python selectors, plus
  tie-breaks, override history, mandatory reviews, slot limits, and staff time.
- A shared rubric and references to evidence available under both rules.
  An incumbent-triggered customer call cannot be reused as an independent
  grade simply by sending its transcript to another reviewer.

A replay that reproduces the original winners can still have an incomplete
eligible pool. Telryn checks membership before scoring. See the
[comparison contract](docs/comparison.md) for fields, supported overrides,
and limits on queues with carryover or continuous reassignment. Treat
`report.html` as private and send reviewers only the appropriate packet.

## Read the verdict and use the CLI gate

| Verdict | Meaning |
| --- | --- |
| **Hold** | A verified operational safeguard failed, or the upper bound fell below the hurdle after the required cross-checks. |
| **Inconclusive** | Evidence is still open. The report names missing inputs, staffing measurements, grades, or cross-checks. |
| **Try in shadow** | All required shifts replay, the lower bound meets the hurdle, and the required safeguards and cross-checks pass. Record candidate picks in your existing system while the live rule keeps routing. |

```sh
python3 -m telryn explain runs/tuesday/report.html --format json --require-shadow
```

Here the ungraded Tuesday comparison exits **1**. With the gate enabled,
**0** means **Try in shadow** and **2** means an execution or input error.
Without the gate, a completed report exits **0** for any verdict.
Upgrade from v0.1.0 by regenerating the comparison and issuing a fresh
packet before relying on the gate.

## What the release verifies

```sh
python3 -m telryn qualify
```

The suite exercises missing cutoff evidence, timestamp offsets, rejected
sources, reordered returns, adjudication, recovery, and 90 fresh-process
comparisons. CI also runs the clean checkout and source archive with networking
disabled. The synthetic SQL baseline reaches the same completed decision,
including a dispute and its resolution. Use the [comparison protocol](docs/evaluation.md)
to measure setup, grading, adjudication, and decision time on a real workflow.
The published evidence establishes the mechanics and the synthetic result.

[Release evidence](https://github.com/davidahma/telryn/releases) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Apache-2.0](LICENSE)
