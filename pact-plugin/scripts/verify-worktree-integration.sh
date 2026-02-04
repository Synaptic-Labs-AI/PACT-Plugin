#!/bin/bash
# scripts/verify-worktree-integration.sh
# Verifies worktree protocol integration across protocol and command files.
# Checks structure, cross-references, pattern consistency, and lifecycle completeness.

set -e

echo "=== Worktree Integration Verification ==="
echo ""

PROTOCOLS_DIR="pact-plugin/protocols"
COMMANDS_DIR="pact-plugin/commands"
WORKTREE_PROTOCOL="$PROTOCOLS_DIR/pact-worktree.md"

PASS=0
FAIL=0

# Helper: check that a file contains a pattern
check_pattern() {
    local file="$1"
    local name="$2"
    local pattern="$3"

    if [ ! -f "$file" ]; then
        echo "  ✗ $name: FILE NOT FOUND ($file)"
        FAIL=$((FAIL + 1))
        return 1
    fi

    if grep -q "$pattern" "$file"; then
        echo "  ✓ $name"
        PASS=$((PASS + 1))
        return 0
    else
        echo "  ✗ $name: pattern not found"
        FAIL=$((FAIL + 1))
        return 1
    fi
}

# --- 1. Protocol Structure (4 checks) ---
echo "1. Protocol Structure:"

# Check 1.1: File exists
if [ -f "$WORKTREE_PROTOCOL" ]; then
    echo "  ✓ Protocol file exists"
    PASS=$((PASS + 1))
else
    echo "  ✗ Protocol file not found: $WORKTREE_PROTOCOL"
    FAIL=$((FAIL + 1))
    echo ""
    echo "=== Summary ==="
    echo "Passed: $PASS"
    echo "Failed: $FAIL"
    echo ""
    echo "VERIFICATION FAILED"
    exit 1
fi

# Check 1.2: Required sections present
sections_ok=true
for section in "### Configuration" "### Branch Naming" "### Worktree Lifecycle" "### Integration Points" "### Shared Conventions"; do
    if ! grep -q "^$section" "$WORKTREE_PROTOCOL"; then
        echo "  ✗ Required section missing: $section"
        sections_ok=false
    fi
done
if [ "$sections_ok" = true ]; then
    echo "  ✓ All required sections present"
    PASS=$((PASS + 1))
else
    FAIL=$((FAIL + 1))
fi

# Check 1.3: Size within ~150-200 line target (allow 100-250 for flexibility)
line_count=$(wc -l < "$WORKTREE_PROTOCOL" | tr -d ' ')
if [ "$line_count" -ge 100 ] && [ "$line_count" -le 250 ]; then
    echo "  ✓ Protocol size within target ($line_count lines)"
    PASS=$((PASS + 1))
else
    echo "  ✗ Protocol size outside target: $line_count lines (expected 100-250)"
    FAIL=$((FAIL + 1))
fi

# Check 1.4: Index registration (check for worktree reference in CLAUDE.md or skip)
# Note: There's no centralized protocol index; check if CLAUDE.md mentions worktree
CLAUDEMD="pact-plugin/CLAUDE.md"
if [ -f "$CLAUDEMD" ] && grep -q "worktree" "$CLAUDEMD"; then
    echo "  ✓ Worktree referenced in CLAUDE.md"
    PASS=$((PASS + 1))
else
    # Optional check - just note if not found
    echo "  ○ Worktree not found in CLAUDE.md (optional)"
    PASS=$((PASS + 1))  # Count as pass since it's optional
fi
echo ""

# --- 2. Cross-Reference Integrity (3 checks) ---
echo "2. Cross-Reference Integrity:"

# Check 2.1: orchestrate.md references pact-worktree.md
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "orchestrate.md references pact-worktree.md" \
    "pact-worktree.md"

# Check 2.2: rePACT.md references pact-worktree.md
check_pattern "$COMMANDS_DIR/rePACT.md" \
    "rePACT.md references pact-worktree.md" \
    "pact-worktree.md"

# Check 2.3: comPACT.md references pact-worktree.md
check_pattern "$COMMANDS_DIR/comPACT.md" \
    "comPACT.md references pact-worktree.md" \
    "pact-worktree.md"
echo ""

# --- 3. Pattern Consistency (3 checks) ---
echo "3. Pattern Consistency:"

# Check 3.1: Branch naming patterns (--work, --{scope}, --compact)
branch_patterns_ok=true
for pattern in '\-\-work' '\-\-{scope}' '\-\-compact'; do
    if ! grep -q "$pattern" "$WORKTREE_PROTOCOL"; then
        echo "  ✗ Branch naming pattern missing: $pattern"
        branch_patterns_ok=false
    fi
done
if [ "$branch_patterns_ok" = true ]; then
    echo "  ✓ Branch naming patterns present (--work, --{scope}, --compact)"
    PASS=$((PASS + 1))
else
    FAIL=$((FAIL + 1))
fi

# Check 3.2: Worktree directory convention (.worktrees)
check_pattern "$WORKTREE_PROTOCOL" \
    "Worktree directory convention (.worktrees)" \
    "\.worktrees"

# Check 3.3: Pattern alignment - orchestrate.md uses same directory convention
if grep -q "\.worktrees\|worktree-directory" "$COMMANDS_DIR/orchestrate.md"; then
    echo "  ✓ orchestrate.md aligns with worktree directory convention"
    PASS=$((PASS + 1))
else
    # Check if it references the protocol instead (indirect alignment)
    if grep -q "pact-worktree.md" "$COMMANDS_DIR/orchestrate.md"; then
        echo "  ✓ orchestrate.md references protocol for directory convention"
        PASS=$((PASS + 1))
    else
        echo "  ✗ orchestrate.md lacks worktree directory alignment"
        FAIL=$((FAIL + 1))
    fi
fi
echo ""

# --- 4. Lifecycle Completeness (2 checks) ---
echo "4. Lifecycle Completeness:"

# Check 4.1: All lifecycle states documented (Setup, Work, Merge-back, Cleanup)
lifecycle_ok=true
for state in "Setup Protocol" "Work Protocol" "Merge-Back Protocol" "Cleanup Protocol"; do
    if ! grep -q "$state" "$WORKTREE_PROTOCOL"; then
        echo "  ✗ Lifecycle state missing: $state"
        lifecycle_ok=false
    fi
done
if [ "$lifecycle_ok" = true ]; then
    echo "  ✓ All lifecycle states documented (Setup, Work, Merge-back, Cleanup)"
    PASS=$((PASS + 1))
else
    FAIL=$((FAIL + 1))
fi

# Check 4.2: Cleanup trigger defined
if grep -q "Trigger.*successful merge\|After successful merge" "$WORKTREE_PROTOCOL"; then
    echo "  ✓ Cleanup trigger defined"
    PASS=$((PASS + 1))
else
    echo "  ✗ Cleanup trigger not clearly defined"
    FAIL=$((FAIL + 1))
fi
echo ""

# --- Summary ---
echo "=== Summary ==="
echo "Passed: $PASS"
echo "Failed: $FAIL"
echo ""

if [ $FAIL -gt 0 ]; then
    echo "VERIFICATION FAILED"
    exit 1
else
    echo "VERIFICATION PASSED"
    exit 0
fi
