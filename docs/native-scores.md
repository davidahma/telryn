# Import scores from the QA tool

In the desk open **Audit an external QA scorecard**, choose the export format, then select the original UTF-8 export. Enter the scorecard or evaluator ID, its deployed version, cutoff and scope. For a Zendesk or MaestroQA percentage, enter the firm's passing threshold. A score exactly at the threshold passes. The bank can replay the audit against the same unmodified file.

| Desk format | Original record | What Telryn checks |
| --- | --- | --- |
| Zendesk QA | The Export API's JSON response with conversations, reviews and pagination. Use one connection, workspace and scorecard. | Links the ticket's external ID to the reviewed conversation, reads the scorecard ID, review ID, numeric score, creation and last-update times. [Zendesk Export API](https://developer.zendesk.com/documentation/qa/exporting-data/) |
| MaestroQA | `Rubric_scores.csv` from Export Raw Scores for a single rubric. | Uses `gradable_id`, `rubric_id`, final percentage and the saved time in `date_graded`. Native times are GMT. Production grading rows are eligible. Calibration rows and repeated grades require a corrected source selection. [MaestroQA raw exports](https://help.maestroqa.com/en/articles/1363737-exporting-raw-scores) |
| Langfuse v2 | JSON response from the v2 Scores API. Choose trace-level BOOLEAN records with source EVAL for a named overall pass condition. | Uses the trace ID as case ID. Choose a separate boolean complaint evaluator if available. Telryn retains an unknown complaint prediction for a missing classifier and rejects ambiguous duplicates. The two decisions must be available by the cutoff and concern the frozen revision. [Langfuse boolean score fields](https://js.reference.langfuse.com/%2F/interfaces/_langfuse_core.GetScoresResponseData.Boolean.html) |

Use all pages for a single frozen query. A single JSON page is accepted directly. For multiple pages, save the complete native page objects together in a JSON array without changing their row fields. Save that file as the bank's original. The page numbers and counts must reconcile. Use production score history as it existed at cutoff. If today's export only shows a later edit, obtain the archived version for the week. A score for another version, a partial page set or duplicate scores for one case can change the answer.

The source owner confirms which conversation revision and deployed scorecard version each native result graded. Native files do not always identify those revisions. The desk requires this attestation and freezes it with the format, threshold and original file digest. If the owner cannot verify the mapping, retain the source gap. The optional case prefix is an exact namespace such as `zendesk:workspace:` when the population came from a Telryn connector. Bare ticket IDs must be unique within that source. The scorecard field takes a Zendesk scorecard ID, MaestroQA rubric ID or Langfuse evaluator name. An overall percentage alone supplies no complaint prediction. The audit still checks the register for all eligible QA passes.

Scheduled audits use the same settings. Add this directly after the weekly plan's existing `[score_audit]` section.

```toml
[score_audit.native]
format = "maestroqa"
pass_percent = 85
attest_revisions = true
```

For `format = "langfuse-v2"`, omit `pass_percent` and optionally set `complaint_evaluator = "is-complaint"`. The mapped score reference identifies the source row or pair of evaluator records within the frozen file. The general Telryn CSV also accepts independent pass/fail and complaint labels from other evaluators. The importer contracts have local fixture and browser checks. Tenant configuration, access and export completeness require the source owner's verification. [Score audit and denominators](qa-score-audit.md)
