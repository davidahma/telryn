# Issue vendor conduct evidence for a bank

The attestation binds three things a vendor-risk reviewer can use together. The test seed receipt records whether a particular agent release registered the synthetic test cases on time. The quarterly calibration shows results for the approved review queue, including missing weeks. The bank statement records one week’s deadline findings and follow-up in more detail.

The vendor and the bank should agree on the issuer key, test suite, run ID, quarter and profile. The profile links the tested deployment to the operational queue. The suite pins the test workspace, agent revision, policy revision, and handoff revision. The package records the vendor's evidence and deployment declaration. The bank decides whether those materials substantiate its control and whether approvals or independent tests are still due.

## Issue a package

Use OpenSSH `ssh-keygen` with Ed25519 support. Keep the private signing key separate from the shared weekly audit store. An issuer running Telryn from the minimal image can use the PyPI CLI on its approved signing host. Use a passphrase-free service key provisioned for the isolated signer, and retain it under the issuer’s key-management policy. The bank obtains the public key through a separate approved channel.

Save this profile as `profile.json` and select the bank’s control identifiers.

```json
{
  "schema": 1,
  "vendor": "Test agent vendor",
  "customer": "Test payments bank",
  "source_queue": "Payments support",
  "bank_queue": "Synthetic payments support",
  "scorecard": "payments-support",
  "scorecard_version": "1.0",
  "evidence": "synthetic",
  "customer_controls": {
    "seed-receipt.json": ["QA-RELEASE"],
    "calibration.html": ["QA-MONITOR"],
    "bank-statement.html": ["CONDUCT-FOLLOWUP"]
  }
}
```

Choose `operational` for a real audit. The bank statement and weekly series must agree with that choice. The release suite always targets a test deployment. The bank statement must replay from a verified report in the selected quarter. Review the output for permitted recipients. The receipt contains synthetic test workspace and register references. The calibration preserves its cohort counts, approved queue labels and missing weeks.

```sh
telryn attestation issue --profile profile.json --suite approved-suite.json \
  --run-id approved-build-42 --quarter 2026-Q4 --key issuer \
  --agent-adapter /approved/agent-adapter.py --register-adapter csv-register \
  --bank private/bank-statement.html --out handoff/vendor-attestation \
  private/weeks/*/report.html
```

The issuer reads weekly reports using the original private team trust store and runs the pinned [test seed](agent-release.md) with the selected adapters. It stops if the source revision has changed, the policy needs re-review, the statement cannot be reproduced, or a required test fails. Validation of report inputs and a local signing-key check occur before the seed runs. Use the same run ID when retrying the same suite. Issuing invokes the test adapters. Those adapters may use an explicitly approved test endpoint.

## Verify evidence as the bank

```sh
telryn attestation verify handoff/vendor-attestation \
  --profile bank-approved-profile.json --suite bank-approved-suite.json \
  --run-id approved-build-42 --quarter 2026-Q4 --key vendor-approved.pub
```

The receiver checks the Ed25519 signature with its pinned key, then checks the three file digests, the pinned deployment and test outcomes, and the bank statement’s membership in the signed series. Verification works offline with the approved suite and profile. The receiver does not need the private issuer or audit key. Use the signed weekly digest and the [bank replay workflow](bank-report.md) to inspect a disputed result using private original evidence. A signature binds the issuer to the supplied evidence. The bank's approval of the key, scope, recency and evidence sources establishes its trust in the claims.

## Attach evidence to existing controls

| Evidence file | AIUC-1 reference | What the file can contribute |
| --- | --- | --- |
| `seed-receipt.json` | E004.1 | Test receipt for the accountable lead's approval record |
| `calibration.html` | E008 | Results of periodic review for the approved queue, scorecard and time period |
| `bank-statement.html` | E008 | Dated complaint registration and aggregate disposition evidence for the reviewed week |

The manifest exposes those AIUC-1 references alongside the bank’s own control IDs. Use the mapping to attach the verified file to the relevant evidence slot in the bank’s vendor-risk process or Drata. The pack records supporting conduct evidence. AIUC-1 certification and control assessment belong to the accredited auditor. [AIUC-1 E004](https://standard.aiuc-1.com/accountability/assign-accountability) · [AIUC-1 E008](https://standard.aiuc-1.com/accountability/review-internal-processes) · [Drata control evidence](https://developers.drata.com/openapi/reference/v1/tag/Controls/)
