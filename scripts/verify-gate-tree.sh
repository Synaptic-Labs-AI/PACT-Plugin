#!/usr/bin/env bash
# Run the test suite against a DECLARED tree, and record which tree was measured.
#
# WHAT THIS SOLVES
#   A suite run reports a count. The count is correct for the tree the run
#   measured, and the capture file does not say which tree that was. So a run
#   in a different checkout produces a clean summary that certifies nothing
#   about the branch under review, and it reads the same as a faithful run.
#
# WHAT THIS DOES
#   1. REFUSES before pytest starts if the run would measure a tree other than
#      the declared one.
#   2. STAMPS the tree identity into the capture file above the pytest output,
#      so a later reader can tell what was measured without a re-run.
#   3. READS HEAD and the complete porcelain state again after pytest, and marks
#      the run stable only when both are unchanged. A green run whose tree moved
#      is unattributable and returns 1; a red pytest status is preserved.
#
#   The STAMP is the load-bearing part. The REFUSAL is the enhancement. If the
#   two are ever in tension, keep the stamp.
#
#   The after-run comparison is a cheap shared-tree guard, not decisive clean-SHA
#   certification. An in-place edit while already dirty can leave porcelain text
#   unchanged, and ignored files are outside it. Use a detached clean worktree at
#   the named SHA for decisive local certification.
#
# WHAT THIS DOES NOT COVER
#   This wrapper gates the MERGE-GATE suite run only. It requires a git
#   worktree, because tree identity is what it asserts. An exported or copied
#   tree has no identity to assert, so this wrapper REFUSES it and tells the
#   operator to call pytest directly. That refusal is loud and immediate, and
#   the operator routes around it in seconds.
#
# USAGE
#   verify-gate-tree.sh <declared-worktree-path> <capture-file> [pytest args...]
#
# EXIT CODES
#   0    pytest passed and the tree stayed stable
#   2    refused before pytest started, including a temporary-file allocation
#        failure; a refusal writes no capture
#   1    a usage error, pytest exited 1 (test failures), or pytest was green
#        but the tree became unattributable
#   any other nonzero pytest status is preserved verbatim

set -u

usage() {
    cat >&2 <<'USAGE'
Usage: verify-gate-tree.sh <declared-worktree-path> <capture-file> [pytest args...]

  declared-worktree-path  the worktree this run must measure
  capture-file            where to write the stamps and the pytest output

Example, run from the pact-plugin directory of the declared worktree:
  ../scripts/verify-gate-tree.sh "$PWD/.." /tmp/gate.txt -q
USAGE
}

if [ "$#" -lt 2 ]; then
    usage
    exit 1
fi

declared="$1"
capture="$2"
shift 2

if ! command -v git >/dev/null 2>&1; then
    printf 'GATE REFUSED: git is not available, so the tree cannot be identified.\n' >&2
    printf '  To run the suite without this check, call pytest directly.\n' >&2
    exit 2
fi

# The capture-path containment check below is computed by python3. Without it
# that check would read an empty path, match nothing, and silently proceed to
# write the capture inside the tree it is meant to keep clear.
if ! command -v python3 >/dev/null 2>&1; then
    printf 'GATE REFUSED: python3 is not available, so the capture path cannot be checked.\n' >&2
    printf '  To run the suite without this check, call pytest directly.\n' >&2
    exit 2
fi

if [ ! -d "$declared" ]; then
    printf 'GATE REFUSED: the declared path is not a directory.\n' >&2
    printf '  declared: %s\n' "$declared" >&2
    printf '  Give the path of the worktree this run must measure.\n' >&2
    exit 2
fi

# The tree this run will measure, resolved from the current directory.
actual="$(git rev-parse --show-toplevel 2>/dev/null || true)"

if [ -z "$actual" ]; then
    printf 'GATE REFUSED: the working directory is not in a git worktree.\n' >&2
    printf '  working directory: %s\n' "$(pwd -P)" >&2
    printf '  This wrapper gates the merge-gate suite run, and it identifies the tree by git.\n' >&2
    printf '  An exported or copied tree has no identity to check.\n' >&2
    printf '  To run the suite there, call pytest directly:\n' >&2
    printf '    python3 -m pytest [args]\n' >&2
    exit 2
fi

# Compare resolved paths, because one tree can be spelled two ways through a
# symlink, and a trailing slash or a relative path names the same directory.
declared_real="$(cd "$declared" && pwd -P)"
actual_real="$(cd "$actual" && pwd -P)"

if [ "$declared_real" != "$actual_real" ]; then
    printf 'GATE REFUSED: this run would measure a different tree than the declared one.\n' >&2
    printf '  declared: %s\n' "$declared_real" >&2
    printf '  actual  : %s\n' "$actual_real" >&2
    printf '  A summary from the actual tree is correct about that tree, and says nothing about the declared one.\n' >&2
    printf '  Either change to the declared tree, or declare the tree you are in.\n' >&2
    exit 2
fi

capture_real="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$capture")"
case "$capture_real" in
    "$declared_real"|"$declared_real"/*)
        printf 'GATE REFUSED: the capture path must be outside the declared tree.\n' >&2
        printf '  declared: %s\n' "$declared_real" >&2
        printf '  capture : %s\n' "$capture_real" >&2
        printf '  Choose a capture path outside this worktree; no capture or pytest run was started.\n' >&2
        exit 2
        ;;
esac

head_sha="$(git -C "$actual_real" rev-parse HEAD)"
porcelain_before="$(mktemp)" || {
    printf 'GATE REFUSED: cannot allocate the before-run porcelain capture.\n' >&2
    exit 2
}
porcelain_after="$(mktemp)" || {
    rm -f "$porcelain_before"
    printf 'GATE REFUSED: cannot allocate the after-run porcelain capture.\n' >&2
    exit 2
}
trap 'rm -f "$porcelain_before" "$porcelain_after"' EXIT
# --untracked-files=all overrides status.showUntrackedFiles from git config;
# without it a repo configured to hide untracked files disarms this check.
if ! git -C "$actual_real" status --porcelain --untracked-files=all > "$porcelain_before"; then
    printf 'GATE REFUSED: git status failed before pytest.\n' >&2
    exit 2
fi
dirty_count="$(wc -l < "$porcelain_before" | tr -d ' ')"

# Make the capture directory, because a missing directory is a faithful call
# with an absent parent, and a refusal there would teach the operator nothing.
capture_dir="$(dirname "$capture")"
mkdir -p "$capture_dir" || {
    printf 'GATE REFUSED: the capture directory cannot be made.\n' >&2
    printf '  capture: %s\n' "$capture" >&2
    exit 2
}

{
    printf 'GATE-TREE %s\n' "$actual_real"
    printf 'GATE-HEAD %s\n' "$head_sha"
    printf 'GATE-DIRTY-PATHS %s\n' "$dirty_count"
    printf 'GATE-CWD %s\n' "$(pwd -P)"
    printf 'GATE-COMMAND python3 -m pytest %s\n' "$*"
} > "$capture"

# The stamp records only argv. An ambient PYTEST_ADDOPTS can narrow the run
# without appearing in it, so enforce what the canary asks the operator to
# remember.
unset PYTEST_ADDOPTS
python3 -m pytest "$@" >> "$capture" 2>&1
status=$?

head_after="$(git -C "$actual_real" rev-parse HEAD 2>/dev/null || true)"
porcelain_readable=1
if ! git -C "$actual_real" status --porcelain --untracked-files=all > "$porcelain_after"; then
    porcelain_readable=0
fi
if [ "$porcelain_readable" -eq 1 ]; then
    dirty_after="$(wc -l < "$porcelain_after" | tr -d ' ')"
else
    dirty_after="unknown"
fi
attribution="unattributable"
if [ "$porcelain_readable" -eq 1 ] && [ -n "$head_after" ] \
        && [ "$head_after" = "$head_sha" ] \
        && cmp -s "$porcelain_before" "$porcelain_after"; then
    attribution="stable"
fi

{
    printf 'GATE-HEAD-AFTER %s\n' "$head_after"
    printf 'GATE-DIRTY-PATHS-AFTER %s\n' "$dirty_after"
    printf 'GATE-TREE-ATTRIBUTION %s\n' "$attribution"
    printf 'GATE-PYTEST-EXIT %s\n' "$status"
} >> "$capture"

if [ "$status" -ne 0 ]; then
    exit "$status"
fi
if [ "$attribution" != "stable" ]; then
    exit 1
fi
exit 0
