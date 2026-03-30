#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_end.py
Summary: SessionEnd hook that captures a last-session snapshot for cross-session
         continuity.
Used by: hooks.json SessionEnd hook

Actions:
1. Write last-session snapshot to ~/.claude/pact-sessions/{slug}/last-session.md
2. Detect open PRs that were not paused (append warning to snapshot)

Purely observational — no destructive operations. Cannot block session termination.

Input: JSON from stdin with session context
Output: None (SessionEnd hooks cannot inject context)
"""

import json
import re
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared.error_output import hook_error_json

from shared.task_utils import get_task_list

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


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
            metadata = task.get("metadata") or {}

            if status == "completed":
                # Extract 1-line summary from handoff decisions if present
                handoff = metadata.get("handoff") or {}
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
                handoff = metadata.get("handoff") or {}
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
    snapshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshot_file = snapshot_dir / "last-session.md"
    snapshot_file.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(str(snapshot_file), 0o600)


def check_unpaused_pr(
    tasks: list[dict] | None,
    project_slug: str,
    sessions_dir: str | None = None,
) -> None:
    """
    Safety-net: detect open PRs that were NOT paused (no memory consolidation).

    If paused-state.json exists, consolidation already happened — no warning needed.
    If no paused-state.json but task metadata indicates an open PR, append a warning
    to the last-session.md snapshot so the next session can flag it.

    This is detection-only. SessionEnd is async fire-and-forget and cannot run agents
    or memory operations.

    Args:
        tasks: List of task dicts from get_task_list(), or None
        project_slug: Project identifier for the session directory
        sessions_dir: Override for sessions base directory (for testing)
    """
    if not project_slug or not tasks:
        return

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    session_dir = Path(sessions_dir) / project_slug

    # If paused-state.json exists, consolidation already happened — no warning
    if (session_dir / "paused-state.json").exists():
        return

    # Scan task metadata for open PR indicators
    pr_number = None
    for task in tasks:
        metadata = task.get("metadata") or {}
        # Check for pr_number in task metadata (set by peer-review workflow)
        if metadata.get("pr_number") is not None:
            pr_number = metadata["pr_number"]
            break
        # Also check handoff metadata for pr_url patterns
        handoff = metadata.get("handoff") or {}
        for value in handoff.values():
            if isinstance(value, str):
                # Extract PR number from GitHub URL like "https://github.com/owner/repo/pull/288"
                match = re.search(r'github\.com/[^/]+/[^/]+/pull/(\d+)', value)
                if match:
                    pr_number = match.group(1)
                    break
        if pr_number:
            break

    if not pr_number:
        return

    # Append warning to existing snapshot
    snapshot_file = session_dir / "last-session.md"
    if not snapshot_file.exists():
        return

    try:
        warning = (
            f"\n## Pause-Mode Warning\n"
            f"Session ended without memory consolidation. "
            f"PR #{pr_number} is open but pause-mode was not run. "
            f"Run /PACT:pause or /PACT:wrap-up in next session to capture session knowledge.\n"
        )
        existing = snapshot_file.read_text(encoding="utf-8")
        snapshot_file.write_text(existing + warning, encoding="utf-8")
        os.chmod(str(snapshot_file), 0o600)
    except (IOError, OSError):
        pass  # Best-effort — never block session end


def cleanup_teachback_markers(
    project_slug: str,
    sessions_dir: str | None = None,
) -> None:
    """
    Remove teachback warning marker files from the session directory.

    Marker files (teachback-warned-{agent}-{task_id}) accumulate during a session
    and are no longer needed once the session ends. Cleanup is best-effort.

    Args:
        project_slug: Project identifier for the session directory
        sessions_dir: Override for sessions base directory (for testing)
    """
    if not project_slug:
        return

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    session_dir = Path(sessions_dir) / project_slug
    if not session_dir.exists():
        return

    try:
        for marker in session_dir.iterdir():
            if marker.name.startswith("teachback-warned-"):
                try:
                    marker.unlink()
                except OSError:
                    pass  # Best-effort cleanup
    except OSError:
        pass  # Can't iterate — skip cleanup


def main():
    try:
        project_slug = get_project_slug()

        # Write last-session snapshot from task states for cross-session continuity
        tasks = get_task_list()
        write_session_snapshot(
            tasks=tasks,
            project_slug=project_slug,
        )

        # Safety-net: warn if open PR detected but pause-mode wasn't run
        check_unpaused_pr(
            tasks=tasks,
            project_slug=project_slug,
        )

        # Clean up teachback warning markers (no longer needed after session)
        cleanup_teachback_markers(project_slug=project_slug)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (session_end): {e}", file=sys.stderr)
        print(hook_error_json("session_end", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
