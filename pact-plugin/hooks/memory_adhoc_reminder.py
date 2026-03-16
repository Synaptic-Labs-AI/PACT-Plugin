#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/memory_adhoc_reminder.py
Summary: Stop hook that reminds about memory saves for ad-hoc sessions where
         no formal PACT workflow ran.
Used by: hooks.json Stop hook

Non-blocking (always exit 0). Only fires when no breadcrumb file exists
(workflows create breadcrumbs via handoff_gate.py, so this skips workflow
sessions). Emits a systemMessage reminder if the session had substantive
work outside formal PACT workflows.

Input: JSON from stdin with transcript (Stop hook payload)
Output: JSON with systemMessage if reminder needed, nothing otherwise
"""

import json
import os
import sys
from pathlib import Path


def should_remind(team_name: str, transcript: str) -> bool:
    """
    Determine if the ad-hoc memory reminder should fire.

    Returns True only when:
    - team_name is present (session had a team)
    - No breadcrumb file exists (no formal workflow ran)
    - Transcript is substantive (>= 500 chars)

    Args:
        team_name: Session team name from env
        transcript: Session transcript text

    Returns:
        True if reminder should fire
    """
    if not team_name:
        return False

    # If breadcrumb file exists, a workflow handled memory — skip
    breadcrumb = Path.home() / ".claude" / "teams" / team_name / "completed_handoffs.jsonl"
    if breadcrumb.exists():
        return False

    # Trivial sessions don't need reminders
    if len(transcript) < 500:
        return False

    return True


def main():
    try:
        # Reentrancy guard — prevent loops if systemMessage triggers another Stop
        if os.environ.get("PACT_STOP_HOOK_ACTIVE"):
            sys.exit(0)
        os.environ["PACT_STOP_HOOK_ACTIVE"] = "1"

        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            sys.exit(0)

        team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "").lower()
        transcript = input_data.get("transcript", "")

        if should_remind(team_name, transcript):
            output = {
                "systemMessage": (
                    "This session had work outside formal PACT workflows. "
                    "If significant decisions or discoveries were made, consider "
                    "sending the memory agent a save request via SendMessage."
                )
            }
            print(json.dumps(output))

        sys.exit(0)

    except Exception:
        # Fail silent — never block session stop
        sys.exit(0)


if __name__ == "__main__":
    main()
