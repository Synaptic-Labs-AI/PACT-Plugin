#!/usr/bin/env bash
# Run only the bench harness tests, from any cwd. The workflow brackets this
# with the repository CI environment verifier.
set -euo pipefail
cd "$(dirname "$0")/.."
py=python
# CI's setup-python provides `python`; a stock Linux host may have only python3.
command -v "$py" >/dev/null 2>&1 || py=python3
exec "$py" -m pytest bench/tests -q
