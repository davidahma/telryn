# An account-takeover report closed without escalation

A UK fintech QA lead is reviewing closed support cases handled by people and AI agents. The example team's policy requires a fraud-desk referral before closing a reported compromise that meets its escalation criteria. A reviewer checks the case record for that referral. A versioned screen helps choose cases to review. The example uses synthetic records, grades, review durations and queue owner decisions. Replace the example policy with one approved by your own team.

From the repository root, run the three weeks locally with Python 3.12 or later.

```sh
python3 -m examples.fintech --out runs/fintech
```

| Week | What the lead can check |
| --- | --- |
| November 2 | Ordinary QA reviewed two of ten conversations. The example grades all eight remaining cases. Four met the serious-case policy, including one the complaints team already knew. The three new cases are T03, T04 and T07. T07 reached the reviewer through the unflagged group. Review took 37 staged minutes. |
| November 9 | A candidate developed from the separate 48-case synthetic cohort reserves one review slot for the highest-priority flagged case. At the same two-slot capacity, H03 gains review, H02 loses it, and mandatory case H01 keeps its slot. Under the original serious-case question, the change gains one serious case and loses zero. Candidate workload fits the 12-minute budget. |
| November 16 | The example owner records two new shadow picks and 11 minutes. The owner chooses to continue the trial. A real queue owner supplies actual picks, measured minutes and a dated decision after the held-out evaluation. |

Open `runs/fintech/weekly/report.html` for the findings and sampling details. Open `runs/fintech/comparison/report.html` to inspect the historical comparison and recorded shadow observations. Eight of eight residual conversations received a grade in this small example, so the weekly count is exact. For larger changing queues you can set `unflagged_sample = "5%"` in the plan. Telryn rounds up to a whole number of unflagged, ordinarily unreviewed cases. The uncertainty depends on the actual sample and grades.

## Follow the evidence

[The weekly plan](week.toml) binds the November 2 population and ordinary QA selections to the example policy. [The staged grades](staged-grades.csv) explain each human finding and record who owns newly discovered cases. Two of four flagged cases and one of four unflagged cases were newly found. Those counts identify cases to act on. Their uncertainty is too large to support a queue change, so a proposal from this report returns an inconclusive error.

The demo then uses a [separate development cohort](development.toml), constructed to exercise the rule search. Of 24 flagged cases in that cohort, 22 contained newly discovered issues. Two of 24 unflagged cases did. The simultaneous bounds support testing a reserved slot. The development export, grades and measured minutes are synthetic. The report is saved under `runs/fintech/development/report.html`. [Development labels](development-grades.csv) · [Statistical method](../../docs/statistics.md)

[The held-out export](held-out.json) contains a new set of cases, their queue order and the inputs available at cutoff. The generated rule uses the same screen version and keeps the original slot limit. Telryn replays both selectors, grades changed cases against the weekly serious-case rubric and checks mandatory review, staffing and serious losses. [The staged shadow file](shadow-template.json) contains a third set of cases. The runner captures the comparison ID and qualifying decision revision before the synthetic trial and includes both in its observations.

The demo has already run the held-out replay and recorded a shadow observation on it. Read the digest and check whether the saved comparison still qualifies against the latest evidence in your trust store.

```sh
python3 -m telryn weekly runs/fintech/weekly/report.html --digest
python3 -m telryn explain runs/fintech/comparison/report.html \
  --require-shadow --require-weekly-source --format json
```

For an additional held-out cohort, use a new comparison definition and frozen export with `compare DEFINITION --proposal runs/fintech/proposal/proposal.json --out NEW_RUN --require-shadow --require-weekly-source`. Keep the saved comparison and original packet when further grades or shadow observations arrive. Replaying identical inputs cannot reset their accepted history.

Production assignments are available in `grades.csv` from the weekly command. A reviewer completes that exact packet against the supplied evidence references. The synthetic runner's `synthetic-return.csv` shows a finished return.

## Try a local model

[Two synthetic transcripts](transcripts.jsonl) exercise the account-compromise screen. One agent closes a case without a fraud referral. The other records the referral. With Ollama running locally, use the exact tag shown by `ollama list`.

```sh
python3 -m telryn screen examples/fintech/transcripts.jsonl \
  --model "$TELRYN_LOCAL_MODEL" --policy account-compromise --out runs/fintech-transcripts
```

Inspect the message passages in `citations.jsonl` and the resulting `population.csv`. This two-case screen is separate from the ten-case weekly example. For the ten example IDs, Telryn can apply the policy directly to the [structured signal CSV](signals.csv).

```sh
python3 -m telryn screen examples/fintech/signals.csv \
  --policy account-compromise --out runs/fintech-screen
```

To use either output in a new weekly plan, supply matching ordinary-review IDs, use the generated `population.csv`, copy `screen_version` from `receipt.json` and set `screen_receipt` to that file. Paths resolve relative to the plan. [Source coverage and model extraction](../../docs/transcripts.md)
