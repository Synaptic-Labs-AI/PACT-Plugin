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
verify "pact-s4-checkpoints.md" "S4 Checkpoints (lines 154-224)" "154,224"
verify "pact-s4-environment.md" "S4 Environment (lines 226-298)" "226,298"
verify "pact-s4-tension.md" "S4 Tension (lines 300-363)" "300,363"
verify "pact-s1-autonomy.md" "S1 Autonomy (lines 526-599)" "526,599"
verify "pact-variety.md" "Variety (lines 644-705)" "644,705"

# Combined-range extracts
verify "pact-s2-coordination.md" "S2 Coordination (lines 365-525 + 987-1001)" "365,525" "987,1001"
verify "pact-workflows.md" "Workflows (lines 706-848)" "706,848"
verify "pact-task-hierarchy.md" "Task Hierarchy (lines 860-984)" "860,984"
verify "pact-phase-transitions.md" "Phase Transitions (lines 849-859 + 1002-1079)" "849,859" "1002,1079"
verify "pact-documentation.md" "Documentation (lines 1080-1104)" "1080,1104"
verify "pact-agent-stall.md" "Agent Stall Detection (lines 1105-1136)" "1105,1136"
verify "pact-completeness.md" "Completeness Signals (lines 1138-1172)" "1138,1172"
verify "pact-scope-detection.md" "Scope Detection (lines 1174-1305)" "1174,1305"
verify "pact-scope-contract.md" "Scope Contract (lines 1307-1463)" "1307,1463"
verify "pact-scope-phases.md" "Scoped Phases (lines 1465-1540)" "1465,1540"

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
