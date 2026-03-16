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

Uses a file-based reentrancy guard (~/.claude/teams/{team_name}/.adhoc_reminded)
to prevent duplicate reminders across process invocations. The guard file is
cleaned up automatically when the team directory is removed (TeamDelete).

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
    - No .adhoc_reminded guard file exists (not already reminded)
    - Transcript is substantive (>= 500 chars)
    - Transcript contains evidence of file modifications (Edit or Write tool use)

    Args:
        team_name: Session team name from env
        transcript: Session transcript text

    Returns:
        True if reminder should fire
    """
    if not team_name:
        return False

    teams_dir = Path.home() / ".claude" / "teams" / team_name

    # If breadcrumb file exists, a workflow handled memory — skip
    if (teams_dir / "completed_handoffs.jsonl").exists():
        return False

    # If already reminded this session, skip
    if (teams_dir / ".adhoc_reminded").exists():
        return False

    # Trivial sessions don't need reminders
    if len(transcript) < 500:
        return False

    # Only remind for work sessions (file modifications), not pure chat
    if "Edit" not in transcript and "Write" not in transcript:
        return False

    return True


def _write_guard_file(team_name: str) -> None:
    """Write the .adhoc_reminded guard file to prevent duplicate reminders."""
    teams_dir = Path.home() / ".claude" / "teams" / team_name
    if not teams_dir.exists():
        return
    guard_path = teams_dir / ".adhoc_reminded"
    try:
        fd = os.open(str(guard_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    except OSError:
        pass  # Already exists or write failure — either way, safe to continue


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            sys.exit(0)

        team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "").lower()
        transcript = input_data.get("transcript", "")

        if should_remind(team_name, transcript):
            _write_guard_file(team_name)
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
