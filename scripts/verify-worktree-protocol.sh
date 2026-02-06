#!/bin/bash
# scripts/verify-worktree-protocol.sh
# Verification for worktree integration across PACT commands and protocols.
# Checks that worktree-setup and worktree-cleanup skills exist, and that
# all workflow commands properly reference worktree lifecycle operations.

set -e

# Validate running from repo root
if [ ! -d "pact-plugin" ]; then
    echo "ERROR: Must run from repo root (pact-plugin/ directory not found)"
    exit 1
fi

echo "=== Worktree Protocol Verification ==="
echo ""

PROTOCOLS_DIR="pact-plugin/protocols"
COMMANDS_DIR="pact-plugin/commands"
SKILLS_DIR="pact-plugin/skills"

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

    if grep -q -- "$pattern" "$file"; then
        echo "  ✓ $name"
        PASS=$((PASS + 1))
        return 0
    else
        echo "  ✗ $name: pattern not found"
        FAIL=$((FAIL + 1))
        return 1
    fi
}

# --- 1. worktree-setup skill exists and has required sections ---
echo "1. worktree-setup skill exists and has required sections:"
SETUP_SKILL="$SKILLS_DIR/worktree-setup/SKILL.md"
if [ ! -f "$SETUP_SKILL" ]; then
    echo "  ✗ worktree-setup SKILL.md exists: FILE NOT FOUND ($SETUP_SKILL)"
    FAIL=$((FAIL + 1))
else
    echo "  ✓ worktree-setup SKILL.md exists"
    PASS=$((PASS + 1))
    # Check for frontmatter (--- delimiters)
    if head -1 "$SETUP_SKILL" | grep -q -- "^---"; then
        echo "  ✓ worktree-setup has frontmatter"
        PASS=$((PASS + 1))
    else
        echo "  ✗ worktree-setup has frontmatter: missing opening ---"
        FAIL=$((FAIL + 1))
    fi
fi
echo ""

# --- 2. worktree-cleanup skill exists and has required sections ---
echo "2. worktree-cleanup skill exists and has required sections:"
CLEANUP_SKILL="$SKILLS_DIR/worktree-cleanup/SKILL.md"
if [ ! -f "$CLEANUP_SKILL" ]; then
    echo "  ✗ worktree-cleanup SKILL.md exists: FILE NOT FOUND ($CLEANUP_SKILL)"
    FAIL=$((FAIL + 1))
else
    echo "  ✓ worktree-cleanup SKILL.md exists"
    PASS=$((PASS + 1))
    # Check for frontmatter (--- delimiters)
    if head -1 "$CLEANUP_SKILL" | grep -q -- "^---"; then
        echo "  ✓ worktree-cleanup has frontmatter"
        PASS=$((PASS + 1))
    else
        echo "  ✗ worktree-cleanup has frontmatter: missing opening ---"
        FAIL=$((FAIL + 1))
    fi
fi
echo ""

# --- 3. orchestrate.md references worktree-setup at workflow start ---
echo "3. orchestrate.md references worktree-setup at workflow start:"
check_pattern "$COMMANDS_DIR/orchestrate.md" "orchestrate.md references worktree-setup at workflow start" "worktree-setup"
echo ""

# --- 4. comPACT.md references worktree-setup in pre-invocation ---
echo "4. comPACT.md references worktree-setup in pre-invocation:"
check_pattern "$COMMANDS_DIR/comPACT.md" "comPACT.md references worktree-setup in pre-invocation" "worktree-setup"
echo ""

# --- 5. comPACT.md includes peer-review prompt after commit ---
echo "5. comPACT.md includes peer-review prompt after commit:"
check_pattern "$COMMANDS_DIR/comPACT.md" "comPACT.md includes peer-review prompt after commit" "Create PR"
echo ""

# --- 6. peer-review.md includes worktree-cleanup after merge ---
echo "6. peer-review.md includes worktree-cleanup after merge:"
check_pattern "$COMMANDS_DIR/peer-review.md" "peer-review.md includes worktree-cleanup after merge" "worktree-cleanup"
echo ""

# --- 7. pact-scope-phases.md ATOMIZE references worktree-setup ---
echo "7. pact-scope-phases.md ATOMIZE references worktree-setup:"
# Extract ATOMIZE section and check for worktree-setup within it
atomize_section=$(sed -n '/^### ATOMIZE Phase/,/^### /p' "$PROTOCOLS_DIR/pact-scope-phases.md" | sed '$d')
if echo "$atomize_section" | grep -q "worktree-setup"; then
    echo "  ✓ ATOMIZE phase references worktree-setup"
    PASS=$((PASS + 1))
else
    echo "  ✗ ATOMIZE phase references worktree-setup: pattern not found in ATOMIZE section"
    FAIL=$((FAIL + 1))
fi
echo ""

# --- 8. pact-scope-phases.md CONSOLIDATE references worktree-cleanup ---
echo "8. pact-scope-phases.md CONSOLIDATE references worktree-cleanup:"
# Extract CONSOLIDATE section and check for worktree-cleanup within it
consolidate_section=$(sed -nE '/^### CONSOLIDATE Phase/,/^### |^---$/p' "$PROTOCOLS_DIR/pact-scope-phases.md" | sed '$d')
if echo "$consolidate_section" | grep -q "worktree-cleanup"; then
    echo "  ✓ CONSOLIDATE phase references worktree-cleanup"
    PASS=$((PASS + 1))
else
    echo "  ✗ CONSOLIDATE phase references worktree-cleanup: pattern not found in CONSOLIDATE section"
    FAIL=$((FAIL + 1))
fi
echo ""

# --- 9. pact-scope-phases.md documents suffix branch behavior for scoped execution ---
echo "9. pact-scope-phases.md documents suffix branch behavior for scoped execution:"
check_pattern "$PROTOCOLS_DIR/pact-scope-phases.md" \
    "pact-scope-phases.md documents suffix branch" \
    "suffix branch"
check_pattern "$PROTOCOLS_DIR/pact-scope-phases.md" \
    "pact-scope-phases.md documents worktree isolation" \
    "Worktree isolation"
echo ""

# --- 10. orchestrate.md contains worktree path propagation instruction ---
echo "10. orchestrate.md contains worktree path propagation instruction:"
check_pattern "$COMMANDS_DIR/orchestrate.md" "orchestrate.md contains worktree path propagation instruction" "worktree_path"
echo ""

# --- 11. comPACT.md agent prompt templates include worktree path ---
echo "11. comPACT.md agent prompt templates include worktree path:"
check_pattern "$COMMANDS_DIR/comPACT.md" "comPACT.md agent prompt templates include worktree path" "worktree_path"
echo ""

# --- 12. pact-scope-phases.md documents worktree path in teammate spawn ---
echo "12. pact-scope-phases.md documents worktree path in spawn prompt:"
check_pattern "$PROTOCOLS_DIR/pact-scope-phases.md" "pact-scope-phases.md documents worktree path in spawn prompt" "worktree_path\|Worktree"
echo ""

# --- 13. plan-mode.md does not reference worktree skills ---
echo "13. plan-mode.md does not reference worktree skills:"
if grep -qE "worktree-setup|worktree-cleanup" "$COMMANDS_DIR/plan-mode.md" 2>/dev/null; then
  echo "  ✗ plan-mode.md should NOT reference worktree skills"
  FAIL=$((FAIL + 1))
else
  echo "  ✓ plan-mode.md correctly excludes worktree references"
  PASS=$((PASS + 1))
fi
echo ""

# --- 14. Skill files contain Edge Cases sections ---
echo "14. Skill files contain Edge Cases sections:"
check_pattern "$SKILLS_DIR/worktree-setup/SKILL.md" \
    "worktree-setup SKILL.md has Edge Cases section" \
    "## Edge Cases"
check_pattern "$SKILLS_DIR/worktree-cleanup/SKILL.md" \
    "worktree-cleanup SKILL.md has Edge Cases section" \
    "## Edge Cases"
echo ""

# --- 15. imPACT.md contains worktree context for phase re-entry ---
echo "15. imPACT.md contains worktree context for phase re-entry:"
check_pattern "$COMMANDS_DIR/imPACT.md" \
    "imPACT.md references worktree context" \
    "worktree"
echo ""

# --- 16. orchestrate.md CONSOLIDATE references worktree-cleanup ---
echo "16. orchestrate.md CONSOLIDATE references worktree-cleanup:"
# Extract the CONSOLIDATE Phase section from orchestrate.md
consolidate_orchestrate=$(sed -n '/^### CONSOLIDATE Phase/,/^### \|^---$/p' "$COMMANDS_DIR/orchestrate.md" | sed '$d')
if echo "$consolidate_orchestrate" | grep -q "worktree-cleanup"; then
    echo "  ✓ orchestrate.md CONSOLIDATE phase references worktree-cleanup"
    PASS=$((PASS + 1))
else
    echo "  ✗ orchestrate.md CONSOLIDATE phase references worktree-cleanup: pattern not found in CONSOLIDATE section"
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
