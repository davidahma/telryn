# Run Telryn inside your network

The Linux image runs the weekly audit and QA desk as UID/GID 65532 on amd64 and arm64. It includes the CLI, examples, policy packs, checks, time zones and the Bedrock SDK. Run it with Docker Engine on Linux or Docker Desktop using Linux containers on macOS or Windows. The weekly command works with networking disabled. The desk needs an inbound connection from its operator. Both workflows use local files.

## Verify the release and load the image

Download the release files to an empty directory using GitHub CLI, or download them from the release page. The following verification commands use a POSIX shell, Cosign 3, and GNU sha256sum (on macOS use `shasum -a 256 -c SHA256SUMS.txt`). On Windows run them in WSL, then use the same Docker Desktop engine to start the desk.

```sh
gh release download v0.9.1 --repo davidahma/telryn
cosign verify-blob --bundle SHA256SUMS.sigstore.json \
  --certificate-identity 'https://github.com/davidahma/telryn/.github/workflows/release-container.yml@refs/heads/main' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' SHA256SUMS.txt
sha256sum -c SHA256SUMS.txt
docker load -i telryn-0.9.1.oci.tar
```

The signed manifest covers the source archive, the OCI archive, the reports, both SPDX SBOMs, the image digest inventory and the consolidated security review pack. Signature verification normally contacts the Sigstore trust service. Perform this step on the approved intake host before moving verified files into a restricted environment. The archive works independently of registry access. Use a Docker engine with the containerd image store for multi-platform OCI imports. [Docker image store](https://docs.docker.com/engine/storage/containerd/) · [Cosign verification](https://docs.sigstore.dev/cosign/verifying/verify/)

For an authorized registry pull, read the exact index digest from the verified inventory. You can mirror it to the firm's registry after verifying the image signature. Each child image also has an SPDX attestation. The example verifies the amd64 child.

```sh
image=ghcr.io/davidahma/telryn
index=$(jq -r '.digest' image-digests.json)
child=$(jq -r '.platforms["linux/amd64"].digest' image-digests.json)
identity='https://github.com/davidahma/telryn/.github/workflows/release-container.yml@refs/heads/main'
issuer='https://token.actions.githubusercontent.com'
cosign verify --certificate-identity "$identity" --certificate-oidc-issuer "$issuer" "$image@$index"
cosign verify-attestation --type spdxjson --certificate-identity "$identity" \
  --certificate-oidc-issuer "$issuer" "$image@$child"
docker pull "$image@$index"
```

## Start the private desk

Create separate Docker-managed volumes for the audit files and team trust store. Back up both encrypted, including their ownership and permissions. A local Docker administrator can access the files and the desk's session URL.

```sh
docker volume create telryn-data
docker volume create telryn-trust
docker run --name telryn-desk --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m \
  --publish 127.0.0.1:8765:8765 \
  --volume telryn-data:/data --volume telryn-trust:/trust \
  ghcr.io/davidahma/telryn:v0.9.1
```

Open the `http://127.0.0.1:8765/…/` URL printed in the terminal. Use a Docker host under the lead's control, publish only to its loopback address, and protect container logs as session credentials. Host and Origin checks expect the same port inside and outside the container. To change ports, update both sides of the mapping and pass `desk --out /data/runs --container-port PORT --no-browser` after the image name. The source CLI still binds loopback by default. Teams using Kubernetes or a shared host must also enforce operator access, network isolation and approved egress at the deployment boundary. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/) · [Security review](security-review.md)

## Run a weekly audit offline

This command runs the bundled plan and persists the report and packet. Choose an unused output directory for each audit. The second command verifies the signed report against the same trust store.

```sh
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m \
  --volume telryn-data:/data --volume telryn-trust:/trust \
  ghcr.io/davidahma/telryn:v0.9.1 weekly examples/fintech/week.toml --out /data/runs/fintech
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m \
  --volume telryn-data:/data --volume telryn-trust:/trust \
  ghcr.io/davidahma/telryn:v0.9.1 weekly /data/runs/fintech/report.html --format json
```

For your queue, mount the plan and inputs read-only under `/input` and pass its path instead of `examples/fintech/week.toml`. A custom bind-mounted output or trust directory must be writable by container UID 65532. The named volumes in the examples receive the image's initial ownership. Python's per-user package path is disabled so the interpreter loads its installed libraries from the image. Keep source credentials out of the image and mount only the records needed for the job. Enable network access explicitly for authorized vendor pulls, SMTP delivery or model extraction.

## Release procedure

Maintainers push an annotated version tag and create a draft release with the source archive from the CI-qualified commit. Dispatch `release-container.yml` from protected `main` with `tag` and `commit`. The workflow requires the tag to identify a CI-qualified commit in main’s history. The publishing job checks the draft with its write credential before it builds anything. It builds the tagged product with the release tooling from the selected main revision and checks the source contents, executable bits and completed main CI. It runs the Ubuntu census and the synthetic complaints rehearsal from that checkout using a fresh private trust store, verifies both reports and uploads the HTML. The synthetic report includes Mira’s case from the saved SQL search. The Telryn draw can differ from the v0.8 first run. The fixture still has 1,350 possible two-week draws. [Rehearsal and sensitivity results](../examples/complaints_pilot/README.md)

The workflow builds a multi-platform OCI archive, records each platform digest, publishes that exact index to GHCR, signs using GitHub's workflow identity and verifies both platform attestations. The draft receives the verified materials and signed checksum manifest. The maintainer verifies the downloaded artifacts and publishes the immutable release.
