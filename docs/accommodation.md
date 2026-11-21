# Check a promised accommodation

A statement assistant agrees to record a customer's request and deliver the September statement in large print by 17:00 UTC on 9 October. The adjustments register records the request on time. The dispatch system marks the statement sent on 8 October. The delivery provider records receipt on 12 October at 10:02 UTC.

Run the worked case from any directory after installing Telryn. It uses the same deadline rules as the complaints-register check. In JSON, `outcome_type` is `accommodation` for this worksheet and `complaint_registration` in the weekly complaint reconciliation.

```sh
telryn outcome --example accommodation
```

At the 9 October cutoff, Telryn reports registration on time and delivery missing. At follow-up the delivery is late. The report shows the agent that logged the request, the dispatch event, and the team on the dated delivery receipt. The customer's request and the dated agreement come from two passages in the same conversation revision. The conversation and both ledgers are synthetic. [Worked inputs](../examples/accommodation/manifest.json)

## Check one request from your queue

Copy the [manifest](../examples/accommodation/manifest.json), [redacted conversation](../examples/accommodation/chat.json), [register](../examples/accommodation/register.csv) and [delivery ledger](../examples/accommodation/delivery.csv) into a private directory. Replace each file with authorized records. Then run:

```sh
telryn outcome /path/to/manifest.json --format json
```

The version 1 manifest names the policy revision, scope, cutoff, follow-up and source files. Set `evidence` to `operational` for your own records. Identify the conversation with its case ID, source reference and revision. A reviewer records the requested adjustment, whether the policy requires action, the agreed deadline and the numbered passages for the request and agreement. The request must cite a customer passage. The agreement must cite a separate reply from human or AI support. Set the source’s `speaker_role` to `customer`, `human_support` or `ai_support` from the transcript. The reviewer checks that the responder agreed to this adjustment, deadline and time zone in the cited words. A deadline the customer suggested on their own leaves the agreement unresolved. Number passages from one. Use an offset or UTC for each timestamp. A single request is the unit of analysis.

Use `required: false` when the reviewer rules out an obligation and `null` when the source cannot settle it. Leave an unknown deadline, agreement passage or match key (`request_id`) null. An uncertain deadline or identity leaves the timing unresolved. An explicit request and cited agreement establish what to check. The tool makes no inference about disability or customer vulnerability.

Supply separate register and delivery exports at immutable source revisions. Both use these columns:

```csv
request_id,case_id,accommodation,event_id,status,actor_type,actor,recorded_at,evidence_ref
```

Each row records a source event. Use the same stable request ID, case ID and accommodation code across systems. The register accepts `recorded`. The delivery ledger distinguishes `queued`, `sent`, `failed` and `delivered`. Only a delivery receipt for the requested adjustment satisfies delivery. Record the responsible human, agent or system from the source row and a reference the reviewer can reopen. The reviewer checks who delivered the agreed adjustment against that receipt. Use one immutable event ID per source. An event linked to a different case under the same request ID, a date before the request or multiple possible successful receipts leaves the result unresolved at the horizon where it appears.

Use the authoritative contemporaneous timestamp of the actual recording or delivery. If an export only has a later edit or an earlier effective date, obtain the original dated receipts before drawing a conclusion. Each `complete_through` is the source owner's attestation that all relevant history for this request and scope is available through that inclusive instant. Use `null` when the source cannot establish absence. A correctly matched receipt can establish an on-time outcome even with partial coverage. Establishing a late outcome requires coverage through the deadline so an earlier success can be ruled out. A missing result requires coverage through the observation cutoff. After the deadline, a dispatch entry by itself leaves delivery missing when the receipt ledger is complete.

JSON output contains each milestone at the two observation times, the relevant dated events, the reviewer and cited passages. Keep the inputs to rerun it. The ledger hashes identify their file bytes, while the manifest and conversation digests identify their canonical JSON. This local worksheet contains private evidence and has no team signature. Use the signed weekly workflow for complaints sampling and grading.
