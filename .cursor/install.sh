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
#   python -m pip install --upgrade -c constraints-ci.txt pip pytest
#   hypothesis httpx pytest-asyncio pyyaml pysqlite3 sqlite-vec model2vec ruff
# constraints-ci.txt pins the direct deps to the TEST-verified versions, so a
# local green and a CI green resolve the same set. pip stays unconstrained
# (pinned self-upgrades can deadlock the bootstrap); it is upgraded separately
# above, mirroring the CI line's `pip` operand.
python -m pip install --upgrade -c constraints-ci.txt \
  pytest \
  hypothesis \
  httpx \
  pytest-asyncio \
  pyyaml \
  pysqlite3 \
  sqlite-vec \
  model2vec \
  ruff

# Claude Code CLI — enables the live end-to-end plugin smoke test
# (.cursor/smoke-test.sh). Installed into a user-local npm prefix so no sudo is
# needed; node/npm come from the base image. Idempotent: skipped when present.
NPM_GLOBAL="$HOME/.npm-global"
export PATH="$NPM_GLOBAL/bin:$PATH"
if ! command -v claude >/dev/null 2>&1; then
  if command -v npm >/dev/null 2>&1; then
    mkdir -p "$NPM_GLOBAL"
    npm install -g --prefix "$NPM_GLOBAL" @anthropic-ai/claude-code
  else
    echo "WARNING: npm not found; skipping Claude Code CLI install (live smoke test unavailable)." >&2
  fi
fi

# Put the CLI on PATH for future interactive shells (idempotent).
BASHRC="$HOME/.bashrc"
LINE='export PATH="$HOME/.npm-global/bin:$PATH"'
if [ -f "$BASHRC" ] && ! grep -Fqx "$LINE" "$BASHRC"; then
  printf '\n# Claude Code CLI (installed by .cursor/install.sh)\n%s\n' "$LINE" >> "$BASHRC"
fi

echo "PACT plugin environment ready. Activate with: source .venv/bin/activate"
echo "Run the suite with: cd pact-plugin && python -m pytest -ra"
echo "Live plugin smoke test: bash .cursor/smoke-test.sh (needs ANTHROPIC_API_KEY or a Claude Code login for the live phase)"
