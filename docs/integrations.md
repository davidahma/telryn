# Bring your support queue into a weekly audit

A weekly cohort contains each visible conversation that reached resolution or closure during the agreed UTC week. A case opened months ago can qualify. Count a case once even when it reopened, or completed twice in the same week. An administrative move from resolved to closed ends the same episode.

## Pull the completion cohort

Use a credential scoped to the queue the report should cover. The first date is inclusive and the second exclusive. Supply an independent count for the same definition when available.

```sh
python3 -m telryn integrations pull zendesk \
  --since 2026-11-02 --until 2026-11-09 \
  --expected-count 412 --out population.csv
```

The private CSV contains `case_id,revision,evidence_ref,screen_flag` and optional dimensions. Use `--dimension channel=via.channel` on Zendesk, or repeat `--dimension` to map other source fields to `channel`, `agent_type` or `product`. Every included row needs a value for each mapped field. A native tag, mapped dimension or Fin-participation flag from a later revision could leak future information. For those cases supply the metadata as it stood at completion in a warehouse ledger. Use a customer ledger to derive group names that require a lookup. The adjacent `population.csv.coverage.json` records the scope, dates, counts, and the earliest observed in-week completion per case (`completed_at`). A fingerprint binds these cutoffs to the included IDs, revisions, references and groups. Keep both files. Reviewers need the archived case or ticket history behind each reference. An ID and timestamp cannot recover overwritten text.

| Source and credentials | Completion evidence |
| --- | --- |
| Zendesk. `TELRYN_ZENDESK_TOKEN` (OAuth bearer), `TELRYN_ZENDESK_SUBDOMAIN` | Paged ticket audits identify transitions into solved or closed. A later reopen keeps the earlier episode eligible. An automatic solved-to-closed transition adds no episode. Requires audit read access. [Search Export](https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/#export-search-results) · [Ticket audits](https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_audits/#list-audits-for-a-ticket) |
| Freshdesk. `TELRYN_FRESHDESK_TOKEN` (agent API key), `TELRYN_FRESHDESK_SUBDOMAIN` | Uses ticket stats, preferring `resolved_at` and falling back to `closed_at`. Status 4 (Resolved) and 5 (Closed) are both terminal. Latest stats can hide earlier episodes. The default list excludes spam and deleted tickets. The connector stops at a potentially incomplete 300-page result. [Freshdesk tickets](https://developers.freshdesk.com/api/#list_all_tickets) |
| Intercom, including Fin. `TELRYN_INTERCOM_TOKEN`, `TELRYN_INTERCOM_WORKSPACE`, optional `TELRYN_INTERCOM_REGION` (`us`, `eu`, `au`) | Reads first and last close timestamps. An intervening closure can be missing. Use `--fin-only` to restrict the scan to Fin conversations. The token must identify its workspace via `/me`. [Conversation search](https://developers.intercom.com/docs/references/2.14/rest-api/api.intercom.io/conversations/searchconversations) |
| Salesforce. `TELRYN_SALESFORCE_TOKEN` (OAuth bearer), `TELRYN_SALESFORCE_HOST` (My Domain hostname, e.g. `example.my.salesforce.com`) | Reads `ClosedDate` and `IsClosed` from modified cases, including old cases. A reopen or multiple closures can overwrite evidence. Use a field-history ledger for those queues. [Salesforce Query](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-query.html) |
| Decagon. `TELRYN_DECAGON_TOKEN`, `TELRYN_DECAGON_WORKSPACE` (stable local label) | The connector's completion tag lacks an event time. Use a dated ledger for a completion week. For a historical creation-based comparison, supply `--basis created --closed-tag TAG --expected-count N`. The connector spaces export pages one second apart. [Decagon export connector reference](https://github.com/PostHog/posthog/tree/df5cb5727890f4743aff8704bd66fe7472ed860c/products/warehouse_sources/sources/decagon) |
| Sierra. Customer AWS profile or role and versioned export | Use a dated ledger, or `pull-sierra` for a weekly roster your warehouse has already reconciled. [Customer-managed Sierra delivery](integrations-sierra.md) |

Without a tag, all rows receive `screen_flag=false`. Set `screen_version="none"` for a random-only audit. For Zendesk, Freshdesk, Intercom or Decagon, `--flag-tag` reads an existing versioned risk tag. Confirm that every eligible case encountered that screen. To read the conversation text, follow the [transcript workflow](transcripts.md).

### Read the exclusions

`eligible`, `open`, `outside` and `unknown` sum to `scanned`. An open exclusion has no observed in-week completion. Outside rows have an established exclusion such as a completion before the week. Unknown means the history or state leaves membership unresolved. The separate `still_open` count includes all observed currently open records, including cases that finished in the week and later reopened. A live scan covers visible records modified since the start and created before the end. An untouched backlog or a deleted case can fall outside it. Check the scope and unknowns before describing an estimate as applying to the whole queue.

On snapshot sources a later completion can erase an earlier one. Restore the history from an event ledger when unknowns matter to the decision. `--basis created` reproduces the old created-in-range, closed-at-retrieval definition. Its open counts are limited by vendor-side filters.

### Use a dated lifecycle ledger

For any provider, export one row per distinct conversation in a declared scope. `completion_events` contains all resolution episodes, with timezone-qualified timestamps. Use `[]` for a known empty history or `null` for unavailable history. Use `open`, `completed` or `unknown` for the retrieval state. Reconcile duplicates and delayed events before export. Freeze any screen flag and dimensions as of the first in-week completion. Point the evidence reference at the corresponding archived case history. This synthetic ledger includes an eligible reopened case, an open exclusion, an outside completion and an unknown.

```csv
case_id,revision,evidence_ref,screen_flag,state,completion_events,channel
c01,r4,archive/c01/r4,false,open,"[""2026-11-04T11:00:00Z""]",email
c02,r2,archive/c02/r2,false,open,[],chat
c03,r3,archive/c03/r3,false,completed,"[""2026-10-20T12:00:00Z""]",chat
c04,r9,archive/c04/r9,false,unknown,null,email
```

```sh
python3 -m telryn integrations cohort lifecycle.csv \
  --since 2026-11-02 --until 2026-11-09 --scope "UK support warehouse" --out population.csv
```

If an interrupted export leaves just the coverage receipt, repeat the original export. An identical receipt can finish the publication. For a different export, choose a fresh destination. All connector writers reserve the data file and its adjacent receipt together. The weekly plan discovers the adjacent coverage receipt. When it is stored elsewhere, set `coverage_receipt="path/to/coverage.json"`. Telryn checks the seven-day interval and exact membership before drawing. To fetch transcripts or hand off through a native connector, keep its namespaced case IDs, such as `zendesk:acme:902`.

## Match the ordinary QA reviews

The ordinary review list must use the same namespaced IDs as the population. If your baseline is a recorded weekly assignment sample, supply the assignment records from that cutoff. If your baseline is **completed human reviews** from one Zendesk QA scorecard and workspace, the following command creates the matching `reviewed.csv`. Set `TELRYN_ZENDESK_QA_TOKEN` to the QA connection token and `TELRYN_ZENDESK_QA_SUBDOMAIN` to the same tenant used for the population.

```sh
python3 -m telryn integrations reviewed-zendesk population.csv \
  --workspace 11759 --scorecard standard-human-v2 \
  --since 2026-11-02 --until 2026-11-10 --out reviewed.csv
```

The dates here bound review creation, so the end date can extend past the conversation completion week. Telryn matches the ticket URL and external ID, ignores other scorecards and tickets outside the population, and writes each matched ID once. The operator chooses a human-only scorecard. For an in-window match the export must show a reviewer ID or email, a numeric score (zero counts), and a last update before the cutoff. If later edits or incomplete records leave the historical review uncertain, supply the archived review ledger. Reviewer details stay out of the output. Set the review window through your ordinary QA cutoff and supply every review source in the baseline. For multiple scorecards or tools, combine their records locally into one `case_id` column and remove duplicates. Store the original dated exports beside the weekly evidence. [Zendesk QA workspace review export](https://developer.zendesk.com/documentation/qa/exporting-data/)

## Use a customer-exported agent file

`import-agent` accepts existing CSV exports for Fin, Decagon, and Sierra. Choose one closed-conversation cohort upstream. Map the stable ID, the evidence revision, and a reference your grader can resolve. If supplied, the screen column must contain a decision for every row.

```sh
python3 -m telryn integrations import-agent sierra closed-sierra.csv \
  --workspace uk-support --id-column conversation_id \
  --revision-column export_revision --evidence-column archive_ref \
  --flag-column escalation_risk --out population.csv
```

Record the export's date range, row count, and source version. The mapper copies only the selected columns. To pull an already published S3 export directly, follow the [customer-managed Sierra delivery guide](integrations-sierra.md).

## Hand the sample to your QA tool

Use a tag unique to the audit week. The handoff reads the sampled case IDs and revisions from the frozen weekly report. Every sampled group receives the same tag. Preview lists the count and tenant without making an HTTP request.

```sh
python3 -m telryn integrations handoff zendesk runs/weekly/report.html --tag telryn_2026w45
python3 -m telryn integrations handoff zendesk runs/weekly/report.html --tag telryn_2026w45 --apply
```

- **Zendesk QA**. Allow closed tickets to be modified and use a credential authorized to update them. Telryn adds the tag through Zendesk Support's bulk ticket API and reads the resulting tag back from every updated ticket. After your QA connection syncs, use its source-tag filters or ticket ID search to select the cohort and assign reviewers. Check the selected count against Telryn's sample. [Modify closed tickets](https://support.zendesk.com/hc/en-us/articles/7335734335258-Modifying-closed-tickets) · [Zendesk QA conversation view](https://support.zendesk.com/hc/en-us/articles/7043661945370-Using-the-Conversations-view)
- **Intercom QA**. Use `handoff intercom` with an existing numeric conversation tag ID. Telryn confirms the workspace and checks the sample revisions. It rechecks each conversation immediately before adding the tag and reads the tag back afterward. Configure the reviewer and scorecard in your Intercom workflow and check the actual review list. Confirmed handoff means that the ticket has the tag. Assignments and completed grades are separate records. [Conversation tags](https://developers.intercom.com/docs/references/2.14/rest-api/api.intercom.io/conversations/attachtagtoconversation) · [Intercom human QA](https://www.intercom.com/help/en/articles/14840301-getting-started-with-human-teammate-qa-in-monitors)
- **MaestroQA**. Generate a single-column CSV of native ticket IDs. In MaestroQA's Tickets view, upload the list, choose **Create/Add to Assignment**, select **Agent QA**, and set the graders and due date. Tickets must already exist in the connected workspace. [MaestroQA assignment instructions](https://help.maestroqa.com/en/articles/4249565-manually-creating-assignments)

```sh
python3 -m telryn integrations maestroqa runs/weekly/report.html \
  --source zendesk --workspace acme --out maestro-ticket-ids.csv
```

Telryn rejects a mismatched tenant or a changed, untagged revision before attempting writes. A network error can occur after the service accepted some tags. Rerunning the same command checks existing tags and skips records already marked. When Zendesk provides a pending job ID, check its status before retrying. Intercom's tag endpoint has no atomic revision precondition, so a conversation can change between the revision read and tagging. Use the frozen evidence for grading.

## Validation

Run `python3 -m unittest tests.test_integrations tests.test_integrations_sierra tests.test_integrations_coverage tests.test_transcripts` for the vendor contracts. Tests exercise the CLI, HTTP request encoding and response parsing, cursor handling, per-record results, tenant checks, and the path from a source pull to a weekly report and review handoff with fixture responses. Those checks run offline. Access scopes, QA subscription features, sync schedules, and downstream assignment rules require confirmation in your tenant.

## Check Intercom scorecard evidence for a complaints pilot

Run the following read-only comparison from the checkout. With no arguments it uses synthetic captures and an independent synthetic ledger. To check a private archive on the lead’s machine, pass both files.

```sh
python3 -m examples.complaints_pilot.intercom
python3 -m examples.complaints_pilot.intercom --archive captures.json --ledger lead-ledger.json
```

Use one workspace, scorecard definition, version and eligible population in both files. The interval includes its start and excludes its end. Unix times are seconds. Each capture records when the conversation response was saved. Keep the data on the lead’s machine and project out transcript bodies, notes and reasoning. The sample files show which fields the check consumes. [Archive example](../examples/complaints_pilot/intercom-archive.json) · [Ledger and timing example](../examples/complaints_pilot/intercom-ledger.json)

The 2.17 conversation endpoint can include scorecards. A monitor result shows that a conversation met selection criteria. A scorecard’s assignee, AI score and reviewed teammate each answer a different question. The rehearsal looks for a submitted review status and the human who submitted it. The API describes `evaluated_at` as the last evaluation time. A note timestamp or a later human edit also cannot date the original submission. The ordinary Telryn Intercom adapter keeps its existing API version. [Intercom 2.17 conversation contract](https://developers.intercom.com/docs/references/2.17/rest-api/api.intercom.io/conversations/retrieveconversation)

For a conversation created during the week, creation and the first qualifying observation bound when the human could have submitted. For an older conversation, the check needs an earlier observation in the week of the same scorecard version and named reviewee showing an assignment, an unreviewed card or an AI submission. A missing card or a different reviewee supplies no lower time bound. This comparison assumes captures have the same scorecard visibility and describe the same conversation. Keep assignment-only and AI-only observations in the population. A first capture after cutoff cannot establish a timely human review, even if the card shows an earlier evaluation. Look at the dates in the lead’s ledger to resolve cases that only one source found. Even when a case appears on both lists, inspect its unverified submissions. A single observation can fit multiple submissions by the same person. The command prints a comparison and leaves the ordinary-review CSV to the lead.

In the fixture the archive establishes human submissions for cases 103 and 104. The lead’s ledger also lists 105, whose first capture came after cutoff. The simulated assisted workflow took 41 minutes against 50 for the manual workflow. The agreed material saving was 15 minutes. For a real week, count export, exception handling and verification time. A live importer earns its place if this is the team’s recurring setup bottleneck, the results reconcile, and the time saving meets the lead’s predeclared threshold.
