# Run a weekly support audit

Choose a week, the queue it covers, and a policy that two reviewers can apply to the same evidence. In the fintech starter, the question is whether a reported account compromise was closed without the escalation required by the example team's policy. Have the support lead and policy owner agree on what counts and where to find the evidence. The browser desk also includes editable starter questions for a missed required escalation, a promised refund without the payment record, an unlogged complaint and a missed legal escalation. Each has a matching local screen policy. Give each question its own version. The FCA describes monitoring of support outcomes in its Consumer Duty work. An institution's policy owner determines the applicable requirements. [Fintech example](../examples/fintech/README.md) · [FCA consumer support review](https://www.fca.org.uk/publications/good-and-poor-practice/consumer-support-outcome-good-practices-areas-improvement)

The commands below use the `telryn` executable installed by `pipx install telryn`. Run commands against your own input directory and choose a new output directory for each audit. If you are running only from a source checkout, invoke the same commands there with `python3 -m telryn`.

## Get the two exports

The population must cover every eligible conversation in the declared cohort. Settle the time basis in advance. Use the week of the resolution or closure event in UTC. A conversation opened a month earlier can belong to this week. Attach the source coverage receipt. It binds the eligible IDs and revisions to the dates and gives counts for open, excluded and unresolved membership within the scanned source scope. Where a source retains only its latest completion, a dated lifecycle ledger gives stronger historical coverage. With a bare CSV, the report can describe only the supplied rows. The ordinary-review list must contain the IDs that actually received routine human QA by the agreed review deadline. A vendor's resolved, escalated, viewed or AI-scored status alone cannot establish that a person graded a case. [Supported sources and assignments](integrations.md)

| File | Required columns | Meaning |
| --- | --- | --- |
| Eligible population | `case_id,revision,evidence_ref,screen_flag` | One row per unique conversation. Use a stable case ID, a frozen revision and an authorized reference to that revision. Set the flag for every row. Optional `channel`, `agent_type` and `product` columns let you sample within these groups. |
| Ordinary human reviews | `case_id` | One row per reviewed conversation in the same population. A header-only file declares that none received ordinary review. A missing ledger leaves that question unanswered. |

Acceptable flags are `true`, `false`, `1` and `0`. Use metadata-only references such as internal revision keys. Reviewers need access to the version of the source evidence those keys identify. Keep the source snapshot and its retrieval time. A vendor modification timestamp alone cannot recover overwritten text.

When a case completed more than once in the week, grade the first qualifying completion the source can establish. The packet's `completed_at` column fixes that instant in UTC. A later referral or a payment recorded after the cutoff can change the current case record. Grade the archived evidence at the cutoff and return `insufficient` if that evidence is unavailable. A screen generated for the current snapshot cannot be attached to a different historical cohort.

## Assign and grade the week

Initialize the team key before the first audit with `telryn trust init`. Run `telryn desk --out runs/desk` for the local browser interface. Choose both CSVs and the rubric. Enter staff minutes, expected minutes per grade and the anticipated finding rate. Preview the allocation and expected width, then commit the sample. You can also set counts directly. Uploads allow 24 MiB per file and 64 MiB per request. The CLI accepts larger exports. In a larger intake or a recurring job, use a TOML file like this. Paths resolve relative to the plan.

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
telryn weekly week.toml --out runs/2026-11-02
telryn weekly runs/2026-11-02/report.html --grades returned.csv
telryn weekly runs/2026-11-02/report.html --format json
```

The fresh run writes a self-contained `report.html` for the lead and a blind `grades.csv` for reviewers. The packet identifies the rubric, evidence reference and revision. For each case, answer `yes`, `no` or `insufficient` in `serious`. A yes takes `new`, `already_known` or `unknown` in `disposition`. A new case needs an owner and an action or work item. A no takes `none`. An insufficient grade takes `unknown`. Enter the cumulative whole minutes spent on the row. Keep personally identifying details and transcripts in their source system.

Leave unanswered rows blank in a partial return. More evidence can resolve an insufficient answer. If an accepted yes or no was wrong, ask a second person to grade the same blinded packet independently. Import with `--second-review returned.csv --reviewer REVIEWER_ID`. A disagreement opens a dispute. A third person selects the accepted answer and records their reason and minutes. The report preserves both answers, shows reviewer agreement and recomputes its bounds. [Correction and adjudication commands](trust.md)

### Pick up a partly graded week

To review a scheduled CLI run, start the desk with the parent of the dated output directory, for example `telryn desk --out /srv/telryn/runs`, under the same account and with the same `TELRYN_TRUST_DIR`. Reload the desk after a new delivery. It looks one directory deep and omits hidden and symlinked directories. Open **Open a saved audit** in the desk. Each verified entry shows the week, rubric, and how many primary grades have come back. Choose **Download unanswered assignments** to send the next reviewer only cases that still lack a first return. The rows keep their original audit IDs, case revisions, cutoffs and blank answer fields. They can be returned through the same grade import. For an independent second review, use **Download full blind packet**.

The history counts insufficient returns, open disputes and serious findings with unknown prior handling separately. An insufficient return calls for more evidence. A dispute calls for adjudication. An unknown prior disposition calls for checking the other controls. These cases already have primary grades.

Open the private audit to see **Cases needing follow-up**. For each case, the table gives the evidence reference, revision, any completion cutoff, the first reviewer and the review round to use. Before a second review, the original reviewer can amend the primary row and include cumulative minutes. After an independent check, ask a new independent reviewer to complete the same row in the full blind packet and import it as a second review. If the answers conflict, the case appears under **Resolve a disagreement**. Choose the case and record the accepted answer, adjudicator, reason and minutes. Selecting another dispute clears the draft. Give reviewers their blind packet and authorized source access. The lead keeps the follow-up table private.

The CLI can export the same outstanding rows from a saved report. Use a fresh path for each handoff. A header-only file means every assignment has a primary return.

```sh
telryn weekly runs/2026-11-02/report.html --outstanding unanswered.csv
```

If a saved audit cannot be verified, history lists its directory ID and withholds the report and grading links. Restore the latest signed report and its matching team trust store, then reload the desk. The original rubric and grading progress reappear after verification succeeds. [Trust and recovery](trust.md)

## What the estimate measures

The unit is a conversation that met the team's eligibility definition and had no routine human QA review. The outcome is a human-confirmed finding under the frozen rubric. The total includes cases another control already discovered. The follow-up table distinguishes those from newly discovered cases. Estimated counts cannot identify individual unsampled cases.

Telryn subtracts routine reviews and samples without replacement within the chosen groups. A team key fixes the case ordering for the same weekly membership. The trust ledger records the frame and design before case selection. Retrying the same inputs yields the same cases, including when CSV rows have been reordered. Reviewers grade the frozen revision. [Commitments and report verification](trust.md)

For a group of size `N` with `n` sampled and `y` positive grades, its estimated total is `N × y / n`. The headline sums these totals. The interval combines finite-population bounds across groups. If selected cases are unresolved, Telryn computes the lower result assigning them all negative and the upper result assigning them all positive. Read that sensitivity range alongside the wider interval for sampling uncertainty. A nonempty group with zero selection probability leaves a gap in the estimate. [Statistical assumptions and worked examples](statistics.md)

To preview an allocation without issuing cases, run:

```sh
telryn plan-week week.toml \
  --minutes 36 --minutes-per-grade 6 --expected-rate 0.05
```

To apply your budget when the sample is drawn, save `minutes`, `minutes_per_grade` and `expected_rate` in a `[planning]` table in the plan. Optional `flagged_rate` and `unflagged_rate` use fractions from 0 to 1. The browser accepts percentages. Add `group_by = ["channel", "agent_type", "product"]` at the top level to group by those columns, or choose a subset. Planning uses the actual residual populations after excluding ordinary reviews.

## Screening with a local model

Use a local transcript export and a policy the team approved. From the [matching source checkout](#use-the-example-input-files), with Ollama running locally and the requested tag installed, try the two synthetic transcripts.

```sh
telryn screen examples/fintech/transcripts.jsonl \
  --model llama3.2:latest --policy account-compromise --workers 2 --out runs/transcript-screen
```

The extractor cites the redacted transcript passages supporting a signal. Telryn checks the cited text against the source and applies the policy to the resulting signals. Inspect the citations to assess interpretation. The checkpoint retains completed cases if extraction stops midway. If the source already exports structured policy events, use `screen examples/fintech/signals.csv --policy account-compromise --out runs/event-screen` without a model. Set the weekly plan's `screen_version` and `screen_receipt` from the issued receipt. [Connector transcript coverage, redaction and checkpoint retention](transcripts.md)

## Give the lead a weekly digest

```sh
telryn weekly runs/2026-11-02/report.html --digest
```

The digest contains aggregate results. To deliver it through the customer's mail server, configure `TELRYN_SMTP_HOST` and optionally `TELRYN_SMTP_PORT` (default 465). For authentication, set both `TELRYN_SMTP_USERNAME` and `TELRYN_SMTP_PASSWORD`. Use `--email-to` and `--email-from` on the saved report for one TLS delivery. Supply internal addresses authorized for those aggregates. Schedule this invocation with the team's scheduler after the grading deadline. Each invocation sends a message. Check the server's delivery log before retrying an uncertain send. The report, grader identities and work items stay out of the message.

## Suggest a change to the next queue

Once the graders have resolved the findings, run `weekly REPORT --propose DIRECTORY --max-reserved 2` to search supported segments within a two-seat ceiling. Small observed differences can leave every candidate inconclusive. When a rule qualifies for evaluation, replay it on an independent later cohort with `compare DEFINITION --proposal DIRECTORY/proposal.json --out RUN --require-shadow`. Existing capacity, staffing, serious-loss and mandatory-case checks still apply. The queue owner records observed picks and minutes in a subsequent shadow trial. [Worked example](../examples/fintech/README.md)

## Keep weekly reports readable

Weekly reports issued from v0.5 use a signed version-2 envelope. The CLI verifies the report bytes and revision against the local team's key and registry. The browser desk serves verified report bytes. An HTML file opened directly is a static view. For a v0.4 unsigned report, use `weekly REPORT --legacy` for an explicitly historical reading. Restore original source evidence, the audit directory, and the matching trust store to resume a signed audit. Keep customer exports, checkpoints and sent digests under your organization's retention policy. [Audit trust and corrections](trust.md) · [Historical comparison reports](comparison.md#grading-and-recovery)

An existing signed v0.5 HTML report keeps its original display. Use `telryn weekly REPORT --digest` or `--format json` to read the current calculations from its saved grades. The next accepted grade changes the signed revision and refreshes the display.

## Check a complaints register after grading

A complaints QA lead can attach the queue’s register and owner-work ledger to the same signed report. Supply the team’s policy deadline for each case and the dates through which the sources are complete. The report separates missing registrations that need an owner from accepted work, completed work with a register discrepancy, and cases needing more evidence. Use the original packet for independent review. [Complaint evidence and file formats](complaint-records.md)

### Issue a weekly complaints control statement

Add `control = "control.json"` above any TOML table in the weekly plan, or choose **Complaint control profile** in the desk. The complaints lead and second line agree on a scope, an ordered taxonomy from lowest to highest, and a follow-up date before issuing the packet. The signed report freezes their choices. This abbreviated example covers chat and records a phone gap.

```json
{
  "schema": 1,
  "scope": "Payments servicing, October queue",
  "categories": ["Service complaint", "Priority complaint"],
  "gap_owner": "Complaints case manager",
  "gap_review_date": "2026-10-20",
  "onboarding_minutes": 70,
  "channels": [
    {"name":"chat","owner":"Support ops","coverage":"covered","ordinary_qa":"Human scorecard sample","register_link":"Ticket reference","retention":"90 days","review_date":"2026-10-20"},
    {"name":"phone","owner":"Contact centre","coverage":"uncovered","ordinary_qa":"Call quality sample","register_link":"Manual lookup","retention":"30 days","review_date":"2026-10-20"}
  ]
}
```

Inventory every route a customer can use, including AI agents, human chat, email, phone, disputes, chargebacks and app or social reviews. Use `partial` when Telryn can read only some of the records. If the population contains a `channel` column, its values must match a covered or partial inventory entry, ignoring case. The inventory records the team’s assessment. A connector’s coverage receipt separately records which source rows entered the week. `onboarding_minutes` is the team’s one-time setup effort charged to this run.

Give each reviewer the blind CSV and authorized access to the cited revision. The original `serious` column answers the firm’s complaint question. Complete the four additional columns on every returned row.

| Column | What to record |
| --- | --- |
| `category` | For a complaint, use one of the labels in `category_options` or `unknown`. Use `none` for a negative grade and `unknown` for insufficient evidence. |
| `vulnerability` | `yes`, `no` or `unknown`, independent of the complaint grade. |
| `redress` | `none`, `pending`, `offered`, `completed` or `unknown`. A refund and registration are separate events. |
| `classification_reason` | Cite the evidence or policy clause behind the grade. Keep personal details in the source system. |

A second reviewer records an independent answer on the same blind packet. A lower category, including removal of a confirmed complaint, opens a dispute. The signed history records the requester, original and requested categories, reason, adjudicator and decision. The report keeps dimensions both reviewers agree on while the dispute is open.

Attach dated register and action records. The statement at the top of the report links its counts to the sample, register reconciliation, sampling intervals and channel inventory. Telryn counts a confirmed episode once when the episode evidence establishes the subject and matter. A conversation with a confirmed register result and no verified episode identity contributes to a separate conversation count at cutoff and follow-up. Several of those conversations may concern one complaint. The 95% range counts conversations containing an overdue complaint with no entry **at the cutoff**. It covers the queue outside ordinary human QA and treats inconclusive grades or register matches both ways. A complaint registered late but already present at the cutoff has a separate status in the register table. When comparing two shortlists, set `"population_basis": "shortlist_union"` in the profile. The interval then covers only their union.

The open-work table gives each unresolved registration or missing grade an owner and review date. Age is elapsed whole days from conversation close to the register observation date. The timing measure is the median hours from close to a matched registration across conversations with both timestamps. Registration can precede closure. Missing timestamps stay unknown. The optional aggregate email identifies the exact signed report revision and omits case references, free text and names. [Register and action records](complaint-records.md)

Set the optional boolean `synthetic` to `true` in a rehearsal control profile. The signed report and the aggregate email subject identify the simulation.

## Use the example input files

The installed `telryn demo --complaints --out runs/complaints-pilot` uses bundled records and prints the two report paths. To edit a worked plan or run a guide that uses `examples/…`, get the source files for your installed version. Run these commands in the directory where you keep examples.

```sh
git clone --depth 1 --branch "v$(telryn --version)" https://github.com/davidahma/telryn.git telryn-examples
cd telryn-examples
```

Run the guide's example commands from here. For the policy pack walkthrough, `examples/policy_packs/` contains the plan, population, completed-review ledger and control profile. Keep your team's live inputs and outputs in a separate private directory.

## Schedule a weekly run

On Linux or macOS install the CLI with `pipx install telryn` using CPython 3.12–3.15 with SQLite. Run `command -v telryn` as the account that will own the audits. Record that absolute executable path in the scheduler. To pin an approved version, use `pipx install 'telryn==0.10.0'` in a fresh environment. Use `pipx upgrade telryn` when the team approves a newer release.

In this cron example the export job delivers a directory every Monday, named by **delivery date in UTC**. Each directory contains a plan for the completed audit week, its frozen population and its review ledger. Use paths relative to the plan for those inputs. Set up the inbox and output parent under the audit account, then save this script at `/srv/telryn/run-weekly.sh`. Replace the executable and storage paths with the ones on your host.

```sh
#!/bin/sh
set -eu
umask 077
exec >> /srv/telryn/weekly.log 2>&1
delivery=${1:-$(date -u +%F)}
export TELRYN_TRUST_DIR=/srv/telryn/private/team
exec /home/qa/.local/bin/telryn weekly "/srv/telryn/inbox/$delivery/week.toml" \
  --delivery-date "$delivery" --out "/srv/telryn/runs/$delivery"
```

Schedule it after the export arrives, for example with this entry in the audit account’s crontab. The trigger time uses the scheduler’s time zone. Give that account permission to read the inputs and write the report, trust store and log. Keep the log owner-readable only. The script creates a new log with mode 0600.

```cron
15 8 * * 1 /bin/sh /srv/telryn/run-weekly.sh
```

If Monday’s job misses an export, wait for the full frozen directory and rerun with its original Monday date, for example `/bin/sh /srv/telryn/run-weekly.sh 2026-11-09`. This selects the 9 November delivery, including on a later day. Its plan must start on 2 November. Telryn checks the seven-day period before committing the draw. Deliver complete inputs together by renaming the prepared directory into the inbox on the same filesystem.

Each delivery has its own output directory. A repeat against an existing report fails and preserves the issued sample. To verify that report or import grades, pass the existing HTML to `telryn weekly` with the same trust directory. Monitor the job’s exit status and back up the trust store with the reports.

To sample the cases a vendor QA tool passed, open **Audit an external QA scorecard** in the desk or add the score audit table to the weekly TOML plan. Choose the scope explicitly. Source coverage must include each conversation’s closure timestamp. Attach the register and owner ledger with the weekly CLI after grading. [Score CSV and denominators](qa-score-audit.md)
