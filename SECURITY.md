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
reviewers under the permissions of your workspace. A saved report's checksum
detects corruption. Verify its origin before using it for a decision.
