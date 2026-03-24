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
verify "pact-s4-checkpoints.md" "S4 Checkpoints (lines 70-149)" "70,149"
verify "pact-s4-environment.md" "S4 Environment (lines 151-223)" "151,223"
verify "pact-s4-tension.md" "S4 Tension (lines 225-288)" "225,288"
verify "pact-ct-teachback.md" "CT Teachback (lines 290-396)" "290,396"
verify "pact-s1-autonomy.md" "S1 Autonomy (lines 541-614)" "541,614"
verify "pact-variety.md" "Variety (lines 629-751)" "629,751"

# Combined-range extracts
verify "pact-s2-coordination.md" "S2 Coordination (lines 398-539 + 1070-1083)" "398,539" "1070,1083"
verify "pact-workflows.md" "Workflows (lines 753-930)" "753,930"
verify "pact-task-hierarchy.md" "Task Hierarchy (lines 943-1068)" "943,1068"
verify "pact-phase-transitions.md" "Phase Transitions (lines 932-941 + 1085-1121)" "932,941" "1085,1121"
verify "pact-agent-stall.md" "Agent Stall Detection (lines 1123-1154)" "1123,1154"
verify "pact-completeness.md" "Completeness Signals (lines 1156-1194)" "1156,1194"
verify "pact-scope-detection.md" "Scope Detection (lines 1196-1298)" "1196,1298"
verify "pact-scope-contract.md" "Scope Contract (lines 1300-1414)" "1300,1414"
verify "pact-scope-phases.md" "Scoped Phases (lines 1416-1497)" "1416,1497"
verify "pact-audit.md" "Concurrent Audit (lines 1499-1636)" "1499,1636"

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
