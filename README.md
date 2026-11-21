# Telryn

Telryn helps a complaints lead at a lender or payments company find closed support conversations that should have reached the complaints register. It brings human and AI support contacts, policy deadlines, register entries and owner actions into one local report. Reviewers get a separate, blinded grading packet.

In the synthetic rehearsal, Mira tells the Fin agent about a duplicate charge on 4 August. “Even with a refund I want to know how this happened.” Fin refunds her and closes the chat. Two weeks later she calls, and an agent records a query. In October the lead can trace both contacts to the same charge, read Mira’s original words and see what the case manager still owes her. Under the example firm’s policy, the eight-week final-response deadline was 29 September. Registration has its own clock. [Open the worked report](https://github.com/davidahma/telryn/releases/download/v0.9.3/synthetic-complaints-audit.html)

## Install

For an analyst on Linux or macOS, use pipx with CPython 3.12–3.15 and SQLite. The base CLI has no third-party runtime dependencies. Run the two-week rehearsal from any directory. Choose a new output directory each time.

```sh
pipx install telryn
telryn demo --complaints --out runs/complaints-pilot
```

The demo signs its reports in a separate rehearsal trust store and prints the verification command. Open `runs/complaints-pilot/2026-10-05/report.html` and find case **C108**. The report lists which channels the team could see. An incomplete register or an ambiguous match leaves a case unresolved. [PyPI package](https://pypi.org/project/telryn/) · [Rehearsal inputs and results](https://github.com/davidahma/telryn/blob/v0.9.3/examples/complaints_pilot/README.md)

For a managed deployment, IT can import the signed Linux image for amd64 or arm64. Windows users can run it through Docker Desktop with Linux containers. The weekly job runs with networking disabled. The security review pack includes the data flow, outbound connections, storage responsibilities and an SPDX software inventory for each architecture. [Container instructions](https://github.com/davidahma/telryn/blob/v0.9.3/docs/container.md) · [Download the security review pack](https://github.com/davidahma/telryn/releases/download/v0.9.3/telryn-security-review.tar.gz)

## Run a week with your team

Start the private browser desk with `telryn desk --out runs/desk`. For a scheduled job, use `telryn weekly /path/to/week.toml --out /path/to/new-run`. Keep the team signing key and revision registry in persistent private storage. [Weekly operating guide and scheduler example](https://github.com/davidahma/telryn/blob/v0.9.3/docs/weekly-qa.md#schedule-a-weekly-run)

1. **Set the scope.** Support operations supplies closed conversations. QA supplies completed human reviews. The contact centre and AI agent owner supply transcript references and handoff records. Complaints supplies the dated register and action history. Record missing channels and who owns each gap.
2. **Issue the sample.** The policy owner approves the rubric, clock rules and calendar. Choose a versioned FCA DISP, US Regulation E or CFPB Company Portal starter, or use the firm’s own policy. Preview what the grading budget can detect. Telryn draws from both flagged and unflagged conversations that ordinary QA left unreviewed. [Policy packs](https://github.com/davidahma/telryn/blob/v0.9.3/docs/policy-packs.md)
3. **Grade and follow up.** Reviewers decide whether a case qualifies before seeing the register finding. Record second reviews and settle disagreements. The lead can verify the signed report, follow a case to its source passages, and check the deadline, register coverage and owner action. [Evidence formats](https://github.com/davidahma/telryn/blob/v0.9.3/docs/complaint-records.md) · [Report verification](https://github.com/davidahma/telryn/blob/v0.9.3/docs/trust.md)

## What the evidence supports

A clean sample can miss rare failures. If exactly 25 of 5,000 unreviewed conversations qualify and reviewers recognize every one, a uniform random sample of 50 has a 22.3% chance of finding at least one. Reaching 95% takes 564 first grades. [Sampling assumptions](https://github.com/davidahma/telryn/blob/v0.9.3/docs/statistics.md)

The rehearsal compares Telryn with a saved SQL search using the same records and staff budget. Across its 1,350 possible two-week draws, the saved search finds at least as many owner-accepted episodes in 96% of draws. The cases and staff times are synthetic. The field protocol measures whether either method brings action forward or saves staff time on a real queue. [Field protocol](https://github.com/davidahma/telryn/blob/v0.9.3/docs/evaluation.md#two-weeks-on-one-complaints-queue)

## AI agents and integrations

- **Before an agent release**, send synthetic complaints through the test agent and check its test register independently. A missing, late or unverifiable entry keeps the release gate closed. [Agent test and adapter contract](https://github.com/davidahma/telryn/blob/v0.9.3/docs/agent-release.md)
- **For transcript screening**, use structured signals or select Ollama, Azure OpenAI or regional Amazon Bedrock. Each definite extracted fact needs a cited passage. Bedrock users can install the AWS SDK with `pipx install 'telryn[bedrock]'`. [Model setup and data handling](https://github.com/davidahma/telryn/blob/v0.9.3/docs/transcripts.md)
- **For intake and handoff**, use helpdesk connectors or approved exports. The integration guide lists the evidence each provider exposes, including when the team must supply a lifecycle ledger. [Supported integrations](https://github.com/davidahma/telryn/blob/v0.9.3/docs/integrations.md)

Run `telryn qualify` to exercise the bundled checks. [Architecture](https://github.com/davidahma/telryn/blob/v0.9.3/docs/architecture.md) · [Public support dataset](https://github.com/davidahma/telryn/blob/v0.9.3/examples/public_demo/NOTICE.md) · [Contributing](https://github.com/davidahma/telryn/blob/v0.9.3/CONTRIBUTING.md) · [Apache-2.0](https://github.com/davidahma/telryn/blob/v0.9.3/LICENSE)
