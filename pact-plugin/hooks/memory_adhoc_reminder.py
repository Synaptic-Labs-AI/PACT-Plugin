#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/memory_adhoc_reminder.py
Summary: Stop hook that emits memory-related reminders at session end.
Used by: hooks.json Stop hook

Non-blocking (always exit 0). Two reminder paths:
1. "unprocessed_handoffs" — breadcrumb file exists at session end, meaning
   workflow HANDOFFs were captured but never processed by the memory agent.
2. "adhoc_save" — no breadcrumb file but session had substantive ad-hoc work
   outside formal PACT workflows.

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

MIN_TRANSCRIPT_LENGTH = 500

REMINDER_UNPROCESSED_HANDOFFS = "unprocessed_handoffs"
REMINDER_ADHOC_SAVE = "adhoc_save"


def get_reminder_type(team_name: str, transcript: str) -> str | None:
    """
    Determine which reminder to emit, if any.

    Returns:
        REMINDER_UNPROCESSED_HANDOFFS — breadcrumbs exist (workflow ran but memory not processed)
        REMINDER_ADHOC_SAVE — no breadcrumbs but substantive ad-hoc work detected
        None — no reminder needed

    Args:
        team_name: Session team name from env
        transcript: Session transcript text
    """
    if not team_name:
        return None

    teams_dir = Path.home() / ".claude" / "teams" / team_name

    # If already reminded this session, skip
    if (teams_dir / ".adhoc_reminded").exists():
        return None

    # Path 1: Breadcrumbs exist → unprocessed HANDOFFs
    if (teams_dir / "completed_handoffs.jsonl").exists():
        return REMINDER_UNPROCESSED_HANDOFFS

    # Path 2: No breadcrumbs but substantive ad-hoc work
    if len(transcript) < MIN_TRANSCRIPT_LENGTH:
        return None

    # Only remind for work sessions (file modifications), not pure chat.
    # Match quoted tool names ('"Edit"', '"Write"') to avoid false-positives
    # on words like "Editorial" or "Rewrite" in discussion text.
    if '"Edit"' not in transcript and '"Write"' not in transcript:
        return None

    return REMINDER_ADHOC_SAVE


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


_MESSAGES = {
    REMINDER_UNPROCESSED_HANDOFFS: (
        "Unprocessed HANDOFFs detected from this session's workflow. "
        "Consider running /PACT:wrap-up or ensuring the memory agent "
        "processes them in the next session."
    ),
    REMINDER_ADHOC_SAVE: (
        "This session had work outside formal PACT workflows. "
        "If significant decisions or discoveries were made, consider "
        "sending the memory agent a save request via SendMessage."
    ),
}


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            sys.exit(0)

        team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "").lower()
        transcript = input_data.get("transcript", "")

        reminder_type = get_reminder_type(team_name, transcript)
        if reminder_type and reminder_type in _MESSAGES:
            _write_guard_file(team_name)
            print(json.dumps({"systemMessage": _MESSAGES[reminder_type]}))

        sys.exit(0)

    except Exception:
        # Fail silent — never block session stop
        sys.exit(0)


if __name__ == "__main__":
    main()
