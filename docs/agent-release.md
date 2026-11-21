# Check a support agent before release

The complaints lead chooses synthetic messages and a registration deadline for each. The AI agent owner runs them through the candidate prompt, policy and handoff in a test workspace. A separate read of the complaints register decides whether the release passes. An agent can promise to escalate and still leave the register empty.

## Run the local rehearsal

```sh
python3 -m examples.agent_release
```

The first simulated prompt registers three of four complaints. It drops the customer who cannot read the letters. The second registers all four. The example submits messages, writes register records through its routing logic, and opens SQLite in read-only mode to check what arrived. Its clock and phrases are synthetic. This verifies the release-check workflow. A production provider's ability to recognize complaints needs its own evidence.

## Use a test deployment

Copy the [example suite](../examples/agent_release/suite.json). Pin the workspace and the revisions actually deployed for the agent (including its prompt), approved policy and handoff. Set each case's deadline in elapsed minutes after receipt. These short test deadlines measure the agent-to-register handoff. Use the weekly policy clocks for customer cases. Run the following as a blocking CI step on each change to those revisions.

```sh
python3 -m telryn seed suite.json --run-id "$TRIAL_ID" \
  --agent-adapter /opt/complaints/submit-test.py \
  --register-adapter /opt/complaints/read-test-register.py \
  --format json > release-receipt.json
```

Choose a fresh trial ID per suite and candidate. Retry the same trial after the register catches up. Exit **0** requires a unique, on-time register entry for every synthetic conversation. Exit **1** carries the case results for missing, late, duplicate, pending or incomplete evidence. Exit **2** means the configuration, adapter or evidence failed validation. Keep the receipt with the build that it gates. A later successful check still uses the first submission time.

### Adapter contract

Telryn invokes a trusted executable for each operation and exchanges one JSON object on stdin and stdout. For a Python file it uses the current interpreter in isolated mode. Paths are explicit CLI inputs. Supply a test-only sender and a read-only register credential. Each adapter must independently verify the identity and environment of its configured destination before writes or reads. Refuse a production endpoint or revision mismatch inside the adapter itself. Report revisions from the deployment or its authoritative release manifest.

Every request contains `schema: 1`, `environment: "test"`, `scope` (the four deployment fields), `operation`, `run_id` and `suite_digest`. Every response contains `schema: 1`, the observed environment and scope, and `request_digest`. To compute the digest, serialize the request as sorted-key, ASCII-escaped JSON with comma and colon separators and no whitespace, then SHA-256 hash the bytes. The digest correlates responses. Access controls on the bridge and underlying sources establish trust.

| Operation | Request fields | Required response |
| --- | --- | --- |
| `identify` | `role`, either `agent` or `register` | The role served by the configured adapter and the actual deployed scope. Both roles are checked before and after the run. |
| `submit` | `case_id`, `marker`, `phrase` | Deliver the phrase through the normal test support channel. Return its marker, unique `conversation_id` and original provider `received_at`. Persist or use native idempotency for the marker. Bind the run ID to its suite digest and reject reuse with changed inputs. |
| `read_register` | `markers` | Read authoritative dated entries for those markers from the test complaints register, including duplicates. Return `source_ref`, `as_of`, `complete_through` (or null when unknown), and `entries`. Each entry has `marker`, `conversation_id`, unique `entry_id` and `recorded_at`. |

Use timestamps with UTC offsets. Report a complete-through time only when the register feed is complete to that point. A confirmed positive entry is evidence even when full coverage is unknown. An empty or stale feed cannot clear the release. Keep test credentials outside the suite and use access-controlled CI storage for the receipt. The [local bridge](../examples/agent_release/bridge.py) implements the protocol with a synthetic agent and a separate register table. Vendor-specific bridge credentials and mappings belong to the deploying team.
