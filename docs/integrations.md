# Bring your support queue into a weekly audit

A live pull produces the four columns the weekly audit accepts. It reads the provider's current records for one tenant. The cohort consists of conversations **created on or after the first date and before the second date, in UTC, and closed when retrieved**. A conversation opened earlier and closed this week falls outside that definition. Reopening a ticket or exporting it later can change membership. For a cohort defined by an exact historical closure event, use your event warehouse's reconciled export.

## Pull closed conversations

Set credentials through your secret manager or shell environment. Use a customer-controlled credential with access to the whole eligible queue. A token limited to one team produces that team's population. Each invocation writes a new file privately, with mode `0600`. Check `--expected-count` against an independent count for the same dates, closure definition, and credential scope.

| Source | Environment | Closure and screen |
| --- | --- | --- |
| Intercom, including Fin | `TELRYN_INTERCOM_TOKEN`, `TELRYN_INTERCOM_WORKSPACE`, optional `TELRYN_INTERCOM_REGION` (`us`, `eu`, `au`) | Conversation state `closed`. Add `--fin-only` for conversations with Fin participation. `--flag-tag` takes a conversation tag name. The token needs conversation reads and access to identify its workspace through `/me`. |
| Zendesk Support | `TELRYN_ZENDESK_TOKEN` (OAuth bearer), `TELRYN_ZENDESK_SUBDOMAIN` | Status `closed`, retrieved through cursor-paginated Search Export. `--flag-tag` takes a ticket tag name. |
| Salesforce Service Cloud | `TELRYN_SALESFORCE_TOKEN` (OAuth bearer), `TELRYN_SALESFORCE_HOST` (My Domain hostname, e.g. `example.my.salesforce.com`) | Cases with `IsClosed = true`. The SOQL query selects the case ID, creation time, modification time, and closed flag. Apply your screen separately. |
| Freshdesk | `TELRYN_FRESHDESK_TOKEN` (agent API key), `TELRYN_FRESHDESK_SUBDOMAIN` | Ticket status 5, Closed. The default list excludes spam and deleted tickets. `--flag-tag` takes a ticket tag name. At the provider's 300-page ceiling, Telryn stops if the cohort may extend past that page. |
| Decagon | `TELRYN_DECAGON_TOKEN`, `TELRYN_DECAGON_WORKSPACE` (a stable local label for this token's workspace) | Supply `--closed-tag` with the tag your team uses for completed conversations. Supply an independent cohort count with `--expected-count`. An unfamiliar pagination contract can otherwise hide missing cases. Optionally supply a separate `--flag-tag`. The connector spaces consecutive export pages one second apart. |
| Sierra | Your AWS profile or role and versioned customer export | `pull-sierra` retrieves one immutable S3 object from your team's Sierra delivery. See [Sierra setup](integrations-sierra.md). |

For example, a lead who counted 412 closed Zendesk tickets for this creation week can run:

```sh
python3 -m telryn integrations pull zendesk \
  --since 2026-11-02 --until 2026-11-09 \
  --flag-tag risk_screen_v3 --expected-count 412 --out population.csv
```

The CSV contains `case_id,revision,evidence_ref,screen_flag`. A ticket can appear as `zendesk:acme:902`, with its source modification time as the revision. The evidence reference joins the source, tenant, native ID and revision. Give reviewers access to the underlying ticket history or an archived snapshot keyed by that reference. A saved ID and timestamp alone cannot reconstruct an overwritten transcript. Pulls discard vendor message bodies, names, email addresses and attachments after parsing the response.

Without `--flag-tag`, every row has `screen_flag=false`. Use `screen_version="none"` in the weekly plan for a random-only audit. When you do use a tag, record the screen's version in the plan. Absence of the tag is a negative classification only if the screen had a chance to process the entire eligible cohort. Fin participation alone describes the conversation's participants.

Intercom documents the searchable fields, including Fin participation. Zendesk's export API and Salesforce's query locator support paging large sets. Freshdesk documents paging limits and ticket status codes. Decagon restricts its developer reference to customers. Its public integration in PostHog supplies the conversation export route, timestamp filters, cursor names, and rate limit used here. [Intercom search](https://developers.intercom.com/docs/references/2.14/rest-api/api.intercom.io/conversations/searchconversations) · [Zendesk Search Export](https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/#export-search-results) · [Salesforce Query](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-query.html) · [Freshdesk tickets](https://developers.freshdesk.com/api/#list_all_tickets) · [PostHog's Decagon connector](https://github.com/PostHog/posthog/tree/df5cb5727890f4743aff8704bd66fe7472ed860c/products/warehouse_sources/sources/decagon)

## Match the ordinary QA reviews

The ordinary review list must use the same namespaced IDs as the population. If your baseline is a recorded weekly assignment sample, supply the assignment records from that cutoff. If your baseline is **completed human reviews** from one Zendesk QA scorecard and workspace, the following command creates the matching `reviewed.csv`. Set `TELRYN_ZENDESK_QA_TOKEN` to the QA connection token and `TELRYN_ZENDESK_QA_SUBDOMAIN` to the same tenant used for the population.

```sh
python3 -m telryn integrations reviewed-zendesk population.csv \
  --workspace 11759 --scorecard standard-human-v2 \
  --since 2026-11-02 --until 2026-11-10 --out reviewed.csv
```

The dates here bound review creation, so the end date can extend past the conversation creation week. Telryn matches the ticket URL and external ID, ignores other scorecards and tickets outside the population, and writes each matched ID once. Set the review window through your ordinary QA cutoff and supply every review source in the baseline. For multiple scorecards or tools, combine their records locally into one `case_id` column and remove duplicates. Store the original dated exports beside the weekly evidence. [Zendesk QA workspace review export](https://developer.zendesk.com/documentation/qa/exporting-data/)

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

Use a tag unique to the audit week. The handoff reads the sampled case IDs and revisions from the frozen weekly report. Both sample strata receive the same tag. Preview lists the count and tenant without making an HTTP request.

```sh
python3 -m telryn integrations handoff zendesk runs/weekly/report.html --tag telryn_2026w45
python3 -m telryn integrations handoff zendesk runs/weekly/report.html --tag telryn_2026w45 --apply
```

- **Zendesk QA**. Allow closed tickets to be modified and use a credential authorized to update them. Telryn adds the tag through Zendesk Support's bulk ticket API and reads the resulting tag back from every updated ticket. After your QA connection syncs, use its source-tag filters or ticket ID search to select the cohort and assign reviewers. Check the selected count against Telryn's sample. [Modify closed tickets](https://support.zendesk.com/hc/en-us/articles/7335734335258-Modifying-closed-tickets) · [Zendesk QA conversation view](https://support.zendesk.com/hc/en-us/articles/7043661945370-Using-the-Conversations-view)
- **Intercom QA**. Use `handoff intercom` with an existing numeric conversation tag ID. Telryn confirms the workspace, reads the current tags, and applies the tag using the authorized admin ID. Configure the reviewer and scorecard in your Intercom workflow and check the actual review list. Confirmed handoff means that the ticket has the tag. Assignments and completed grades are separate records. [Conversation tags](https://developers.intercom.com/docs/references/2.14/rest-api/api.intercom.io/conversations/conversation) · [Intercom human QA](https://www.intercom.com/help/en/articles/14840301-getting-started-with-human-teammate-qa-in-monitors)
- **MaestroQA**. Generate a single-column CSV of native ticket IDs. In MaestroQA's Tickets view, upload the list, choose **Create/Add to Assignment**, select **Agent QA**, and set the graders and due date. Tickets must already exist in the connected workspace. [MaestroQA assignment instructions](https://help.maestroqa.com/en/articles/4249565-manually-creating-assignments)

```sh
python3 -m telryn integrations maestroqa runs/weekly/report.html \
  --source zendesk --workspace acme --out maestro-ticket-ids.csv
```

Telryn rejects a mismatched tenant or a changed, untagged revision before attempting writes. A network error can occur after the service accepted some tags. Rerunning the same command checks existing tags and skips records already marked. When Zendesk provides a pending job ID, check its status before retrying. Intercom's tag endpoint has no atomic revision precondition, so a conversation can change between the revision read and tagging. Use the frozen evidence for grading.

## Validation

Run `python3 -m unittest tests.test_integrations tests.test_integrations_sierra` for the vendor contracts. Tests exercise the CLI, HTTP request encoding and response parsing, cursor handling, per-record results, tenant checks, and the path from a source pull to a weekly report and review handoff with fixture responses. Those checks run offline. Access scopes, QA subscription features, sync schedules, and downstream assignment rules require confirmation in your tenant.
