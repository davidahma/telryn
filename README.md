# Telryn

**Find complaints your AI and human support teams missed.** Telryn helps a complaints lead at a lender or payments company check which closed conversations passed QA while a required complaint was still missing from the register at its deadline. It reads the team’s score export, asks reviewers for an independent policy grade, then checks dated register entries and owner actions.

On 6 October, a customer told a payments agent, “Please open a complaint about this duplicate charge.” In Telryn’s synthetic rehearsal the scorecard passed the conversation and classified it as *no complaint*. The firm’s registration deadline was 7 October at 17:00 UTC. On 12 October the case manager accepted the work at 11:00. Registration and completion followed at noon. A second case shows a different failure: the scorecard recognized the complaint, passed the conversation, and the deadline still went by. [Open the first weekly audit](https://github.com/davidahma/telryn/releases/download/v0.12.0/synthetic-qa-pass-audit.html)

## Try the two-week audit

Use pipx on Linux or macOS with CPython 3.12–3.15 and SQLite. The base package uses the Python standard library. Choose a new output directory.

```sh
pipx install telryn
telryn demo --qa-passes --out runs/qa-passes
```

Open the first `report.html`, find **C102** in the scorecard results and follow its link to the dated evidence. In week two, open **C204**. Each week has one signed private report and one blinded grading packet. The command prints a verification command for its private trust store inside the output directory. In `bank/report.html`, a bank reviewer can see the scope, denominators, unresolved evidence and what owners did. [PyPI](https://pypi.org/project/telryn/) · [Sample bank statement](https://github.com/davidahma/telryn/releases/download/v0.12.0/synthetic-bank-statement.html) · [Rehearsal records](https://github.com/davidahma/telryn/blob/v0.12.0/examples/complaints_pilot/README.md)

## Use your own scorecard

1. **Freeze one queue and one scorecard version.** Supply the closed conversations, original revisions, score times and QA decisions as of a declared cutoff. A complaint prediction and the overall pass/fail decision are separate fields. Choose whether the scored population includes cases ordinary human QA already reviewed. Unscored conversations appear in the coverage count.
2. **Blind the reviewers.** Reviewers receive case references and the firm’s policy question. The lead can request a second review and adjudicate disputes. Telryn estimates how often the vendor’s complaint classification disagreed with the human grade. For QA passes it separately estimates how often a required registration missed its deadline. Each measure has its own denominator and uncertainty range. [CSV and weekly plan](https://github.com/davidahma/telryn/blob/v0.12.0/docs/qa-score-audit.md) · [Sampling method](https://github.com/davidahma/telryn/blob/v0.12.0/docs/statistics.md)
3. **Check what happened.** Attach the dated register and work ledger. The lead can follow a case from the customer’s words to the deadline, acceptance, registration and completion. Incomplete sources and ambiguous matches stay visible. The bank can replay a disputed finding against its private copy and original exports using the digest supplied through an authenticated channel. [Complaint evidence](https://github.com/davidahma/telryn/blob/v0.12.0/docs/complaint-records.md) · [Bank handoff](https://github.com/davidahma/telryn/blob/v0.12.0/docs/bank-report.md)

In the desk, a lead can import Zendesk QA, MaestroQA or AI QA scores, attach the case records, review the policy and download the bank statement. Start it with `telryn desk --out runs/desk`. IT can put it behind the firm’s SSO proxy with separate lead and reviewer roles. [Score formats](https://github.com/davidahma/telryn/blob/v0.12.0/docs/native-scores.md) · [Hosted desk](https://github.com/davidahma/telryn/blob/v0.12.0/docs/hosted-desk.md)

Schedule `telryn weekly /path/to/week.toml --out /path/to/new-run`. The weekly job can run offline. IT can deploy the signed Linux container on amd64 and arm64, including through Docker Desktop on Windows. [Weekly operating guide](https://github.com/davidahma/telryn/blob/v0.12.0/docs/weekly-qa.md) · [Container deployment](https://github.com/davidahma/telryn/blob/v0.12.0/docs/container.md) · [Security review pack and SBOMs](https://github.com/davidahma/telryn/releases/download/v0.12.0/telryn-security-review.tar.gz)

## Give risk teams evidence they can verify

A bank can keep signed weekly reports across quarters, check whether the same population and clocks still apply, and verify the series against an earlier checkpoint. An agent vendor can issue an attestation with a passing release test, quarterly calibration, a bank statement and the customer’s control IDs. The recipient verifies it with an approved issuer key. [Signed history](https://github.com/davidahma/telryn/blob/v0.12.0/docs/signed-history.md) · [Vendor attestation](https://github.com/davidahma/telryn/blob/v0.12.0/docs/vendor-attestation.md)

- **Deadline data.** Download the versioned rules, schemas and dated calendars separately. Reports show the calendar used and flag approaching expiry. [Signed data package](https://github.com/davidahma/telryn/releases/download/v0.12.0/telryn-deadlines-1.0.0.tar.gz)
- **Peer benchmarks.** An independent operator can collect opt-in signed aggregates and return a configuration’s frozen peer result to qualifying contributors. [Collector guide](https://github.com/davidahma/telryn/blob/v0.12.0/docs/peer-benchmark.md)
- **Data and agent evals.** Capture a read-only warehouse snapshot, or run Telryn’s cited rubric and deadline checks from Braintrust or Langfuse. [Warehouse snapshots](https://github.com/davidahma/telryn/blob/v0.12.0/docs/warehouse.md) · [Scorer adapters](https://github.com/davidahma/telryn/blob/v0.12.0/docs/conduct-scorers.md)

## What the examples establish

The first scored week contains seven QA passes. Three missed their registration deadlines, three had determinate answers without a miss, and one remained unresolved. The supported count is three to four of seven. All cases are synthetic.

A separate two-week rehearsal compares Telryn with an analyst’s saved SQL search at the same staff budget. Across all 1,350 possible draws, SQL matches or exceeds Telryn’s count of owner-accepted episodes 96% of the time. Run `telryn demo --complaints --out runs/sql-comparison` to see the selections and staff minutes. The field protocol asks whether a lead can bring a consequential case to action sooner or reach the same finding with less total effort, then repeat the work in week two. Customer savings and repeat demand remain unmeasured. [Field comparison](https://github.com/davidahma/telryn/blob/v0.12.0/docs/evaluation.md#two-weeks-on-one-complaints-queue)

A separate detection study tested 320 authored chats using local cited extraction and a frozen keyword search. Across all cases, recall was 90.0% for extraction and 47.5% for SQL. Precision was 72.4% and 77.6%, respectively. At the same modeled review budget, extraction found 89 complaints and SQL found 85. The interval for the four-case difference ran from −5 to +15. [Study, labels and intervals](https://github.com/davidahma/telryn/blob/v0.12.0/docs/detection-study.md)

## Other outcomes and agent checks

- **Requested accommodations.** Run `telryn outcome --example accommodation` to trace a large-print statement from the customer’s request to the provider’s delivery receipt. It was registered on time, dispatched before the agreed deadline and delivered late. A team can supply the same records for an individual case. [Evidence format](https://github.com/davidahma/telryn/blob/v0.12.0/docs/accommodation.md)
- **Disclosed support needs.** Run `telryn outcome --example vulnerability` to compare register recognition across groups. Small cells are withheld. [Cohort evidence](https://github.com/davidahma/telryn/blob/v0.12.0/docs/accommodation.md)
- **Vendor and board reporting.** Generate a weekly calibration trend or quarterly committee pack from verified audits. An optional benchmark file stays local until the firm decides to share it. [Oversight reports](https://github.com/davidahma/telryn/blob/v0.12.0/docs/oversight.md)
- **Agent releases.** Run `telryn demo --agent-release --out runs/agent-check`. Exercise an agent with synthetic complaints before rollout and check its test register independently. A missing, late or unverifiable entry keeps the release gate closed. [Agent test and adapter contract](https://github.com/davidahma/telryn/blob/v0.12.0/docs/agent-release.md)
- **Screening and intake.** Use structured signals, approved helpdesk exports or connectors. Optional Ollama, Azure OpenAI and Bedrock extraction requires a cited passage for each definite fact. [Integrations](https://github.com/davidahma/telryn/blob/v0.12.0/docs/integrations.md) · [Model setup](https://github.com/davidahma/telryn/blob/v0.12.0/docs/transcripts.md)

Run `telryn qualify` for the bundled checks. [Architecture](https://github.com/davidahma/telryn/blob/v0.12.0/docs/architecture.md) · [Policy packs](https://github.com/davidahma/telryn/blob/v0.12.0/docs/policy-packs.md) · [Contributing](https://github.com/davidahma/telryn/blob/v0.12.0/CONTRIBUTING.md) · [Apache-2.0](https://github.com/davidahma/telryn/blob/v0.12.0/LICENSE)
