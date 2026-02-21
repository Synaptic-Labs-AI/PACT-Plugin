#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/validate_handoff.py
Summary: SubagentStop hook that validates PACT agent/teammate handoff format.
Used by: Claude Code hooks.json SubagentStop hook (fires for both background
         Task agents and Agent Teams teammates)

Validates that PACT agents complete with proper handoff information
(produced, decisions, next steps) in their transcript text.

Note: Task protocol compliance (status, metadata) is NOT validated here.
Task state may still be in flux at SubagentStop time (agents self-manage
status under Agent Teams, and the lead may process output after this hook
fires), so Task state cannot be reliably checked here.

Input: JSON from stdin with `last_assistant_message` (preferred, SDK v2.1.47+),
       `transcript` (fallback), and `agent_id`
Output: JSON with `systemMessage` if handoff format is incomplete
"""

import json
import sys
import re


# Required handoff elements with their patterns and descriptions
HANDOFF_ELEMENTS = {
    "what_produced": {
        "patterns": [
            r"(?:produced|created|generated|output|implemented|wrote|built|delivered)",
            r"(?:file|document|component|module|function|class|api|endpoint|schema)",
            r"(?:completed|finished|done with)",
        ],
        "description": "what was produced",
    },
    "key_decisions": {
        "patterns": [
            r"(?:decision|chose|selected|opted|rationale|reason|because)",
            r"(?:trade-?off|alternative|approach|strategy|pattern)",
            r"(?:decided to|went with|picked)",
        ],
        "description": "key decisions",
    },
    "next_steps": {
        "patterns": [
            r"(?:next|needs|requires|depends|should|must|recommend)",
            r"(?:follow-?up|remaining|todo|to-?do|action item)",
            r"(?:test engineer|tester|reviewer|next agent|next phase)",
        ],
        "description": "next steps/needs",
    },
}


def validate_handoff(transcript: str) -> tuple:
    """
    Check if transcript contains proper handoff elements.

    Args:
        transcript: The agent's complete output/transcript

    Returns:
        Tuple of (is_valid, missing_elements)
    """
    missing = []

    # First, check for explicit handoff section (indicates structured handoff)
    has_handoff_section = bool(re.search(
        r"(?:##?\s*)?(?:handoff|hand-off|hand off|summary|output|deliverables)[\s:]*\n",
        transcript,
        re.IGNORECASE
    ))

    # If there's an explicit handoff section, be more lenient
    if has_handoff_section:
        return True, []

    # Otherwise, check for implicit handoff elements
    transcript_lower = transcript.lower()

    for element_key, element_info in HANDOFF_ELEMENTS.items():
        found = False
        for pattern in element_info["patterns"]:
            if re.search(pattern, transcript_lower):
                found = True
                break

        if not found:
            missing.append(element_info["description"])

    # Consider valid if at least 2 out of 3 elements are present
    # (some agents may not have explicit decisions if straightforward)
    is_valid = len(missing) <= 1

    return is_valid, missing


def is_pact_agent(agent_id: str) -> bool:
    """
    Check if the agent is a PACT framework agent.

    Args:
        agent_id: The identifier of the agent

    Returns:
        True if this is a PACT agent that should be validated
    """
    if not agent_id:
        return False

    pact_prefixes = ["pact-", "PACT-", "pact_", "PACT_"]
    return any(agent_id.startswith(prefix) for prefix in pact_prefixes)


def main():
    """
    Main entry point for the SubagentStop hook.

    Reads agent/teammate transcript from stdin and validates handoff format
    (prose) for PACT agents. Fires for both background Task agents and
    Agent Teams teammates. Outputs warning messages if validation fails.
    """
    try:
        # Read input from stdin
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            # No input or invalid JSON - can't validate
            sys.exit(0)

        # Prefer last_assistant_message (SDK v2.1.47+), fall back to transcript
        transcript = input_data.get("last_assistant_message", "") or input_data.get("transcript", "")
        agent_id = input_data.get("agent_id", "")

        # Only validate PACT agents
        if not is_pact_agent(agent_id):
            sys.exit(0)

        warnings = []

        # Skip transcript validation if very short (likely an error case)
        if len(transcript) >= 100:
            is_valid, missing = validate_handoff(transcript)

            if not is_valid and missing:
                warnings.append(
                    f"PACT Handoff Warning: Agent '{agent_id}' completed without "
                    f"proper handoff. Missing: {', '.join(missing)}. "
                    "Consider including: what was produced, key decisions, and next steps."
                )

        # Output warnings if any
        if warnings:
            output = {
                "systemMessage": " | ".join(warnings)
            }
            print(json.dumps(output))

        sys.exit(0)

    except Exception as e:
        # Don't block on errors - just warn
        print(f"Hook warning (validate_handoff): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
