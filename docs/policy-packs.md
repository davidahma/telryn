# Use a versioned complaint policy and rubric

A pack is one JSON file containing the reviewer’s question, an ordered category list, source references and dated clock clauses. The complaints lead selects a version for the queue. Reviewers receive its question and category options in their usual blinded packet. The signed report retains the complete pack, its version and its canonical SHA-256 digest. Later changes to the source file leave issued findings reproducible.

## Run the supplied example

```sh
python3 -m telryn pack list
python3 -m telryn pack show firm-example@1.0.0
python3 -m telryn weekly examples/policy_packs/week.toml --out runs/pack-week
python3 -m telryn weekly runs/pack-week/report.html --format json
```

This run creates a signed report and one grading packet for a synthetic payments queue. The weekly plan selects an exact `policy_pack` and a separate `control` file. The latter supplies the firm's queue, channel coverage, owners, review dates and onboarding time. The pack supplies `rubric`, `rubric_question` and ordered categories. If the plan or control repeats those fields, they must agree. [Example plan](../examples/policy_packs/week.toml) · [Example control profile](../examples/policy_packs/control.json)

For the browser desk, save a pack from `pack show` to a local JSON file. Upload it with the weekly control profile. The pack supplies the question and rubric version in both the preview and issued audit. Without a pack the desk accepts its existing question and category inputs.

## Choose a starting point

| Pack | What its clauses measure | What the policy owner must decide |
| --- | --- | --- |
| `fca-disp@1.0.0` | Third-business-day summary-resolution route, general eight-week written response, ordinary 15-business-day PSD/EMD final response, or exceptional 15-day holding and 35-day final responses. | Select the applicable route and cite receipt and qualifying response. Follow separate communication and acknowledgement requirements. The eight-week clause accepts the permitted written response for the applicable case. Check current exclusions, amendments and any temporary rules for the product. [FCA DISP 1.5–1.6](https://handbook.fca.org.uk/handbook/disp1) |
| `us-reg-e@1.0.0` | Ordinary ten-business-day investigation or applicable 20-business-day new-account path. Separate conditional 45- or 90-calendar-day investigation paths. One business day to correct a determined error and three to report investigation results. | Determine whether the notice qualifies under §1005.11. Check provisional credit, disclosure and availability conditions and any exceptions before using an extended path. The trigger for an extended investigation must cite the **original notice date**. Match business days to the institution's actual functions. [Regulation E §1005.11](https://www.consumerfinance.gov/rules-policy/regulations/1005/11/) · [Business-day definition](https://www.consumerfinance.gov/rules-policy/regulations/1005/2/) |
| `cfpb-company-portal@1.0.0` | Response to a complaint forwarded to the company’s CFPB portal within 15 calendar days, and a final response within 60 calendar days from routing on the in-progress path. | Verify the portal forwarding date, whether the company submitted a timely in-progress response, and the dated final response. A complaint made only to the firm has its own intake process. [CFPB company response process](https://www.consumerfinance.gov/compliance/consumer-complaint-program/company-process/) |
| `firm-example@1.0.0` | A fictional payments firm's internal registration and owner-response commitments, with a three-level taxonomy. | Replace the question, category order, eligibility events, jurisdiction and internal clock settings with the firm's actual procedure. [Example pack](../telryn/policy_packs/firm-example@1.0.0.json) |

These starter files were checked against the linked sources on 21 November 2026. Each includes an **illustrative internal one-business-day registration target**, a 17:00 local close, an empty holiday list and a 90-day episode window. Those are example firm settings. A start event asserts that a human has determined the clock applies. It must cite a dated passage in the retained evidence. Clocks with different eligibility conditions have different event names so one customer contact need not activate every route. A missing start remains unresolved. These checks describe the dated evidence supplied to Telryn. A legal determination of compliance requires the full facts and the applicable rules.

## Adopt the firm’s own version

```sh
python3 -m telryn pack show us-reg-e@1.0.0 > lender-policy.json
```

1. Give `clock_policy.id` a firm-owned lowercase ID and set `clock_policy.version` to a major.minor.patch version. Edit the applicability note, sources, question and categories. Order categories from lowest to highest priority so Telryn can recognize downgrade requests. Keep the policy owner’s approval and effective dates in your policy repository.
2. Set the local time zone, holidays and close of business for each clock. The optional `business_weekdays` array uses ISO weekdays, Monday 1 through Sunday 7. The default is Monday to Friday. An institution that performs its relevant business functions on Saturday can include 6. Verify the resulting dates against the approved operational calendar. If the calendar or exception cannot be represented, leave that route for manual review.
3. Run `python3 -m telryn pack show lender-policy.json` to validate the file. Set `policy_pack = "lender-policy.json"` in the weekly TOML beside it. Use a new version for each change and retain the approved file. Telryn rejects an edited file that reuses a shipped pack’s identity. A local pack requires no plug-in, expression evaluator or network connection.

## Attach the register and revisit a change

Use the normal complaints manifest and the audit ID from the packet. In a pack-backed report, set the manifest `rubric` and each case's `policy_ref` to the exact issued reference, for example `example-lender@1.0.0`. Supply `episode_evidence`, leave `deadline` null and omit `clock_policy`. Telryn already has the policy bytes signed into the report. Missing contacts or citations leave the affected clock unresolved. [Manifest fields](complaint-records.md) · [Contact events and source passages](complaint-clocks.md)

After changing the firm's approved pack, run the following against previously issued reports.

```sh
python3 -m telryn policy-review lender-policy-v1.1.0.json runs/
```

The scan checks reports using the same policy ID. Reusing a bundled identity for an edited pack is rejected at intake and at review. A signed change notice records the revised pack digest, even when only a clock changed. A change to a used clock clause, its calendar or its trigger can mark the affected week. A change to the reviewer question, ranked taxonomy, applicability or source references flags pack-backed grades for review, including reports still awaiting the register. The signed notice preserves the original result and identifies what changed. Reports using packs require v0.9.0 or newer to update. Older ordinary weekly reports retain their documented readers and formats. [Review and replay](trust.md)
