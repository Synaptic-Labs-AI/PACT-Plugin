#!/bin/bash
# scripts/verify-protocol-extracts.sh
# Verifies that protocol extract files match their SSOT sections verbatim

set -e

echo "=== Protocol Extract Verification ==="
echo ""

SOURCE="pact-plugin/protocols/pact-protocols.md"
PROTOCOLS_DIR="pact-plugin/protocols"

if [ ! -f "$SOURCE" ]; then
    echo "ERROR: Source file $SOURCE not found"
    exit 1
fi

PASS=0
FAIL=0

# Function to verify verbatim match
# Args: extract_file, description, line_ranges (space-separated sed ranges)
verify() {
    local file="$1"
    local name="$2"
    shift 2
    local ranges="$@"

    if [ ! -f "$PROTOCOLS_DIR/$file" ]; then
        echo "✗ $name: FILE NOT FOUND ($PROTOCOLS_DIR/$file)"
        FAIL=$((FAIL + 1))
        return
    fi

    # Extract SSOT content using sed ranges to a temp file
    local tmpfile=$(mktemp)
    trap 'rm -f "$tmpfile"' RETURN

    for range in $ranges; do
        sed -n "${range}p" "$SOURCE" >> "$tmpfile"
    done

    # Compare with extract file
    if diff -q "$PROTOCOLS_DIR/$file" "$tmpfile" > /dev/null 2>&1; then
        echo "✓ $name: MATCH"
        PASS=$((PASS + 1))
    else
        echo "✗ $name: DIFFERS"
        echo "  Diff output:"
        diff "$PROTOCOLS_DIR/$file" "$tmpfile" 2>&1 | head -20 | sed 's/^/    /'
        FAIL=$((FAIL + 1))
    fi
}

# Single-range extracts
verify "pact-s5-policy.md" "S5 Policy (lines 13-152)" "13,152"
verify "pact-s4-checkpoints.md" "S4 Checkpoints (lines 154-233)" "154,233"
verify "pact-s4-environment.md" "S4 Environment (lines 235-307)" "235,307"
verify "pact-s4-tension.md" "S4 Tension (lines 309-372)" "309,372"
verify "pact-ct-teachback.md" "CT Teachback (lines 374-479)" "374,479"
verify "pact-s1-autonomy.md" "S1 Autonomy (lines 642-715)" "642,715"
verify "pact-variety.md" "Variety (lines 761-822)" "761,822"

# Combined-range extracts
verify "pact-s2-coordination.md" "S2 Coordination (lines 481-641 + 1111-1125)" "481,641" "1111,1125"
verify "pact-workflows.md" "Workflows (lines 823-972)" "823,972"
verify "pact-task-hierarchy.md" "Task Hierarchy (lines 984-1108)" "984,1108"
verify "pact-phase-transitions.md" "Phase Transitions (lines 973-983 + 1126-1205)" "973,983" "1126,1205"
verify "pact-documentation.md" "Documentation (lines 1206-1230)" "1206,1230"
verify "pact-agent-stall.md" "Agent Stall Detection (lines 1231-1260)" "1231,1260"
verify "pact-completeness.md" "Completeness Signals (lines 1262-1298)" "1262,1298"
verify "pact-scope-detection.md" "Scope Detection (lines 1300-1431)" "1300,1431"
verify "pact-scope-contract.md" "Scope Contract (lines 1433-1589)" "1433,1589"
verify "pact-scope-phases.md" "Scoped Phases (lines 1591-1666)" "1591,1666"

echo ""
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
