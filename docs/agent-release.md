# Check a support agent before release

The complaints lead chooses synthetic messages and a registration deadline for each. The AI agent owner runs them through the candidate prompt, policy and handoff in a test workspace. A separate read of the complaints register decides whether the release passes. An agent can promise to escalate and still leave the register empty.

## Run the local rehearsal

From the [matching source checkout](weekly-qa.md#use-the-example-input-files), run the synthetic agent and register together.

```sh
python3 -m examples.agent_release
```

The first simulated prompt registers three of four complaints. It drops the customer who cannot read the letters. The second registers all four. The example submits messages, writes register records through its routing logic, and opens SQLite in read-only mode to check what arrived. Its clock and phrases are synthetic. This verifies the release-check workflow. A production provider's ability to recognize complaints needs its own evidence.

## Use a test deployment

Copy the [example suite](../examples/agent_release/suite.json). Pin the workspace and the revisions actually deployed for the agent (including its prompt), approved policy and handoff. Set each case's deadline in elapsed minutes after receipt. These short test deadlines measure the agent-to-register handoff. Use the weekly policy clocks for customer cases. Run the following as a blocking CI step on each change to those revisions.

```sh
telryn seed suite.json --run-id "$TRIAL_ID" \
  --agent-adapter /opt/complaints/submit-test.py \
  --register-adapter /opt/complaints/read-test-register.py \
  --format json > release-receipt.json
```

Choose a fresh trial ID per suite and candidate. Each marker also binds the suite digest, including the candidate revisions and case wording, so evidence from a different suite cannot clear this gate. Retry the same trial after the register catches up. Exit **0** requires a unique, on-time register entry for every synthetic conversation. Exit **1** carries the case results for missing, late, duplicate, pending or incomplete evidence. Exit **2** means the configuration, adapter or evidence failed validation. Keep the receipt with the build that it gates. A later successful check still uses the first submission time.

### Adapter contract

Telryn invokes a trusted executable for each operation and exchanges one JSON object on stdin and stdout. For a Python file it uses the current interpreter in isolated mode. Paths are explicit CLI inputs. Supply a test-only sender and a read-only register credential. Each adapter must independently verify the identity and environment of its configured destination before writes or reads. Refuse a production endpoint or revision mismatch inside the adapter itself. Report revisions from the deployment or its authoritative release manifest.

Every request contains `schema: 1`, `environment: "test"`, `scope` (the four deployment fields), `operation`, `run_id` and `suite_digest`. Every response contains `schema: 1`, the observed environment and scope, and `request_digest`. To compute the digest, serialize the request as sorted-key, ASCII-escaped JSON with comma and colon separators and no whitespace, then SHA-256 hash the bytes. The digest correlates responses. Access controls on the bridge and underlying sources establish trust.

| Operation | Request fields | Required response |
| --- | --- | --- |
| `identify` | `role`, either `agent` or `register` | The role served by the configured adapter and the actual deployed scope. Both roles are checked before and after the run. |
| `submit` | `case_id`, `marker`, `phrase` | Deliver the phrase through the normal test support channel. Return its marker, unique `conversation_id` and original provider `received_at`. Persist or use native idempotency for the marker. Bind the run ID to its suite digest and reject reuse with changed inputs. |
| `read_register` | `markers` | Read authoritative dated entries for those markers from the test complaints register, including duplicates. Return `source_ref`, `as_of`, `complete_through` (or null when unknown), and `entries`. Each entry has `marker`, `conversation_id`, unique `entry_id` and `recorded_at`. |

Use timestamps with UTC offsets. Report a complete-through time only when the register feed is complete to that point. A confirmed positive entry is evidence even when full coverage is unknown. An empty or stale feed cannot clear the release. Keep test credentials outside the suite and use access-controlled CI storage for the receipt. The [local bridge](../examples/agent_release/bridge.py) implements the protocol with a synthetic agent and a separate register table. Vendor-specific bridge credentials and mappings belong to the deploying team.

## Run the installed demonstration

`telryn demo --agent-release --out runs/agent-check` sends synthetic messages to a local SQLite simulation. The first revision misses the request for readable letters. The second registers all four cases. The two JSON receipts, fixture suites and test database stay in the new output directory.

## Zendesk sandbox and a dated CSV register

For a configured Zendesk test workspace, use `telryn seed suite.json --run-id release-42 --agent-adapter zendesk-sandbox --register-adapter csv-register`. The sandbox's normal ticket-creation triggers must exercise the agent being released. An administrator provisions a synthetic requester with a bank-controlled test mailbox, a dedicated sandbox credential, and a deployment-owned ZIS configuration. Use the sandbox account ID from the administrator's inventory. Keep the local manifest, test receipt database and export directory private. The API contracts are exercised with offline fixtures. Tenant configuration and delivery through a live agent require a tenant test.

Set `TELRYN_ZENDESK_SUBDOMAIN`, `TELRYN_ZENDESK_TOKEN` (sandbox tickets), `TELRYN_SEED_ZIS_TOKEN` (sandbox configuration read) and `TELRYN_SEED_ZENDESK_MANIFEST` (local JSON path). Populate the manifest with the actual deployment and its test requester.

```json
{"schema":1,"environment":"test","scope":{"workspace":"your-sandbox","agent_revision":"build-42","policy_revision":"p3","handoff_revision":"h1"},"integration":"sandbox_release","account_id":123,"test_requester_id":45}
```

Provision a ZIS configuration in that integration with scope `telryn-release`. Its `config` contains exactly the same `environment`, `scope` and `test_requester_id`. The adapter checks the remote account, deployment revisions and requester before submitting. It reserves the marker locally, sends it as both the external ID and idempotency key, and persists the original receipt. If a write has an unknown outcome, reconcile the ticket with its external ID before repairing the local receipt. The adapter sets both the ticket requester and submitter to the synthetic customer. Zendesk uses the submitter as the author of the first comment, so the test exercises a customer-originated message. The environment must route notifications only to controlled test destinations. [Zendesk requester and submitter](https://developer.zendesk.com/api-reference/ticketing/tickets/tickets/#requesters-and-submitters) [Zendesk configuration API](https://developer.zendesk.com/api-reference/integration-services/configs/configs/) · [Ticket idempotency](https://developer.zendesk.com/api-reference/ticketing/introduction/)

For the independent register, set `TELRYN_SEED_REGISTER_MANIFEST` to a JSON file with `schema: 1`, `environment: "test"`, the deployer's original `scope`, `file`, `sha256`, `source_ref`, `as_of` and `complete_through`. Use a relative CSV in the same directory with header `marker,conversation_id,entry_id,recorded_at`. The exporter states how far its ledger is complete. Use null for unknown coverage. Refresh the snapshot and rerun the same run ID after asynchronous handoff. The adapter checks the frozen bytes and includes every matching entry, including duplicate registrations with different entry IDs.
