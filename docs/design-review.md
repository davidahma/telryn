# Design review · weekly evidence and the next decision

The weekly report serves three people. A reviewer needs the next case and the evidence as it stood at closure. A QA lead needs to distinguish serious cases from findings that need new follow-up. A queue owner needs to know whether last week's evidence still justifies a shadow trial. The v0.6 review tested the points where work moves between those people.

| Finding in v0.5 | Consequence | Change and acceptance case |
| --- | --- | --- |
| A saved comparison could continue to pass a shadow gate after a development grade was disputed. | A job acting on the saved comparison could authorize work that a fresh replay would refuse. | Authenticate the source and current comparison for operational use. Preserve the frozen verdict for inspection. Test a correction after publication, a stale copy, a later trial and a historical read. |
| A process could stop after writing a correction to disk while the registry still authorized the previous revision. | A second operator working from a copy could proceed using superseded evidence. | Commit the new head and a recovery record together before replacing the visible file. Test interruptions before and after replacement for a weekly source and a held-out comparison, including a second copy. |
| One graded positive and one graded negative could display 100% precision, recall and agreement. | A lead could mistake two correct predictions for evidence of reliable queue coverage. | Carry the sampled count bounds into each metric and show its denominator. In a residual queue of 50 flagged and 50 unflagged cases, the same two grades give each metric a 2%–100% range. |
| The headline estimated serious cases outside ordinary QA. Prior handling appeared only as observed sample counts. | The lead could mistake all serious cases for work that no other control had caught. | Estimate the previously undiscovered count separately. Test known, newly discovered, and unresolved dispositions. A census with unknown prior handling still has uncertainty about new findings. |
| The only download contained the entire original sample. An unreadable saved report disappeared from desk history. | A second shift could repeat completed work or assume an entire audit had been lost. | Derive a blinded packet for outstanding first grades. Keep disputes and insufficient evidence visible separately. On a trust mismatch, show the directory identifier and recovery guidance. |

The implementation follows the same dependency order as the audit itself. Derive estimates and work queues from the frozen sample and saved grades. Bind downstream actions to the evidence revision. Expose those decisions through the CLI, desk and report. Validate each handoff across a partial return, correction and restart. [Architecture](architecture.md)

## What changed in the v0.6.1 review

| Where the error reached the lead | How the audit now handles it |
| --- | --- |
| Two reviewers agreed a case was serious but disagreed about whether another control had caught it. The disagreement removed the case from the confirmed serious count. | The severity estimate keeps their agreed answer. Only the disputed part stays open. The CLI can recover that agreement from the votes in an existing signed report. [Statistical method](statistics.md) |
| A preview could arrive after the lead changed the budget. Moving to another dispute could leave the previous case's choice and rationale in the form. | An edit invalidates the pending preview. Changing disputes clears the draft and requires a fresh choice. The browser test follows two disputes through adjudication. [Weekly desk](weekly-qa.md) |
| A source export could misidentify a speaker, include an unsent email, or miscount records at a weekly boundary. | The adapters check author evidence, message status and page membership before passing a cohort to the screen. [Connector coverage](integrations.md) · [Transcript sources](transcripts.md) |
| A model interruption could save results before the installed weights had been checked. A retry could reuse those results under the original model's name. | A private checkpoint commits groups of results after checking the model digest. Restarting reuses verified work. [Transcript checkpoints](transcripts.md) |

The numerical review compared the exact finite-population intervals with direct enumeration on small populations and an independent hypergeometric implementation on larger populations. Browser acceptance used Chrome. Connector tests used synthetic vendor responses. A separate smoke test ran the two bundled transcripts through local Ollama, returned human grades and reopened the signed weekly report. These checks establish behavior on the tested inputs. Customer permissions, source history and model accuracy on a team's own rubric still need to be checked in that team's environment.

## What the release can establish

The public Ubuntu corpus provides an independent check on the count of human-annotated friction cases. It contains no record of which cases another operational control already knew about. A census can therefore give an exact friction count while leaving the newly discovered count unresolved. The fintech example exercises a later held-out comparison and shadow trial with synthetic data. [Public data and attribution](../examples/public_demo/NOTICE.md) · [Fintech example](../examples/fintech/README.md)

The outstanding product question is whether a team finds enough previously unknown cases to justify a second week of review. That requires a QA lead's own roster, recorded review minutes, prior-handling evidence and actual follow-up. Comparing percentages across weeks also requires compatible policies, source coverage and queue composition. The pilot guide describes what to collect before interpreting such a change. [Pilot evaluation](evaluation.md)
