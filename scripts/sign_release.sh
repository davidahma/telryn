#!/usr/bin/env bash
# Run only from the trusted, CI-qualified main workflow after its tag/draft checks.
set -euo pipefail
export GH_REPO="$GITHUB_REPOSITORY"
version=${TAG#v}
image="ghcr.io/${GITHUB_REPOSITORY,,}"
identity="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/.github/workflows/release-container.yml@refs/heads/main"
issuer="https://token.actions.githubusercontent.com"
authority=(--certificate-identity "$identity" --certificate-oidc-issuer "$issuer")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
out="$work/release"
mkdir "$out" "$work/layout"
source_name="telryn-$version.tar.gz"
archive_name="telryn-$version.oci.tar"
gh release download "$TAG" --dir "$out" -p "$source_name" -p ubuntu-support-audit.html -p synthetic-complaints-audit.html -p SHA256SUMS.txt
python scripts/release_artifacts.py source "$out/$source_name" "$TAG" "$COMMIT"
# Recheck only the three approved initial assets. A retried draft's manifest may
# also include the generated artifacts from an earlier workflow attempt.
for file in "$source_name" synthetic-complaints-audit.html ubuntu-support-audit.html; do
  actual=$(sha256sum "$out/$file" | cut -d' ' -f1)
  test "$(awk -v f="$file" '$2 == f {print $1}' "$out/SHA256SUMS.txt")" = "$actual"
done
echo "$GH_TOKEN" | docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin
docker buildx build --platform linux/amd64,linux/arm64 --provenance=false --sbom=false \
  --build-arg "REVISION=$COMMIT" -t "$image:$TAG" \
  --output "type=oci,dest=$out/$archive_name" .
tar -xf "$out/$archive_name" -C "$work/layout"
python scripts/release_artifacts.py inventory "$work/layout" "$image" "$TAG" "$COMMIT" "$out/image-digests.json"
skopeo copy --all --preserve-digests --authfile "$HOME/.docker/config.json" \
  "oci-archive:$out/$archive_name" "docker://$image:$TAG"
digest=$(jq -r '.digest' "$out/image-digests.json")
remote_digest=$(skopeo inspect --raw --authfile "$HOME/.docker/config.json" "docker://$image:$TAG" | sha256sum | cut -d' ' -f1)
test "$digest" = "sha256:$remote_digest"
cosign sign --yes "$image@$digest"
cosign verify "${authority[@]}" "$image@$digest" > /dev/null
for arch in amd64 arm64; do
  sbom="telryn-linux-$arch.spdx.json"
  "$SYFT" "oci-dir:$work/$arch" --source-name "telryn-linux-$arch" --source-version "$version" -o "spdx-json=$out/$sbom"
  jq -e '.spdxVersion == "SPDX-2.3" and any(.packages[]; .name == "boto3") and (.packages | length > 5)' "$out/$sbom" > /dev/null
  platform_digest=$(jq -r --arg platform "linux/$arch" '.platforms[$platform].digest' "$out/image-digests.json")
  cosign attest --yes --type spdxjson --predicate "$out/$sbom" "$image@$platform_digest"
  cosign verify-attestation "${authority[@]}" --type spdxjson "$image@$platform_digest" > "$work/$arch-attestation.json"
  jq -se --slurpfile sbom "$out/$sbom" 'flatten | any(.[]; (.payload | @base64d | fromjson | .predicate) == $sbom[0])' "$work/$arch-attestation.json" > /dev/null
done

pack="$work/telryn-security-review"
mkdir "$pack"
cp docs/security-review.md "$pack/README.md"
cp SECURITY.md docs/container.md "$out/image-digests.json" "$out"/telryn-linux-*.spdx.json "$pack/"
tar -C "$work" -czf "$out/telryn-security-review.tar.gz" telryn-security-review
(
  cd "$out"
  sha256sum "$source_name" ubuntu-support-audit.html synthetic-complaints-audit.html "$archive_name" \
    telryn-linux-amd64.spdx.json telryn-linux-arm64.spdx.json image-digests.json telryn-security-review.tar.gz > SHA256SUMS.txt
  sha256sum -c SHA256SUMS.txt
  cosign sign-blob --yes --bundle SHA256SUMS.sigstore.json SHA256SUMS.txt
  cosign verify-blob "${authority[@]}" --bundle SHA256SUMS.sigstore.json SHA256SUMS.txt
  gh release view "$TAG" --json isDraft,targetCommitish | jq -e --arg sha "$COMMIT" '.isDraft and .targetCommitish == $sha'
  gh release upload "$TAG" --clobber "$archive_name" telryn-linux-amd64.spdx.json telryn-linux-arm64.spdx.json \
    image-digests.json telryn-security-review.tar.gz SHA256SUMS.txt SHA256SUMS.sigstore.json
)
