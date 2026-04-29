#!/bin/bash
# scripts/verify-protocol-extracts.sh
# Verifies that protocol extract files match their SSOT sections verbatim.
#
# Sections are anchored by H2 heading text (start) + the next H2 heading text
# (end sentinel), not by line numbers. This prevents the line-shift regression
# class that occurs when content is added or removed in the SSOT.

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

# Extract bytes from SSOT spanning `## <start>` (inclusive) up to the line
# before `## <end>` (exclusive), then strip a single trailing blank line if
# present. Heading match is exact-string on the H2 line minus the leading
# `## ` prefix. Output appended to the file path passed as $3.
extract_section() {
    local start="$1"
    local end="$2"
    local outfile="$3"

    awk -v start="## $start" -v end="## $end" '
        # Toggle fence state on lines beginning with ```; ignore start/end
        # heading matches inside fences so in-fence ## lines (e.g. template
        # bodies in pact-protocols.md) cannot be confused for sentinels.
        /^```/ { in_fence = !in_fence }
        !in_fence && $0 == start { capture = 1 }
        !in_fence && capture && $0 == end { capture = 0; exit }
        capture { buf[++n] = $0 }
        END {
            # Strip exactly one trailing blank line if present.
            if (n > 0 && buf[n] == "") n--
            for (i = 1; i <= n; i++) print buf[i]
        }
    ' "$SOURCE" >> "$outfile"
}

# Verify a standalone extract against one or more (start_heading, end_heading)
# pairs. Combined extracts pass multiple pairs; each pair's bytes are appended
# in order to the temp file before diff -q against the standalone.
#
# Args: extract_file description start_heading end_heading [start_heading end_heading ...]
verify() {
    local file="$1"
    local name="$2"
    shift 2

    if [ ! -f "$PROTOCOLS_DIR/$file" ]; then
        echo "✗ $name: FILE NOT FOUND ($PROTOCOLS_DIR/$file)"
        FAIL=$((FAIL + 1))
        return
    fi

    local tmpfile
    tmpfile=$(mktemp)
    # RETURN trap assumes verify() is called from script-top-level; nesting
    # verify() inside another bash function with its own RETURN trap may
    # produce unexpected cleanup behavior.
    trap 'rm -f "$tmpfile"' RETURN

    local first=1
    while [ $# -ge 2 ]; do
        if [ $first -eq 0 ]; then
            echo "" >> "$tmpfile"
        fi
        extract_section "$1" "$2" "$tmpfile"
        first=0
        shift 2
    done

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

# Verify a callsite's H2-sentinel-delimited block against a canonical fixture.
# Twin of verify() for the inbox-wake canonical-mirror surface (#591): reuses
# the same awk extractor logic but reads from per-callsite source files
# (pact-plugin/commands/*.md) and diffs against canonical fixture files
# (pact-plugin/tests/fixtures/inbox-wake-canonical/*.txt). Purely additive —
# does not touch the existing 18 protocol verify() calls.
#
# Counter-test recipe (exercised by pact-plugin/tests/test_inbox_monitor_wake.py
# in TEST phase via subprocess + tempfile mutation): copy a callsite to a
# tempfile, mutate one byte inside its sentinel-delimited block (e.g.,
# `sed 's/Monitor(/MONITOR(/'`), point this script at the mutated copy, and
# assert the script exits non-zero. The script's deterministic-exit-on-DIFFERS
# shape (FAIL increment + final exit 1) is what makes it counter-testable;
# the actual counter-test artifact lives in the Python test harness.
#
# Args: callsite_file fixture_file name start_heading end_heading
verify_inbox_wake() {
    local callsite="$1"
    local fixture="$2"
    local name="$3"
    local start="$4"
    local end="$5"

    if [ ! -f "$callsite" ]; then
        echo "✗ $name: CALLSITE NOT FOUND ($callsite)"
        FAIL=$((FAIL + 1))
        return
    fi

    if [ ! -f "$fixture" ]; then
        echo "✗ $name: FIXTURE NOT FOUND ($fixture)"
        FAIL=$((FAIL + 1))
        return
    fi

    local tmpfile
    tmpfile=$(mktemp)
    trap 'rm -f "$tmpfile"' RETURN

    # Inline the awk extractor against an arbitrary source file.
    # extract_section() above is hard-coded to read from $SOURCE.
    awk -v start="## $start" -v end="## $end" '
        /^```/ { in_fence = !in_fence }
        !in_fence && $0 == start { capture = 1 }
        !in_fence && capture && $0 == end { capture = 0; exit }
        capture { buf[++n] = $0 }
        END {
            if (n > 0 && buf[n] == "") n--
            for (i = 1; i <= n; i++) print buf[i]
        }
    ' "$callsite" > "$tmpfile"

    if diff -q "$fixture" "$tmpfile" > /dev/null 2>&1; then
        echo "✓ $name: MATCH"
        PASS=$((PASS + 1))
    else
        echo "✗ $name: DIFFERS"
        echo "  Diff output:"
        diff "$fixture" "$tmpfile" 2>&1 | head -20 | sed 's/^/    /'
        FAIL=$((FAIL + 1))
    fi
}

# Single-section extracts: (start_heading, end_heading_sentinel)
verify "pact-s5-policy.md"        "S5 Policy"               "S5 Policy Layer (Governance)"           "S4 Checkpoint Protocol"
verify "pact-s4-checkpoints.md"   "S4 Checkpoints"          "S4 Checkpoint Protocol"                 "S4 Environment Model"
verify "pact-s4-environment.md"   "S4 Environment"          "S4 Environment Model"                   "S3/S4 Tension Detection and Resolution"
verify "pact-s4-tension.md"       "S4 Tension"              "S3/S4 Tension Detection and Resolution" "Conversation Theory: Teachback Protocol"
verify "pact-ct-teachback.md"     "CT Teachback"            "Conversation Theory: Teachback Protocol" "S2 Coordination Layer"
verify "pact-s1-autonomy.md"      "S1 Autonomy"             "S1 Autonomy & Recursion"                "Algedonic Signals (Emergency Bypass)"
verify "pact-variety.md"          "Variety"                 "Variety Management"                     "The PACT Workflow Family"
verify "pact-workflows.md"        "Workflows"               "The PACT Workflow Family"               "Phase Handoffs"
verify "pact-task-hierarchy.md"   "Task Hierarchy"          "Task Hierarchy"                         "Backend ↔ Database Boundary"
verify "pact-agent-stall.md"      "Agent Stall Detection"   "Agent Stall Detection"                  "Incompleteness Signals"
verify "pact-completeness.md"     "Completeness Signals"    "Incompleteness Signals"                 "Scope Detection"
verify "pact-scope-detection.md"  "Scope Detection"         "Scope Detection"                        "Scope Contract"
verify "pact-scope-contract.md"   "Scope Contract"          "Scope Contract"                         "Scoped Phases (ATOMIZE and CONSOLIDATE)"
verify "pact-scope-phases.md"     "Scoped Phases"           "Scoped Phases (ATOMIZE and CONSOLIDATE)" "Concurrent Audit Protocol"
verify "pact-audit.md"            "Concurrent Audit"        "Concurrent Audit Protocol"              "Documentation Locations"
verify "pact-state-recovery.md"   "State Recovery"          "State Recovery Protocol"                "Session Continuity"

# Combined-section extracts: two heading-pairs concatenated in order.
verify "pact-s2-coordination.md"  "S2 Coordination" \
    "S2 Coordination Layer"       "S1 Autonomy & Recursion" \
    "Backend ↔ Database Boundary" "Test Engagement"

verify "pact-phase-transitions.md" "Phase Transitions" \
    "Phase Handoffs"              "Task Hierarchy" \
    "Test Engagement"             "Agent Stall Detection"

# ---------------------------------------------------------------------------
# Inbox-wake canonical blocks (#591) — Monitor (5 callsites)
# Each callsite mirrors the canonical Monitor block byte-equivalent between
# `## Inbox Wake — Arm Monitor (start)` and `## Inbox Wake — Arm Monitor (end)`
# H2 sentinels. Diff target: pact-plugin/tests/fixtures/inbox-wake-canonical/monitor-block.txt
INBOX_FIXTURES="pact-plugin/tests/fixtures/inbox-wake-canonical"
COMMANDS_DIR="pact-plugin/commands"

verify_inbox_wake "$COMMANDS_DIR/orchestrate.md"  "$INBOX_FIXTURES/monitor-block.txt"  "Monitor @ orchestrate"  "Inbox Wake — Arm Monitor (start)"  "Inbox Wake — Arm Monitor (end)"
verify_inbox_wake "$COMMANDS_DIR/comPACT.md"      "$INBOX_FIXTURES/monitor-block.txt"  "Monitor @ comPACT"      "Inbox Wake — Arm Monitor (start)"  "Inbox Wake — Arm Monitor (end)"
verify_inbox_wake "$COMMANDS_DIR/rePACT.md"       "$INBOX_FIXTURES/monitor-block.txt"  "Monitor @ rePACT"       "Inbox Wake — Arm Monitor (start)"  "Inbox Wake — Arm Monitor (end)"
verify_inbox_wake "$COMMANDS_DIR/plan-mode.md"    "$INBOX_FIXTURES/monitor-block.txt"  "Monitor @ plan-mode"    "Inbox Wake — Arm Monitor (start)"  "Inbox Wake — Arm Monitor (end)"
verify_inbox_wake "$COMMANDS_DIR/peer-review.md"  "$INBOX_FIXTURES/monitor-block.txt"  "Monitor @ peer-review"  "Inbox Wake — Arm Monitor (start)"  "Inbox Wake — Arm Monitor (end)"

# Inbox-wake canonical blocks (#591) — Cron (5 callsites; peer-review wraps inside conditional)
verify_inbox_wake "$COMMANDS_DIR/orchestrate.md"  "$INBOX_FIXTURES/cron-block.txt"     "Cron @ orchestrate"     "Inbox Wake — Arm Cron (start)"     "Inbox Wake — Arm Cron (end)"
verify_inbox_wake "$COMMANDS_DIR/comPACT.md"      "$INBOX_FIXTURES/cron-block.txt"     "Cron @ comPACT"         "Inbox Wake — Arm Cron (start)"     "Inbox Wake — Arm Cron (end)"
verify_inbox_wake "$COMMANDS_DIR/rePACT.md"       "$INBOX_FIXTURES/cron-block.txt"     "Cron @ rePACT"          "Inbox Wake — Arm Cron (start)"     "Inbox Wake — Arm Cron (end)"
verify_inbox_wake "$COMMANDS_DIR/plan-mode.md"    "$INBOX_FIXTURES/cron-block.txt"     "Cron @ plan-mode"       "Inbox Wake — Arm Cron (start)"     "Inbox Wake — Arm Cron (end)"
verify_inbox_wake "$COMMANDS_DIR/peer-review.md"  "$INBOX_FIXTURES/cron-block.txt"     "Cron @ peer-review"     "Inbox Wake — Arm Cron (start)"     "Inbox Wake — Arm Cron (end)"

# Inbox-wake canonical blocks (#591) — Write State File (5 callsites)
verify_inbox_wake "$COMMANDS_DIR/orchestrate.md"  "$INBOX_FIXTURES/state-file-block.txt"  "WriteStateFile @ orchestrate"  "Inbox Wake — Write State File (start)"  "Inbox Wake — Write State File (end)"
verify_inbox_wake "$COMMANDS_DIR/comPACT.md"      "$INBOX_FIXTURES/state-file-block.txt"  "WriteStateFile @ comPACT"      "Inbox Wake — Write State File (start)"  "Inbox Wake — Write State File (end)"
verify_inbox_wake "$COMMANDS_DIR/rePACT.md"       "$INBOX_FIXTURES/state-file-block.txt"  "WriteStateFile @ rePACT"       "Inbox Wake — Write State File (start)"  "Inbox Wake — Write State File (end)"
verify_inbox_wake "$COMMANDS_DIR/plan-mode.md"    "$INBOX_FIXTURES/state-file-block.txt"  "WriteStateFile @ plan-mode"    "Inbox Wake — Write State File (start)"  "Inbox Wake — Write State File (end)"
verify_inbox_wake "$COMMANDS_DIR/peer-review.md"  "$INBOX_FIXTURES/state-file-block.txt"  "WriteStateFile @ peer-review"  "Inbox Wake — Write State File (start)"  "Inbox Wake — Write State File (end)"

# Inbox-wake canonical blocks (#591) — Teardown (2 callsites)
verify_inbox_wake "$COMMANDS_DIR/wrap-up.md"      "$INBOX_FIXTURES/teardown-block.txt" "Teardown @ wrap-up"     "Inbox Wake — Teardown (start)"     "Inbox Wake — Teardown (end)"
verify_inbox_wake "$COMMANDS_DIR/pause.md"        "$INBOX_FIXTURES/teardown-block.txt" "Teardown @ pause"       "Inbox Wake — Teardown (start)"     "Inbox Wake — Teardown (end)"

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
