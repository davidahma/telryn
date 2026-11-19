# An account-takeover report closed without escalation

A UK fintech QA lead is reviewing closed support cases handled by people and AI agents. The example team's policy requires a fraud-desk referral before closing a reported compromise that meets its escalation criteria. A reviewer checks the case record for that referral. A versioned screen helps choose cases to review. The example uses synthetic records, grades, review durations and queue owner decisions. Replace the example policy with one approved by your own team.

From the repository root, run the three weeks locally with Python 3.12 or later.

```sh
python3 -m examples.fintech --out runs/fintech
```

| Week | What the lead can check |
| --- | --- |
| November 2 | Ordinary QA reviewed two of ten conversations. The example grades all eight remaining cases. Four met the serious-case policy, including one the complaints team already knew. The three new cases are T03, T04 and T07. T07 reached the reviewer through the unflagged group. Review took 37 staged minutes. |
| November 9 | The proposal reserves one review slot for the highest-priority flagged case. At the same two-slot capacity, H03 gains review, H02 loses it, and mandatory case H01 keeps its slot. Under the original serious-case question, the change gains one serious case and loses zero. Candidate workload fits the 12-minute budget. |
| November 16 | The example owner records two new shadow picks and 11 minutes. The owner chooses to continue the trial. A real queue owner supplies actual picks, measured minutes and a dated decision after the held-out evaluation. |

Open `runs/fintech/weekly/report.html` for the findings and sampling details. Open `runs/fintech/comparison/report.html` to inspect the historical comparison and recorded shadow observations. Eight of eight residual conversations received a grade in this small example, so the weekly count is exact. For larger changing queues you can set `unflagged_sample = "5%"` in the plan. Telryn rounds up to a whole number of unflagged, ordinarily unreviewed cases. The uncertainty depends on the actual sample and grades.

## Follow the evidence

[The weekly plan](week.toml) binds the November 2 population and ordinary QA selections to the example policy. [The staged grades](staged-grades.csv) explain each human finding and record who owns newly discovered cases. Two of four flagged cases and one of four unflagged cases were newly found. Telryn uses that observed difference to propose a candidate for testing. In the absence of a difference it declines to suggest a rule.

[The held-out export](held-out.json) contains a new set of cases, their queue order and the inputs available at cutoff. The generated rule uses the same screen version and keeps the original slot limit. Telryn replays both selectors, grades changed cases against the weekly serious-case rubric and checks mandatory review, staffing and serious losses. [The staged shadow file](shadow-template.json) contains a third set of cases. The runner fills in its comparison ID before recording the observations.

To inspect the lead digest, export the suggestion again and replay it against the supplied held-out batch, run these commands after the demo.

```sh
python3 -m telryn weekly runs/fintech/weekly/report.html --digest
python3 -m telryn weekly runs/fintech/weekly/report.html --propose runs/fintech-candidate
python3 -m telryn compare examples/fintech/held-out.toml \
  --proposal runs/fintech-candidate/proposal.json --out runs/fintech-held-out --require-shadow
python3 -m telryn explain runs/fintech/comparison/report.html --format json
```

Production assignments are available in `grades.csv` from the weekly command. A reviewer completes that exact packet against the supplied evidence references. The synthetic runner's `synthetic-return.csv` shows a finished return.

## Try a local model

[The event projection](signals.csv) provides the same ten case IDs and revisions with eight policy-event fields. With Ollama running locally, use the exact tag returned by `ollama list`.

```sh
python3 -m telryn screen examples/fintech/signals.csv \
  --model "$TELRYN_LOCAL_MODEL" --policy account-compromise --out runs/fintech-screen
```

For a new weekly plan, use the generated `population.csv`, set `screen_version` to the value in `receipt.json` and set `screen_receipt` to that file. Plan paths resolve relative to the plan file. The model's flags can differ from the staged screen used above. Review the cases assigned by the new run against the same customer-approved policy.
