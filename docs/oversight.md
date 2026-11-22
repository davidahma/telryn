# Bring weekly results to a vendor or committee

A report series starts with verified weekly reports from the same team trust store. Supply issued reports with the same control scope, rubric, channels, scored population and vendor scorecard version. For native imports, the format, pass threshold, complaint evaluator and case-ID mapping must also match. Each must include dated complaint records. The quarter assigns weeks by their Monday start date. Missing weeks are listed explicitly. Weeks marked for policy re-review cannot enter a new oversight statement or benchmark export. The deadline columns identify the clock source and flag a change in clock policy, manually supplied policy references or cutoff lag. A manual date needs separate comparability checks. A vendor report requires a score audit every week. The committee pack can also read ordinary weekly complaint controls.

```sh
telryn demo --qa-passes --out runs/scored
export TELRYN_TRUST_DIR=runs/scored/trust
telryn oversight vendor --quarter 2026-Q4 --out runs/vendor runs/scored/2026-*/report.html
telryn oversight board --quarter 2026-Q4 --out runs/board runs/scored/2026-*/report.html
```

Both commands write an offline HTML page. Agreement and disagreement use cases with a definite vendor classification. Deadline misses use QA passes, including passes that correctly identified a complaint. Review the denominators before comparing the numbers. The difference between successive weeks is expressed in percentage points. Its bounds are descriptive endpoints from the two weekly intervals. Changes in staff, incoming work, evidence availability or policy can explain a change in the figures. The board pack also shows unregistered conversations at cutoff and unresolved work at follow-up. The reports carry aggregate figures and issuer-provided scope labels that the recipient must be approved to see.

## Opt in to a local benchmark file

```sh
telryn oversight benchmark --opt-in --vendor example --configuration v3 --min-cell 10 \
  --quarter 2026-Q4 --out runs/benchmark runs/scored/2026-*/report.html
```

This writes `aggregate.json`. Schema 1 contains the quarter, approved vendor and configuration aliases, synthetic flag, population basis, threshold and weekly metrics. A visible metric has its denominator, assigned count, estimated rate and bounds. Each nonzero observed outcome class and the determinate sample must meet the threshold, at least five. Suppressed metrics expose only the suppression flag. The file omits conversations, sources, team keys, queue names, free text and customer identifiers. Telryn has no collector or automatic upload. The bank controls any subsequent disclosure and should consider what other reports a recipient already holds.
