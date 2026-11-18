# Compare Telryn with your current method

The included comparison exercises the same evidence and decision from start
to finish. It tests agreement with an independent calculation. Use the
protocol below to measure operator effort.

## Reproduce the synthetic decision

Follow the [refund walkthrough](refund-walkthrough.md) for the Telryn path.
For the SQLite path, share the same export and archive of grades. The worked
ledger carries five agreed returns, an independent conflicting read of R-091,
and an adjudication of that dispute against the same evidence revisions.

```sh
python3 -m examples.refunds.baseline
python3 -m examples.refunds.baseline --ledger examples/refunds/review-ledger.json
python3 -m telryn qualify --case handoff
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
