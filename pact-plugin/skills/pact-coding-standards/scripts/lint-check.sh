#!/bin/bash
# ============================================================================
# PACT Coding Standards - Lint Check Script
# ============================================================================
# Location: pact-coding-standards/scripts/lint-check.sh
# Purpose: Two modes.
#   --files mode (import-hygiene): check EXACTLY the named .py files for
#     unused imports / undefined names via an execution-probed linter ladder
#     (ruff -> pyflakes -> flake8 -> stdlib check_unused_imports.py), scope
#     pinned to the import-hygiene classes only. Fail-open: a missing or
#     crashing checker degrades to a SKIPPED verdict, never a block.
#     The LAST stdout line is always exactly one verdict:
#       IMPORT-HYGIENE: PASS
#       IMPORT-HYGIENE: FINDINGS (n)
#       IMPORT-HYGIENE: SKIPPED (<reason>)
#     Exit 0 = pass or gracefully degraded (PASS / SKIPPED); exit 1 = findings.
#     This mode NEVER falls back to whole-tree checking: it checks only the
#     files named on the command line.
#   Legacy directory mode: run the project-type-appropriate whole-tree linter
#     (unchanged behavior; reachable ONLY by passing a directory, never from
#     the --files shape).
# Usage:
#   ./lint-check.sh --files FILE.py [FILE.py ...]   # import-hygiene mode
#   ./lint-check.sh [directory]                     # legacy whole-tree mode
# ============================================================================

# ────────────────────────────────────────────────────────────────────────────
# Import-hygiene mode (--files). Runs BEFORE `set -e` is enabled: this mode
# owns its error handling explicitly so that a probe failure or checker crash
# can never kill the process before the verdict line is printed (fail-open
# verdict contract).
# ────────────────────────────────────────────────────────────────────────────
if [ "$1" = "--files" ]; then
    shift
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

    # Keep only .py arguments — the contract is Python files; anything else
    # a caller passes (e.g. every modified file regardless of type) is
    # filtered here rather than refused, so mixed file lists stay friction-free.
    # Paths that no longer exist (deleted/renamed in the same change set) are
    # noted on stderr and dropped so the remaining files still get checked.
    # This pre-filter is the consumer tier's choice only — the checker itself
    # keeps its unreadable-fails-loud contract for the strict tier.
    PY_FILES=()
    for f in "$@"; do
        case "$f" in
            *.py)
                if [ -e "$f" ]; then
                    PY_FILES+=("$f")
                else
                    echo "import-hygiene: skipping missing path (deleted/renamed?): $f" >&2
                fi
                ;;
        esac
    done

    if [ ${#PY_FILES[@]} -eq 0 ]; then
        # No Python files to check is a graceful degradation, not an error —
        # but it is VISIBLE: the verdict says so, and the coder records it.
        # Deliberately NOT a fallback to whole-tree checking.
        echo "IMPORT-HYGIENE: SKIPPED (no Python files given)"
        exit 0
    fi

    # Count lines that look like findings (path:line: ...) — a format shared
    # by every rung of the ladder; falls back to non-empty line count.
    count_findings() {
        local n
        n=$(printf '%s\n' "$1" | grep -Ec ':[0-9]+:' 2>/dev/null)
        if [ "${n:-0}" -eq 0 ]; then
            n=$(printf '%s\n' "$1" | grep -c . 2>/dev/null)
        fi
        echo "${n:-0}"
    }

    # Emit a rung's outcome and exit; returns 1 to the caller only when the
    # rung itself failed so the ladder can try the next rung.
    run_rung() {
        local rung_name="$1"
        shift
        local out rc
        out=$("$@" 2>&1)
        rc=$?
        if [ $rc -eq 0 ]; then
            echo "IMPORT-HYGIENE: PASS"
            exit 0
        elif [ $rc -eq 1 ]; then
            # Exit 1 alone does not prove findings: an unhandled Python
            # exception ALSO exits 1, so a traceback would otherwise be
            # misread as findings (and block a tier that must fail open).
            # Require at least one finding-format line before declaring
            # FINDINGS; exit-1 output without one is a crash — fall through
            # to rung failure.
            if printf '%s\n' "$out" | grep -Eq ':[0-9]+:'; then
                printf '%s\n' "$out"
                echo "IMPORT-HYGIENE: FINDINGS ($(count_findings "$out"))"
                exit 1
            fi
        fi
        # The checker itself failed (crash, bad invocation, missing module
        # at run time). Loud on stderr, then let the ladder continue.
        echo "import-hygiene: $rung_name failed (exit $rc); trying next checker" >&2
        [ -n "$out" ] && printf '%s\n' "$out" >&2
        return 1
    }

    # Detection ladder. EXECUTION probes only — running the tool proves it
    # works. PATH-presence checks (command -v) are banned here: pyenv shims
    # make a command "present" that dies at execution time.
    # Every rung's finding scope is pinned to the import-hygiene classes
    # (unused imports / undefined names) — never a tool's full default
    # ruleset, which would bury the signal in style noise.
    if ruff --version >/dev/null 2>&1; then
        run_rung "ruff" ruff check --quiet --select F401,F821 -- "${PY_FILES[@]}"
    fi
    if python3 -m pyflakes --version >/dev/null 2>&1; then
        # pyflakes has no rule selection; its native scope is already the
        # import-hygiene class family (unused imports, undefined names).
        run_rung "pyflakes" python3 -m pyflakes -- "${PY_FILES[@]}"
    fi
    if python3 -m flake8 --version >/dev/null 2>&1; then
        run_rung "flake8" python3 -m flake8 --select=F401,F821 -- "${PY_FILES[@]}"
    fi
    if python3 -c "import ast" >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/check_unused_imports.py" ]; then
        # Stdlib floor: always available wherever python3 runs. Advisory
        # try-scope strictness is this consumer-facing tier's explicit
        # choice (try/except-scoped imports are often optional-dependency
        # probes); the strictness parameter has no default by design.
        run_rung "stdlib checker" python3 "$SCRIPT_DIR/check_unused_imports.py" \
            --try-scope advisory -- "${PY_FILES[@]}"
    fi

    # Every rung unavailable or crashed: fail open, visibly.
    echo "IMPORT-HYGIENE: SKIPPED (no usable import checker on this system)"
    exit 0
fi

# ────────────────────────────────────────────────────────────────────────────
# Legacy whole-tree mode (directory argument). Unchanged behavior.
# ────────────────────────────────────────────────────────────────────────────

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Directory to check (default to current)
DIR="${1:-.}"

echo "Running lint check in: $DIR"
echo "----------------------------------------"

# Detect project type and run appropriate linter
if [ -f "$DIR/package.json" ]; then
    echo -e "${GREEN}Detected: Node.js/JavaScript project${NC}"

    # Check for lint script in package.json
    if grep -q '"lint"' "$DIR/package.json"; then
        echo "Running: npm run lint"
        cd "$DIR" && npm run lint 2>&1 || {
            echo -e "${RED}Linting issues found${NC}"
            exit 1
        }
    elif [ -f "$DIR/.eslintrc.js" ] || [ -f "$DIR/.eslintrc.json" ] || [ -f "$DIR/eslint.config.js" ]; then
        echo "Running: npx eslint ."
        cd "$DIR" && npx eslint . --ext .js,.jsx,.ts,.tsx 2>&1 || {
            echo -e "${RED}Linting issues found${NC}"
            exit 1
        }
    else
        echo -e "${YELLOW}No ESLint configuration found${NC}"
        echo "Consider adding ESLint: npm init @eslint/config"
    fi

elif [ -f "$DIR/pyproject.toml" ] || [ -f "$DIR/setup.py" ]; then
    echo -e "${GREEN}Detected: Python project${NC}"

    # Try different Python linters (execution probes — running the tool is
    # the only proof it works; PATH checks false-positive under pyenv shims)
    if ruff --version &> /dev/null; then
        echo "Running: ruff check"
        cd "$DIR" && ruff check . 2>&1 || {
            echo -e "${RED}Linting issues found${NC}"
            exit 1
        }
    elif python3 -m flake8 --version &> /dev/null; then
        echo "Running: flake8"
        cd "$DIR" && python3 -m flake8 . 2>&1 || {
            echo -e "${RED}Linting issues found${NC}"
            exit 1
        }
    elif python3 -m pylint --version &> /dev/null; then
        echo "Running: pylint"
        cd "$DIR" && python3 -m pylint **/*.py 2>&1 || {
            echo -e "${RED}Linting issues found${NC}"
            exit 1
        }
    else
        echo -e "${YELLOW}No Python linter found${NC}"
        echo "Consider installing: pip install ruff"
    fi

elif [ -f "$DIR/go.mod" ]; then
    echo -e "${GREEN}Detected: Go project${NC}"

    echo "Running: go vet"
    cd "$DIR" && go vet ./... 2>&1 || {
        echo -e "${RED}Go vet found issues${NC}"
        exit 1
    }

    if golangci-lint --version &> /dev/null; then
        echo "Running: golangci-lint"
        cd "$DIR" && golangci-lint run 2>&1 || {
            echo -e "${RED}Linting issues found${NC}"
            exit 1
        }
    fi

elif [ -f "$DIR/Cargo.toml" ]; then
    echo -e "${GREEN}Detected: Rust project${NC}"

    echo "Running: cargo clippy"
    cd "$DIR" && cargo clippy -- -D warnings 2>&1 || {
        echo -e "${RED}Clippy found issues${NC}"
        exit 1
    }

else
    echo -e "${YELLOW}No recognized project type found${NC}"
    echo "Supported project types:"
    echo "  - Node.js (package.json)"
    echo "  - Python (pyproject.toml or setup.py)"
    echo "  - Go (go.mod)"
    echo "  - Rust (Cargo.toml)"
    exit 0
fi

echo "----------------------------------------"
echo -e "${GREEN}Lint check passed!${NC}"
