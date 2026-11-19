# Screen the conversation behind a case

Use the local model when the evidence is conversation text. Telryn asks it for policy facts, checks that each yes or no cites a passage in the redacted source, then applies the selected rule in code. The flag assigns review priority. A human grades the case against the documented policy.

## Get the text for the frozen population

```sh
python3 -m telryn integrations transcripts zendesk population.csv --out transcripts.jsonl
```

Use the credential and workspace that produced the [source population](integrations.md). The connector verifies each source ID and revision. For a completion cohort the reader checks the timestamp against the recorded completion cutoff for **every** case before fetching text. A ticket reopened on Friday cannot supply a transcript for its Monday completion. If any source revision is later than the cutoff, use the archived JSONL route below. The reader also stops on a changed revision, missing required text, failed page or recognized truncation.

| Provider | Text sources |
| --- | --- |
| Zendesk | Ticket comments, including internal notes visible to the credential. The requester is marked as the customer. Other authors use Zendesk user-directory roles when the role record predates the comment. Missing or subsequently changed roles remain unknown. Requires user-read access. [User roles](https://developer.zendesk.com/api-reference/ticketing/users/users/) · [Ticket comments](https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_comments/#list-comments) |
| Freshdesk | Ticket description and paginated replies and notes. A message from the requester is labeled customer. A message from the assigned agent is labeled agent. Other authors remain unknown. Freshdesk's `incoming` flag describes where the reply originated. [Freshdesk conversations](https://developers.freshdesk.com/api/#list_all_ticket_conversations) |
| Intercom / Fin | Conversation source and text-bearing parts in plaintext. Each part needs a distinct ID and a last update at or before the frozen revision. The reader stops on redacted text and at the 500-part cap. [Retrieve a conversation](https://developers.intercom.com/docs/references/2.14/rest-api/api.intercom.io/conversations/retrieveconversation) |
| Salesforce | Case description, comments and received or sent email, ordered by record creation time. The query excludes unsent drafts. Inbound email carries an unknown speaker because the direction alone cannot identify the sender. Separate voice, messaging and chat-transcript objects require an upstream join. [EmailMessage](https://developer.salesforce.com/docs/platform/field-reference/references/field-reference/salesforce-field-reference-emailmessage.html) · [Email delivery statuses](https://help.salesforce.com/s/articleView?id=000384028&language=en_US&type=1) |
| Decagon, Sierra, other channels | Import customer-authorized text in the JSONL format below. Use stable source revisions and references your graders can open. |

These readers cover text visible to the credential. Attachments, audio, payment records and external escalation logs require separate evidence where the rubric depends on them. Preserve the evidence behind a model's claim of a completed action.

### Import a customer archive

Supply one JSON object per case. `case_id`, `revision` and `evidence_ref` are nonempty strings. Messages are ordered, with a nonempty `text` and a `role` of `customer`, `agent`, `system` or `unknown`. Each row may also carry the same set of string dimensions from `channel`, `agent_type` and `product`. The local model receives only passage text, roles, passage numbers and the policy.

```json
{"case_id":"C01","revision":"r3","evidence_ref":"archive/C01/r3","as_of":"2026-11-04T11:00:00Z","channel":"chat","agent_type":"ai","messages":[{"role":"customer","text":"Someone signed into my account."},{"role":"agent","text":"I referred this case to the fraud desk."}]}
```

A native fetch carries the coverage receipt alongside the JSONL. For an archive, copy the receipt to `transcripts.jsonl.coverage.json` and set `as_of` on **each** row to that case’s timestamp in the receipt’s `completed_at` map. Export only evidence available at that instant, including the message versions then visible. A current transcript with a historical label is not an archive. Use references that let reviewers reopen the archived evidence. Telryn checks the full set of IDs, revisions, references, dimensions and cutoff declarations. The supplying archive is responsible for the actual provenance of its text.

For a structured fact CSV from a completion cohort, include the same per-case `as_of` column and place the matching receipt at `signals.csv.coverage.json`. A transcript screen writes that pair for reuse. Bare inputs can screen a supplied snapshot, but cannot establish when it was true. The issued screen receipt binds the presence and contents of its coverage receipt.

## Run the local extractor

Start your locally administered Ollama daemon with local weights installed. Use the exact model tag shown by `ollama list`. Telryn connects directly to `127.0.0.1:11434` and checks the installed weights. Ollama supports disabling cloud features in the daemon's configuration. [Ollama chat API](https://docs.ollama.com/api/chat) · [Local-only configuration](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)

```sh
python3 -m telryn screen transcripts.jsonl \
  --model "$TELRYN_LOCAL_MODEL" --policy account-compromise \
  --policy-text approved-policy.txt --redact private-identifiers.txt \
  --workers 2 --checkpoint private-checkpoints/week45 --out runs/week45-screen
```

Supply a short approved policy defining when an escalation, refund action or complaint entry is required. Without `--policy-text`, Telryn uses a built-in demonstration policy. Choose `account-compromise`, `missed-escalation`, `promised-refund`, `legal-threat`, `unlogged-complaint` or `serious-cases`. The extractor asks for the facts relevant to that rule. For example, the account-compromise rule flags an evidenced compromise with a required referral and no evidence of an actual referral.

Redaction replaces common email addresses, URLs and number sequences. The optional private file lists additional literal identifiers, one per line. Names, identifiers and sensitive details can survive these substitutions. Apply your team's own redaction and access controls as needed. The source JSONL contains raw text. Citations and the private checkpoint retain redacted passages. Keep all three inside the permitted environment and expire them under your retention policy.

Every passage is processed in bounded chunks. Source quotes and offsets refer to the **redacted** message. Each definite answer must cite a passage from that chunk. A refund promise, recorded refund, completed escalation or logged complaint needs evidence from an agent or system. A customer's request or an unidentified speaker leaves the staff action unresolved. Missing or conflicting evidence becomes unknown. A real passage can still be interpreted incorrectly, and evidence split across chunks can lose context. Inspect the citations and grade a random sample of unflagged cases to measure where the screen misses serious failures.

The default is two workers, configurable from one to sixteen. If a run stops, repeat it with the same input, model, policy, redaction configuration, checkpoint and output path. Telryn checks the installed model digest before committing a group of results to its private SQLite checkpoint. A retry reuses verified groups and reruns work interrupted before verification. On the first resume after upgrading from v0.6.0 or earlier, Telryn reprocesses the old checkpoint once. Only a complete screen publishes a population. Keep the same local weights throughout a run and use a fresh checkpoint and output directory when changing the inputs.

## Review the output

| File | How to use it |
| --- | --- |
| `population.csv` | Use as `population` in the weekly audit. It preserves every case, revision and grouping dimension. |
| `receipt.json` | Copy `screen_version` into the weekly plan and set `screen_receipt` to this file. It binds the model digest, recipe, inputs and output bytes. |
| `signals.csv` | Read the extracted yes/no/unknown fields or apply another deterministic rule when the required facts were extracted. |
| `citations.jsonl` | Inspect cited passage, message number, role, redacted offsets and classification. Empty citations mean no supported fact for that field. |
| `population.csv.coverage.json` | When supplied with the source, carries the verified completion cohort and exclusions into the weekly report. |

The population and signals files preserve the source identifiers verbatim for machine import. If you inspect them in a spreadsheet, import identifier, revision and reference columns as text. Use the weekly desk’s grading CSV when sharing assignments with reviewers.

With an upstream export of the eight policy facts, Telryn evaluates the rule directly. No model server or transcript is required.

```sh
python3 -m telryn screen examples/fintech/signals.csv \
  --policy account-compromise --out runs/fintech-events
```

The [synthetic transcript pair](../examples/fintech/transcripts.jsonl) includes one case closed without a fraud referral and one with a documented referral. It can exercise the local model and citation path without customer data. Two examples cannot establish accuracy on a production support queue.
