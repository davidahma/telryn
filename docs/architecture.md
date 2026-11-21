# How a weekly audit fits together

A QA lead owns the rubric, the eligibility window and the review budget. A reviewer needs only a blinded packet and access to the evidence revision it names. The browser desk and CLI call the same Python engine. A scheduled job can use the CLI from exports through the weekly digest.

| Stage | What passes to the next stage | What the QA lead can check |
| --- | --- | --- |
| Intake | One row per eligible conversation, its evidence revision and reference, plus a separate ordinary-review ledger | The coverage receipt names the time basis, source scope, eligible cases and exclusions. A dated lifecycle ledger can establish historical completions that a current snapshot cannot reconstruct. [Vendor intake](integrations.md) |
| Screen | A frozen flag for every row, a versioned receipt and optional sampling dimensions | With transcripts, the selected inference provider extracts policy signals and cites redacted passages. Code checks that the passages exist, then applies the rubric's screening rule. Source event flags can enter that rule directly. A human grades the case. [Transcript processing](transcripts.md) |
| Plan and draw | The assignments, their inclusion probabilities and a blinded packet | The ordinary workflow removes completed human reviews. Score audits freeze the external scorecard decisions and explicitly choose all scored cases or the scored cases ordinary QA left unreviewed. Preview the cost and uncertainty for the residual queue. The committed design and team key fix the draw across retries. Grouping changes the allocation and the precision. [Statistics](statistics.md) · [Key and draw registry](trust.md) |
| Grade and publish | A signed revision of the local HTML report and an aggregate digest | Human findings include owner and follow-up. Pending or disputed answers widen the estimate. A second reviewer and adjudicator can correct a case while retaining its prior answers. [Grading and recovery](trust.md#correct-a-grade) |
| Reconcile complaint records | A signed attachment in the same weekly report binds the policy, case revisions, explicit deadlines, and dated register and work exports | The lead can separate the register as it stood at cutoff from subsequent owner action. The grading packet and original probability sample keep their identities. [Complaint evidence](complaint-records.md) |
| Audit QA passes | A score tied to each eligible case revision, its version and decision time | Compare the classification with the blind grade and the pass with dated registration evidence. Keep each denominator distinct. [Score audit](qa-score-audit.md) |
| Share a statement | A recipient-safe summary derived from a verified weekly revision | Reproduce the case in the bank’s private environment using the pinned report and approved original records. [Bank handoff](bank-report.md) |
| Change a future queue | An evidence-supported candidate, a replay on a later disjoint cohort, then a shadow decision | The proposal must clear uncertainty across the groups searched. Replay checks capacity, displacement and staffing on the held-out queue. The owner records the decision after observing a shadow trial. [Queue comparison](comparison.md) |

## Data and operating boundaries

Vendor credentials belong to the intake or assignment process. Transcript extraction uses local Ollama or an explicitly selected cloud model. Review the cloud account, route and payload before authorizing egress. Keep the authorized source, extraction checkpoint and citation files in private storage and apply the team's deletion schedule. The weekly evaluator consumes flags, evidence references, grades and group labels. It can run disconnected. When the cohort has dated completion events, the screen and reviewer use evidence as of each case's completion. The report retains individual cutoffs for the assigned cases and a fingerprint of the full coverage receipt.

The trust store lives outside the report directory. It contains the team key, committed designs and latest signed report revisions. Back it up together with the reports, and limit signing access to the audit operator. HTML opened directly is a static view. The desk and CLI check its signature and revision when serving a current audit. An exported packet contains case references and the rubric without the model's answer. An emailed digest contains aggregate counts. [Security and retention](../SECURITY.md) · [Weekly operating guide](weekly-qa.md)

## Comparing discovery methods

The two-week rehearsal asks which complaint episodes each method found. Freeze the native and Telryn selections first. For each week, grade their union once and keep the selection method out of the packet. Counts in that signed report describe the union. The original probability sample of the unreviewed queue has a different denominator. Charge each arm for the review time its picks would have required, including shared cases. Count owner acceptance and completed action from the dated evidence. [Field protocol](evaluation.md#two-weeks-on-one-complaints-queue)

The archived Intercom check compares human submissions with the lead’s independent ledger before changing how ordinary QA membership is established. It reads local files and emits discrepancies. The live adapter and its write permissions stay outside this rehearsal. [Scorecard evidence](integrations.md#check-intercom-scorecard-evidence-for-a-complaints-pilot)

## Historical records and current decisions

The weekly report owns the human grades. The population estimator derives ranges for two questions from them. One asks how many serious cases ordinary QA left out. The other asks how many were previously undiscovered. A known complaint contributes to the first total. An unresolved complaint history widens the second range. A screen's precision, recall and agreement carry the uncertainty of the human sample. [Statistical methods](statistics.md)

The full blinded packet stays available for independent review. The desk and CLI derive a remaining packet from the current report, with the same row identifiers. They put already-returned cases in separate groups for insufficient evidence, disputed grades and unknown prior handling. The history screen reads progress only from verifiable evidence. [Weekly operating guide](weekly-qa.md)

A proposal-backed comparison has two readers. A lead inspecting history can read the recorded result offline. A job authorizing a shadow trial needs the originating team registry, the current weekly source and the current comparison. A submitted trial names the decision revision its owner saw. After a correction, check the gate again before starting work. [Comparison and gates](comparison.md) · [Release design review](design-review.md)

## From contact to complaint control

A conversation is the sampling unit. A complaint episode can include earlier contacts from other channels. Evidence about those contacts can change the first cited expression, the clock and the registration outcome without adding cases to the sample. Keep receipt dates, policy deadlines, registration and remedy distinct. The lead owns one signed weekly report. Reviewers return one blinded packet. The inventory records which channels the team could examine and who owns each gap. An email recipient can read the control summary and open the signed report under the team's normal access controls.

A release check asks a different question before a changed support agent goes live. Its sender submits synthetic complaints to the test deployment. Its reader checks the test register for the corresponding dated entries. The build receives a receipt and a blocking exit code. Release-check cases never enter the weekly queue or its population estimate. [Agent release check](agent-release.md)

Complaint registration and accommodation delivery use the same rule for classifying a dated obligation at a cutoff. Each keeps its own source records, required evidence and owner events. The accommodation worksheet checks one request and a provider receipt. [Accommodation example](accommodation.md)
