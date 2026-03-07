#!/bin/bash
# scripts/verify-scope-detection-scenarios.sh
# Verifies that scope detection precondition scenarios in orchestrate.md
# stay consistent with evaluation timing logic in pact-scope-detection.md.
# Catches drift if one file is updated without the other.

set -e

echo "=== Scope Detection Scenario Consistency ==="
echo ""

ORCHESTRATE="pact-plugin/commands/orchestrate.md"
DETECTION="pact-plugin/protocols/pact-scope-detection.md"

PASS=0
FAIL=0

check_marker() {
    local file="$1"
    local label="$2"
    local pattern="$3"

    if grep -qE "$pattern" "$file"; then
        return 0
    else
        return 1
    fi
}

# Verify both files exist
for f in "$ORCHESTRATE" "$DETECTION"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: $f not found"
        exit 1
    fi
done

# Define scenario markers that must exist in BOTH files
# Format: "label|pattern"
SCENARIOS=(
    "Scenario 1: PREPARE ran -> proceed|PREPARE ran|PREPARE.*phase runs"
    "Scenario 2: PREPARE skipped with plan -> proceed|PREPARE.*skipped.*plan|PREPARE output.*plan content"
    "Scenario 3: No input, Scope >= 3 -> force PREPARE|Scope >= 3.*[Ff]orce PREPARE"
    "Scenario 4: No input, Scope < 3 -> skip detection|Scope < 3.*[Ss]kip.*detection|Variety Scope < 3.*[Ss]kip.*detection"
)

for scenario in "${SCENARIOS[@]}"; do
    label="${scenario%%|*}"
    pattern="${scenario#*|}"

    orch_ok=true
    det_ok=true

    if ! grep -qE "$pattern" "$ORCHESTRATE"; then
        orch_ok=false
    fi
    if ! grep -qE "$pattern" "$DETECTION"; then
        det_ok=false
    fi

    if $orch_ok && $det_ok; then
        echo "  ✓ $label: present in both files"
        PASS=$((PASS + 1))
    else
        echo "  ✗ $label: MISSING"
        $orch_ok || echo "    Missing in: $ORCHESTRATE"
        $det_ok  || echo "    Missing in: $DETECTION"
        FAIL=$((FAIL + 1))
    fi
done

echo ""

# Cross-check: verify the number of evaluation timing steps match
orch_steps=$(grep -cE "PREPARE ran|PREPARE skipped.*plan|Scope >= 3|Scope < 3" "$ORCHESTRATE" 2>/dev/null || echo 0)
det_steps=$(grep -cE "PREPARE.*skipped|Scope >= 3|Scope < 3" "$DETECTION" 2>/dev/null || echo 0)

# Both files should have at least 3 scenario markers (scenarios 2-4; scenario 1 "PREPARE ran" may appear more often)
if [ "$orch_steps" -ge 3 ] && [ "$det_steps" -ge 3 ]; then
    echo "  ✓ Marker density: orchestrate=$orch_steps, detection=$det_steps (both >= 3)"
    PASS=$((PASS + 1))
else
    echo "  ✗ Marker density: orchestrate=$orch_steps, detection=$det_steps (expected >= 3 each)"
    FAIL=$((FAIL + 1))
fi

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
