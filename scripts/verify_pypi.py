"""Verify PyPI publish attestations for the two distributions tested by CI."""

import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pypi_attestations import Distribution, Provenance
from sigstore.verify.policy import Identity


def fetch_provenance(url):
    # An edge can see the distribution before its integrity record is available.
    # An existing record is parsed immediately; signature failures are final.
    for attempt in range(12):
        request = Request(
            f"{url}?verify={time.time_ns()}",
            headers={"Cache-Control": "no-cache"},
        )
        try:
            with urlopen(request, timeout=15) as response:
                return Provenance.model_validate_json(response.read())
        except HTTPError as exc:
            exc.close()
            if exc.code != 404 or attempt == 11:
                raise
        time.sleep(5)


def verify(file, version):
    repository = "davidahma/telryn"
    policy = Identity(
        identity=f"https://github.com/{repository}/.github/workflows/workflow.yml@refs/heads/main",
        issuer="https://token.actions.githubusercontent.com",
    )
    url = f"https://pypi.org/integrity/telryn/{version}/{file.name}/provenance"
    provenance = fetch_provenance(url)
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
