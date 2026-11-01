#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for the PACT plugin test harness.
#
# Mirrors the CI dependency set in .github/workflows/tests.yml. CI runs the
# floor interpreter with the pins in constraints-ci.txt; this script installs
# the same pinned direct dependencies and refuses any interpreter below the
# floor. A local green matches CI only on the floor version itself: a newer
# interpreter is allowed but CI does not test it. Installs into a repo-local
# venv (.venv) to keep the system interpreter clean. Safe to run repeatedly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# The declared floor (requires-python in pyproject.toml), stated once here.
PYTHON_FLOOR_MINOR=12

# Refuse an interpreter below the floor. Without this, an older interpreter
# installs the pinned set cleanly and the suite then fails on syntax-floor
# tests whose messages never name the interpreter.
require_floor() {
  if ! "$1" -c "import sys; sys.exit(sys.version_info < (3, $PYTHON_FLOOR_MINOR))" 2>/dev/null; then
    local found
    found="$("$1" -c 'import sys; print(sys.version.split()[0])' 2>/dev/null)" || found="unknown (it did not run)"
    echo "ERROR: $2 is Python $found; the floor is 3.$PYTHON_FLOOR_MINOR." >&2
    echo "  $3" >&2
    exit 1
  fi
}

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
  require_floor python3 "python3 ($(command -v python3))" \
    "Put a Python 3.$PYTHON_FLOOR_MINOR+ python3 first on PATH and re-run."
  python3 -m venv .venv
fi
# An existing .venv is reused, so check the interpreter it was built with.
# Never deleted here: it may hold work the user wants to keep.
require_floor .venv/bin/python "the existing .venv" \
  "Remove .venv, put a Python 3.$PYTHON_FLOOR_MINOR+ python3 first on PATH, and re-run."

# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade -c constraints-ci.txt pip

# CI-parity install: the command below mirrors the install line in
# .github/workflows/tests.yml — the authoritative copy; read it there rather
# than here. The package-set parity arm in
# pact-plugin/tests/test_memory_init.py reddens if the two surfaces drift.
# constraints-ci.txt pins the direct deps to the TEST-verified versions, so a
# local green and a CI green resolve the same direct-dependency set.
# pip stays unconstrained (no pin in constraints-ci.txt; a pinned
# self-upgrade can deadlock the bootstrap). Its upgrade line above carries
# the same `-c` — inert for pip, uniform with the package line — so every
# install command applies the constraints file.
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
    # Unpinned deliberately: a vendor CLI on an opt-in, local-machine smoke
    # path — CI never installs it. The vendor re-versions frequently, so a
    # pin would need its own refresh discipline for no CI benefit; the float
    # is bounded to this machine's PATH.
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
