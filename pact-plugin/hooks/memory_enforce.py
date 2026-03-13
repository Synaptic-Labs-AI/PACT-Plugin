#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/memory_enforce.py
Summary: SubagentStop hook that ENFORCES memory saves after PACT agent work.
Used by: Claude Code hooks.json SubagentStop hook (fires for both background
         Task agents and Agent Teams teammates)

When a PACT specialist agent/teammate completes meaningful work, this hook
tells the orchestrator/lead they MUST save memory before continuing. Uses
strong language and additionalContext to make the instruction visible and
mandatory.

This addresses the pattern where memory saves are forgotten after agent work.

Note: The pact-memory-agent itself is excluded (PACT_WORK_AGENTS list) to
avoid recursion — if the memory agent itself triggers this hook, it would
create an infinite save loop.

Input: JSON from stdin with `transcript`, `agent_id`, `transcript_path`
Output: JSON with `additionalContext` forcing memory save
"""

import json
import re
import sys
from pathlib import Path


# PACT agents that do work requiring memory saves
# Explicitly exclude pact-memory-agent to avoid recursion
PACT_WORK_AGENTS = [
    "pact-preparer",
    "pact-architect",
    "pact-backend-coder",
    "pact-frontend-coder",
    "pact-database-engineer",
    "pact-devops-engineer",
    "pact-n8n",
    "pact-test-engineer",
    "pact-security-engineer",
    "pact-qa-engineer",
]

# Patterns indicating meaningful work was done
WORK_PATTERNS = [
    # File operations
    r"(?:created|wrote|edited|modified|updated|implemented)\s+(?:\S+\.(?:py|ts|js|md|json|yaml|yml|sql|go|rs|rb))",
    r"(?:file|document|component|module|schema|migration|test)",
    # Architecture work
    r"(?:designed|architected|defined|specified|planned)",
    r"(?:diagram|interface|contract|api)",
    # Code work
    r"(?:function|class|method|endpoint|handler|service|model)",
    r"(?:implemented|refactored|fixed|added|removed)",
    # Research work
    r"(?:researched|gathered|documented|analyzed|evaluated)",
    r"(?:docs/preparation|docs/architecture)",
]

# Patterns indicating decisions were made (high-value for memory)
DECISION_PATTERNS = [
    r"(?:decided|chose|selected|opted)\s+(?:to|for)",
    r"(?:trade-?off|because|rationale|reason)",
    r"(?:alternative|approach|strategy)",
]


def is_pact_work_agent(agent_id: str) -> bool:
    """Check if this is a PACT agent that does work needing memory saves."""
    if not agent_id:
        return False
    agent_lower = agent_id.lower()
    return any(agent in agent_lower for agent in PACT_WORK_AGENTS)


def did_meaningful_work(transcript: str) -> tuple[bool, list[str]]:
    """
    Analyze transcript for meaningful work that should be saved.

    Returns:
        Tuple of (did_work, reasons_list)
    """
    if not transcript or len(transcript) < 200:
        return False, []

    transcript_lower = transcript.lower()
    reasons = []

    # Check for work patterns
    for pattern in WORK_PATTERNS:
        if re.search(pattern, transcript_lower):
            reasons.append("work completed")
            break

    # Check for decisions (high value)
    for pattern in DECISION_PATTERNS:
        if re.search(pattern, transcript_lower):
            reasons.append("decisions made")
            break

    # Check for explicit file mentions
    file_patterns = r"(?:\.claude/|docs/|src/|lib/|test|spec)"
    if re.search(file_patterns, transcript_lower):
        if "file operations" not in reasons:
            reasons.append("file operations")

    return len(reasons) > 0, reasons


def format_enforcement_message(agent_id: str, reasons: list[str]) -> str:
    """Format the mandatory memory save message."""
    reasons_str = ", ".join(reasons) if reasons else "work completed"

    return f"""
🚨 MANDATORY MEMORY SAVE REQUIRED 🚨

Agent '{agent_id}' just completed with: {reasons_str}

You MUST now delegate to pact-memory-agent to save this context.
This is NOT optional. Skipping this = lost context = repeated work.

Action required:
SendMessage the existing pact-memory-agent teammate: "Save memory: [summarize what {agent_id} just did, decisions made, lessons learned]"
If no memory agent is running yet, dispatch one using standard Agent Teams pattern:
TaskCreate + TaskUpdate(owner) + Task(name="memory-agent", team_name="{{team_name}}", subagent_type="pact-memory-agent")

Do this NOW before any other work.
"""


def main():
    """
    Main entry point for the memory enforcement hook.

    Fires for both background Task agents and Agent Teams teammates
    via the SubagentStop event.
    """
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            sys.exit(0)

        agent_id = input_data.get("agent_id", "")
        transcript = input_data.get("transcript", "")
        stop_hook_active = input_data.get("stop_hook_active", False)

        # Skip if already in a stop hook (prevent loops)
        if stop_hook_active:
            sys.exit(0)

        # Only process PACT work agents
        if not is_pact_work_agent(agent_id):
            sys.exit(0)

        # Check if meaningful work was done
        did_work, reasons = did_meaningful_work(transcript)

        if did_work:
            message = format_enforcement_message(agent_id, reasons)
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStop",
                    "additionalContext": message
                }
            }
            print(json.dumps(output))

        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (memory_enforce): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
