#!/usr/bin/env bash
# Create the hermetic/verifier Python environment for bench/ from the hashed
# lock in bench/constraints.txt (Python >= 3.12; pytest and pyyaml). Each run
# builds a fresh venv in a temp dir and replaces bench/.venv only after the
# install succeeds, so a failed run leaves the previous bench/.venv in place.
# Run its tools as bench/.venv/bin/python -m <tool>: the moved venv's console
# scripts (bin/pytest, bin/pip) keep the temp dir in their shebang.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
venv="$here/.venv"

python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    sys.exit(f"bench needs python3 >= 3.12, found {sys.version.split()[0]}")
PY

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
python3 -m venv "$tmp/venv"
"$tmp/venv/bin/python" -m pip install --only-binary=:all: --require-hashes -r "$here/constraints.txt"
rm -rf "$venv"
mv "$tmp/venv" "$venv"

"$venv/bin/python" - <<'PY'
import sys, pytest, yaml
print("bench python", sys.version.split()[0])
print("bench pytest", pytest.__version__)
print("bench pyyaml", yaml.__version__)
PY
