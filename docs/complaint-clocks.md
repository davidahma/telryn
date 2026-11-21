# Replay a complaint episode and its clocks

A customer can raise one issue with a bot and then call about it. To review the handling of both contacts, attach the source references and the firm's approved clock policy to the [dated complaint records](complaint-records.md). The weekly sample still counts conversations. The case detail shows all linked contacts, the first cited trigger, the deadline and the evidence for stopping each clock.

## Prepare the source evidence

Add `"episode_evidence": "contacts.json"` and `"clock_policy": "policy.json"` to the complaint manifest. Put both files beside the manifest. Keep a case entry for each sampled contact in the archive, use its frozen revision and register key, and set its manual `deadline` to `null`. Sampled cases outside the archive can still use a supplied deadline.

The contacts file has `schema: 1` and a `contacts` array. Each contact has the following fields.

| Field | Source |
| --- | --- |
| `case_id`, `revision`, `evidence_ref` | Frozen conversation or call identifier, revision and restricted source reference. A sampled contact must match the weekly export. Earlier or later contacts can supply evidence without entering the sample. |
| `subject_ref`, `matter_ref` | Stable source identifiers for the customer and the particular issue, such as a disputed transaction. Use the same identifiers across channels. Use `null` where identity or issue is ambiguous. |
| `channel`, `contact_at`, `match_key` | Source channel, offset-aware time of first contact and opaque register alias. Use `null` for an unknown alias. |
| `passages` | Array of objects containing a dated `at` and redacted source `text`. Keep the original revision available for independent review. |
| `events` | Array of reviewed annotations. Each has a policy event name in `event`, a one-based `passage` number, and an exact nonempty `quote` from that passage. An exact quote locates the evidence. The reviewer answers for its interpretation. |

The [worked contacts](../examples/complaints_pilot/prior-contacts.json) show a Fin chat and a phone call. The same scope and team HMAC key produce the same opaque subject reference across reports. A distinct issue receives a distinct matter reference. Within each customer and issue, contacts join through the inclusive end of the policy window measured from the first contact. A chain of repeat calls cannot extend the window indefinitely. The evidence must cover the known history of that issue back to the first relevant contact. Use a stable opaque case ID and register alias. A name or email embedded in a quote or source reference will remain visible to readers.

Register lookup checks every linked contact's alias. An alias shared across different issues, unknown identity or missing mapping leaves the registration unresolved. Two register entries for one episode also need reconciliation. At the historical cutoff only contacts and entries already visible can establish the answer. A later phone registration can settle the follow-up while preserving an earlier miss. In a control-profile audit, each linked contact must belong to a covered or partially covered channel. List a historical phone source as partial and describe its limits if only selected calls are available. When a control-profile audit’s export supplies a channel, the sampled conversation retains it for checking against the attached contact, including when the team samples across channels. A cited event on that sampled revision must fall at or before its recorded closure. Supply later events as separately dated source contacts. A source owner still has to attest that the register covers all relevant aliases and dates.

## Define the decision clock

Use the [example policy](../examples/complaints_pilot/clock-policy.json) as a file-format example. Issue a stable policy `id` and a new `version` for each revision. Specify the IANA `timezone`, local holiday dates, the maximum `episode_days`, and the name of the `registration_clause`. Each named clause defines its own `start_event`, `stop_event`, `unit`, positive `count` and `due_time`. `register_entry` is reserved for the registration clause and comes from the dated register export.

The clock starts at the earliest cited start event visible in the episode. The local triggering date counts as day zero. Choose `calendar_days`, `weeks` of seven calendar days, or `business_days` counting Monday through Friday outside the supplied holidays. Use a local 24-hour `HH:MM` or `same` to keep the starting wall time. Telryn converts the resulting deadline to UTC. A due time skipped or repeated during a timezone transition needs an explicit policy decision. An event exactly on the deadline meets it.

Registration, summary resolution, final response and redress can have different evidence and deadlines. For example, a firm can configure a three-business-day summary-resolution check and a separate response clock. FCA DISP distinguishes the general eight-week written response from time limits for relevant payment-services and e-money complaints. The latter normally require a final response within 15 business days and provide an exceptional route up to 35 business days. An eight-week final-response target in the fictional rehearsal belongs to its example policy. Likewise US Regulation E distinguishes investigation, correction and notification, with conditional extensions and exceptions. Have the policy owner choose applicable triggers, clocks, holiday calendars and exceptions before running a real queue. Telryn evaluates explicit clauses and has no built-in jurisdiction classifier. [FCA DISP 1](https://handbook.fca.org.uk/handbook/disp1) · [CFPB Regulation E § 1005.11](https://www.consumerfinance.gov/rules-policy/regulations/1005/11/)

Here are two illustrative entries for a policy's `clauses` object. The event names identify cases the policy owner has already confirmed are on that path. Operators supply the applicable policy version, evidence and calendar for the period.

```json
{
  "summary-resolution": {"start_event": "eligible_complaint_received", "stop_event": "customer_accepts_resolution", "unit": "business_days", "count": 3, "due_time": "17:00"},
  "reg-e-initial-investigation": {"start_event": "eligible_standard_error_notice", "stop_event": "investigation_completed", "unit": "business_days", "count": 10, "due_time": "17:00"}
}
```

The first example tracks acceptance of resolution by the close of the third business day. A required summary-resolution communication needs its own evidence. The second illustrates the ordinary ten-business-day investigation path. The firm must evaluate new-account rules, provisional credit and extensions separately. The 17:00 close and holiday calendar in these examples are firm assumptions. Use them only where they match the responsible institution's actual business days and applicable policy. [FCA DISP 1.5](https://handbook.fca.org.uk/handbook/disp1) · [Regulation E § 1005.11(c)](https://www.consumerfinance.gov/rules-policy/regulations/1005/11/)

The report shows `met`, `late`, `running`, `past_due_without_evidence` or `unresolved` for each clause. Missing start evidence leaves the deadline unknown. A phone agent's closure, a refund or a completed referral stops a response clock only if a reviewer cites the actual event required by the clause. An overdue response with no cited stop describes the evidence available for review. A confirmed missing registration also requires a positive human complaint grade, a reliable join and the register owner's declared coverage. If a confirmed complaint has a past-due response or other policy clock with no cited completion, the report queues it for evidence review even when the register entry was on time. An owner can then find the response or arrange follow-up.

## Find issued weeks that need another review

Run the changed policy against the team's weekly reports. Supply individual report paths or a directory tree of `report.html` files.

```sh
python3 -m telryn policy-review inputs/policy-v2.json runs/
```

The JSON lists each affected week, path and changed clause. Telryn verifies each signed weekly report with the team trust store. It checks the saved clause contents, trigger, timezone, episode window and the holiday calendar used by business-day clocks. A newly applicable clause can affect an earlier week. Changing an unused clause leaves that week alone. Verified queue-comparison reports are listed separately. Unreadable directories or invalid evidence stop the scan. A directory scan stays inside the selected tree. Pass specific report paths for linked trees or reports stored elsewhere. A retry safely resumes reports already marked.

Each affected report keeps the original calculation and gains a signed re-review notice in its case register, JSON and weekly digest. The blind grading packet stays valid. Existing shadow proposals refer to the earlier signed revision, and a marked report cannot issue a fresh proposal. To review under the new policy, prepare a fresh weekly publication with the frozen population and original trust store, independently confirm grades and citations, and attach the revised evidence to that publication. Retain the original as the historical record. A local scan covers only the files supplied; include every relevant team's report directory using its own trust store.

The signed report retains the policy, pseudonymous links, cited excerpts and projected source records. Unquoted passage text and the raw subject and matter inputs stay outside it. Protect local inputs, the signing key, report, backups and exported JSON under the firm's retention and access rules.

A [versioned policy and rubric pack](policy-packs.md) can supply the frozen clock policy directly. In a pack-backed audit the manifest names the episode evidence and the report supplies the approved clocks. A custom clock can set `business_weekdays` to the institution's ISO business weekdays. Reports issued with this calendar require v0.9.0 or later.
