# Security policy

Report a vulnerability through
[GitHub private vulnerability reporting](https://github.com/davidahma/telryn/security/advisories/new).
Include the Telryn version, impact, and a synthetic reproduction. Keep real
customer artifacts and credentials out of public issues.

For deployment review, use the [security review pack](docs/security-review.md) and [signed image and SBOM instructions](docs/container.md).

Use the latest published release for security fixes. Telryn executes selector
source with your local Python process's permissions. Run selectors you trust,
keep reports and packets in access-controlled locations, and open referenced
evidence under your existing source-system permissions. See the
[comparison reference](docs/comparison.md) for the data and retention boundaries.

Telryn creates new reports and grading packets with owner-only permissions on
Linux and macOS. Protect exports, existing packets, and any copies saved by
reviewers under the permissions of your workspace. Weekly reports issued from v0.5 use HMAC-SHA-256 over the complete signed report content and check its revision against the team registry. Keep the team key and registry in access-controlled storage separate from the report. Holders of the shared key can sign. Opening an HTML file directly displays its contents without authenticating them. Older unsigned reports and comparison checksums detect accidental corruption. [Trust model and migration](docs/trust.md)

For a proposal-backed shadow decision, use the authenticated CLI gate. It checks the latest saved comparison and the weekly report revision that justified its rule. Static comparison HTML remains a historical display. Keep the private report directory and team registry together in backups. [Current decision checks](docs/comparison.md#check-a-weekly-proposal-before-running-a-trial)

The default weekly desk uses a private loopback URL and checks the origin of submissions. Keep the URL private and stop the process at the end of the session. A shared deployment can leave the backend on loopback behind the firm's HTTPS and SSO gateway. The desk verifies a private proxy credential, maps authenticated staff to lead or reviewer operations and records the authenticated reviewer on grade changes. Both roles can see blind packets for all weeks in that desk's chosen directory. Use separate desks for teams with different case-access boundaries. [Hosted desk and proxy configuration](docs/hosted-desk.md)

Live connector reads can receive sensitive vendor responses in memory. The saved population contains identifiers, revisions, evidence references and flags. Give connectors least-privilege credentials scoped to the intended tenant. Local review uses no vendor credentials. Source tagging and aggregate SMTP delivery require explicit operator commands. The optional transcript extractor sends redacted passages to the selected local Ollama service, Azure OpenAI deployment or regional Bedrock model. Approve the account, identity, endpoint, model, data handling and network route before enabling cloud inference. Configure upstream deidentification and the supplied redactions, restrict access to transcripts, citations and checkpoint files, and include those files in retention and deletion policies. Structured-event screening runs without a model. Store credentials outside plans, exports and version control. [Integration access and handoff](docs/integrations.md) · [Weekly retention](docs/weekly-qa.md#keep-weekly-reports-readable)

A complaints attachment keeps matched register entries, owner names, work references and timestamps inside the signed lead report. Raw exports and manual review ledgers may cover a much larger population. Store both with the team's approved access and retention controls. The blinded grading packet and aggregate email omit the attached register and work records. The public complaints example uses synthetic identities. [Complaint evidence and updates](docs/complaint-records.md)

Episode identifiers are keyed pseudonyms. Linked contacts and quoted passages can still identify customers. Restrict the lead report and its input files accordingly. The optional agent release check executes operator-supplied adapters with the CI account's permissions. Install adapter code from a trusted source outside writable export directories, limit the sender to a test tenant and the register reader to read-only access, and keep release receipts private. [Agent test boundary](docs/agent-release.md)
