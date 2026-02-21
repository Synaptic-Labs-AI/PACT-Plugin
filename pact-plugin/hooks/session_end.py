#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_end.py
Summary: SessionEnd hook that captures a last-session snapshot for cross-session
         continuity.
Used by: hooks.json SessionEnd hook

Actions:
1. Write last-session snapshot to ~/.claude/pact-sessions/{slug}/last-session.md
2. Clean up the current session's team directory (and its task dir)

Cannot block session termination — fire-and-forget.

Input: JSON from stdin with session context
Output: None (SessionEnd hooks cannot inject context)
"""

import json
import shutil
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared.task_utils import get_task_list


def get_project_slug() -> str:
    """Derive project slug from environment."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project_dir:
        return Path(project_dir).name
    return ""


def write_session_snapshot(
    tasks: list[dict] | None,
    project_slug: str,
    sessions_dir: str | None = None,
) -> None:
    """
    Write a structured last-session snapshot from task states.

    Reads completed and incomplete tasks to produce a markdown summary at
    ~/.claude/pact-sessions/{project_slug}/last-session.md. This file is
    read by session_init.py on the next session start to provide continuity.

    Args:
        tasks: List of task dicts from get_task_list(), or None
        project_slug: Project identifier for the session directory
        sessions_dir: Override for sessions base directory (for testing)
    """
    if not project_slug:
        return

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Last Session: {now}", ""]

    completed_lines = []
    incomplete_lines = []
    decision_lines = []
    unresolved_lines = []

    if tasks:
        for task in tasks:
            task_id = task.get("id", "?")
            subject = task.get("subject", "unknown")
            status = task.get("status", "unknown")
            metadata = task.get("metadata", {})

            if status == "completed":
                # Extract 1-line summary from handoff decisions if present
                handoff = metadata.get("handoff", {})
                decisions = handoff.get("decisions", [])
                if decisions and isinstance(decisions, list):
                    summary = decisions[0] if isinstance(decisions[0], str) else str(decisions[0])
                    # Truncate long summaries
                    if len(summary) > 80:
                        summary = summary[:77] + "..."
                    completed_lines.append(f"- #{task_id} {subject} -> {summary}")
                else:
                    completed_lines.append(f"- #{task_id} {subject}")

            elif status in ("in_progress", "pending"):
                incomplete_lines.append(f"- #{task_id} {subject} -- {status}")

            # Collect decisions from all completed tasks with handoff metadata
            if status == "completed":
                handoff = metadata.get("handoff", {})
                for decision in handoff.get("decisions", []):
                    if isinstance(decision, str) and decision not in decision_lines:
                        decision_lines.append(decision)

            # Collect unresolved blockers/algedonic signals
            if metadata.get("type") in ("blocker", "algedonic") and status != "completed":
                unresolved_lines.append(f"- #{task_id} {subject}")

    # Build sections
    lines.append("## Completed Tasks")
    if completed_lines:
        lines.extend(completed_lines)
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Incomplete Tasks")
    if incomplete_lines:
        lines.extend(incomplete_lines)
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Key Decisions")
    if decision_lines:
        for d in decision_lines[:10]:  # Cap at 10 decisions
            lines.append(f"- {d}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Unresolved")
    if unresolved_lines:
        lines.extend(unresolved_lines)
    else:
        lines.append("- (none)")
    lines.append("")

    # Write snapshot file
    snapshot_dir = Path(sessions_dir) / project_slug
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_file = snapshot_dir / "last-session.md"
    snapshot_file.write_text("\n".join(lines), encoding="utf-8")


def cleanup_stale_teams(
    team_name: str | None = None,
    teams_dir: str | None = None,
    tasks_dir: str | None = None,
) -> list[str]:
    """
    Remove the current session's team directory on session end.

    Scoped to only the current session's team (identified by CLAUDE_CODE_TEAM_NAME
    env var or explicit team_name parameter) to avoid accidentally removing another
    concurrent session's team. Also removes the corresponding task directory.

    This is best-effort: errors are silently ignored to never block session end.

    Args:
        team_name: Team name to clean up (defaults to CLAUDE_CODE_TEAM_NAME env var)
        teams_dir: Override for teams base directory (for testing)
        tasks_dir: Override for tasks base directory (for testing).
                   Defaults to sibling "tasks" directory of teams_dir.

    Returns:
        List of team names that were cleaned up.
    """
    if team_name is None:
        team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "").lower()
    if not team_name:
        return []

    if teams_dir is None:
        teams_dir = str(Path.home() / ".claude" / "teams")

    teams_path = Path(teams_dir)
    team_dir = teams_path / team_name
    if not team_dir.is_dir():
        return []

    cleaned = []
    try:
        shutil.rmtree(str(team_dir), ignore_errors=True)
        cleaned.append(team_name)
    except OSError:
        pass

    # Also clean corresponding task directory
    if tasks_dir is None:
        tasks_base = teams_path.parent / "tasks"
    else:
        tasks_base = Path(tasks_dir)
    if tasks_base.is_dir():
        task_dir = tasks_base / team_name
        if task_dir.is_dir():
            shutil.rmtree(str(task_dir), ignore_errors=True)

    return cleaned


def main():
    try:
        project_slug = get_project_slug()

        # Snapshot MUST run before cleanup: snapshot reads from the task list
        # (keyed by task_list_id in ~/.claude/tasks/), while cleanup removes
        # the team directory (keyed by team_name in ~/.claude/teams/) and its
        # corresponding task directory. If cleanup ran first, get_task_list()
        # would return empty results and the snapshot would be blank.
        tasks = get_task_list()
        write_session_snapshot(
            tasks=tasks,
            project_slug=project_slug,
        )

        # Best-effort cleanup of current session's team and task directories
        cleanup_stale_teams()

        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (session_end): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
