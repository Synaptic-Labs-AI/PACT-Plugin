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
verify "pact-s5-policy.md" "S5 Policy (lines 13-68)" "13,68"
verify "pact-s4-checkpoints.md" "S4 Checkpoints (lines 70-160)" "70,160"
verify "pact-s4-environment.md" "S4 Environment (lines 162-248)" "162,248"
verify "pact-s4-tension.md" "S4 Tension (lines 250-313)" "250,313"
verify "pact-ct-teachback.md" "CT Teachback (lines 315-421)" "315,421"
verify "pact-s1-autonomy.md" "S1 Autonomy (lines 595-668)" "595,668"
verify "pact-transduction.md" "Transduction (lines 683-754)" "683,754"
verify "pact-variety.md" "Variety (lines 756-915)" "756,915"

# Combined-range extracts
verify "pact-s2-coordination.md" "S2 Coordination (lines 423-593 + 1230-1243)" "423,593" "1230,1243"
verify "pact-workflows.md" "Workflows (lines 917-1088)" "917,1088"
verify "pact-task-hierarchy.md" "Task Hierarchy (lines 1103-1228)" "1103,1228"
verify "pact-phase-transitions.md" "Phase Transitions (lines 1090-1101 + 1245-1281)" "1090,1101" "1245,1281"
verify "pact-agent-stall.md" "Agent Stall Detection (lines 1283-1316)" "1283,1316"
verify "pact-completeness.md" "Completeness Signals (lines 1318-1356)" "1318,1356"
verify "pact-scope-detection.md" "Scope Detection (lines 1358-1460)" "1358,1460"
verify "pact-scope-contract.md" "Scope Contract (lines 1462-1576)" "1462,1576"
verify "pact-scope-phases.md" "Scoped Phases (lines 1578-1659)" "1578,1659"
verify "pact-audit.md" "Concurrent Audit (lines 1661-1798)" "1661,1798"
verify "pact-self-repair.md" "Self-Repair (lines 1800-1867)" "1800,1867"
verify "pact-channel-capacity.md" "Channel Capacity (lines 1869-1948)" "1869,1948"

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
