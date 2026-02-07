#!/usr/bin/env bash
# scripts/verify-agent-teams.sh
# Verifies that v3.0 Agent Teams patterns are consistently applied across
# command files, CLAUDE.md, agent definitions, hooks, and protocols.
# Checks that old subagent/background patterns have been replaced and
# new Agent Teams patterns (TeamCreate, SendMessage, teammate) are present.

set -e

# Validate running from repo root
if [ ! -d "pact-plugin" ]; then
    echo "ERROR: Must run from repo root (pact-plugin/ directory not found)"
    exit 1
fi

echo "=== Agent Teams Verification ==="
echo ""

COMMANDS_DIR="pact-plugin/commands"
AGENTS_DIR="pact-plugin/agents"
HOOKS_DIR="pact-plugin/hooks"
PROTOCOLS_DIR="pact-plugin/protocols"
SKILLS_DIR="pact-plugin/skills"
CLAUDE_MD="pact-plugin/CLAUDE.md"
PLUGIN_JSON="pact-plugin/.claude-plugin/plugin.json"

PASS=0
FAIL=0

# Helper: check that a file contains a pattern
check_pattern() {
    local file="$1"
    local name="$2"
    local pattern="$3"

    if [ ! -f "$file" ]; then
        echo "  ✗ $name: FILE NOT FOUND ($file)"
        FAIL=$((FAIL + 1))
        return 1
    fi

    if grep -q "$pattern" "$file"; then
        echo "  ✓ $name"
        PASS=$((PASS + 1))
        return 0
    else
        echo "  ✗ $name: pattern not found"
        FAIL=$((FAIL + 1))
        return 1
    fi
}

# Helper: check that a file does NOT contain a pattern (negative check)
check_absent() {
    local file="$1"
    local name="$2"
    local pattern="$3"

    if [ ! -f "$file" ]; then
        echo "  ✗ $name: FILE NOT FOUND ($file)"
        FAIL=$((FAIL + 1))
        return 1
    fi

    if grep -q "$pattern" "$file"; then
        echo "  ✗ $name: pattern should NOT be present"
        FAIL=$((FAIL + 1))
        return 1
    else
        echo "  ✓ $name"
        PASS=$((PASS + 1))
        return 0
    fi
}

# --- 1. Command files reference Agent Teams patterns ---
echo "1. Command files reference Agent Teams patterns:"
for cmd_file in orchestrate.md comPACT.md peer-review.md plan-mode.md imPACT.md; do
    check_pattern "$COMMANDS_DIR/$cmd_file" \
        "$cmd_file references teammate" \
        "teammate"
done
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "orchestrate.md references SendMessage" \
    "SendMessage"
check_pattern "$COMMANDS_DIR/orchestrate.md" \
    "orchestrate.md references team_name" \
    "team_name"
echo ""

# --- 2. CLAUDE.md Team Lifecycle section ---
echo "2. CLAUDE.md Team Lifecycle section:"
check_pattern "$CLAUDE_MD" \
    "CLAUDE.md has Team Lifecycle section" \
    "### Team Lifecycle"
check_pattern "$CLAUDE_MD" \
    "CLAUDE.md references TeamCreate" \
    "TeamCreate"
check_pattern "$CLAUDE_MD" \
    "CLAUDE.md has teammate spawning pattern" \
    "Teammate lifecycle"
check_pattern "$CLAUDE_MD" \
    "CLAUDE.md has Plan Approval pattern" \
    "Plan Approval"
echo ""

# --- 3. pact-task-tracking SKILL.md references SendMessage ---
echo "3. pact-task-tracking SKILL.md:"
TASK_TRACKING="$SKILLS_DIR/pact-task-tracking/SKILL.md"
check_pattern "$TASK_TRACKING" \
    "pact-task-tracking references SendMessage" \
    "SendMessage"
check_pattern "$TASK_TRACKING" \
    "pact-task-tracking references HANDOFF" \
    "HANDOFF"
check_pattern "$TASK_TRACKING" \
    "pact-task-tracking references BLOCKER" \
    "BLOCKER"
echo ""

# --- 4. Agent definitions reference pact-task-tracking skill ---
echo "4. Agent definitions reference pact-task-tracking skill:"
for agent_file in "$AGENTS_DIR"/*.md; do
    agent_name=$(basename "$agent_file" .md)
    check_pattern "$agent_file" \
        "$agent_name references pact-task-tracking" \
        "pact-task-tracking"
done
echo ""

# --- 5. Agent definitions reference HANDOFF via SendMessage ---
echo "5. Agent definitions reference HANDOFF delivery via SendMessage:"
for agent_file in "$AGENTS_DIR"/*.md; do
    agent_name=$(basename "$agent_file" .md)
    check_pattern "$agent_file" \
        "$agent_name references SendMessage" \
        "SendMessage"
done
echo ""

# --- 6. Protocol files use teammate terminology ---
echo "6. Protocol files use teammate terminology:"
check_pattern "$PROTOCOLS_DIR/pact-task-hierarchy.md" \
    "Task hierarchy uses teammate" \
    "teammate"
check_pattern "$PROTOCOLS_DIR/algedonic.md" \
    "Algedonic protocol uses teammate" \
    "teammate"
check_pattern "$PROTOCOLS_DIR/pact-agent-stall.md" \
    "Agent stall protocol uses teammate" \
    "teammate"
check_pattern "$PROTOCOLS_DIR/pact-scope-contract.md" \
    "Scope contract uses teammate" \
    "teammate"
echo ""

# --- 7. hooks.json preserves SubagentStop event name ---
echo "7. hooks.json preserves SubagentStop event name:"
check_pattern "$HOOKS_DIR/hooks.json" \
    "hooks.json has SubagentStop event (platform name)" \
    "SubagentStop"
echo ""

# --- 8. Hook source files contain Agent Teams functions ---
echo "8. Hook source files contain Agent Teams functions:"
check_pattern "$HOOKS_DIR/shared/team_utils.py" \
    "team_utils.py exists" \
    "team"
check_pattern "$HOOKS_DIR/session_init.py" \
    "session_init.py has _team_instruction" \
    "_team_instruction"
check_pattern "$HOOKS_DIR/stop_audit.py" \
    "stop_audit.py has audit_team_state" \
    "audit_team_state"
echo ""

# --- 9. Refresh system contains team context handling ---
echo "9. Refresh system contains team context handling:"
check_pattern "$HOOKS_DIR/refresh/checkpoint_builder.py" \
    "checkpoint_builder.py has _get_team_context" \
    "_get_team_context"
check_pattern "$HOOKS_DIR/refresh/checkpoint_builder.py" \
    "checkpoint_builder.py references SendMessage" \
    "SendMessage"
check_pattern "$HOOKS_DIR/refresh/patterns.py" \
    "patterns.py has team-related patterns" \
    "TEAM"
check_pattern "$HOOKS_DIR/refresh/transcript_parser.py" \
    "transcript_parser.py has team interaction parsing" \
    "team"
echo ""

# --- 10. No run_in_background in command files ---
echo "10. No run_in_background in command files (replaced by team spawning):"
for cmd_file in orchestrate.md comPACT.md peer-review.md plan-mode.md imPACT.md; do
    check_absent "$COMMANDS_DIR/$cmd_file" \
        "$cmd_file has no run_in_background" \
        "run_in_background"
done
echo ""

# --- 11. No TaskOutput in command files ---
echo "11. No TaskOutput in command files (replaced by SendMessage):"
for cmd_file in orchestrate.md comPACT.md peer-review.md plan-mode.md imPACT.md; do
    check_absent "$COMMANDS_DIR/$cmd_file" \
        "$cmd_file has no TaskOutput" \
        "TaskOutput"
done
echo ""

# --- 12. plugin.json version is 3.0.0 ---
echo "12. plugin.json version:"
check_pattern "$PLUGIN_JSON" \
    "plugin.json version is 3.0.0" \
    '"version": "3.0.0"'
echo ""

# --- 13. rePACT.md no longer exists in commands ---
echo "13. rePACT.md removed from commands:"
if [ ! -f "$COMMANDS_DIR/rePACT.md" ]; then
    echo "  ✓ rePACT.md does not exist in commands/ (correctly removed)"
    PASS=$((PASS + 1))
else
    echo "  ✗ rePACT.md still exists in commands/ (should be removed)"
    FAIL=$((FAIL + 1))
fi
echo ""

# --- Summary ---
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
