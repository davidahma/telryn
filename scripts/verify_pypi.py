"""Verify PyPI publish attestations for the two distributions tested by CI."""

import sys
from pathlib import Path
from urllib.request import urlopen

from pypi_attestations import Distribution, Provenance
from sigstore.verify.policy import Identity


def verify(file, version):
    repository = "davidahma/telryn"
    policy = Identity(
        identity=f"https://github.com/{repository}/.github/workflows/workflow.yml@refs/heads/main",
        issuer="https://token.actions.githubusercontent.com",
    )
    url = f"https://pypi.org/integrity/telryn/{version}/{file.name}/provenance"
    with urlopen(url, timeout=30) as response:
        provenance = Provenance.model_validate_json(response.read())
    expected = {
        "kind": "GitHub",
        "repository": repository,
        "workflow": "workflow.yml",
        "environment": "pypi",
    }
    for bundle in provenance.attestation_bundles:
        if bundle.publisher.model_dump() == expected:
            for attestation in bundle.attestations:
                predicate, _ = attestation.verify(policy, Distribution.from_file(file))
                if predicate == "https://docs.pypi.org/attestations/publish/v1":
                    print(f"Verified protected-main publication of {file.name}")
                    return
    raise ValueError(f"Missing trusted PyPI publish attestation for {file.name}")


if __name__ == "__main__":
    directory, version = sys.argv[1:]
    for filename in (f"telryn-{version}-py3-none-any.whl", f"telryn-{version}.tar.gz"):
        verify(Path(directory) / filename, version)
