#!/bin/bash
# scripts/verify-task-hierarchy.sh
# Verifies that Task Hierarchy sections in command files contain
# the expected lifecycle patterns (TaskCreate, in_progress, completed)

set -e

echo "=== Task Hierarchy Verification ==="
echo ""

COMMANDS_DIR="pact-plugin/commands"

if [ ! -d "$COMMANDS_DIR" ]; then
    echo "ERROR: Commands directory $COMMANDS_DIR not found"
    exit 1
fi

PASS=0
FAIL=0

# Function to check a command file for required patterns
# Args: file, description, pattern1, pattern2, ...
# Optional env var SECTION_HEADING overrides the default "## Task Hierarchy"
verify_patterns() {
    local file="$1"
    local name="$2"
    shift 2
    local patterns=("$@")

    local filepath="$COMMANDS_DIR/$file"
    local heading="${SECTION_HEADING:-## Task Hierarchy}"

    if [ ! -f "$filepath" ]; then
        echo "  ✗ $name: FILE NOT FOUND ($filepath)"
        FAIL=$((FAIL + 1))
        return
    fi

    # Extract section from heading to next ## heading
    local section
    section=$(sed -n "/^${heading}/,/^## /p" "$filepath" | sed '$d')

    if [ -z "$section" ]; then
        echo "  ✗ $name: No '$heading' section found"
        FAIL=$((FAIL + 1))
        return
    fi

    local missing=()
    for pattern in "${patterns[@]}"; do
        if ! echo "$section" | grep -q "$pattern"; then
            missing+=("$pattern")
        fi
    done

    if [ ${#missing[@]} -eq 0 ]; then
        echo "  ✓ $name: all ${#patterns[@]} patterns present"
        PASS=$((PASS + 1))
    else
        echo "  ✗ $name: missing ${#missing[@]} of ${#patterns[@]} patterns:"
        for m in "${missing[@]}"; do
            echo "      - \"$m\""
        done
        FAIL=$((FAIL + 1))
    fi
}

# --- orchestrate.md ---
echo "orchestrate.md:"
verify_patterns "orchestrate.md" "Feature task lifecycle" \
    "TaskCreate: Feature task" \
    "in_progress" \
    "completed"
verify_patterns "orchestrate.md" "Phase task lifecycle" \
    "TaskCreate: Phase tasks" \
    "in_progress" \
    "completed"
verify_patterns "orchestrate.md" "Agent task lifecycle" \
    "agent task" \
    "in_progress" \
    "completed"
verify_patterns "orchestrate.md" "Skipped phase handling" \
    "Skipped phases" \
    "completed" \
    "metadata"
echo ""

# --- comPACT.md ---
echo "comPACT.md:"
verify_patterns "comPACT.md" "Feature task lifecycle" \
    "TaskCreate: Feature task" \
    "in_progress" \
    "completed"
verify_patterns "comPACT.md" "Agent task lifecycle" \
    "Agent task" \
    "in_progress" \
    "completed"
echo ""

# --- peer-review.md ---
echo "peer-review.md:"
verify_patterns "peer-review.md" "Review task lifecycle" \
    "TaskCreate: Review task" \
    "in_progress" \
    "completed"
verify_patterns "peer-review.md" "Reviewer task lifecycle" \
    "TaskCreate: Reviewer" \
    "in_progress" \
    "completed"
echo ""

# --- plan-mode.md ---
echo "plan-mode.md:"
verify_patterns "plan-mode.md" "Planning task lifecycle" \
    "TaskCreate: Planning task" \
    "in_progress" \
    "completed"
verify_patterns "plan-mode.md" "Consultation task lifecycle" \
    "Consultation task" \
    "in_progress" \
    "completed"
echo ""

# --- rePACT.md ---
echo "rePACT.md:"
verify_patterns "rePACT.md" "Sub-feature task lifecycle" \
    "TaskCreate: Sub-feature task" \
    "in_progress" \
    "completed"
echo ""

# --- imPACT.md ---
# imPACT uses different section names than other commands
echo "imPACT.md:"
SECTION_HEADING="## Task Operations" \
verify_patterns "imPACT.md" "Blocker task lifecycle" \
    "TaskCreate" \
    "completed"
SECTION_HEADING="## Phase Re-Entry Task Protocol" \
verify_patterns "imPACT.md" "Phase re-entry lifecycle" \
    "TaskCreate" \
    "in_progress" \
    "completed"
echo ""

# --- Scope-Aware Task Conventions ---
echo "Scope-aware conventions (rePACT.md):"
verify_patterns "rePACT.md" "Scope naming prefix" \
    "\[scope:" \
    "scope_id"
verify_patterns "rePACT.md" "Scope metadata convention" \
    "scope_id" \
    "metadata"
echo ""

# --- Phase headings ---
echo "Phase headings (orchestrate.md):"

# Helper: check that orchestrate.md contains a pattern (reuse check_pattern logic inline)
check_pattern_file() {
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

# Standard phases (unnumbered - PACT acronym provides sequencing)
check_pattern_file "$COMMANDS_DIR/orchestrate.md" "PREPARE Phase exists" "### PREPARE Phase"
check_pattern_file "$COMMANDS_DIR/orchestrate.md" "ARCHITECT Phase exists" "### ARCHITECT Phase"
check_pattern_file "$COMMANDS_DIR/orchestrate.md" "CODE Phase exists" "### CODE Phase"
check_pattern_file "$COMMANDS_DIR/orchestrate.md" "TEST Phase exists" "### TEST Phase"
# Scoped phases (only active when decomposition occurs)
check_pattern_file "$COMMANDS_DIR/orchestrate.md" "ATOMIZE Phase exists" "ATOMIZE Phase (Scoped Orchestration Only)"
check_pattern_file "$COMMANDS_DIR/orchestrate.md" "CONSOLIDATE Phase exists" "CONSOLIDATE Phase (Scoped Orchestration Only)"
echo ""

echo "Scope-aware conventions (pact-protocols.md SSOT):"
PROTOCOLS_FILE="pact-plugin/protocols/pact-protocols.md"
if [ -f "$PROTOCOLS_FILE" ]; then
    # Check that the task hierarchy section in SSOT contains scope conventions
    section=$(sed -n '/^## Task Hierarchy/,/^## /p' "$PROTOCOLS_FILE" | sed '$d')

    # Check 1: Scope naming convention documented
    if echo "$section" | grep -q "\[scope:{scope_id}\]"; then
        echo "  ✓ SSOT scope naming convention: pattern present"
        PASS=$((PASS + 1))
    else
        echo "  ✗ SSOT scope naming convention: [scope:{scope_id}] pattern missing"
        FAIL=$((FAIL + 1))
    fi

    # Check 2: Scope metadata documented
    if echo "$section" | grep -q '"scope_id"'; then
        echo "  ✓ SSOT scope metadata: scope_id field present"
        PASS=$((PASS + 1))
    else
        echo "  ✗ SSOT scope metadata: scope_id field missing in metadata example"
        FAIL=$((FAIL + 1))
    fi

    # Check 3: Scoped hierarchy diagram present (ATOMIZE and CONSOLIDATE)
    atomize_ok=false
    consolidate_ok=false
    if echo "$section" | grep -q "ATOMIZE Phase Task"; then
        atomize_ok=true
    fi
    if echo "$section" | grep -q "CONSOLIDATE Phase Task"; then
        consolidate_ok=true
    fi
    if [ "$atomize_ok" = true ] && [ "$consolidate_ok" = true ]; then
        echo "  ✓ SSOT scoped hierarchy: ATOMIZE and CONSOLIDATE Phase Tasks in diagram"
        PASS=$((PASS + 1))
    else
        echo "  ✗ SSOT scoped hierarchy: missing ATOMIZE/CONSOLIDATE Phase Tasks in hierarchy diagram"
        FAIL=$((FAIL + 1))
    fi
else
    echo "  ✗ SSOT file not found: $PROTOCOLS_FILE"
    FAIL=$((FAIL + 3))
fi
echo ""

# --- Post-review remediation improvements ---
echo "Post-review remediation improvements:"

# comPACT.md: agent commit reference exists in After Specialist Completes
check_pattern_file "$COMMANDS_DIR/comPACT.md" \
    "comPACT has agent commit verification step" \
    "Verify agent commits"

# orchestrate.md: Post-CODE Checkpoint section exists
check_pattern_file "$COMMANDS_DIR/orchestrate.md" \
    "orchestrate has Post-CODE Checkpoint" \
    "Post-CODE Checkpoint"

# orchestrate.md: PR creation constraint
check_pattern_file "$COMMANDS_DIR/orchestrate.md" \
    "orchestrate has PR creation constraint (MUST NOT)" \
    "MUST NOT.*git push\|MUST NOT.*gh pr create"

# comPACT.md: PR creation constraint
check_pattern_file "$COMMANDS_DIR/comPACT.md" \
    "comPACT has PR creation constraint (MUST NOT)" \
    "MUST NOT.*git push\|MUST NOT.*gh pr create"

# peer-review.md: Verify-Only Re-Review section exists
check_pattern_file "$COMMANDS_DIR/peer-review.md" \
    "peer-review has Verify-Only Re-Review section" \
    "Verify-Only Re-Review"

# peer-review.md: Verify-Only Re-Review has comparison table
check_pattern_file "$COMMANDS_DIR/peer-review.md" \
    "peer-review Verify-Only Re-Review has comparison table" \
    "Full Review"

# peer-review.md: Integration Verification section exists
check_pattern_file "$COMMANDS_DIR/peer-review.md" \
    "peer-review has Integration Verification section" \
    "Integration Verification"

# peer-review.md: Integration Verification has test suite step
check_pattern_file "$COMMANDS_DIR/peer-review.md" \
    "peer-review Integration Verification references test suite" \
    "test suite"

# peer-review.md: Parallel Domain-Batched Remediation named pattern
check_pattern_file "$COMMANDS_DIR/peer-review.md" \
    "peer-review has Parallel Domain-Batched Remediation pattern" \
    "Parallel Domain-Batched Remediation"

# peer-review.md: Parallel Domain-Batched Remediation has batching example
check_pattern_file "$COMMANDS_DIR/peer-review.md" \
    "peer-review Parallel Domain-Batched Remediation has example" \
    "Review findings:.*blocking items\|Backend:.*Frontend:"

# comPACT.md After Specialist Completes: old orchestrator commit step removed (negative check)
# Extract the After Specialist Completes section and verify old pattern is gone
after_section=$(sed -n '/^## After Specialist Completes/,/^## /p' "$COMMANDS_DIR/comPACT.md" | sed '$d')
if echo "$after_section" | grep -q "Create atomic commit"; then
    echo "  ✗ comPACT After Specialist Completes: old 'Create atomic commit' step should be removed"
    FAIL=$((FAIL + 1))
else
    echo "  ✓ comPACT After Specialist Completes: old orchestrator commit step removed"
    PASS=$((PASS + 1))
fi
echo ""

# --- Agent Teams integration ---
echo "Agent Teams integration:"
check_pattern_file "$COMMANDS_DIR/orchestrate.md" "orchestrate TeamCreate present" "TeamCreate"
check_pattern_file "$COMMANDS_DIR/comPACT.md" "comPACT TeamCreate present" "TeamCreate"
check_pattern_file "$COMMANDS_DIR/peer-review.md" "peer-review TeamCreate present" "TeamCreate"
check_pattern_file "$COMMANDS_DIR/plan-mode.md" "plan-mode TeamCreate present" "TeamCreate"
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
