# Telryn

**Was this customer’s complaint handled under our policy?** Telryn helps the complaints lead at a lender or payments company answer that question across human and AI support. It joins conversations to the complaints register, checks the firm’s clocks and follows what the case owner did. The lead gets one local, signed report. Reviewers get one blinded grading packet.

In the synthetic rehearsal, Mira tells Fin about a duplicate charge on 4 August. “Even with a refund I want to know how this happened.” Fin refunds her and closes the chat. She calls on 18 August. The agent records a query. When her case comes up in October, the report traces it back to the August passage. Under the example firm’s policy, its eight-week final-response deadline was 29 September. The registration check uses a separate clock. The register and action history show what the case manager still owes her. Refunding the charge settled only part of the complaint.

[Download Telryn](https://github.com/davidahma/telryn/releases/latest) · [Open the synthetic complaints report](https://github.com/davidahma/telryn/releases/download/v0.9.1/synthetic-complaints-audit.html) · [Apache-2.0](LICENSE) · [![CI](https://github.com/davidahma/telryn/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/davidahma/telryn/actions/workflows/ci.yml)

## Deploy inside your network

IT can run the desk and weekly job from the signed Linux container on amd64 or arm64. Start with the offline weekly job. It reads mounted exports and writes the report and packet on storage you control. On Windows, use a Linux container engine and follow the volume setup in the deployment guide.

The security review pack includes a data-flow diagram, answers to vendor-risk questions and an SPDX inventory for each architecture. The release also includes a portable image for teams that import approved software into a private registry. [Container deployment and signature verification](docs/container.md) · [Read the security review](docs/security-review.md) · [Download the review pack with both SBOMs](https://github.com/davidahma/telryn/releases/download/v0.9.1/telryn-security-review.tar.gz)

## Follow one complaint through two weeks of QA

Use CPython 3.12–3.15 with SQLite on Linux or macOS. The included examples run offline.

```sh
git clone --depth 1 https://github.com/davidahma/telryn.git
cd telryn
python3 -m examples.complaints_pilot --out runs/complaints-pilot
```

Open **`runs/complaints-pilot/2026-10-05/report.html`**. Start with the channels the team could see and the week’s control statement. Follow the case references to inspect contacts, cited passages, deadlines, dated register evidence and owner action. A missing entry supports a finding only if the records cover the relevant period. A shared identifier or incomplete export can leave the case unresolved.

The rehearsal gives Telryn and a saved SQL search the same records, selection capacity and staff budget. Both submit their picks before reviewers grade the combined shortlist. Across all 1,350 possible two-week draws on this fixture, Telryn finds more accepted episodes in 54, ties in 324 and finds fewer in 972. The saved search finds at least as many in **96%** of draws. Your run shows which episodes each method found and what each cost. These are synthetic cases with assigned times. The field test repeats the comparison with the team’s usual tools and measures actual work, earlier action and whether the lead pays to run it again. [Rehearsal and results](examples/complaints_pilot/README.md) · [Field protocol](docs/evaluation.md#two-weeks-on-one-complaints-queue)

## Set up the weekly check

The complaints lead owns the decision, the register evidence and the follow-up. Each team supplies a different part of the record.

| Team | What it connects |
| --- | --- |
| Support operations | Helpdesk conversations, dated closures and channel coverage |
| AI agent owner | Bot configuration, prompt revision and handoff logs |
| Complaints | Dated register exports, case owners and subsequent actions |
| Second line | Approved policy, clock clauses, holidays and classification taxonomy |
| QA | Ordinary human review ledger and independent grades |
| Contact centre | Calls, transcript revisions and the customer and matter references used to link them |

Start from a versioned FCA DISP, US Regulation E or CFPB complaint-handling pack, or supply the firm’s own rubric and taxonomy. The policy owner chooses which clauses apply and approves the operating calendar. [Configure policy and rubric packs](docs/policy-packs.md)

Record which channels the team can read, who owns the gaps and when they will review them. Count onboarding time, the delay from closure to registration and the age of open follow-ups. The checklist covers chat, calls, email, disputes and social channels. [Evaluation guide](docs/evaluation.md) · [Evidence formats](docs/complaint-records.md)

Start the private browser desk on the lead’s machine.

```sh
python3 -m telryn desk --out runs/desk
```

1. **Plan the work.** Import this week’s queue and the ledger of ordinary human reviews. Set a reviewer budget, including second reviews and disputes. Preview the chance of detecting a problem before issuing the sample.
2. **Review the evidence.** Use the blinded packet to grade selected conversations. Record the complaint decision, classification, vulnerability and redress separately when the control profile is configured. Return grades, obtain second reviews and adjudicate disagreements. The report preserves the history.
3. **Check what happened.** Attach approved policy, contact, register and action evidence. Read the weekly statement alongside its coverage gaps. Use the signed report to investigate a count or trace a case. The lead can also send an aggregate digest through the team’s SMTP service.

A weekly plan runs from the CLI for scheduled work. The desk and CLI verify the report against the team’s private signing key and revision registry. Back them up together. An HTML file opened directly is a static copy. [Weekly operating guide](docs/weekly-qa.md) · [Trust and migration](docs/trust.md) · [Security](SECURITY.md)

## Test a changed AI support agent before release

The lead supplies synthetic complaint phrases and the registration deadlines. The AI agent owner submits them to the test deployment after changing the prompt, policy or handoff. Telryn reads the test register separately and checks that a unique entry arrived on time for every case. A missing entry blocks the release. A pending or unverifiable result also keeps the gate closed.

```sh
python3 -m examples.agent_release
```

The first simulated agent drops one complaint. The next passes all four. To gate your own deployment, run `telryn seed` with a test sender and a read-only register adapter. [CI command and adapter contract](docs/agent-release.md)

## What the numbers cover

A sampled conversation and a customer’s complaint episode answer different questions. Two contacts about one charge can support one episode. A signed report can establish a missed registration for a graded case when it has sufficient policy and register evidence. A population estimate also needs a probability sample covering that population. A head-to-head shortlist describes the cases the two methods picked.

Even a clean sample can be weak evidence. If exactly 25 of 5,000 unreviewed conversations qualify and reviewers recognize each one, a uniform random sample of 50 has a **22.3%** chance of finding one. Reaching 95% takes 564 first grades. The planner exposes these assumptions before the team spends its hours. [Sampling and uncertainty](docs/statistics.md)

## Other uses and integrations

- **Helpdesks and AI support.** Pull completions from Intercom (including Fin), Zendesk, Salesforce Service Cloud or Freshdesk. Use a lifecycle export where a provider lacks dated resolution history. Import a versioned Sierra export from customer-owned S3 or map an approved Fin, Sierra or Decagon CSV. Hand work back to existing QA tools. [Integration coverage](docs/integrations.md)
- **Transcript screening.** Select local Ollama, an Azure OpenAI deployment or regional Amazon Bedrock in an approved account to extract signals from authorized transcript revisions. Each definite answer requires a supporting passage. A deterministic policy flags cases for review. Teams with structured events can use them directly. [Transcript intake and citations](docs/transcripts.md)
- **Other serious support failures.** Run `python3 -m examples.fintech --out runs/fintech` for an account-compromise audit. Replay a proposed queue change against staffing and serious-loss checks before a trial. [Fintech example](examples/fintech/README.md) · [Comparison and exit codes](docs/comparison.md)
- **Public benchmark.** The Ubuntu example uses 200 real technical-support dialogues, researchers’ human labels and archived GPT-4o flags. Ordinary QA selection is simulated. Run `python3 -m examples.public_demo --census --out runs/public-census` to verify the 86 human-labeled friction cases among the 160 left for audit. [Dataset and reproduction](examples/public_demo/NOTICE.md)

Run `python3 -m telryn qualify` from a checkout or unpacked release. [Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md)
