#!/usr/bin/env bash
# Run only the bench harness tests. The workflow brackets this with the
# repository CI environment verifier.
set -euo pipefail
python -m pytest bench/tests -q
