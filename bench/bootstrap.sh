#!/usr/bin/env bash
# Create the hermetic/verifier Python environment for bench/.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
venv="$here/.venv"

python3 -m venv "$venv"
"$venv/bin/python" -m pip install --upgrade pip
"$venv/bin/python" -m pip install -r "$here/constraints.txt"

"$venv/bin/python" - <<'PY'
import sys, pytest
print("bench python", sys.version.split()[0])
print("bench pytest", pytest.__version__)
PY
