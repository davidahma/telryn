# Security policy

Report a vulnerability through
[GitHub private vulnerability reporting](https://github.com/davidahma/telryn/security/advisories/new).
Include the Telryn version, impact, and a synthetic reproduction. Keep real
customer artifacts and credentials out of public issues.

Use the latest published release for security fixes. Telryn executes selector
source with your local Python process's permissions. Run selectors you trust,
keep reports and packets in access-controlled locations, and open referenced
evidence under your existing source-system permissions. See the
[comparison reference](docs/comparison.md) for the data and retention boundaries.

Telryn creates new reports and grading packets with owner-only permissions on
Linux and macOS. Protect exports, existing packets, and any copies saved by
reviewers under the permissions of your workspace. Weekly reports issued from v0.5 use HMAC-SHA-256 over the complete signed report content and check its revision against the team registry. Keep the team key and registry in access-controlled storage separate from the report. Holders of the shared key can sign. Opening an HTML file directly displays its contents without authenticating them. Older unsigned reports and comparison checksums detect accidental corruption. [Trust model and migration](docs/trust.md)

The weekly desk binds to loopback and uses a session-specific access URL and a same-origin check for submissions. Use it on the QA lead's machine. Keep that URL private. It serves run files from the selected directory. Stop the process when the session ends.

Live connector reads can receive sensitive vendor responses in memory. The saved population contains identifiers, revisions, evidence references and flags. Give connectors least-privilege credentials scoped to the intended tenant. Local review uses no vendor credentials. Source tagging and aggregate SMTP delivery require explicit operator commands. The optional transcript extractor reads authorized conversation text on the customer’s local Ollama service. Configure upstream deidentification and the supplied redactions, restrict access to transcripts, citations and checkpoint files, and include those files in retention and deletion policies. Structured-event screening runs without a model. Store credentials outside plans, exports and version control. [Integration access and handoff](docs/integrations.md) · [Weekly retention](docs/weekly-qa.md#keep-weekly-reports-readable)
