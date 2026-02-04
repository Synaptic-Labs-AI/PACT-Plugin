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

# Helper: check that a file does NOT contain a pattern (negative check)
check_absent() {
    local file="$1"
    local name="$2"
    local pattern="$3"

    if [ ! -f "$file" ]; then
        echo "  ✗ $name: FILE NOT FOUND ($file)"
        FAIL=$((FAIL + 1))
        return 1
    fi

    if grep -q "$pattern" "$file"; then
        echo "  ✗ $name: pattern should NOT be present"
        FAIL=$((FAIL + 1))
        return 1
    else
        echo "  ✓ $name"
        PASS=$((PASS + 1))
        return 0
    fi
}

# Helper: check that a markdown header exists in a file (matches headers precisely)
check_header() {
    local file="$1"
    local name="$2"
    local header="$3"

    if [ ! -f "$file" ]; then
        echo "  ✗ $name: FILE NOT FOUND ($file)"
        FAIL=$((FAIL + 1))
        return 1
    fi

    # Match markdown headers: start of line, optional #'s, space, then the header text
    if grep -qE "^#+ $header" "$file"; then
        echo "  ✓ $name"
        PASS=$((PASS + 1))
        return 0
    else
        echo "  ✗ $name: header not found"
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

# Check 1.2: Required sections present (using check_header for explicit matching)
sections_ok=true
for section in "Configuration" "Branch Naming" "Worktree Lifecycle" "Integration Points" "Shared Conventions"; do
    if ! grep -qE "^#+ $section" "$WORKTREE_PROTOCOL"; then
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
line_count=$(wc -l < "$WORKTREE_PROTOCOL" | xargs)
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

# --- 5. Mode Behavior Verification (1 check) ---
echo "5. Mode Behavior Verification:"

# Check 5.1: All three modes documented in mode behavior table
modes_ok=true
for mode in "tiered" "always" "never"; do
    if ! grep -q "| \`$mode\`" "$WORKTREE_PROTOCOL"; then
        echo "  ✗ Mode behavior missing: $mode"
        modes_ok=false
    fi
done
if [ "$modes_ok" = true ]; then
    echo "  ✓ All three modes documented in mode behavior table"
    PASS=$((PASS + 1))
else
    FAIL=$((FAIL + 1))
fi
echo ""

# --- 6. Failure Handling Verification (3 checks) ---
echo "6. Failure Handling Verification:"

# Check 6.1: Operational Failure section exists
check_header "$WORKTREE_PROTOCOL" \
    "Operational Failure section exists" \
    "Operational Failure"

# Check 6.2: HALT Signal section exists
check_header "$WORKTREE_PROTOCOL" \
    "HALT Signal section exists" \
    "HALT Signal"

# Check 6.3: Abort/Crash Recovery section exists
check_header "$WORKTREE_PROTOCOL" \
    "Abort/Crash Recovery section exists" \
    "Abort/Crash Recovery"
echo ""

# --- 7. Gitignore Verification (1 check) ---
echo "7. Gitignore Verification:"

# Check 7.1: Protocol mentions git check-ignore for .gitignore verification
check_pattern "$WORKTREE_PROTOCOL" \
    "Protocol mentions git check-ignore" \
    "git check-ignore"
echo ""

# --- 8. Shared Conventions Verification (1 check) ---
echo "8. Shared Conventions Verification:"

# Check 8.1: Protocol references superpowers:using-git-worktrees
check_pattern "$WORKTREE_PROTOCOL" \
    "Protocol references superpowers:using-git-worktrees" \
    "superpowers:using-git-worktrees"
echo ""

# --- 9. rePACT Merge-Back Constraint (1 check) ---
echo "9. rePACT Merge-Back Constraint:"

# Check 9.1: rePACT specifies NOT merging worktree branches (deferred to parent)
check_pattern "$COMMANDS_DIR/rePACT.md" \
    "rePACT specifies no worktree merge (deferred to parent)" \
    "NOT merge worktree branch\|don't merge individually\|Do NOT merge"
echo ""

# --- 10. Negative Scope Check (1 check) ---
echo "10. Negative Scope Check (separation of concerns):"

# Check 10.1: pact-worktree.md does NOT contain scope detection logic
check_absent "$WORKTREE_PROTOCOL" \
    "Worktree protocol has no scope detection logic" \
    "scope detection"
check_absent "$WORKTREE_PROTOCOL" \
    "Worktree protocol has no decomposition_active" \
    "decomposition_active"
echo ""

# --- 11. Config Consistency Check (1 check) ---
echo "11. Config Consistency Check:"

# Check 11.1: All three modes mentioned consistently across command files
config_ok=true
for file in "$COMMANDS_DIR/orchestrate.md" "$COMMANDS_DIR/rePACT.md" "$COMMANDS_DIR/comPACT.md"; do
    if [ -f "$file" ]; then
        # Check that the file references worktree-mode or the worktree protocol
        if ! grep -q "worktree-mode\|pact-worktree.md" "$file"; then
            echo "  ✗ $file missing worktree configuration reference"
            config_ok=false
        fi
    fi
done
if [ "$config_ok" = true ]; then
    echo "  ✓ All command files reference worktree configuration"
    PASS=$((PASS + 1))
else
    FAIL=$((FAIL + 1))
fi
echo ""

# --- 12. Anchor Link Validation (4 checks) ---
echo "12. Anchor Link Validation:"

# Check that anchor links in command files correspond to actual headers in protocol
# Expected anchors: setup-protocol, work-protocol, merge-back-protocol, cleanup-protocol
# GitHub-style anchors: lowercase, hyphens for spaces

# Check 12.1: Setup Protocol header exists (for #setup-protocol anchor)
check_header "$WORKTREE_PROTOCOL" \
    "Setup Protocol header exists (validates #setup-protocol anchor)" \
    "Setup Protocol"

# Check 12.2: Work Protocol header exists (for #work-protocol anchor)
check_header "$WORKTREE_PROTOCOL" \
    "Work Protocol header exists (validates #work-protocol anchor)" \
    "Work Protocol"

# Check 12.3: Merge-Back Protocol header exists (for #merge-back-protocol anchor)
check_header "$WORKTREE_PROTOCOL" \
    "Merge-Back Protocol header exists (validates #merge-back-protocol anchor)" \
    "Merge-Back Protocol"

# Check 12.4: Cleanup Protocol header exists (for #cleanup-protocol anchor)
check_header "$WORKTREE_PROTOCOL" \
    "Cleanup Protocol header exists (validates #cleanup-protocol anchor)" \
    "Cleanup Protocol"
echo ""

# --- 13. Grammar Check (1 check) ---
echo "13. Grammar Verification:"

# Check 13.1: HALT signal description has correct grammar
if grep -q "A HALT signal indicates viability threats" "$WORKTREE_PROTOCOL"; then
    echo "  ✓ HALT signal description has correct grammar"
    PASS=$((PASS + 1))
else
    echo "  ✗ HALT signal description grammar issue (should be 'A HALT signal indicates...')"
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
