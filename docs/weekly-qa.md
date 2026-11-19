# Run a weekly support audit

Choose a week, the queue it covers, and a policy that two reviewers can apply to the same evidence. In the fintech starter, the question is whether a reported account compromise was closed without the escalation required by the example team's policy. Have the support lead and policy owner agree on what counts and where to find the evidence. The browser desk also includes editable starter questions for a missed required escalation, a promised refund without the payment record, an unlogged complaint and a missed legal escalation. Each has a matching local screen policy. Give each question its own version. The FCA describes monitoring of support outcomes in its Consumer Duty work. An institution's policy owner determines the applicable requirements. [Fintech example](../examples/fintech/README.md) · [FCA consumer support review](https://www.fca.org.uk/publications/good-and-poor-practice/consumer-support-outcome-good-practices-areas-improvement)

## Get the two exports

The population must cover every eligible conversation in the declared cohort. Settle the time basis in advance. The native pulls use creation during the UTC interval and closed status at retrieval. Match that definition to an independent count. For a historical close-event cohort, use the archived event ledger. The ordinary-review list must contain the IDs that actually received routine human QA by the agreed review deadline. A vendor's resolved, escalated, viewed or AI-scored status alone cannot establish that a person graded a case. [Supported sources and assignments](integrations.md)

| File | Required columns | Meaning |
| --- | --- | --- |
| Eligible population | `case_id,revision,evidence_ref,screen_flag` | One row per unique conversation. Use a stable case ID, a frozen revision and an authorized reference to that revision. Set the flag for every row. |
| Ordinary human reviews | `case_id` | One row per reviewed conversation in the same population. A header-only file declares that none received ordinary review. A missing ledger leaves that question unanswered. |

Acceptable flags are `true`, `false`, `1` and `0`. Use metadata-only references such as internal revision keys. Reviewers need access to the version of the source evidence those keys identify. Keep the source snapshot and its retrieval time. A vendor modification timestamp alone cannot recover overwritten text.

## Assign and grade the week

Run `python3 -m telryn desk --out runs/desk` for the local browser interface. Choose both CSVs, enter the rubric and the number of reviews available to each group, and create the audit. In a larger intake or a recurring job, use a TOML file like this. Paths resolve relative to the plan.

```toml
schema = 1
week = "2026-11-02"
rubric = "account-escalation-v1"
rubric_question = "Was a reported account compromise closed without the required fraud-desk referral?"
screen_version = "support-risk-v3"
population = "population.csv"
reviewed = "ordinary-reviews.csv"
flagged_sample = 40
unflagged_sample = 25
```

```sh
python3 -m telryn weekly week.toml --out runs/2026-11-02
python3 -m telryn weekly runs/2026-11-02/report.html --grades returned.csv
python3 -m telryn weekly runs/2026-11-02/report.html --format json
```

The fresh run writes a self-contained `report.html` for the lead and a blind `grades.csv` for reviewers. The packet identifies the rubric, evidence reference and revision. For each case, answer `yes`, `no` or `insufficient` in `serious`. A yes takes `new`, `already_known` or `unknown` in `disposition`. A new case needs an owner and an action or work item. A no takes `none`. An insufficient grade takes `unknown`. Enter the cumulative whole minutes spent on the row. Keep personally identifying details and transcripts in their source system.

Leave unanswered rows blank in a partial return. When more evidence arrives, return the same row to resolve an `insufficient` grade or change a confirmed finding’s `unknown` disposition to `new` or `already_known`. Include all minutes spent on that row to date. A new finding requires an owner and action. The report retains every accepted answer and counts the latest cumulative minutes once. Reimporting an earlier accepted answer leaves the latest finding in place. Conflicting returns preserve the previous report for the audit owner to investigate.

## What the estimate measures

The unit is a conversation that met the team's eligibility definition and had no routine human QA review. The outcome is a human-confirmed finding under the frozen rubric. The total includes cases another control already discovered. The follow-up table distinguishes those from newly discovered cases. Estimated counts cannot identify individual unsampled cases.

Telryn subtracts the ordinary reviews, splits the residual population by the screen flag, then samples without replacement in each group. A fresh secret seed determines the draw. The published report freezes that seed, the membership fingerprints, selection counts, screen and rubric so returning grades never redraws the cases. Keep the first draw. Choosing the most interesting of several redraws invalidates the interval.

For a group of size `N` with `n` sampled and `y` positive grades, its estimated total is `N × y / n`. The headline sums the two totals. The interval inverts the finite-population hypergeometric distribution for small samples or small unsampled complements. Larger groups use a conservative finite-population concentration bound. The two group intervals are combined with a Bonferroni correction for at least 95% design coverage. A fully graded census returns the count exactly. Coverage describes repeated draws under this sampling design. It assumes an accurate population, ordinary-review list and human labels.

An integer in the plan caps the review slots for that group. A percentage such as `unflagged_sample = "5%"` selects that share of the residual group, rounded up.

A group with zero selection probability, a pending grade or an insufficient grade makes the weekly estimate unavailable. One verified case can still need action. For `screen_version = "none"`, supply all false flags and sample from the unflagged group. With a screen, the report gives design-weighted agreement, precision and recall against human grades in the residual population. An empty denominator gives an unavailable metric. The point estimates for precision and recall carry sampling uncertainty too.

Set the sample using available reviewer hours and the smallest count that would change a decision. Even if 10 of 10,000 unreviewed conversations are serious, a simple random sample of 200 has about an 82% chance of finding none. A weekly interval can be too wide to support a staffing change. Retain individual reports with their populations and rubrics for a longer, prespecified evaluation.

## Screening with a local model

Use your team's frozen screen labels, start with a random audit, or score every exported row with a locally installed Ollama model. The built-in screen passes eight structured yes/no/unknown policy events to the model. This allows a team to omit transcript text, case IDs, evidence references, names and contact details from the prompt. Prepare those events within your trusted system. The screen only sees facts represented in that projection.

```sh
python3 -m telryn screen examples/fintech/signals.csv \
  --model llama3.2:latest --policy account-compromise --out runs/local-screen
```

Ollama must already be running on `127.0.0.1:11434` with the requested model installed locally. Use the exact tag from `ollama list`. The `serious-cases` policy also considers missed escalations, unrecorded promised refunds, legal threats and unlogged complaints. Use a matching human rubric when reporting model performance. A run produces `population.csv` and `receipt.json` together. Set the plan's `screen_version` to the value in the receipt and its `screen_receipt` path to that file. Telryn checks the receipt against the population bytes before it draws the sample. A partial or malformed model response fails the batch. The receipt also fingerprints the parsed input events. Keep that export with the receipt. Run Ollama under customer administration and disable its cloud features with `OLLAMA_NO_CLOUD=1` in the daemon environment. [Ollama local-only configuration](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features) [Event columns](../examples/fintech/signals.csv) · [Ollama chat API](https://docs.ollama.com/api/chat)

## Give the lead a weekly digest

```sh
python3 -m telryn weekly runs/2026-11-02/report.html --digest
```

The digest contains aggregate results. To deliver it through the customer's mail server, configure `TELRYN_SMTP_HOST` and optionally `TELRYN_SMTP_PORT` (default 465). For authentication, set both `TELRYN_SMTP_USERNAME` and `TELRYN_SMTP_PASSWORD`. Use `--email-to` and `--email-from` on the saved report for one TLS delivery. Supply internal addresses authorized for those aggregates. Schedule this invocation with the team's scheduler after the grading deadline. Each invocation sends a message. Check the server's delivery log before retrying an uncertain send. The report, grader identities and work items stay out of the message.

## Suggest a change to the next queue

When both screen groups have determinate grades and known serious-case dispositions, `weekly REPORT --propose DIRECTORY` compares the observed rates of newly found serious cases. A difference can generate a candidate to reserve one seat for the highest-priority case in the higher-rate group. The remaining seats follow numeric priority. A tie provides no basis for a suggestion. The candidate applies to numeric priority queues with ascending ID tie-breaks. Sparse data can yield unstable differences. Qualification comes from a separate later replay against the actual rule and staff constraints.

Use `compare DEFINITION --proposal DIRECTORY/proposal.json --out RUN --require-shadow`. The replay checks the frozen SQL, comparable screen and rubric, disjoint case IDs and a later cutoff. It reproduces the original picks and tests the serious-case gain and zero-loss guard at the recorded capacity. After a qualifying comparison, use `record-shadow` with subsequent observed picks, recorded work and the lead's decision. [Worked commands](../examples/fintech/README.md) · [Comparison contract](comparison.md)

## Keep weekly reports readable

The portable weekly envelope and evidence both carry schema version 1. Each report embeds its frozen state and checksum. Reading it requires no model, vendor token or source-system connection. The release suite keeps a saved v1 report and checks its original result on every run. Comparison reports use a separate schema. Retain original reports, packets, source snapshots and policy versions together for the required retention period. Removing an audit directory removes its local report and packet. Also expire source copies, sent digests and vendor assignments according to your policy. [Historical comparison reports](comparison.md#grading-and-recovery) · [Security](../SECURITY.md)
