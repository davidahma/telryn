# Read a Sierra export from customer-owned S3

`integrations pull-sierra` reads one immutable weekly conversation export through the AWS CLI. It produces the same `population.csv` used by the [weekly audit](weekly-qa.md). Sierra supports customer export through its export API, Amazon EventBridge and Google Cloud Pub/Sub. The Sierra payload definition and authentication details are available to customers through its documentation portal. This connector starts at the S3 object your team publishes. [Sierra data export](https://sierra.ai/blog/your-agent-laid-bare-and-why-it-matters) · [Sierra customer documentation](https://docs.sierra.ai/)

## Prepare the delivery

Use your approved Sierra feed to produce a closed-conversation roster for the audit week. For an EventBridge feed, a customer-operated consumer can consolidate complete conversation events into this roster and write it to a versioned S3 bucket. Reconcile delayed events and repeated deliveries against Sierra, then record the expected number of distinct eligible conversations independently of the CSV. Supply the ordinary human QA review ledger separately. [EventBridge targets](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-targets.html)

Publish a metadata-only UTF-8 CSV. One row identifies a conversation and the evidence revision a reviewer can retrieve in your workspace. Keep messages, names and model prompts in your source system. The optional flag records a versioned customer's screen result. For example, this entirely synthetic delivery contains two closed cases.

```csv
conversation_id,revision,evidence_ref,flag
case-100,v4,QA-EVIDENCE-100-v4,true
case-101,v2,QA-EVIDENCE-101-v2,false
```

## Read the frozen week

Install AWS CLI v2 and configure the usual AWS credentials and region. Scope `s3:GetObjectVersion` to the approved delivery. A customer-managed KMS key can also require `kms:Decrypt`. The command uses the same version ID and expected bucket owner for the metadata check and the download. It rejects a null version, additional CSV columns, duplicate conversation IDs, an unexpected count, or a mismatched optional SHA-256. A single file can contain up to 64 MiB of metadata. [S3 GetObject](https://docs.aws.amazon.com/cli/latest/reference/s3api/get-object.html) · [S3 HeadObject](https://docs.aws.amazon.com/cli/latest/reference/s3api/head-object.html)

```sh
python3 -m telryn integrations pull-sierra \
  --workspace fintech-uk \
  --bucket company-qa-deliveries --bucket-owner 123456789012 \
  --key sierra/2026-11-16/closed.csv --version-id "$SIERRA_OBJECT_VERSION" \
  --expected-count 2 --flag-column flag --out runs/sierra-population.csv
```

Use `--sha256` when the delivery manifest records a digest. `--id-column`, `--revision-column` and `--evidence-column` map different header names. With no `--flag-column`, omit that column from the delivery and set `screen_version = "none"` in the weekly plan. IDs in the output include the source and workspace, for example `sierra:fintech-uk:case-100`. Use the same IDs in `reviewed.csv`.

The command prints the downloaded object's SHA-256 for reconciliation. It removes the temporary input after conversion and writes the private output once. Keep the source version, manifest and corresponding evidence under your team's retention policy. To qualify a deployment, compare one complete week of output with the Sierra-side roster and open a sampled evidence revision as a reviewer. Repository tests exercise the command with synthetic object responses. A real AWS CLI signed request was also checked against a local S3-compatible fixture. Customer tenant access and end-to-end upstream delivery still require qualification in your own environment.
