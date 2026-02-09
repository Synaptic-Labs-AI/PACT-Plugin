#!/bin/bash
# scripts/verify-scope-integrity.sh
# Cross-cutting verification for scope-related protocols and conventions.
# Checks cross-references between scope contract, task hierarchy, and scope
# detection protocols; verifies comPACT bypasses scope detection; validates
# flow ordering consistency.

set -e

echo "=== Scope Integrity Verification ==="
echo ""

PROTOCOLS_DIR="pact-plugin/protocols"
COMMANDS_DIR="pact-plugin/commands"
SKILLS_DIR="pact-plugin/skills"
HOOKS_DIR="pact-plugin/hooks"
AGENTS_DIR="pact-plugin/agents"
SSOT="$PROTOCOLS_DIR/pact-protocols.md"

if [ ! -f "$SSOT" ]; then
    echo "ERROR: SSOT file $SSOT not found"
    exit 1
fi

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

# --- 1. comPACT Negative Check ---
# comPACT must NOT reference scope contracts, scope_id metadata, or scope detection.
# It is a single-domain workflow that bypasses scope detection entirely.
echo "1. comPACT scope bypass:"
check_absent "$COMMANDS_DIR/comPACT.md" \
    "comPACT has no scope_id metadata" \
    '"scope_id"'
check_absent "$COMMANDS_DIR/comPACT.md" \
    "comPACT has no scope contract reference" \
    'scope contract'
echo ""

# --- 2. Cross-Reference: Scope Contract → Task Hierarchy ---
# The scope contract protocol must reference scope_id which is the key linking
# contracts to the task hierarchy naming/metadata conventions.
echo "2. Cross-references (scope contract ↔ task hierarchy):"
check_pattern "$PROTOCOLS_DIR/pact-scope-contract.md" \
    "Scope contract defines scope_id field" \
    "scope_id"
# Task hierarchy SSOT must reference scope_id in its scope-aware section
task_hierarchy_section=$(sed -n '/^## Task Hierarchy/,/^## /p' "$SSOT" | sed '$d')
if echo "$task_hierarchy_section" | grep -q "scope_id"; then
    echo "  ✓ Task hierarchy SSOT references scope_id"
    PASS=$((PASS + 1))
else
    echo "  ✗ Task hierarchy SSOT does not reference scope_id"
    FAIL=$((FAIL + 1))
fi
echo ""

# --- 3. Cross-Reference: Scope Detection → Scope Contract ---
# Detection protocol must reference scope contract generation as the post-detection step.
echo "3. Cross-references (scope detection → scope contract):"
check_pattern "$PROTOCOLS_DIR/pact-scope-detection.md" \
    "Scope detection references contract generation" \
    "Scope Contract"
check_pattern "$PROTOCOLS_DIR/pact-scope-contract.md" \
    "Scope contract references detection in lifecycle" \
    "Detection"
echo ""

# --- 4. Flow Ordering: Detection → Contract → rePACT → Scope Verification ---
# Verify the expected flow ordering is documented in the scope contract lifecycle.
echo "4. Flow ordering (detection → contract → rePACT → scope verification):"
contract_lifecycle=$(sed -n '/^## Scope Contract/,/^## /p' "$SSOT" | sed '$d')
# Check that the lifecycle section mentions the expected flow stages in order
flow_ok=true
for stage in "Detection" "Contracts generated" "rePACT" "Scope verification protocol"; do
    if ! echo "$contract_lifecycle" | grep -q "$stage"; then
        echo "  ✗ Flow ordering: '$stage' not found in scope contract lifecycle"
        FAIL=$((FAIL + 1))
        flow_ok=false
    fi
done
if [ "$flow_ok" = true ]; then
    echo "  ✓ Flow ordering: all stages present in scope contract lifecycle"
    PASS=$((PASS + 1))
fi
echo ""

# --- 5. rePACT Scope Contract Reception ---
# rePACT must document how it receives and operates on scope contracts.
echo "5. rePACT scope contract reception:"
check_pattern "$COMMANDS_DIR/rePACT.md" \
    "rePACT documents scope contract reception" \
    "scope contract"
check_pattern "$COMMANDS_DIR/rePACT.md" \
    "rePACT references contract fulfillment in handoff" \
    "Contract Fulfillment"
echo ""

# --- 6. Scope Naming Consistency ---
# The naming convention [scope:{scope_id}] must appear in both SSOT task hierarchy
# and rePACT command file to ensure consistency.
echo "6. Scope naming consistency:"
if echo "$task_hierarchy_section" | grep -q '\[scope:'; then
    echo "  ✓ SSOT task hierarchy uses [scope:] naming prefix"
    PASS=$((PASS + 1))
else
    echo "  ✗ SSOT task hierarchy missing [scope:] naming prefix"
    FAIL=$((FAIL + 1))
fi
check_pattern "$COMMANDS_DIR/rePACT.md" \
    "rePACT uses [scope:] naming prefix" \
    '\[scope:'
echo ""

# --- 7. Scoped CODE path and scope verification in orchestrate.md ---
# orchestrate.md must document the scoped CODE path (rePACT + scope verification).
# No separate ATOMIZE/CONSOLIDATE phases -- scoped work happens inside standard phases.
echo "7. Scoped CODE path and scope verification in orchestrate.md:"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Scoped CODE path section exists" \
    "CODE Phase (Scoped Path)"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Scoped CODE path references rePACT dispatch" \
    "rePACT"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Scoped CODE path references scope verification protocol" \
    "pact-scope-verification.md"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Scope verification failure routes through imPACT" \
    "imPACT"
echo ""

# --- 7b. Scope verification protocol content in pact-scope-verification.md ---
# The extracted protocol must contain verification steps and failure handling.
echo "7b. Scope verification protocol content in pact-scope-verification.md:"
check_pattern "$PROTOCOLS_DIR/pact-scope-verification.md" \
    "Scope verification has contract compatibility step" \
    "Contract Compatibility"
check_pattern "$PROTOCOLS_DIR/pact-scope-verification.md" \
    "Scope verification delegates to architect" \
    "pact-architect"
check_pattern "$PROTOCOLS_DIR/pact-scope-verification.md" \
    "Scope verification has integration testing step" \
    "pact-test-engineer"
check_pattern "$PROTOCOLS_DIR/pact-scope-verification.md" \
    "Scope verification failure routes through imPACT" \
    "imPACT"
echo ""

# --- 8. Scoped CODE behavioral checks ---
# Scoped CODE path must dispatch sub-scopes via rePACT and run scope verification.
echo "8. Scoped CODE behavioral checks:"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Scoped CODE dispatches via rePACT" \
    "rePACT"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Scoped CODE has sub-scope task creation" \
    "Sub-feature task"
echo ""

# --- 9. decomposition_active skip reason ---
echo "9. decomposition_active skip reason:"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "decomposition_active skip reason documented" \
    "decomposition_active"
echo ""

# --- 10. Executor interface bidirectional cross-references ---
echo "10. Executor interface cross-references:"
check_pattern "$PROTOCOLS_DIR/pact-scope-contract.md" \
    "Scope contract references rePACT command" \
    "rePACT.md"
check_pattern "$COMMANDS_DIR/rePACT.md" \
    "rePACT references scope contract protocol" \
    "pact-scope-contract.md"
echo ""

# --- 11. Detection bypass within sub-scopes ---
echo "11. Detection bypass within sub-scopes:"
check_pattern "$PROTOCOLS_DIR/pact-scope-detection.md" \
    "Detection bypass within sub-scopes documented" \
    "does not re-evaluate detection"
echo ""

# --- 12. Scoped task hierarchy in orchestrate.md ---
# The task hierarchy in orchestrate.md must describe scoped CODE path (sub-scopes + verification).
echo "12. Scoped task hierarchy:"
task_hierarchy_orchestrate=$(sed -n '/^## Task Hierarchy/,/^## /p' "$COMMANDS_DIR/orchestrate.md" | sed '$d')
if echo "$task_hierarchy_orchestrate" | grep -q "rePACT"; then
    echo "  ✓ orchestrate.md task hierarchy references rePACT for sub-scopes"
    PASS=$((PASS + 1))
else
    echo "  ✗ orchestrate.md task hierarchy missing rePACT reference for sub-scopes"
    FAIL=$((FAIL + 1))
fi
if echo "$task_hierarchy_orchestrate" | grep -q "scope verification\|Scope Verification\|scope_id"; then
    echo "  ✓ orchestrate.md task hierarchy references scope verification"
    PASS=$((PASS + 1))
else
    echo "  ✗ orchestrate.md task hierarchy missing scope verification reference"
    FAIL=$((FAIL + 1))
fi
# TEST Phase exists (PACT acronym provides sequencing)
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "TEST Phase exists" \
    "### TEST Phase"
echo ""

# --- 13. Nesting Limit Value Assertions ---
# The canonical nesting limit must be "1 level" (not "2 levels") across all key files.
# This prevents a coordinated regression that changes the value back in all files.
echo "13. Nesting limit value assertions:"

# Positive checks: canonical files must contain "1 level" nesting limit
check_pattern "$COMMANDS_DIR/rePACT.md" \
    "rePACT has 1-level nesting limit" \
    "Maximum nesting: 1 level"
check_pattern "$PROTOCOLS_DIR/pact-s1-autonomy.md" \
    "S1 autonomy has 1-level nesting limit" \
    "Nesting limit.*1 level"
check_pattern "$PROTOCOLS_DIR/pact-protocols.md" \
    "SSOT S1 extract has 1-level nesting limit" \
    "Nesting limit.*1 level"
# All 8 agent files must have "Max nesting: 1 level"
for agent_file in "$AGENTS_DIR"/*.md; do
    agent_name=$(basename "$agent_file" .md)
    check_pattern "$agent_file" \
        "$agent_name has 1-level nesting limit" \
        "Max nesting: 1 level"
done

# Negative checks: old 2-level nesting limit must not appear anywhere
echo ""
echo "13b. Nesting limit negative checks (old values absent):"
check_absent "$COMMANDS_DIR/rePACT.md" \
    "rePACT has no 2-level nesting reference" \
    "Max nesting: 2"
check_absent "$PROTOCOLS_DIR/pact-s1-autonomy.md" \
    "S1 autonomy has no 2-level nesting reference" \
    "2 levels maximum"
check_absent "$PROTOCOLS_DIR/pact-protocols.md" \
    "SSOT has no 2-level nesting reference" \
    "2 levels maximum"
echo ""

# --- 14. Worktree integration (Phase E) ---
# Cross-cutting checks that worktree lifecycle is wired into workflow commands.
# These complement the dedicated verify-worktree-protocol.sh with scope-integrity
# perspective checks (structural cross-references, not behavioral verification).
echo "14. Worktree integration (Phase E):"
check_pattern "$SKILLS_DIR/worktree-setup/SKILL.md" \
    "worktree-setup skill exists" \
    "worktree-setup"
check_pattern "$SKILLS_DIR/worktree-cleanup/SKILL.md" \
    "worktree-cleanup skill exists" \
    "worktree-cleanup"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "orchestrate.md references worktree-setup" \
    "worktree-setup"
check_pattern "$COMMANDS_DIR/comPACT.md" \
    "comPACT.md references worktree-setup" \
    "worktree-setup"
check_pattern "$COMMANDS_DIR/peer-review.md" \
    "peer-review.md references worktree-cleanup" \
    "worktree-cleanup"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "orchestrate.md propagates worktree path to agents" \
    "worktree_path"
echo ""

# --- 15. Memory hooks baseline ---
# Verify that core memory hook files exist and contain expected entry points.
# These hooks are critical infrastructure that D1 modifies; baseline checks
# catch accidental deletion or function signature changes.
echo "15. Memory hooks baseline:"
check_pattern "$HOOKS_DIR/memory_enforce.py" \
    "memory_enforce.py has main entry point" \
    "def main()"
check_pattern "$HOOKS_DIR/staleness.py" \
    "staleness.py has detect_stale_entries function" \
    "def detect_stale_entries"
check_pattern "$HOOKS_DIR/staleness.py" \
    "staleness.py has apply_staleness_markings function" \
    "def apply_staleness_markings"
echo ""

# --- 16. Executor interface ---
# Verify that the executor interface in pact-scope-contract.md documents
# the Agent Teams mapping and maintains backend-agnostic design.
echo "16. Executor interface:"
check_pattern "$PROTOCOLS_DIR/pact-scope-contract.md" \
    "Scope contract has Agent Teams section" \
    "Agent Teams"
check_pattern "$PROTOCOLS_DIR/pact-scope-contract.md" \
    "Scope contract is backend-agnostic" \
    "Backend-agnostic"
check_pattern "$PROTOCOLS_DIR/pact-scope-contract.md" \
    "Scope contract documents TeamCreate tool" \
    "TeamCreate"
echo ""

# --- 17. Agent persistent memory ---
# All 8 agent definition files must have memory: user in their frontmatter.
# This was added in D3 to enable cross-project domain expertise accumulation.
echo "17. Agent persistent memory (memory: user in all agents):"
for agent_file in "$AGENTS_DIR"/*.md; do
    agent_name=$(basename "$agent_file" .md)
    check_pattern "$agent_file" \
        "$agent_name has memory: user" \
        "memory: user"
done
echo ""

# --- 18. Scope detection heuristics ---
# Verify that the scope detection protocol contains the expected scoring
# system: threshold value, point weights, and activation tiers.
echo "18. Scope detection heuristics:"
check_pattern "$PROTOCOLS_DIR/pact-scope-detection.md" \
    "Detection threshold is 3" \
    "Score >= 3"
check_pattern "$PROTOCOLS_DIR/pact-scope-detection.md" \
    "Strong signals worth 2 points" \
    "Strong (2 pts)"
check_pattern "$PROTOCOLS_DIR/pact-scope-detection.md" \
    "Three activation tiers documented" \
    "Autonomous"
echo ""

# --- 19. Completeness signals ---
# Verify that the SSOT completeness section documents 6 incompleteness signals.
echo "19. Completeness signals:"
check_pattern "$SSOT" \
    "SSOT documents 6 incompleteness signals" \
    "6 incompleteness signals"
echo ""

# --- 20. Agent Teams documentation (post-D2) ---
# Verify that the executor interface section documents the key Agent Teams tools.
echo "20. Agent Teams documentation (post-D2):"
check_pattern "$PROTOCOLS_DIR/pact-scope-contract.md" \
    "Scope contract documents SendMessage tool" \
    "SendMessage"
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
