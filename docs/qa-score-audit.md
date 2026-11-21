# Audit an existing QA tool’s passes

A complaints lead can use a closed queue, the QA tool’s original scores and the dated complaints register to answer two questions. Did reviewers agree with the tool’s complaint classification? Among the conversations the tool passed, how often had a required registration deadline been missed by the audit cutoff?

A scorecard can pass a correctly identified complaint that the firm subsequently registers late. Equally, a tool can overlook a complaint that the complaints team still registers on time. Telryn reports the two rates separately.

## Freeze one week

Prepare a [weekly plan](weekly-qa.md), a source population with its case-specific completion receipt, and a ledger of completed human reviews. Retain the weekly export from the QA tool as it stood at the cutoff. If it stores edits, project the last score version that was actually available by that instant and preserve its original reference. The score timestamp is when that decision on that revision was recorded. Enter the firm's complaint-classification question in the grading rubric.

Add this table **after the top-level settings** in the weekly TOML file.

```toml
[score_audit]
file = "qa-scores.csv"
scorecard = "Payments support QA"
version = "v3"
cutoff = "2026-10-12T08:00:00Z"
scope = "all_scored"
```

```csv
case_id,revision,scorecard,scorecard_version,scored_at,decision,complaint_prediction,score_ref
C102,v1,Payments support QA,v3,2026-10-06T15:00:00Z,pass,no,qa/scores/102/v1
```

The score CSV can be up to 24 MiB. Use one row per scored conversation and one immutable score reference per row. The case ID and conversation revision must match the frozen source. A score may be recorded at closure or later, up to and including the cutoff. Mixed scorecards, duplicate cases and scores from outside those boundaries stop issuance. If a tool only provides an overall pass/fail, enter `unknown` for its complaint prediction. The pass can still be checked against the register. Leave an unscored conversation in the source population and omit its score row. The report counts those gaps. The operator is responsible for reconciling the export with the vendor’s inventory.

- `all_scored` includes scored conversations that ordinary human QA has already reviewed. The new reviewer supplies an independent grade.
- `unreviewed_scored` restricts the draw to scored cases outside the completed-human-review ledger. Use this when the lead’s decision concerns those cases alone.

The sampler uses the plan’s existing screening groups and budgets. A vendor’s pass/fail decision and its complaint prediction remain separate from the sampling screen. The private report records the score export’s SHA-256, denominator in each eligible group and each assigned score reference. The blinded packet omits the vendor results. Keep the original dated export and continue to restrict the report and packet as customer records.

## Grade and reconcile

```sh
telryn plan-week week.toml --minutes 240 --minutes-per-grade 4
telryn weekly week.toml --out runs/oct-05
telryn weekly runs/oct-05/report.html --grades returns.csv --reviewer first-reviewer
telryn weekly runs/oct-05/report.html --complaint-records records.json
telryn weekly runs/oct-05/report.html --format json
```

Give reviewers the frozen source revision and blinded packet. The existing second-review and dispute commands work on these assignments. Use the same audit cutoff in the score table and the [complaint-record manifest](complaint-records.md). The manifest’s follow-up date may be later. Open **Independent QA score audit** in the signed report and follow **Open case evidence** for the passage, deadline, register and owner events.

| Measure | Denominator | Confirmed event |
| --- | --- | --- |
| Classification disagreement | Eligible scored conversations with a definite vendor complaint prediction | The independent human grade differs from that prediction. Pending disputes and insufficient grades remain unknown. |
| Missed deadline among QA passes | All eligible conversations with a vendor pass | The case met the complaint rubric and reliable register evidence establishes absence at its policy deadline by cutoff. Late registration is still a miss. An on-time registration, an outside-rubric case or a deadline still in the future has no miss by cutoff. |

Each denominator counts conversations. Linked contacts may belong to a single underlying complaint. The lead can inspect the separate episode and owner evidence before counting actions. A missing register with unknown coverage cannot establish a miss. A register complete through the deadline can establish a deadline miss even if the evidence cannot settle registration at the later cutoff.

The signed JSON exposes `score_audit.coverage`, `classification_disagreement`, `passed_deadline_miss` and case-level evidence under `score_audit.cases`. For each measure, `assigned`, `determinate` and `confirmed` are observed sample counts. `denominator` is the eligible population for that measure. `estimate` is a proportion. `interval` is the finite-population 95% range for that proportion. `count_interval` gives the corresponding range in conversations. A census of known outcomes produces an exact rate. Unresolved evidence widens the range. A zero denominator yields no rate. [Interval construction](statistics.md#score-audits)

The [two-week synthetic rehearsal](../examples/complaints_pilot/README.md) includes a recorded vendor pass, a blinded human grade, a missing registration and the case manager’s subsequent action. Customer benefit and the effort of repeating this work need a field comparison.
