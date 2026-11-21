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

Publish the signed GitHub release using the [container release procedure](docs/container.md#release-procedure). Its published event starts `.github/workflows/workflow.yml`. The build job validates the release tag, commit, version and green main CI. A separate job in the tag-restricted `pypi` environment uploads the tested files using PyPI Trusted Publishing. The final job compares the public files with their tested hashes and installs the public package with pipx. Retry a failed verification job on the original run. If neither distribution has reached PyPI, the workflow can also be dispatched on the same published version tag. [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
