# Contributing to Telryn

Use a branch and open a pull request against `main`. Explain the workflow or
failure your change addresses and give a small synthetic reproduction when
possible. Changes to a public schema, CLI exit status, or report decision need
an accompanying update to the reference and qualification case.

From the repository root, with CPython 3.12 or later installed:

```sh
python3 -m compileall -q telryn examples tests
python3 -m telryn qualify
python3 -m telryn qualify --case grade-return
```

Use fixtures with directly checkable expected decisions and case IDs. Keep
the reference refund app separate from the checker. Exercise both ends of a
threshold and any relevant missing-evidence, partial-return, or retry case.

Use synthetic data in issues, pull requests, and fixtures. Small redacted
reproductions are welcome in issues. Report vulnerabilities privately through
the [Security tab](https://github.com/davidahma/telryn/security). Code is
licensed under [Apache-2.0](LICENSE).

The public Ubuntu fixture is a licensed projection of published annotations. Keep its source, digest, attribution and reproducible transformation together. Regression cases with customer-like flows use synthetic identities. The weekly report format has its own archived v1 fixture. Exercise backward reading before changing it. Vendor adapters have offline HTTP contract tests. Any claim about a live tenant should identify the account scope, test date and observed result.

CI also opens the QA desk in Google Chrome and previews, commits and downloads an audit. To run that check locally, install Google Chrome and Playwright 1.65.2 in a development environment and run `python3 -m tests.browser_desk`. The product and the offline qualification suite use only the standard library.

## Publish the Python package

Set the release version in `telryn/__init__.py`. Build the wheel and source distribution with `python -m build` and check both with `python -m twine check --strict dist/*`. Install each artifact in a clean environment and run `scripts/verify_installed.py` with its Python interpreter from outside the checkout. CI covers the base install, the optional Bedrock SDK and the native Windows support message.

Publish the signed GitHub release using the [container release procedure](docs/container.md#release-procedure). Its published event starts a notification job without credentials or a checkout. GitHub runs the publisher, `.github/workflows/workflow.yml`, from protected `main` after that job completes. The publisher verifies the immutable release, annotated tag, main CI result, Sigstore signature and source archive against the tagged tree. It builds that commit with read-only permissions. Only the upload job can request a PyPI identity. Configure the GitHub `pypi` environment to allow deployments solely from the **branch** `main`. The final job compares the public files with their tested hashes and installs the public package with pipx.

If an upload stops after one file reaches PyPI, rerun the failed jobs in the original Actions run. The upload job retrieves that run’s retained distributions, checks their hashes, and uploads any missing file. A yanked, unexpected or different public file stops publication. If the files are already on PyPI, the job verifies the pair and proceeds to the public install check.

To run the publisher manually, select protected `main` and pass the original signed tag, for example `gh workflow run workflow.yml --ref main -f tag=v0.11.0`. A new run rebuilds the distributions. Any existing PyPI file must match the new build byte for byte. Where build tools have changed, use the original run and its retained artifacts. [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)

The public verification job checks the wheel and source distribution against PyPI’s publish attestations, including the GitHub OIDC issuer and exact workflow identity on `refs/heads/main`. This identity applies from v0.9.3 onward.

## Develop from a checkout

Use CPython 3.12–3.15 on Linux or macOS. Create a virtual environment and run `python -m pip install -e .` in the checkout. From another directory, run `telryn demo --agent-release --out agent-check` and `telryn qualify`. The editable installation reads examples and tests from the checkout. A wheel or source installation places them under the installed Telryn package. CI exercises all three installations.
