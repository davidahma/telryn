# Telryn

**Find the serious support failures your QA sample missed this week.**

A QA lead can see every ticket their team graded. The harder question is what happened in the conversations nobody opened. Telryn takes the conversations resolved or closed during the week and the record of ordinary human reviews, then draws a second sample from what was left out. It works with queues handled by people, AI support agents, or both.

The lead gets a private HTML report showing how many serious cases ordinary QA likely missed, how many may have escaped every other recorded control, and how much uncertainty remains. Each confirmed new finding has a case, an owner and a follow-up. Reviewers get a separate CSV that hides the screen's prediction. A local model can read transcripts and cite the passages behind a screen flag. If two reviewers agree a case was serious but disagree about who already knew, it stays in the serious count while the lead resolves whether it needs new follow-up.

[Download Telryn](https://github.com/davidahma/telryn/releases/latest) · [Weekly audit guide](docs/weekly-qa.md) · [Integrations](docs/integrations.md) · [Apache-2.0](LICENSE) · [![CI](https://github.com/davidahma/telryn/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/davidahma/telryn/actions/workflows/ci.yml)

## Try a weekly audit

Use CPython 3.12–3.15 with SQLite on Linux or macOS. The examples, statistics, browser report and tests use the Python standard library. Run these commands from a clone or unpacked release. Give each run a new output directory.

```sh
git clone --depth 1 https://github.com/davidahma/telryn.git
cd telryn
python3 -m examples.fintech --out runs/fintech
```

Open **`runs/fintech/weekly/report.html`**. In this synthetic fintech queue, ordinary QA reviewed two of ten conversations. Reviewing the eight others confirmed four missed escalations. The complaints team already knew about one. Three needed new follow-up, including **T07**, which the screen had passed. Because all eight received a grade, the count for this batch is exact.

![Weekly QA report showing four confirmed missed escalations, three newly found, and the owner and action for each new finding.](docs/images/weekly-audit.png)

Those eight grades alone give little evidence for changing the queue. A separate synthetic development cohort in the demo supplies a stronger signal. On held-out cases, the supported candidate picks up one additional serious case at the same two review slots and keeps the mandatory review. The example owner then records a shadow trial. [Run the three weeks individually](examples/fintech/README.md)

## Run it on your queue

Start the private browser desk on the QA lead's machine.

```sh
python3 -m telryn desk --out runs/desk
```

Choose the population and ordinary-review CSVs, then enter the rubric, available minutes and expected finding rate. **Preview sample** shows the allocation and likely interval width before you spend reviewer time. **Commit and create audit** records the design and issues the blinded grading CSV. Return grades, collect an independent second review, and resolve disagreements in the same desk. The signed report preserves the original answers and the final decision. When a reviewer returns part of a packet, the desk shows what still needs a first grade, a better evidence record, or adjudication. Download just the unanswered assignments for the next reviewer. A saved audit that fails verification stays visible with recovery instructions.

For a scheduled job, save the settings in a weekly plan and run one command. The example plan is ready to run.

```sh
python3 -m telryn weekly examples/fintech/week.toml --out runs/weekly
```

| What your team has | How to use it |
| --- | --- |
| An existing help desk or AI support agent | Pull weekly completions from Intercom (including Fin), Zendesk, Salesforce Service Cloud or Freshdesk. For a feed such as Decagon that lacks dated resolution history, supply a lifecycle export for completion-week reporting. Read a versioned Sierra export from customer-owned S3, or map an approved Fin, Sierra or Decagon CSV. [Setup and API coverage](docs/integrations.md) |
| A QA team already grading conversations | Import the ordinary review ledger, including from a Zendesk QA workspace. Hand selected records back through Intercom or Zendesk tags, or import the source ticket list into a MaestroQA assignment. Your team keeps its grading and case evidence in its existing tools. [Assignment and grade handoff](docs/integrations.md) |
| A screen that flags likely failures | Fetch an authorized transcript revision or import local JSONL. Ollama extracts policy signals with supporting passages. A deterministic policy flags the case. Already have structured events? Use the same policy directly. [Transcript intake, citations and resume](docs/transcripts.md) |
| An analyst, coding agent, or CI job | Read a saved weekly result as JSON, render a digest for the lead, or evaluate a frozen proposal with a machine-readable shadow gate. [Weekly commands](docs/weekly-qa.md) · [Comparison exit codes](docs/comparison.md#cli-exit-codes) |

## Read the number

Each sampled conversation stands for others drawn from the same group. If ordinary QA left 1,000 unflagged conversations and you sampled 50 at random, each grade represents 20 conversations in that group. Telryn combines the flagged and unflagged estimates and reports a 95% interval. The report lists individual cases only when a human confirmed the finding.

For a single group of 5,000 conversations, grading 50 and finding none still leaves a 95% upper bound of **353 cases**. At 200 grades it drops to **89**. The planner makes that trade-off visible. If one grade is insufficient, the report shows the range under both possible answers. If a group had no chance of review, the report identifies the gap. Sample by channel, agent type or product when those distinctions matter to the decision. [Statistical methods](docs/statistics.md)

A perfect screen score can come from two grades. Suppose ordinary QA left 50 flagged and 50 unflagged conversations. A reviewer grades one from each group. The flagged case is serious and the unflagged case is clear. The point estimates for precision, recall and agreement are all 100%. Their 95% ranges each span **2%–100%**. Telryn shows the ranges and denominators so the lead can judge whether the screen has earned that confidence.

## Check it against public data

The Ubuntu Chat Logs Corpus contains 200 real technical-support dialogues with human labels for conversational friction and published GPT-4o flags. The demo simulates the ordinary QA selection of 40 conversations. The labels come from the researchers. You can repeat the audit offline.

```sh
python3 -m examples.public_demo --out runs/public-sample
python3 -m examples.public_demo --census --out runs/public-census
```

In the 160 remaining conversations, the human annotators found friction in **86**. The archived model flagged all 86, along with 68 conversations the humans marked negative. That gives it **55.8% precision** in this population. The census reproduces the published labels case by case. The sampled run lets you compare its estimate and interval with the known total. This example measures conversational friction. The corpus contains no fintech escalation grades or operational savings. [Data, attribution and reproduction](examples/public_demo/NOTICE.md)

## Before changing live routing

The proposal searches the screen and recorded segments for groups with a higher rate of newly discovered serious findings. It can reserve review slots only when the lower bound for a group's rate exceeds the upper bound for the rest of the queue, after accounting for the groups searched. On weak evidence it declines to propose a change. A candidate still has to reproduce the historical live queue, preserve capacity and pass the staffing and serious-loss checks on a later batch. [Proposal and replay](docs/statistics.md)

**Hold**, **Inconclusive**, and **Try in shadow** apply to the proposed rule. A shadow trial records what the candidate would pick alongside the live queue. The queue owner decides whether to adopt it. If a source grade changes, the saved comparison retains its historical result. A new shadow decision requires the current signed evidence. A scheduled job can require that specific workflow with `explain REPORT --require-shadow --require-weekly-source`. Teams with a rule already in mind can go straight to a comparison. [Worked support comparison](docs/support-qa.md) · [Comparison and historical report compatibility](docs/comparison.md) · [Refund queue example](docs/refund-walkthrough.md)

The audit and replay run locally. The team’s private trust store fixes the sampling key, records draws and verifies signed weekly reports. Preserve the key and registry together. Older weekly HTML remains available for explicit historical reading. [Audit trust and migration](docs/trust.md) · [Architecture and data flow](docs/architecture.md)

Optional vendor pulls, tag writes and SMTP delivery use your team's credentials. Keep reports and packets in storage with the same access and retention controls as the source cases. Run the qualification suite from the release with `python3 -m telryn qualify`. [Security](SECURITY.md) · [Evaluate Telryn against your current workflow](docs/evaluation.md) · [Contributing](CONTRIBUTING.md)
