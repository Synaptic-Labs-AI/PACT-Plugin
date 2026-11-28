#!/usr/bin/env bash
# Create the hermetic/verifier Python environment for bench/ from the hashed
# lock in bench/constraints.txt (Python >= 3.12; pytest and pyyaml).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
venv="$here/.venv"

python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    sys.exit(f"bench needs python3 >= 3.12, found {sys.version.split()[0]}")
PY

python3 -m venv "$venv"
"$venv/bin/python" -m pip install --only-binary=:all: --require-hashes -r "$here/constraints.txt"

"$venv/bin/python" - <<'PY'
import sys, pytest, yaml
print("bench python", sys.version.split()[0])
print("bench pytest", pytest.__version__)
print("bench pyyaml", yaml.__version__)
PY
