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
verify "pact-s5-policy.md" "S5 Policy (lines 13-155)" "13,155"
verify "pact-s4-checkpoints.md" "S4 Checkpoints (lines 157-236)" "157,236"
verify "pact-s4-environment.md" "S4 Environment (lines 238-310)" "238,310"
verify "pact-s4-tension.md" "S4 Tension (lines 312-375)" "312,375"
verify "pact-ct-teachback.md" "CT Teachback (lines 377-482)" "377,482"
verify "pact-s1-autonomy.md" "S1 Autonomy (lines 645-718)" "645,718"
verify "pact-variety.md" "Variety (lines 764-825)" "764,825"

# Combined-range extracts
verify "pact-s2-coordination.md" "S2 Coordination (lines 484-644 + 1114-1128)" "484,644" "1114,1128"
verify "pact-workflows.md" "Workflows (lines 826-975)" "826,975"
verify "pact-task-hierarchy.md" "Task Hierarchy (lines 987-1111)" "987,1111"
verify "pact-phase-transitions.md" "Phase Transitions (lines 976-986 + 1129-1208)" "976,986" "1129,1208"
verify "pact-documentation.md" "Documentation (lines 1209-1233)" "1209,1233"
verify "pact-agent-stall.md" "Agent Stall Detection (lines 1234-1263)" "1234,1263"
verify "pact-completeness.md" "Completeness Signals (lines 1265-1303)" "1265,1303"
verify "pact-scope-detection.md" "Scope Detection (lines 1305-1436)" "1305,1436"
verify "pact-scope-contract.md" "Scope Contract (lines 1438-1594)" "1438,1594"
verify "pact-scope-phases.md" "Scoped Phases (lines 1596-1671)" "1596,1671"

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
