# Read a weekly audit from the warehouse

Run a warehouse capture with a read-only service identity. The connector issues one fixed SELECT for the frozen conversation cohort and one each for the complaint register and owner actions. Each query carries the same historical observation timestamp. Telryn reads named, approved physical tables that the bank has projected into the fields below. The bank defines queue membership and maintains the original source records. Capture runs only when invoked; the resulting files support offline screening, weekly sampling and register reconciliation.

## Configure the read identity and tables

| Provider | Client and credentials | Historical read |
| --- | --- | --- |
| Snowflake | Install `snowflake-connector-python`. Set `TELRYN_SNOWFLAKE_CONNECTION` to the bank’s configured Snowflake connection profile with warehouse usage and table SELECT permissions. | `AT (TIMESTAMP => …)` on each table. |
| BigQuery | Install `google-cloud-bigquery`. Set `TELRYN_BIGQUERY_PROJECT` and `TELRYN_BIGQUERY_LOCATION`. Use Application Default Credentials with the ability to run query jobs and read the three tables. Set `max_bytes_billed` in the plan. | `FOR SYSTEM_TIME AS OF` on each native BigQuery table. |
| Databricks | Install `databricks-sql-connector` 4.9 or later. Set `DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, and `DATABRICKS_TOKEN` using the dedicated read identity. | `TIMESTAMP AS OF` on each Delta table. |

Use a snapshot that is still available within the provider’s time-travel window. The three native drivers must be installed into the environment that runs Telryn. The base package still uses the standard library. [Snowflake time travel](https://docs.snowflake.com/en/sql-reference/constructs/at-before) · [BigQuery history](https://docs.cloud.google.com/bigquery/docs/access-historical-data) · [Databricks table history](https://docs.databricks.com/aws/en/tables/history)

Keep credentials in the bank’s credential store or SDK profile. Grant each identity only access to the approved projections. Redact transcript messages upstream and keep access to the captured files under the bank’s retention policy. Time travel establishes which table version was queried. Source owners attest how far the projection covers original records, including late arrivals, edits and deleted or superseded entries. The conversation owner must affirm complete coverage of eligible closures through the week end. Use null when register or action coverage is unknown. The connector checks duplicate identifiers and prevents partial publication. A historical table snapshot by itself establishes neither complete source history nor how or when an owner actually acted.

## Project the columns

Every table has a text `scope_key` column. Conversation tables also have a text `audit_week` with the UTC start date of the reporting week. Use the closed, redacted revision visible at its first qualifying completion for that week.

| Source | Selected columns in the bank’s table |
| --- | --- |
| Conversations | `case_id, revision, evidence_ref, screen_flag, channel, agent_type, product, completed_at, messages_json`. Use SQL BOOLEAN for the screen and text for the other columns. `completed_at` is the offset-aware ISO 8601 instant of completion. `messages_json` is a JSON array of redacted `{"role": "customer", "text": "..."}` objects. Allowed roles are customer, agent, system and unknown. |
| Register | `match_key, entry_id, recorded_at, evidence_ref`, all text. |
| Owner actions | `case_id, action_id, status, owner, recorded_at, evidence_ref`, all text. Use requested, accepted or completed for status. |

The register and action projections include history before the reporting week. Use the same stable case and source references throughout. Select physical historical tables from the audit’s own source scope. In Snowflake, the connection’s current schema must resolve the conventional unquoted identifiers; use table and projection column names that the provider can resolve without quoted special characters.

## Capture and verify

```json
{
  "schema": 1,
  "provider": "snowflake",
  "scope": "UK cards servicing",
  "scope_key": "cards-uk",
  "week": "2026-10-05",
  "snapshot_at": "2026-10-12T18:00:00Z",
  "max_rows": 50000,
  "sources": {
    "conversations": {
      "table": "AUDIT.PAYMENTS.CLOSED_CASES",
      "complete_through": "2026-10-12T18:00:00Z"
    },
    "register": {
      "table": "AUDIT.PAYMENTS.REGISTER",
      "complete_through": "2026-10-12T18:00:00Z"
    },
    "actions": {
      "table": "AUDIT.PAYMENTS.OWNER_EVENTS",
      "complete_through": null
    }
  }
}
```

```sh
telryn warehouse capture warehouse.json --out runs/week-41-source
telryn warehouse verify runs/week-41-source --sha256 "$APPROVED_SNAPSHOT_SHA256"
```

Use the actual Monday, snapshot and source coverage for your run. Capture prints the digest for `snapshot.json`. Keep that digest in an authenticated audit record. Verification compares it and each recorded file digest. The receipt records provider query IDs, snapshot time, named projections, SQL hashes and file hashes. Query results are sorted by immutable case or entry IDs. Text and numeric values retain their declared meaning through the CSV and JSON representations. Use the connector when the projection fits the configured row budget and the evidence files fit Telryn’s 32 MiB limit.

Use `population.csv` and its companion coverage document with the weekly plan, and `transcripts.jsonl` with the optional screen command. Build the complaint manifest after issuing the weekly grading packet. In its register and actions sections, copy `complaint_source` from the corresponding snapshot source. Place the manifest next to the exported files. The reviewer supplies a match key, deadline and supporting evidence per sampled case. Send the manifest through the existing weekly reconciliation flow. [Complaint record manifest](complaint-records.md) · [Weekly operation](weekly-qa.md)
