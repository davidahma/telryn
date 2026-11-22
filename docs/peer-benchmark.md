# Contribute to a peer benchmark

A third party can run the reference collector on its own Linux or macOS host. It receives signed aggregate files and returns one frozen view for a vendor configuration and quarter. This gives an approved contributor a peer view while the firms keep transcripts, case IDs, team keys and work records in their own systems. Telryn supplies commands for the exchange. The operator controls delivery, membership and disclosure.

## Agree the cohort before collecting

The operator and participating risk teams assign aliases to the vendor, configuration and common protocol. Agree on the score decision, complaint rubric, deadline rules, eligible population, included weeks and disclosure threshold. Each firm uses those values in its opt-in export. The collector can validate signatures and structure. The protocol and firm attestation establish measurement comparability. Use a separate, reviewed cohort for materially different configurations. Synthetic and operational figures have separate views.

Generate a dedicated OpenSSH Ed25519 key pair. Keep the private key with the firm. The collector enrols the public key through an authenticated channel, confirms control of the key, and maps it to one firm alias. Each firm has one key in a given store. Use the operator's firm alias and protocol identifier.

```sh
ssh-keygen -t ed25519 -N '' -f ./firm-signing-key
telryn peer-benchmark contribute runs/benchmark/aggregate.json \
  --firm firm-a --protocol payments-v1 --opt-in \
  --signing-key ./firm-signing-key --out runs/contribution
```

Send `runs/contribution/signed.json` through the approved channel. The firm signer should review the scope, dates and contents before sending. An export with no shareable metrics cannot contribute. Only the metrics approved by the local exporter are included.

## Run the collector

Create a private directory and a roster alongside the enrolled public keys. An example roster is `{"schema":1,"protocols":["payments-v1"],"firms":{"firm-a":"firm-a.pub","firm-b":"firm-b.pub"}}`. The collector operator controls this file and the SQLite store. Retain consistent firm and configuration aliases across the study. Losing the store also loses evidence of earlier disclosures.

```sh
telryn peer-benchmark receive --store ./collector --roster ./roster.json \
  received/firm-a/signed.json
telryn peer-benchmark seal --store ./collector --roster ./roster.json COHORT_SHA256
```

A cohort fixes the vendor configuration, calendar quarter, included weeks, population, protocol and evidence type. Submission is idempotent for identical data. Seal once at least six approved firms have a shareable metric for every included week. The sealed figures stay fixed. The same vendor configuration and quarter have one frozen result per evidence type, including across different requested week sets and sampling frames. Aliases and protocol access remain under operator policy.

An approved firm signs a request with the approved cohort digest and its enrolled key. The operator verifies the request and returns the result over an authenticated channel for the signer.

```sh
telryn peer-benchmark request COHORT_SHA256 --firm firm-a \
  --signing-key ./firm-signing-key --out runs/request
telryn peer-benchmark view --store ./collector --roster ./roster.json \
  received/firm-a-request/signed.json
```

The signed contribution must have released the metric for every included week. Each returned metric requires five other firms with that same coverage. The view includes the contributing firm and is identical for all eligible recipients. It displays firm and conversation counts, weighted rate endpoints and a point estimate when every included estimate is defined. Endpoints describe the reviewed corpus under its stated assumptions. Recipient-level privacy, collusion risk, key rotation and contribution quality remain the collector operator's responsibilities. OpenSSH is required for signing and verification.
