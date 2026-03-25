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
verify "pact-s5-policy.md" "S5 Policy (lines 13-87)" "13,87"
verify "pact-s4-checkpoints.md" "S4 Checkpoints (lines 89-168)" "89,168"
verify "pact-s4-environment.md" "S4 Environment (lines 170-242)" "170,242"
verify "pact-s4-tension.md" "S4 Tension (lines 244-307)" "244,307"
verify "pact-ct-teachback.md" "CT Teachback (lines 309-415)" "309,415"
verify "pact-s1-autonomy.md" "S1 Autonomy (lines 578-651)" "578,651"
verify "pact-variety.md" "Variety (lines 666-788)" "666,788"

# Combined-range extracts
verify "pact-s2-coordination.md" "S2 Coordination (lines 417-576 + 1102-1115)" "417,576" "1102,1115"
verify "pact-workflows.md" "Workflows (lines 790-963)" "790,963"
verify "pact-task-hierarchy.md" "Task Hierarchy (lines 975-1100)" "975,1100"
verify "pact-phase-transitions.md" "Phase Transitions (lines 964-973 + 1117-1163)" "964,973" "1117,1163"
verify "pact-agent-stall.md" "Agent Stall Detection (lines 1165-1196)" "1165,1196"
verify "pact-completeness.md" "Completeness Signals (lines 1198-1236)" "1198,1236"
verify "pact-scope-detection.md" "Scope Detection (lines 1238-1340)" "1238,1340"
verify "pact-scope-contract.md" "Scope Contract (lines 1342-1500)" "1342,1500"
verify "pact-scope-phases.md" "Scoped Phases (lines 1502-1583)" "1502,1583"
verify "pact-audit.md" "Concurrent Audit (lines 1585-1722)" "1585,1722"

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
