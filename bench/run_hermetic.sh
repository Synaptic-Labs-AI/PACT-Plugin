#!/usr/bin/env bash
# Run only the bench harness tests, from any cwd; extra arguments go to pytest.
# Uses bench/.venv (bench/bootstrap.sh) when it exists, else python, else python3.
# The workflow brackets this with the repository CI environment verifier.
set -euo pipefail
cd "$(dirname "$0")/.."
py=bench/.venv/bin/python
# CI has no bench/.venv and uses setup-python's `python`; a stock Linux host may have only python3.
[ -x "$py" ] || py=python
command -v "$py" >/dev/null 2>&1 || py=python3
exec "$py" -m pytest bench/tests -q "$@"
