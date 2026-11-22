#!/usr/bin/env bash
# Invoke at the repository root with build and twine installed in the active Python.
set -euo pipefail
out=$1
version=$2
repo=$(pwd)
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)
python -m build --outdir "$out"
python -m twine check --strict "$out"/*
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python -m venv "$work/editable"
"$work/editable/bin/python" -m pip install --disable-pip-version-check --no-deps -e "$repo"
(cd "$work" && "$work/editable/bin/telryn" demo --agent-release --out editable-example && "$work/editable/bin/telryn" qualify --case grade-return)
for dist in "$out/telryn-$version-py3-none-any.whl" "$out/telryn-$version.tar.gz"; do
  venv="$work/$(basename "$dist")-env"
  python -m venv "$venv"
  "$venv/bin/python" -m pip install --disable-pip-version-check --no-deps "$dist"
  (cd "$work" && "$venv/bin/python" -I "$repo/scripts/verify_installed.py" "$version")
done
