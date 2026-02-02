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

# --- 4. Flow Ordering: Detection → Contract → rePACT → Integration ---
# Verify the expected flow ordering is documented in the scope contract lifecycle.
echo "4. Flow ordering (detection → contract → rePACT → integration):"
contract_lifecycle=$(sed -n '/^## Scope Contract/,/^## /p' "$SSOT" | sed '$d')
# Check that the lifecycle section mentions the expected flow stages in order
flow_ok=true
for stage in "Detection" "Contracts generated" "rePACT" "Integration phase"; do
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

# --- 7. Integration Phase in orchestrate.md ---
# orchestrate.md must document the integration phase between CODE and TEST,
# including delegation to architect and test engineer.
echo "7. Integration phase in orchestrate.md:"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Integration phase section exists" \
    "Phase 4: INTEGRATION"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Integration delegates to architect" \
    "pact-architect.*contract"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Integration delegates to test engineer" \
    "pact-test-engineer.*cross-scope"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "Integration failure routes through imPACT" \
    "imPACT"
echo ""

# --- 8. Integration Phase Task Hierarchy ---
# The task hierarchy in orchestrate.md must include the INTEGRATION phase task.
echo "8. Integration phase task hierarchy:"
task_hierarchy_orchestrate=$(sed -n '/^## Task Hierarchy/,/^## /p' "$COMMANDS_DIR/orchestrate.md" | sed '$d')
if echo "$task_hierarchy_orchestrate" | grep -q "INTEGRATION"; then
    echo "  ✓ orchestrate.md task hierarchy includes INTEGRATION phase"
    PASS=$((PASS + 1))
else
    echo "  ✗ orchestrate.md task hierarchy missing INTEGRATION phase"
    FAIL=$((FAIL + 1))
fi
# TEST must be Phase 5 (renumbered from Phase 4)
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "TEST is Phase 5 (renumbered)" \
    "Phase 5: TEST"
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
