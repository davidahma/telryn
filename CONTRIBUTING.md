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
