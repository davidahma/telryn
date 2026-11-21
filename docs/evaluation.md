# Compare Telryn with your current method

The included comparison exercises the same evidence and decision from start
to finish. It tests agreement with an independent calculation. Use the
protocol below to measure operator effort.

## Reproduce the synthetic decision

Follow the [refund walkthrough](refund-walkthrough.md) for the Telryn path.
Run the SQLite commands from the [matching source checkout](weekly-qa.md#use-the-example-input-files) with the same export and archive of grades. The worked
ledger carries five agreed returns, an independent conflicting read of R-091,
and an adjudication of that dispute against the same evidence revisions.

```sh
python3 -m examples.refunds.baseline
python3 -m examples.refunds.baseline --ledger examples/refunds/review-ledger.json
telryn qualify --case handoff
```

The qualification case checks agreement before returns, after the five returns,
after the dispute, and after adjudication. Both paths move from **Inconclusive
[−2, +6]** to **Try in shadow [+1, +6]**, return to **Inconclusive [0, +6]** during
the dispute, and recover to **Try in shadow [+1, +6]** after adjudication.
The ledger's recorded minutes are example values. The original SQLite
ranking and the ledger are independent of Telryn's implementation.

## Measure effort with operators

Choose a dated rule change and name the decision owner. Freeze the candidate,
population, cutoff and evidence revisions, workload assumptions, hurdle,
review budget, and required control cases before either run. Use the same
grading evidence and adjudication process for both methods. Keep the rubric
and allowed evidence visible to the reviewer and keep case side and control
status private where possible.

Have a competent analyst use their established method or the plain SQLite
recipe. Run comparable batches with the methods in alternating order, or
assign separate analysts working from the same input. Reusing the same cases
with the same person can make the second method look faster.

| Measure | Record separately for each method |
| --- | --- |
| First use | Time to map source fields, verify membership, configure the rule, and learn the tools. |
| Recurring analysis | Active minutes to prepare the export, replay and verify picks, and assemble the decision. |
| Evidence review | Number of packets, rows and cross-checks, and active minutes for ordinary and control grades. |
| Adjudication | Dispute count, evidence reopened, additional review time, and which decisions changed. |
| Interpretation | Active minutes for the lead to name displaced exceptions, explain the bound against the hurdle, and select the right action. |
| Reliability | Divergent case IDs, unknown or excluded evidence, result and safeguards, and the cause of each difference. |

Record participant roles, prior familiarity, batch IDs, method order, and
first-attempt errors. Agree on the meaningful effort difference in advance.
Judge time savings only when the methods reach equally well-supported
decisions. Record adoption on the next real rule change separately.

## Support QA pilot

**Question to test.** Before changing which completed AI conversations get
reviewed, can a support quality lead explain the consequential cases the two
policies trade and whether both fit the same staffing limit? Start with the
Head of Support Quality or AI Operations and a dated change to selection,
risk scoring, coverage, or reviewer allocation across assistants.

1. Screen five teams. Ask for the last two changes to **which cases were
   selected**, their next decision date, and whether the team can authorize a
   locally handled export. Review volume and policy-change frequency are
   separate measures. Aim for two teams with a dated change and usable evidence.
2. Start with ordinary native exports for both paths. Compare the prior
   public release and the team's native workflow plus its usual SQL, Sheets,
   or coding agent. Record setup and recurring analyst, reviewer, and lead
   minutes separately. Treat an equally supported decision materially faster,
   or a verified consequential miss found by one path, as a pilot outcome.
   Agree on the material timing threshold and serious mistake definition
   before reviewing results.
3. Time evidence intake separately. A proposed target is under an hour to
   establish fit or identify the first missing source. If an export contains
   only reviewed conversations, ask for the independent eligible population
   and the cutoff snapshot. Use that team's policy and authorized evidence.
4. Test the serious-loss calculation manually on the same cases. Ask the
   lead to document whether the threshold changes or conditions the decision.
   Keep representative samples for workforce scoring and overall quality
   measurement separate from this targeted audit.
5. If the team runs a shadow trial in its own system, record actual candidate
   picks, reviewer time, case mix, and the lead's decision. On the next dated
   change, check whether the analyst can run the comparison independently.
   Record a budget owner's payment only if it occurs.

Measure repeat SQL reuse separately from first-time rule translation. Test
the SQL selector when a team already owns a versioned ranking query.
Document unsupported SQL features, any adaptations, and analyst time.

| Pilot record | What to capture |
| --- | --- |
| Before the comparison | Queue owner, last two dated selection changes, next date, decision threshold, export authorization, actual audit cadence, expected policy-change cadence |
| Evidence | Independent eligible IDs, ordered picks, cutoff scores and revisions, tie-breaks, substitutions, workload budget, exact transcript references, excluded sources |
| Equal starting point | Separate setup and recurring time for each path, same permitted evidence and reviewer budget, recorded grades and adjudication |
| Decision | Moved cases, serious cases losing review, material misses confirmed by the lead, time and staffing, verdict and next experiment |
| Recurrence | Subsequent dated change, independent reuse, actual setup payback, and any paid commitment |

For comparable decisions, divide incremental setup hours by the observed
recurring hours saved per actual policy change. Round up to get the number
of changes needed to recover setup. A zero or negative saving gives no
time-based payback. Compare that number with observed policy-change frequency.
Use a separately observed consequential miss to assess value when time is
similar. If the cadence or gain does not support repeat use, keep the release
as OSS and pause broader product investment.

### Record the outcome of a matched benchmark

Before either arm starts, have the lead name the comparison, declare a
material time saving in minutes, and confirm the matching conditions.
Record active person-minutes in each arm for setup, analysis, grading,
adjudication, and lead review. Assign each activity to one phase. Telryn’s
reviewer total includes minutes recorded for ordinary grades and cross-checks.
The adjudication total includes minutes logged for dispute resolution. Add
externally timed work in the appropriate phase. Record
policy changes in the last 12 months from dated decisions. Count changes to
selection policy separately from audits performed.

Copy the [pilot record](../examples/support-qa/pilot-record.json) to your
local runs directory. After grading, put the report’s `comparison_id` and
`decision_revision` in the record. Use the UTC calendar date of the lead’s
decision. It must be on or after the UTC date of every replayed cutoff.
If a new grade or adjudication changes
the revision, check that the benchmark findings still apply before updating it.
The included minutes and cadence are invented to demonstrate the calculation.
Set `evidence` to `observed` for an actual team run. Populate the lead’s
evidence judgment from their review. When the comparator overlooked a serious
case, record its ID only after the lead confirms that it lost review under the
candidate and was missing from the comparator’s findings.

```sh
telryn explain runs/qa/report.html --format json
telryn benchmark runs/qa/report.html runs/pilot-record.json
```

Use the report after grading in [the support QA walkthrough](support-qa.md)
to try the sample. With its fictional timings the equally supported result
saves 120 recurring minutes and needs three policy changes to recover six
hours of incremental setup. The sample team recorded two changes in the
preceding year. That cadence falls short of first-year time-based payback
if it continues.

If the lead asks for help on a second dated change, test a fixed-fee review
covering source mapping, the matched comparison, and a decision handoff.
Record acceptance and payment separately from feedback. Use evidence from
the second change to decide whether a maintained integration is worth building.

### What public evidence supports

Monzo describes reducing its completed-conversation QA sampling rate over
time. A Verint deployment paper describes ranking conversation turns by
risk and having humans review them. Both establish that selection policies
vary. Willingness to pay for a separate comparison is still open.
[Monzo engineering](https://monzo.com/blog/engineering-the-future-of-customer-operations-the-monzo-ops-agent) · [Verint deployment paper](https://cdn.aaai.org/ojs/7017/7017-13-10246-1-10-20200525.pdf)

Start the incumbent arm with the tools people already use. Zendesk has QA
assignments, LangSmith can sample completed threads into annotation queues,
and PetDesk's support lead described a working Google Sheets QA program.
Test exact displaced cases at fixed staffing against that combination.
[Zendesk assignments](https://support.zendesk.com/hc/en-us/articles/7043747327770-Setting-up-agent-QA-assignments) · [LangSmith thread rules](https://docs.langchain.com/langsmith/rules) · [PetDesk operator interview](https://supportman.io/live-chat/ten-ticket-method-stacy-justino/)

Keep refunds as a second discovery lane. Stripe Radar and ezrules already
backtest transaction rules, and Sift can test a route in isolation. Returns
need particular care when the incumbent caused a photo request or inspection
and there is no corresponding evidence for unselected returns.
[Stripe Radar](https://stripe.com/guides/radar-rules-101) · [ezrules](https://github.com/sofeikov/ezrules) · [Sift route backtesting](https://siftscience.zendesk.com/hc/en-us/articles/13388529878547-Route-Backtesting) · [Loop workflows](https://help.loopreturns.com/en/articles/1919041)

### Reproduce the synthetic cross-version benchmark

Run the first intake command in the [support QA walkthrough](support-qa.md).
Use `legacy.toml` from the current checkout with a clean v0.1.1 unpacked
release. Replace the absolute paths in this example:

```sh
# From the unpacked v0.1.1 release
python3 -m telryn compare /path/to/current/examples/support-qa/legacy.toml \
  --export /path/to/current/runs/qa-cutoff.json --out runs/legacy-qa --format json
```

For a plain-SQL cross-check, load the numeric columns in
[cutoff-inputs.csv](../examples/support-qa/cutoff-inputs.csv) independently.
Order the original by `created_priority DESC, conversation_id ASC` and the
candidate by `frozen_risk DESC, conversation_id ASC`, take eight for each,
and reconcile the original with [picks.csv](../examples/support-qa/picks.csv)
and membership with [eligible.csv](../examples/support-qa/eligible.csv).
Apply the [archived defect grades](../examples/support-qa/defect-grades.json)
to the cases that moved. The prior release and independent SQL agree on the
ordered picks and exact net count of +2. The separate serious rubric applied
to the [synthetic transcript evidence](../examples/support-qa/synthetic-evidence.json)
identifies Q-005 losing review. The current release changes the outcome to
**Hold** once that grade is returned. This benchmark checks behavior on
generated exports. It leaves customer effort and adoption to the pilot.

### Prepare a discovery conversation

> I’m testing a small open-source tool for support teams changing which AI
> conversations get a human audit. When you last changed the review rule,
> how did you work out which serious misses the new rule might set aside?
> I’d like to compare that workflow with your existing tools on one dated
> decision, starting with the export structure. We can keep the data with
> your team and agree on the review budget and what would count as an
> improvement before using any records.

The synthetic walkthrough covers intake, replay, grading, and a recorded
shadow trial. The pilot requires teams with dated decisions and measured effort.
Operator time, frequency of usable policy changes, and pricing remain
questions for the pilot.

## Evaluate the weekly audit with a QA lead

Before either team sees the week, agree on one queue, its full eligible population, a serious-case rubric, the ordinary human-review cutoff, and the maximum total reviewer minutes. Have the comparison team use its native QA tools, saved searches, SQL and usual coding agent. Give both teams the same source access. Preserve each team's independently selected cases and account for overlap when comparing findings.

Record each verified new case, independent review of its severity, the owner who accepted it and any action already under way through another control. Record analyst, reviewer and lead time for both workflows. Compare cases found at the same staff budget and show each estimate with its uncertainty. Monetary outcomes need a documented cost, an attributable action and an agreed counterfactual.

A second week establishes whether the lead asks to use the report again and acts on its findings. For discovery, ask a QA lead to walk through the last serious case ordinary QA missed, how it surfaced, who acted and whether the same information was already available in the existing platform. Observe an authorized week before drawing conclusions about adoption, pricing or regulatory benefit.

### Two weeks on one complaints queue

A complaints quality lead at a lender or payments company owns the pilot. Start with the last closed conversation that should have reached the complaints register. Have the lead reopen the dated conversation, the applicable policy clause, the internal deadline, the register as it stood then, and the first record showing who learned about the miss and acted. Record the first missing or inaccessible source. If a reviewer cannot reconstruct the example, resolve that evidence gap before measuring either method. The policy owner supplies the versioned clauses, clock starts and stops, local calendar and approved categories for this queue. A legacy source can supply an explicit deadline per case.

Before opening the two weeks, write down the queue and dates, reporting cutoff, roster of completed ordinary human reviews, evidence permitted at that cutoff, maximum **total staff minutes per method**, assistance allowed in week two and the saving the buyer considers material. Use the same release and rubric both weeks. Keep late edits and post-cutoff actions in a separate observation window. Choose an independent analyst for each method and keep the shortlists hidden from the other analyst until both are frozen.

| Step | What to record |
| --- | --- |
| Run the incumbent | Give the analyst the native QA product, saved searches, SQL and their usual coding agent. Save the queries, source revisions, nominations and nomination times. Keep the lead's own routine wherever the team has one. |
| Run Telryn | Give its operator the same eligible conversations, ordinary-review exclusions, transcript and register evidence, staff allowance and cutoff. Freeze the committed draw and record assistance and rework. |
| Grade once | Mix and deduplicate both shortlists before revealing either selection method. Give a reviewer only case and evidence references, revision, cutoff and rubric. Have an independent reviewer check the grades. Resolve discrepancies with the recorded evidence before revealing which arm selected each case. Keep union results scoped to the selected conversations. |
| Verify action | Match the human complaint grade to the dated register and owner ledger. Record prior handling, whether registration was due, whether an owner accepted the case, and the time and reference for actual completion. Keep open requests separate from owner acceptances. Use a stable complaint-episode reference to count multiple contacts about one issue once. |
| Settle the comparison | Count accepted, previously unknown episodes found only by the native workflow, only by Telryn and by both. Record unresolved evidence and cases awaiting acceptance. Measure elapsed time to acceptance and completion. Charge each arm its actual preparation, selection, lookup, first review, second review, adjudication, lead and assistance minutes. Charge an overlapping case's review to each arm as standalone work; grade it once in the pilot. Retain over-budget weeks and mark them ineligible for an equal-budget claim. |
| Repeat | Have the customer's operator run week two and enter help minutes, who helped and why. Compare equivalent findings and total staff effort against the predeclared thresholds. A shared handoff produces one observed time to action. Establishing acceleration requires dated evidence of what the incumbent did independently. Keep urgent follow-up moving during the comparison. |

Two teams supplying records, a recovered consequential complaint acted on earlier or equivalent findings at materially less total effort, and at least one budget holder paying to repeat are the investment gates. If the incumbent matches the result or the budget cannot detect a miss of interest, stop expansion. Consider an integration only after timing a recurring bottleneck. For Intercom, compare one versioned week against the lead's completed-review ledger and preserve the distinction between assignment, AI scoring, a completed human review and a later edit.

#### Who supplies the evidence

The complaints lead owns the decision to repeat and the budget. Agree on the sources and record the minutes each team spends obtaining access, explaining the data, joining records and resolving gaps.

| Team | Source and acceptance check |
| --- | --- |
| Support operations | A dated export of closed and resolved conversations. Reconcile the queue, time zone and counts. |
| AI agent owner | The agent's release version, conversation evidence, refund events and handoff logs. Distinguish a promised action from an observed outcome. |
| Complaints | The dated register, its coverage and owner-action history. Verify the keys on a registered episode and on a disputed match. |
| Second line | The applicable policy version, clock events, holiday calendar and categories. Assign an owner and a review date to gaps. |
| QA | The completed human review ledger as it stood at cutoff. Exclude assignments, AI scores and later edits from ordinary-review credit. |
| Contact centre | Authorized transcripts, retention dates and join keys. Record which calls were unavailable, including those outside retention. |

Record first-week onboarding by team in minutes and include it once in each method's preparation total. Record recurring effort separately in week two. Report elapsed hours from conversation closure to registration with the count of matched records. Keep earlier registrations visible as such. Count unresolved cases, give their age since closure and assign a person and review date. Read the register and action history again at the agreed follow-up cutoff. These measures tell the lead which gaps are aging and which records need another check. [Example time ledger](../examples/complaints_pilot/onboarding.csv) · [Example channel inventory](../examples/complaints_pilot/channels.csv)

#### Run the local rehearsal

The installed CLI prints both report paths when you run `telryn demo --complaints --out runs/complaints-pilot`. For the full comparison JSON and the Intercom ledger check, run these commands from the [matching source checkout](weekly-qa.md#use-the-example-input-files).

```sh
python3 -m examples.complaints_pilot --out runs/field-rehearsal > field-rehearsal.json
python3 -m examples.complaints_pilot.intercom
```

The first command runs two invented weeks with a capable saved SQL query and Telryn's weekly sample. Both see the same cutoff-limited register, owner actions, repeated contacts, consumer escalation, hardship and post-refund evidence. Four examples exercise complaints phrased without “complaint”. The deduplicated union receives two blind grades. Each dated directory contains one signed report and one grading packet. `field-rehearsal.json` records the actual frozen selections, staff time and owner outcomes. Preserve the JSON and team trust store to replay the assignments.

On the published first draw SQL found five accepted episodes in 125 stipulated staff minutes. Telryn found those five and one other in 131 minutes. Across all 1,350 permitted two-week selections on the fixture, SQL matches or beats Telryn on episode count 96% of the time. That calculation uses the staged acceptances only after the two shortlists are frozen. It describes sensitivity to sampling on this fixture. A field comparison must still establish customer effort, earlier action and willingness to pay. [Worked case, method and results](../examples/complaints_pilot/README.md)
