# Rehearse a complaints pilot

Run from the repository root.

```sh
python3 -m examples.complaints_pilot --out runs/complaints-pilot
python3 -m telryn weekly runs/complaints-pilot/2026-10-05/report.html --format json
```

Each output directory holds one signed weekly report and its blinded grading packet. The report covers the union of two frozen shortlists, so the sample size and estimates shown there describe only that union. The terminal comparison tells the lead which accepted complaint episodes each method found. The original full-queue weekly draw is recorded in the local trust registry. The report embeds a commitment to the selection record printed in the comparison.

The two methods see the same closed conversations. The native stand-in executes a saved search over unreviewed cases, checks complaint and investigation language, and joins the dated register and owner ledger to exclude cases those controls already handled. Telryn uses the fixture's screen flag and the existing committed random draw. The primary and second simulated graders each receive the blank, deduplicated packet without the method or screen flag. Both use staged answers to exercise the actual grading and independent-review imports. A dated register and owner-action export then feed the report's reconciliation.

| Input | What to inspect |
| --- | --- |
| [Protocol](protocol.toml) | Scope, review slots, total minutes available to each arm, allowed help in week two and material savings threshold. |
| [Conversations](conversations.csv) and [native search](native.sql) | Synthetic case text, completion, customer-supplied deadline, ordinary review, screen flag and saved SQL used for selection. |
| [Blind answers](answers.csv) | Synthetic rubric judgments and minutes for each independent review. |
| [Complaints register](register.csv) and [owner events](owner-events.csv) | Exact register matches and first visible timestamps. The [owner episode index](owner-episodes.csv) binds each action ID and case ID to a stable complaint-episode ID for deduplication. Missing or conflicting episode links stay unresolved. Completed actions have their own evidence. |
| [Staff minutes](staff-minutes.csv) | Simulated preparation, selection, lookup, adjudication, lead and assistance time. Each arm also pays for first and second review of every case it selected. |

The clock starts at the common audit cutoff and ends at recorded owner acceptance or completed action. Shared cases have one observed action time. Pending action has no completion time. A completed action without a recorded acceptance retains its completion time and stays out of the accepted-episode count. Compare actual action dates with independently documented incumbent handling before claiming the audit brought action forward. No fixture supplies a paid repeat or measured customer labor. [Field protocol and decision criteria](../../docs/evaluation.md#two-weeks-on-one-complaints-queue)
