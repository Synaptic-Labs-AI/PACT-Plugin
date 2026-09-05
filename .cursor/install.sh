#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for the PACT plugin test harness.
#
# Mirrors the CI dependency set in .github/workflows/tests.yml so that a local
# green and a CI green are the same green. Installs into a repo-local venv
# (.venv) to keep the system interpreter clean. Safe to run repeatedly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# System packages required to build pysqlite3 from source and to create venvs.
# These normally live in the base snapshot; the guard makes the script
# self-healing on a fresh default image without paying apt cost on every run.
if ! dpkg -s build-essential >/dev/null 2>&1 \
  || ! dpkg -s libsqlite3-dev >/dev/null 2>&1 \
  || ! dpkg -s python3-venv >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends \
    build-essential libsqlite3-dev python3-venv
fi

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip

# CI install line (kept in lockstep with .github/workflows/tests.yml):
#   pytest hypothesis httpx pytest-asyncio pyyaml pysqlite3 sqlite-vec model2vec ruff
python -m pip install \
  pytest \
  hypothesis \
  httpx \
  pytest-asyncio \
  pyyaml \
  pysqlite3 \
  sqlite-vec \
  model2vec \
  ruff

echo "PACT plugin environment ready. Activate with: source .venv/bin/activate"
echo "Run the suite with: cd pact-plugin && python -m pytest -ra"
