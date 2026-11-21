# Give a bank reviewer a weekly complaints statement

A complaints lead can use the statement to decide whether to request a case review from the agent vendor. It covers one scored queue, one policy cutoff and one follow-up. A vendor can issue the statement and the bank can inspect the original evidence under its own access controls. The synthetic specimen demonstrates this exchange. No bank has validated the format in a field pilot.

## Prepare the handoff

Start from a current signed weekly score audit with complaint records attached. The operator needs the team trust store used to issue that audit. Use names that the recipient has permission to see in the four display fields.

```sh
telryn bank-report export private/week-one/report.html --out handoff/week-one \
  --queue 'UK card servicing' --sample-owner 'Bank complaints QA' \
  --register-owner 'Bank complaints operations' --excluded-queues 'Collections and mortgages'
```

The new directory contains one offline HTML page. Send the file through the bank’s approved process and supply the printed SHA-256 through a separate authenticated channel. The issuing CLI first checks the private weekly report against its signing key and current revision registry. A bank recipient can check the resulting statement without that key.

The page shows the issuer-declared roles and exclusions, scorecard name and version, human policy rubric ID, weekly population, score coverage, cutoffs, report revision and source digests. Each metric gives its eligible denominator, sample size, confirmed count, unresolved sample count and population rate range. Owner counts describe the sampled **QA passes with a confirmed missed deadline**. A request is a referral, acceptance records ownership, and completion records work done. The public page omits conversation IDs, quotations, individual score records, staff identities from the work ledger and grading packets. Choose a versioned human rubric ID when issuing the audit. Review that ID, the scorecard labels and small counts before sharing.

## Check a disputed case

The recipient first checks the file against the digest received through the authenticated channel. Here, `BANK_SHA256` denotes that digest.

```sh
telryn bank-report verify handoff/week-one --sha256 "$BANK_SHA256"
```

For a dispute, use the **original private weekly report** retained inside the bank, or made available to its authorized reviewer, plus the three original CSVs. The input must be the revision identified in the statement. A later follow-up has a different digest.

```sh
telryn bank-report verify handoff/week-one --sha256 "$BANK_SHA256" \
  --source-report private/week-one/report.html --case C102 \
  --scores originals/scores.csv --register originals/register.csv \
  --actions originals/owner-events.csv
```

With those inputs, Telryn checks the original file digests, rejoins register and work events, compares sampled score rows, recomputes the statement and prints the disputed case locally. Use the source reference, revision and passage number to reopen the authorized transcript and check the human grade. Compare the cutoff and owner’s coverage attestation with the source systems. A matching digest establishes which evidence the issuer used. The bank still needs to establish who selected the sample, whether the source exports cover the queue and whether the policy judgment is sound. The private command output contains case evidence.

To practice, run `telryn demo --qa-passes --out runs/qa-passes`. It prints the digest for `runs/qa-passes/bank/report.html`. The first private report is in `runs/qa-passes/2026-10-05/` and its original inputs are in `runs/qa-passes/source-records/2026-10-05/`. C102 was passed, classified as no complaint, missed registration, then reached owner acceptance and completion. [Weekly score audit](qa-score-audit.md) · [Trust and provenance](trust.md)
