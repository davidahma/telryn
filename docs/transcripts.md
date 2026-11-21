# Screen the conversation behind a case

Choose local Ollama or an approved cloud deployment when the evidence is conversation text. Telryn asks the model for policy facts, checks that each yes or no cites a passage in the redacted source, then applies the selected rule in code. The flag assigns review priority. A human grades the case against the documented policy.

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

Supply one JSON object per case. `case_id`, `revision` and `evidence_ref` are nonempty strings. Messages are ordered, with a nonempty `text` and a `role` of `customer`, `agent`, `system` or `unknown`. Each row may also carry the same set of string dimensions from `channel`, `agent_type` and `product`. The selected model receives redacted passage text, roles, passage numbers, the policy and the response schema.

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

## Use a model in the firm's cloud account

The complaints lead and cloud owner choose the deployment, credential, approved policy and permitted network route. Passing `--model-endpoint` on a transcript screen explicitly sends the approved policy and redacted passages to that deployment. Source case IDs, evidence references, revision labels and grouping columns stay on the machine. Review the firm's terms, provider logging and retention, geographic routing, capacity and private DNS settings. Run a synthetic batch on the actual deployment before authorizing customer data.

**Azure OpenAI.** Create a JSON file for one public Azure resource with private DNS and Private Link where required. The endpoint is `https://<resource>.openai.azure.com/openai/v1/chat/completions`. Supply exactly one credential in the environment: `AZURE_OPENAI_API_KEY` or a short-lived Microsoft Entra access token in `TELRYN_AZURE_ACCESS_TOKEN`. The latter comes from the team's identity process. Refresh an expired token and resume. Use a Chat Completions deployment supporting structured output. Enter the dated model identifier returned by Azure for that deployment; choose an approved, provisioned revision.

```json
{"schema":1,"provider":"azure-openai","resource":"example-bank","model_revision":"gpt-4o-2024-08-06"}
```

```sh
python3 -m telryn screen transcripts.jsonl --model-endpoint azure.json \
  --model complaint-extractor --policy unlogged-complaint \
  --policy-text approved-policy.txt --redact private-identifiers.txt --out runs/azure-screen
```

The request addresses the deployment name. Every reply must report the exact expected dated model, finish normally and contain the requested facts and valid passage numbers. A content refusal, truncated reply or revision mismatch stops publication. [Azure authentication and deployment names](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/chatgpt) · [Structured outputs and response model](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs)

**Amazon Bedrock.** Use the SDK included in the published container. For a source checkout, install `python3 -m pip install --only-binary=:all: --require-hashes -r requirements-bedrock.txt` in the environment used for Telryn. Supply the team's AWS role through the standard SDK credential chain. The role needs regional STS access and `bedrock:InvokeModel` permission scoped to the approved foundation model. Configure both regional STS and Bedrock Runtime private endpoints and DNS if the deployment requires private routing.

```json
{"schema":1,"provider":"bedrock","account_id":"123456789012","region":"us-east-1"}
```

```sh
python3 -m telryn screen transcripts.jsonl --model-endpoint bedrock.json \
  --model amazon.nova-lite-v1:0 --policy unlogged-complaint \
  --policy-text approved-policy.txt --redact private-identifiers.txt --out runs/bedrock-screen
```

Choose a versioned foundation model available for direct in-region Converse in the configured commercial AWS region. Telryn verifies the caller's account with regional STS, invokes the regional foundation-model ARN with signed requests, and checks the account before saving a group of new results. Models requiring cross-region inference profiles need a different deployment. The model must return a complete JSON object; every claimed fact passes the same passage and speaker checks as the Ollama and Azure paths. [AWS credential chain](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html) · [STS caller identity](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html) · [Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)

For both providers the checkpoint binds the endpoint, deployed model, extraction recipe, approved policy, redactions and source. The completion receipt records the cloud scope and the prompt fingerprint. A cloud model ID identifies the provider deployment or revision. It cannot attest to a hash of the provider's private model weights. Copy the issued `screen_version` and `screen_receipt` into the weekly plan. Switching providers, resources, accounts or model revisions requires a different checkpoint and a new receipt.

## Review the output

| File | How to use it |
| --- | --- |
| `population.csv` | Use as `population` in the weekly audit. It preserves every case, revision and grouping dimension. |
| `receipt.json` | Copy `screen_version` into the weekly plan and set `screen_receipt` to this file. It binds the local weights digest or configured cloud model identity, recipe, inputs and output bytes. |
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
