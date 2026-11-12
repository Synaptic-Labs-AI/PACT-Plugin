#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for the PACT plugin test harness.
#
# Mirrors the CI dependency set in .github/workflows/tests.yml. CI runs the
# floor interpreter with the hashed lock in constraints-ci.txt; this script
# installs the same lock with `--require-hashes -r` and refuses any
# interpreter below the floor. A local green matches CI only on the floor
# version itself: a newer interpreter is allowed but CI does not test it. The
# lock is the same for Python 3.12, 3.13 and 3.14 (see constraints-ci.in). Installs into a repo-local venv (.venv) to
# keep the system interpreter clean. Safe to run repeatedly.
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

# CI's install step (.github/workflows/tests.yml) without its
# --only-binary=:all:. constraints-ci.txt is the hashed lock of the requirement
# set, so the package list lives there only, and pip is pinned inside it. This
# install accepts source builds because pysqlite3 has no wheel for aarch64
# Linux or x86_64 macOS; pip installs a source build's own build dependencies
# (setuptools and the like) WITHOUT hash checks, so on such a platform those
# dependencies are not covered by the lock.
python -m pip install --require-hashes -r constraints-ci.txt

# Report how this venv differs from the pinned set. A warning only, never a
# failure: a local venv may legitimately hold extra tools.
python scripts/verify_ci_environment.py --report

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
echo "Run the suite as CI does: cd pact-plugin && python -m pytest -ra -n 4 --dist loadfile (plain -ra runs it serially)"
echo "Live plugin smoke test: bash .cursor/smoke-test.sh (needs ANTHROPIC_API_KEY or a Claude Code login for the live phase)"
