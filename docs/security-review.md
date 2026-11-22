# Telryn security review pack · v0.11.0

**What would leave the bank's network during a weekly audit?** Nothing needs to. The weekly command reads local files, draws the sample, accepts grades and writes a signed report. The desk can serve a local operator or run behind the firm's HTTPS identity gateway. Neither path makes outbound calls by default. Run the weekly job with networking disabled to enforce that boundary independently of the application. Pulling the image and verifying its publisher are deployment steps. IT can do those in a connected staging environment before importing the approved image into a restricted network.

This pack covers the open-source release for the complaints lead, the reviewer who sees a blinded packet and the IT team operating the image. The signed release includes an image for each Linux architecture, a software bill of materials for each image, and the procedure to verify the bytes IT receives. [Release files](https://github.com/davidahma/telryn/releases/tag/v0.11.0) · [Container deployment and verification](https://github.com/davidahma/telryn/blob/v0.11.0/docs/container.md)

Analysts can also install the CLI from [PyPI](https://pypi.org/project/telryn/0.11.0/). The wheel and Python source distribution carry PyPI publisher attestations from the repository’s `workflow.yml` on protected `main`. The PyPI publishing environment accepts only that branch. The workflow checks the signed GitHub source archive against the tagged commit before building the Python distributions. The GitHub source archive includes the full checkout. It shares a filename with the PyPI source distribution, which contains the files used to build and exercise the Python package. Use the SHA-256 digest and provenance from the channel where you obtained the artifact. The core Python install uses the standard library. The Bedrock extra adds the AWS SDK. [Check PyPI provenance](https://docs.pypi.org/attestations/consuming-attestations/)

## Data flow

```text
Sources under the firm's control
  Helpdesk exports, ordinary human QA, register, actions, approved policy
       |
       v
  Read-only input files -----> Weekly CLI or private browser desk
                                     |              |
  Team key + revision registry <-----+              +----> Blinded grading CSV
       |                             |                          |
       |                             |                 Reviewer opens source record
       |                             |                 and returns grades locally
       v                             v                          |
  Verify signed revision <---- Local HTML report <--------------+
                               Cited contacts, clocks, grades,
                               register matches, owners and gaps

Optional, explicitly enabled routes
  SSO gateway -------------> loopback desk --> authorized lead/reviewer operations
  Vendor API / customer S3 --> intake --> local source files
  Redacted transcript -----> chosen model endpoint --> citations and flags
  Selected IDs ------------> QA handoff adapter --> vendor assignments/tags
  Aggregate digest --------> firm's SMTP service --> approved recipient
  Synthetic messages ------> agent test tenant --> separate test register read
```

The desk shares the CLI's report logic. The lead report can identify a customer through a quoted passage, a reference or a linked case. The blinded CSV removes the screening answer and register findings, but still contains identifiers and the references reviewers need. Restrict both artifacts as customer records. [Evidence and retention](https://github.com/davidahma/telryn/blob/v0.11.0/docs/complaint-clocks.md)

[Hosted desk identity, deployment and acceptance checks](https://github.com/davidahma/telryn/blob/v0.11.0/docs/hosted-desk.md)

## What IT should verify

1. Obtain the release checksums and their Sigstore bundle independently of any copy supplied for internal deployment. Verify the expected GitHub Actions workflow identity and issuer, then check the hashes. Record the release tag, source commit and image digest on the approval ticket.
2. Read `image-digests.json` and the SPDX JSON for the architecture IT will deploy. The two SBOM files inventory the shipped images, including the runtime and operating-system packages. Scan the selected digest under the firm's current vulnerability policy. SBOMs describe installed components at build time. New advisories can change that decision later.
3. Use the image by digest or import the verified OCI archive. Mount source exports read-only. Give the process separate writable storage for runs and the private team trust store. Restrict local access to the operator or configure the firm's SSO gateway in front of the hosted loopback backend.
4. Exercise an offline synthetic audit and restore a signed audit with its original trust store from backup. If the team enables cloud extraction, repeat the review for that provider's endpoint, account, credentials, model, network route, logging and retention.

The pack archive contains this guide and the two platform inventories. The release workflow signs the checksum manifest that covers the pack, the portable image, the source and the example reports. Keep the signed manifest and bundle alongside the pack. Signing identifies what the project published. The firm's review determines whether to deploy it. [Release workflow](https://github.com/davidahma/telryn/blob/v0.11.0/.github/workflows/release-container.yml) · [Sigstore verification](https://docs.sigstore.dev/cosign/verifying/verify/)

## Vendor-risk questions

| Question | Answer for this release |
| --- | --- |
| Who hosts the data? | The deploying firm runs the application and controls its volumes, backups and access. Telryn requires no vendor account, hosted dashboard, telemetry service or support upload for a weekly audit. |
| Who can open the desk or grade a case? | Local mode trusts the operator holding the private session URL. Hosted mode requires a trusted HTTPS proxy and its authenticated staff and role assertions. A lead can manage reports. Reviewers can read blind packets and submit grades attributed to their gateway identity. Use separate trust stores and desk roots for separate access groups. File permissions and source-system permissions still govern exported copies and evidence. |
| Where are keys and secrets held? | The team creates and controls the HMAC signing key and revision registry outside its report directory. Anyone with the key and sufficient registry access can sign new revisions. Supply vendor or inference credentials only to the job that uses them, through the firm's secret or identity service. |
| How is report integrity checked? | The CLI or desk checks signed weekly report bytes with the team key and compares the revision against the registry. Opening HTML directly displays a static copy. The public release-signing identity and the firm's private audit-signing key serve separate purposes. |
| How is data encrypted? | The operator supplies encryption for source exports, mounted volumes, backups and their transfer. Weekly HTML and CSV are readable files. Cloud and vendor API calls use TLS. Local Ollama uses the configured local processing path. |
| What leaves during model extraction? | An explicitly selected cloud endpoint receives the extraction instructions and redacted passage text. Application redaction alone cannot guarantee anonymity. Bedrock also calls regional AWS STS to verify the account. A workload's credential provider may contact its identity service. The firm approves what may be sent, the endpoint and account, private routing, cloud logging and retention. A case can be scored from structured events with no model call. |
| Can the model write a complaint into the register? | Extraction produces candidate facts with source citations. The rules screen the case and people grade it. Register reconciliation reads supplied records. A separately authorized seed test submits synthetic complaints through a test agent and checks its test register. |
| Can the software run other code? | Python selectors and release-test adapters are trusted operator-supplied programs with the process's permissions. Review them before use and run them with only the mounts, credentials and network access they need. |
| How do we delete data? | Apply the firm's schedule to input copies, transcript and model checkpoints, citations, report directories, returned grades, recipients' copies, logs and backups. Keep the private key and revision registry with recoverable reports. Deleting either can prevent later verification. Destroying a container alone leaves mounted data intact. |
| Who patches the image? | Maintainers publish releases and instructions for private vulnerability reports. The deploying firm monitors advisories, reviews replacement digests and schedules rollout or rollback. Keep graded reports and their registry together when restoring. |
| What assurance or service commitment comes with the project? | The release provides reviewable source, tests, signatures and inventories under Apache-2.0. This pack supplies technical evidence. Audit certifications, regulatory approval, a support SLA and disaster-recovery targets require separate evidence or agreement. |

[Private vulnerability reporting](https://github.com/davidahma/telryn/security/advisories/new) · [Audit trust and recovery](https://github.com/davidahma/telryn/blob/v0.11.0/docs/trust.md) · [Network integrations](https://github.com/davidahma/telryn/blob/v0.11.0/docs/integrations.md) · [Transcript processing](https://github.com/davidahma/telryn/blob/v0.11.0/docs/transcripts.md)

A scored weekly report retains the sampled vendor decisions and human findings. The bank export reads that verified private report and emits aggregate results, declared public labels and file digests. Its recipient can pin the digest received through an authenticated channel and replay the result locally with authorized source records. The exporter performs no delivery. Treat small counts, queue names, scorecard names and any issued private dispute material according to the bank’s access policy. [Bank handoff and disclosure fields](https://github.com/davidahma/telryn/blob/v0.11.0/docs/bank-report.md)
