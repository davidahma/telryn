# Trace a complaint from conversation to owner action

A payments complaints lead can check a sampled conversation against the register in force at the policy deadline. The same report shows later registration, owner acceptance and completed work. Start with the buyer's approved policy and the historical records for one queue. The [two-week pilot](evaluation.md) describes how to compare that work with the team's usual process.

1. Ask a reviewer whether the frozen conversation met the approved complaint definition. Use the weekly grading packet and its existing review and adjudication steps.
2. Retain the register and owner-work exports, their original revisions, and a record of what the extract covers. For each sampled conversation establish the stable register match key, the applicable policy and the evidence for its deadline. The source can be a cited conversation with a versioned clock or an approved case worksheet.
3. Attach the two dated exports to the signed weekly report. Start with a synthetic rehearsal or a queue the team has authorized.

```sh
telryn weekly runs/complaints/report.html --complaint-records inputs/records.json
telryn weekly runs/complaints/report.html --format json
```

Open **Complaint registration and owner action** near the top of the report. Cases whose deadline has passed by follow-up and whose registration is still missing appear in three queues. A deadline can pass between the cutoff and follow-up. The statement includes those cases in open work while retaining the original estimate at cutoff. The first needs an owner to accept the work. The second has recorded acceptance. The third has recorded completion and a register discrepancy to reconcile. A fourth queue flags missing evidence. Expand a case to inspect the policy, conversation revision, source references and timestamps. The JSON response exposes the same lists under `complaints.worklist`.

The team can trace a repeat contact from chat to phone, calculate registration and response deadlines from cited passages, and flag earlier weeks affected by a policy change. [Episode and clock guide](complaint-clocks.md)

## Supply the frozen scope

Use the exact control-profile scope on the register manifest so the lead can verify which tenant and queue supplied the records. The manifest names the files relative to its own directory. Use the `audit_id` from the blind packet, the weekly rubric ID, and a scope that identifies the tenant, queue and systems covered. In this synthetic example an internal policy worksheet supplies the deadline. Supply an explicit, offset-aware instant for each case. The operator owns calendar calculations, exceptions, jurisdiction and the underlying grade.

```json
{
  "schema": 1,
  "audit_id": "W-replace-with-the-actual-id-from-grades.csv",
  "rubric": "payments-complaints-v1",
  "scope": "Payments UK / cards / complaint register and work queue",
  "cutoff": "2026-11-09T00:00:00Z",
  "as_of": "2026-11-11T16:00:00Z",
  "register": {
    "file": "register.csv",
    "source_ref": "register/export-2026-11-11",
    "complete_through": "2026-11-11T16:00:00Z"
  },
  "actions": {
    "file": "actions.csv",
    "source_ref": "work/export-2026-11-11",
    "complete_through": "2026-11-11T16:00:00Z"
  },
  "cases": [
    {
      "case_id": "C017",
      "revision": "close-v3",
      "deadline": "2026-11-08T12:00:00Z",
      "policy_ref": "policy/v1/case-deadlines/C017",
      "match_key": "cards/C017"
    }
  ]
}
```

The audit `cutoff` sets the historical question. `as_of` is the latest observation time. Each export's `complete_through` is the source owner's attestation that it covers **all relevant history in this scope** through that inclusive instant, including records created before the reporting week, superseded entries and any other identifiers the queue used. For the register, this includes verifying that each supplied key uniquely identifies this case throughout the scope. A current snapshot can establish the existence of a recorded event with reliable historical provenance. If it cannot establish absence, set `complete_through` to `null`. An empty export with unknown coverage cannot establish a miss. For incomplete matching, use `null` for the match key or deadline as appropriate. A sampled case omitted from the manifest stays visible with unresolved evidence.

The register CSV contains one row per immutable entry. Use the authoritative time at which the entry was first available in the register. An effective date entered retrospectively can give the wrong historical answer.

```csv
match_key,entry_id,recorded_at,evidence_ref
cards/C017,R900,2026-11-10T10:21:00Z,register/R900/rev1
```

The owner CSV records events from the authoritative case-level work history. Use one immutable event ID per row. `requested` records a referral, `accepted` records the owner's acceptance, and `completed` records observed completion. Include a stable source reference that a second reviewer can reopen. If the workflow has concurrent tasks or the event history lacks a reliable completed-work status, supply an incomplete work export and inspect the events directly.

```csv
case_id,action_id,status,owner,recorded_at,evidence_ref
C017,A71,requested,complaints-desk,2026-11-09T09:17:00Z,work/event/A71
C017,A72,accepted,complaints-lead,2026-11-09T10:05:00Z,work/event/A72
C017,A73,completed,complaints-lead,2026-11-10T11:35:00Z,work/event/A73
```

With a positive policy grade and complete coverage as declared, C017 was missing at cutoff. The owner subsequently accepted the work, the register gained entry R900, and A73 recorded completion. C017 therefore drops out of the outstanding registration queues. These times document a sequence. To assess whether Telryn brought action forward, use the independent comparison in the pilot and the customer’s own attribution evidence.

## Reproduce the decision

| Evidence | Interpretation |
| --- | --- |
| Recorded at or before the inclusive policy deadline | Registered on time. |
| An entry after the deadline and before or at cutoff | Late if the register covers the deadline. Earlier registration remains unresolved with incomplete coverage. |
| No matching entry by cutoff | Missing only when the deadline has passed, coverage reaches cutoff and the key is reliable. |
| Missing key, shared key, or multiple candidate records visible at cutoff | Unresolved. The report exposes the match status and the dated evidence available for review. |
| Deadline after cutoff, with a reliable key and no entry observed yet | The case was not yet due. A unique entry already visible by cutoff counts as on time, even if the deadline is later. |
| Owner history | A referral alone shows requested work. Incomplete coverage, simultaneous conflicting statuses and apparent regressions such as a new request after completion leave current ownership unresolved. Individual events remain visible. |

The raw exports can include records for other cases. The private report retains the bytes' SHA-256 digests and the projected records relevant to the sample. Each file and the manifest may be up to 32 MiB. Preserve the source files and scope attestation under the same retention policy as the report. The signature authenticates the team’s saved record. The source owner is responsible for export completeness and record provenance.

A later follow-up uses the same command. Set `previous_receipt` in the new manifest to the current `complaints.receipt` from the JSON result. Keep the cutoff, scope, known case mappings and earlier events. You can fill a deadline or key previously marked unknown. Extend `as_of` and the coverage times as records become available. A new mapping cannot invalidate another case’s previously unambiguous register match at the issued horizon. Changed or removed historical entries, or a backfill that contradicts an earlier completeness attestation, require an investigation and a fresh audit. An identical retry preserves the report revision. Each accepted change advances the signed report and requires a new current proposal for a future shadow decision.

Use v0.8.0 or later to update a report with a control profile or linked episodes. An older manual-deadline complaints attachment needs v0.7.0 or later. Grading continues with the original blind packet. Register evidence stays in the lead’s report. The weekly population estimates continue to use the human votes. A register match or proposed work item leaves those votes intact. The observed case queues describe the sample. Customer adoption, payment and effort saved require field evidence.
