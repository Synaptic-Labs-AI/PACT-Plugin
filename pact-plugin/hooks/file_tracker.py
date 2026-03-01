#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/file_tracker.py
Summary: PostToolUse hook matching Edit|Write that tracks which agent edits
         which files and warns on inter-agent conflicts.
Used by: hooks.json PostToolUse hook (matcher: Edit|Write)

Non-blocking (PostToolUse cannot block). Warns via additionalContext when
a different agent has already edited the same file.

Input: JSON from stdin with tool_input.file_path
Output: JSON with additionalContext warning if conflict detected
"""

import json
import os
import sys
import time
from pathlib import Path

try:
    import fcntl
    HAS_FLOCK = True
except ImportError:
    HAS_FLOCK = False


def track_edit(
    file_path: str,
    agent_name: str,
    tool_name: str,
    tracking_path: str,
) -> None:
    """Append a file edit record to the tracking file."""
    tracking_file = Path(tracking_path)
    tracking_file.parent.mkdir(parents=True, exist_ok=True)

    new_entry = {
        "file": file_path,
        "agent": agent_name,
        "tool": tool_name,
        "ts": int(time.time()),
    }

    # Use file locking to prevent concurrent write corruption
    if HAS_FLOCK:
        with open(tracking_file, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                entries = json.loads(content) if content.strip() else []
            except (json.JSONDecodeError, IOError):
                entries = []
            entries.append(new_entry)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(entries))
            fcntl.flock(f, fcntl.LOCK_UN)
    else:
        entries = []
        if tracking_file.exists():
            try:
                entries = json.loads(tracking_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                entries = []
        entries.append(new_entry)
        tracking_file.write_text(json.dumps(entries), encoding="utf-8")


def check_conflict(
    file_path: str,
    agent_name: str,
    tracking_path: str,
) -> str | None:
    """Check if another agent has edited this file."""
    if not agent_name:
        return None

    tracking_file = Path(tracking_path)
    if not tracking_file.exists():
        return None

    try:
        entries = json.loads(tracking_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None

    other_agents = set()
    for entry in entries:
        if entry.get("file") == file_path and entry.get("agent") != agent_name:
            other_agents.add(entry["agent"])

    if other_agents:
        others = ", ".join(sorted(other_agents))
        return (
            f"File conflict: {file_path} was also edited by {others}. "
            f"Consider coordinating via SendMessage to avoid merge conflicts."
        )

    return None


def get_environment_delta(
    since_ts: int,
    requesting_agent: str,
    tracking_path: str,
) -> dict[str, str]:
    """Return files modified by OTHER agents since the given timestamp.

    Returns a dict of {file_path: agent_name} for files modified by agents
    other than requesting_agent after since_ts. Used by orchestrator to
    detect environment drift when dispatching or briefing agents.

    Note: Uses inclusive boundary (>=) — entries AT exactly since_ts are included.
    """
    tracking_file = Path(tracking_path)
    if not tracking_file.exists():
        return {}

    try:
        entries = json.loads(tracking_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return {}

    delta: dict[str, str] = {}
    for entry in entries:
        file_path = entry.get("file")
        agent = entry.get("agent")
        if not file_path or not agent:
            continue
        if entry.get("ts", 0) >= since_ts and agent != requesting_agent:
            delta[file_path] = agent

    return delta


def main():
    team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "").lower()
    if not team_name:
        sys.exit(0)

    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    file_path = input_data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        sys.exit(0)

    agent_name = os.environ.get("CLAUDE_CODE_AGENT_NAME", "")
    tool_name = input_data.get("tool_name", "")

    tracking_path = str(
        Path.home() / ".claude" / "teams" / team_name / "file-edits.json"
    )

    # Check for conflict BEFORE recording this edit
    conflict = check_conflict(file_path, agent_name, tracking_path)

    # Record this edit
    track_edit(file_path, agent_name or "orchestrator", tool_name, tracking_path)

    # Warn if conflict
    if conflict:
        output = {
            "hookSpecificOutput": {
                "additionalContext": f"\u26a0\ufe0f {conflict}"
            }
        }
        print(json.dumps(output))

    sys.exit(0)


if __name__ == "__main__":
    main()
