#!/usr/bin/env bash
# Mirror CI's import-hygiene lint legs locally, fail-closed.
#
# WHAT THIS SOLVES
#   The local merge gate runs the pytest suite but never the lint step CI runs
#   FIRST, so a lint red is invisible to every local review lane and surfaces
#   only as a CI failure after push. The CI invocation lives in
#   .github/workflows/tests.yml; this script runs those exact bytes so a local
#   green and a CI green are the same green for the lint band.
#
# WHAT THIS DOES
#   1. Resolves the repository root from its own location and cd's there
#      INTERNALLY. The CI lines are root-relative (`ruff check ... pact-plugin/`),
#      so running them from inside pact-plugin/ would silently re-narrow the
#      sweep — including re-opening the top-level-file gap that let a stray
#      conftest escape every local gate while CI caught it. Call this script
#      from any directory; the tree it lints is its own, never the caller's.
#   2. Stamps the resolved `ruff --version` and the linted root into the
#      output. CI installs ruff unpinned, so the resolved version is part of
#      what a run certifies: a local green that disagrees with a CI red needs
#      both versions before it can be attributed, and this stamp is the local
#      half of that comparison.
#   3. REFUSES, loudly and before linting anything, when ruff is not on PATH.
#      A missing linter must not read as a pass, and a bare command-not-found
#      exit teaches the operator nothing.
#   4. Runs BOTH CI legs verbatim, in CI's order, and propagates the first
#      failure. CI's run block stops at the first failing leg; this mirrors
#      that, so the two gates fail on the same first finding.
#
# USAGE
#   verify-local-lint-gate.sh          (no arguments)
#
# EXIT CODES
#   0    both legs green
#   2    refused before linting started (ruff absent, or the repository root
#        the script resolved does not carry a pact-plugin/ directory)
#   1    a usage error, or the first failing ruff leg reported findings
#   3    ruff itself failed (a runtime exit above 1 — config error, crash —
#        which is neither a findings count nor a gate refusal). Kept distinct
#        from 2 so a wrapper keying exit codes cannot misread a linter crash
#        as a refusal; the observed ruff exit rides in the marker line.
#
# DRIFT GUARD
#   pact-plugin/tests/test_local_lint_parity.py byte-compares this script's
#   ruff lines against the workflow's lint step. Edit both sides in the same
#   change, or that test reddens the merge gate itself.

set -u

if [ "$#" -ne 0 ]; then
    printf 'usage: verify-local-lint-gate.sh\n' >&2
    printf '  This script takes no arguments; it lints pact-plugin/ from the repository root.\n' >&2
    exit 1
fi

# Resolve the repository root from this script's own location, never from the
# caller's working directory: scripts/ sits directly under the root, and the
# invocation below is root-relative BY CONTRACT.
script_dir="$(cd "$(dirname "$0")" && pwd -P)"
if [ -z "$script_dir" ]; then
    printf 'GATE REFUSED: the script directory could not be resolved, so the repository root cannot be either.\n' >&2
    printf '  This happens only when the invoked path vanishes mid-run; re-invoke by a stable path.\n' >&2
    exit 2
fi
repo_root="$(dirname "$script_dir")"

if [ ! -d "$repo_root/pact-plugin" ]; then
    printf 'GATE REFUSED: the root this script resolved carries no pact-plugin/ directory.\n' >&2
    printf '  resolved root: %s\n' "$repo_root" >&2
    printf '  The CI lint lines are root-relative, so this script lints the tree it\n' >&2
    printf '  lives in. It must sit in scripts/ at the root of a checkout.\n' >&2
    exit 2
fi

if ! command -v ruff >/dev/null 2>&1; then
    printf 'GATE REFUSED: ruff is not available, so the lint legs cannot run.\n' >&2
    printf '  A missing linter must not read as a pass. Install or upgrade ruff:\n' >&2
    printf '    python3 -m pip install --upgrade ruff\n' >&2
    printf '  To lint without this gate, run the CI legs directly from the repo root:\n' >&2
    printf '    ruff check --select F401,F821 --target-version py312 pact-plugin/\n' >&2
    printf '    ruff check --select F821 --target-version py312 pact-plugin/\n' >&2
    exit 2
fi

cd "$repo_root" || {
    printf 'GATE REFUSED: cannot enter the resolved repository root.\n' >&2
    printf '  resolved root: %s\n' "$repo_root" >&2
    exit 2
}

# Evidence stamps: the version and the tree are part of what a run certifies.
printf 'LINT-GATE-ROOT %s\n' "$repo_root"
# The version stamp prints ONLY when `ruff --version` succeeds with output:
# a crashing or silent version call must not leave an empty stamp line that a
# greper could read as version evidence. The absent stamp is disclosed on
# stderr instead, and the run continues — the legs' own exits carry the gate.
ruff_version="$(ruff --version 2>/dev/null)" || ruff_version=""
if [ -n "$ruff_version" ]; then
    printf 'LINT-GATE-RUFF %s\n' "$ruff_version"
else
    printf 'LINT-GATE-RUFF-UNAVAILABLE: ruff --version failed or returned nothing; this run carries no version evidence\n' >&2
fi

# CI's two import-hygiene legs, verbatim and in order. The stripped form of
# these exact lines is byte-compared against the workflow by
# pact-plugin/tests/test_local_lint_parity.py.
ruff check --select F401,F821 --target-version py312 pact-plugin/
status=$?
if [ "$status" -gt 1 ]; then
    printf 'LINT-GATE-LEG-1-ERROR exit %s\n' "$status" >&2
    exit 3
fi
if [ "$status" -eq 1 ]; then
    printf 'LINT-GATE-LEG-1-FAILED exit %s\n' "$status" >&2
    exit "$status"
fi

ruff check --select F821 --target-version py312 pact-plugin/
status=$?
if [ "$status" -gt 1 ]; then
    printf 'LINT-GATE-LEG-2-ERROR exit %s\n' "$status" >&2
    exit 3
fi
if [ "$status" -eq 1 ]; then
    printf 'LINT-GATE-LEG-2-FAILED exit %s\n' "$status" >&2
    exit "$status"
fi

printf 'LINT-GATE-GREEN both legs\n'
exit 0
