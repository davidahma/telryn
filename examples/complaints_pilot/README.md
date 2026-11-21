# Rehearse a two-week complaints check

Mira asks a payments bot why it charged her twice. Fin refunds the debit and closes the chat. Two weeks later she calls. The agent records a query. On 6 October she calls again. Could the complaints lead show a reviewer when the clock started, whether a complaint was registered and who still needs to act?

With the PyPI package installed, run the rehearsal in an empty working directory. The command prints a summary for your draw. Each dated output folder contains a signed local report and one blinded grading packet. The top-level `comparison.json` holds both weeks’ selections, effort and owner-accepted episodes.

```sh
telryn demo --complaints --out runs/complaints-pilot
```

### Follow a miss through to action

1. Open `2026-10-05/report.html` inside the output directory. Under **Complaint registration and owner action**, expand **C102**. On 6 October at 13:42 UTC the customer told Fin, “Please open a complaint about this duplicate charge.” The reviewers graded it as requiring registration. The policy worksheet put the deadline at 17:00 UTC on 7 October.
2. At the 12 October 08:00 UTC cutoff, the register export covered the case and had no entry under `support/C102`. There was no owner event by then. At 11:00, event **A12** recorded the case manager’s acceptance for episode **P-012**. At 12:00, **R12** recorded registration and **A13** recorded completion. Those are separate source records. They establish when the work happened. The records do not establish that Telryn accelerated it. [Conversation](conversations.csv) · [Register](register.csv) · [Owner events](owner-events.csv) · [Episode index](owner-episodes.csv)
3. Open `2026-10-12/report.html` and expand **C204**. The customer asked Fin to register a service complaint on 13 October at 13:18 UTC. It was still missing at 08:00 UTC on 19 October. **A24** recorded acceptance at 12:00 that day. **R24** and **A25** recorded registration and completion at 09:00 the next morning. To verify either report, use the printed `TELRYN_TRUST_DIR` command with that week’s report path. [Register](register.csv) · [Owner events](owner-events.csv)

Then check **C108** in the first report. It links the October contact to the August Fin chat and phone call through the same customer and payment references. The clock starts on 4 August at *“Even with a refund I want to know how this happened.”* This fictional firm's policy makes registration due on 7 August and a final response due on 29 September. At the 12 October cutoff there is no register entry or cited final response. On 12 October at 11:00 UTC the case manager receives a request. The case awaits acceptance, with a review due on 16 October. The two August contacts supply evidence for the October case and count as one episode. [Dated passages](prior-contacts.json) · [Example clock policy](clock-policy.json) · [Owner history](owner-events.csv)

## What the comparison found

Both methods could select five cases per week and spend 80 total staff minutes. Both saw the same conversations, linked history, refund notes, completed human reviews, register and owner ledger as of each cutoff. The native SQL ranks repeat contacts, hardship, ombudsman references, investigation requests and unresolved dissatisfaction after a refund. Telryn applies a transparent screen to the source evidence, then randomly selects four flagged cases and one unflagged case. A neutral question about the ombudsman's address can trigger either screen. The human grade decides whether it meets the firm's policy. [Protocol](protocol.toml) · [Native query](native.sql) · [Telryn screen](signals.py)

| First recorded draw | Native accepted episodes / staff minutes | Telryn accepted episodes / staff minutes |
| --- | ---: | ---: |
| Week of 5 October | 3 / 62 | 3 / 71 |
| Week of 12 October | 2 / 63 | 3 / 60 |
| Two weeks | **5 / 125** | **6 / 131** |

Five accepted episodes overlapped. On this draw Telryn selected C207, where a refund left the customer unhappy. SQL also recognized the case, but higher-ranked cases and a tie on priority filled its five slots. Staged operator help fell from eight minutes to three. The comparison charges both methods for every selected case's first and second review, including overlaps. Recorded time to action begins at the common cutoff. A shared case has one observed action time. [Staged grades and review time](answers.csv) · [Staff time by activity](staff-minutes.csv) · [Accepted episode identities](owner-episodes.csv)

**The first draw was unusually favorable to Telryn.** For the six authored episodes with a later acceptance, there are 1,350 equally likely two-week selections at the frozen sampling rates. Telryn finds more accepted episodes than the fixed SQL in 54 selections (4%), the same number in 324 (24%) and fewer in 972 (72%). The program calculates this distribution after grading. It leaves the issued assignments in place. A different team key can produce a different first draw. Neither customer benefit nor staff savings has been measured. The earlier demonstration used a smaller fixture and a narrower SQL query, so its totals measure a different comparison.

## Read the weekly statement

For the first recorded draw, the week of 5 October has six distinct shortlisted conversations. At cutoff the register lacked a required entry for one verified episode, Mira’s, and for three other conversations without a verified episode link. One of the three conversations had an entry by 14 October. Two still lacked an entry, as did Mira’s episode. Seven days had elapsed since C108 closed. The single later registration was recorded 142 hours after that conversation closed. The statement names the gaps in source coverage, including partial phone coverage and uncovered email, disputes and social sources. Its estimates apply to the combined shortlists only. A queue-wide estimate needs a probability sample of the full unreviewed queue. [Channel inventory](channels.csv)

Each week the program freezes both selections before opening the answers. Simulated reviewers grade one mixed packet. The signed report reconciles those grades with the dated register and owner actions. C203 has a completion event without a recorded acceptance and stays outside the accepted-episode count. Keep the two signed reports, the rehearsal trust store and `comparison.json`. The comparison is a synthetic worksheet. Each signed report includes the commitment to that week’s frozen selection. [Conversation export](conversations.csv) · [Dated register](register.csv) · [Team onboarding minutes](onboarding.csv) · [Field protocol and decision criteria](../../docs/evaluation.md#two-weeks-on-one-complaints-queue)
