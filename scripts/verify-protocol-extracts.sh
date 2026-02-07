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
verify "pact-s1-autonomy.md" "S1 Autonomy (lines 539-608)" "539,608"
verify "pact-variety.md" "Variety (lines 653-714)" "653,714"

# Combined-range extracts
verify "pact-s2-coordination.md" "S2 Coordination (lines 365-538 + 997-1011)" "365,538" "997,1011"
verify "pact-workflows.md" "Workflows (lines 715-856)" "715,856"
verify "pact-task-hierarchy.md" "Task Hierarchy (lines 870-994)" "870,994"
verify "pact-phase-transitions.md" "Phase Transitions (lines 857-869 + 1012-1089)" "857,869" "1012,1089"
verify "pact-documentation.md" "Documentation (lines 1090-1114)" "1090,1114"
verify "pact-agent-stall.md" "Teammate Stall Detection (lines 1115-1145)" "1115,1145"
verify "pact-completeness.md" "Completeness Signals (lines 1147-1181)" "1147,1181"
verify "pact-scope-detection.md" "Scope Detection (lines 1183-1314)" "1183,1314"
verify "pact-scope-contract.md" "Scope Contract (lines 1316-1464)" "1316,1464"
verify "pact-scope-phases.md" "Scoped Phases (lines 1465-1551)" "1465,1551"

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
