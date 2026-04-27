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
verify "pact-s5-policy.md" "S5 Policy (lines 13-156)" "13,156"
verify "pact-s4-checkpoints.md" "S4 Checkpoints (lines 158-237)" "158,237"
verify "pact-s4-environment.md" "S4 Environment (lines 239-311)" "239,311"
verify "pact-s4-tension.md" "S4 Tension (lines 313-376)" "313,376"
verify "pact-ct-teachback.md" "CT Teachback (lines 378-484)" "378,484"
verify "pact-s1-autonomy.md" "S1 Autonomy (lines 647-720)" "647,720"
verify "pact-variety.md" "Variety (lines 766-888)" "766,888"

# Combined-range extracts
verify "pact-s2-coordination.md" "S2 Coordination (lines 486-646 + 1211-1225)" "486,646" "1211,1225"
verify "pact-workflows.md" "Workflows (lines 890-1072)" "890,1072"
verify "pact-task-hierarchy.md" "Task Hierarchy (lines 1084-1209)" "1084,1209"
verify "pact-phase-transitions.md" "Phase Transitions (lines 1073-1082 + 1225-1305)" "1073,1082" "1225,1305"
verify "pact-agent-stall.md" "Agent Stall Detection (lines 1306-1337)" "1306,1337"
verify "pact-completeness.md" "Completeness Signals (lines 1339-1377)" "1339,1377"
verify "pact-scope-detection.md" "Scope Detection (lines 1379-1513)" "1379,1513"
verify "pact-scope-contract.md" "Scope Contract (lines 1515-1672)" "1515,1672"
verify "pact-scope-phases.md" "Scoped Phases (lines 1674-1755)" "1674,1755"
verify "pact-audit.md" "Concurrent Audit (lines 1757-1923)" "1757,1923"
verify "pact-state-recovery.md" "State Recovery (lines 1941-2039)" "1941,2039"

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
