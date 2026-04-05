#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_end.py
Summary: SessionEnd hook that captures a last-session snapshot for cross-session
         continuity and performs session directory cleanup.
Used by: hooks.json SessionEnd hook

Actions:
1. Write last-session snapshot to ~/.claude/pact-sessions/{slug}/last-session.md
2. Detect open PRs that were not paused (append warning to snapshot)
3. Clean up teachback warning markers (session-scoped + legacy slug-level)
4. Clean up stale session directories older than 7 days

Purely observational — no destructive operations on project files. Session
directory cleanup is best-effort and never blocks session termination.

Input: JSON from stdin with session context
Output: None (SessionEnd hooks cannot inject context)
"""

import json
import re
import shutil
import sys
import time
from pathlib import Path

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared.error_output import hook_error_json
import shared.pact_context as pact_context
from shared.pact_context import get_project_dir, get_session_dir, get_session_id, get_team_name
from shared.session_journal import append_event, make_event, read_events

from shared.task_utils import get_task_list

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def get_project_slug() -> str:
    """Derive project slug from session context (basename of project_dir)."""
    project_dir = get_project_dir()
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

    Deprecated: The session journal (session-journal.jsonl) is now the primary
    source for cross-session continuity. The session_end event is written to
    the journal in main(). session_init.py reads the previous session's journal
    to construct resume context.

    This function is preserved as a no-op for backward compatibility with any
    external callers. No slug-level last-session.md is written.

    Args:
        tasks: List of task dicts from get_task_list(), or None
        project_slug: Project identifier for the session directory
        sessions_dir: Override for sessions base directory (for testing)
    """
    # No-op: the session journal replaces slug-level last-session.md.
    # The session_end event (written in main()) and agent_handoff events
    # (written by handoff_gate.py) provide equivalent data for resume.
    pass


def check_unpaused_pr(
    tasks: list[dict] | None,
    project_slug: str,
    sessions_dir: str | None = None,
    team_name: str | None = None,
) -> None:
    """
    Safety-net: detect open PRs that were NOT paused (no memory consolidation).

    Checks the session journal for review_dispatch events (PR was created) and
    the absence of session_paused events (pause was not run). If this condition
    is met, writes a warning session_end event to the journal so the next session
    can flag it.

    Also checks task metadata as fallback for PRs not tracked through the normal
    review workflow (preserves the existing safety-net regex detection).

    This is detection-only. SessionEnd is async fire-and-forget and cannot run
    agents or memory operations.

    Args:
        tasks: List of task dicts from get_task_list(), or None
        project_slug: Project identifier for the session directory
        sessions_dir: Override for sessions base directory (for testing)
        team_name: Team name for journal access (defaults to get_team_name())
    """
    if not project_slug:
        return

    resolved_team = team_name or get_team_name()

    # Check journal for pause state: if session_paused event exists, no warning needed
    if resolved_team:
        paused_events = read_events(resolved_team, "session_paused")
        if paused_events:
            return

    # Check journal for PR creation
    pr_number = None
    if resolved_team:
        review_events = read_events(resolved_team, "review_dispatch")
        if review_events:
            # Use the most recent review_dispatch event's PR number
            pr_number = review_events[-1].get("pr_number")

    # Fallback: scan task metadata for PR indicators (safety net for PRs
    # not tracked through the review workflow journal events)
    if not pr_number and tasks:
        for task in tasks:
            metadata = task.get("metadata") or {}
            if metadata.get("pr_number") is not None:
                pr_number = metadata["pr_number"]
                break
            handoff = metadata.get("handoff") or {}
            for value in handoff.values():
                if isinstance(value, str):
                    match = re.search(r'github\.com/[^/]+/[^/]+/pull/(\d+)', value)
                    if match:
                        pr_number = match.group(1)
                        break
            if pr_number:
                break

    if not pr_number:
        return

    # Write warning to journal (replaces appending to last-session.md)
    if resolved_team:
        append_event(
            make_event(
                "session_end",
                warning=(
                    f"Session ended without memory consolidation. "
                    f"PR #{pr_number} is open but pause-mode was not run. "
                    f"Run /PACT:pause or /PACT:wrap-up in next session."
                ),
            ),
            resolved_team,
        )


def cleanup_teachback_markers(
    project_slug: str,
    session_dir: str | None = None,
    sessions_dir: str | None = None,
) -> None:
    """
    Remove teachback warning marker files from the session directory.

    Marker files (teachback-warned-{agent}-{task_id}) accumulate during a session
    and are no longer needed once the session ends. Cleanup is best-effort.

    Cleans two locations:
    1. Session-scoped dir: {slug}/{session_id}/teachback-warned-* (current format)
    2. Slug-level dir: {slug}/teachback-warned-* (legacy migration sweep)

    Args:
        project_slug: Project identifier for the session directory
        session_dir: The session-scoped directory path (from get_session_dir()).
            When provided, markers are cleaned from this directory.
        sessions_dir: Override for sessions base directory (for testing)
    """
    if not project_slug:
        return

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    # Clean session-scoped markers (current format)
    if session_dir:
        _sweep_teachback_markers(Path(session_dir))

    # Migration sweep: clean orphaned slug-level markers (pre-#345 format)
    slug_dir = Path(sessions_dir) / project_slug
    _sweep_teachback_markers(slug_dir)


def _sweep_teachback_markers(directory: Path) -> None:
    """Remove all teachback-warned-* files in a directory. Best-effort."""
    if not directory.exists():
        return
    try:
        for marker in directory.iterdir():
            if marker.name.startswith("teachback-warned-"):
                try:
                    marker.unlink()
                except OSError:
                    pass
    except OSError:
        pass


# Regex for validating UUID-format directory names (session IDs)
_UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)

# Default threshold for stale session directory cleanup
_SESSION_MAX_AGE_DAYS = 7


def cleanup_old_sessions(
    project_slug: str,
    current_session_id: str,
    sessions_dir: str | None = None,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
) -> None:
    """
    Remove session directories older than max_age_days.

    Best-effort cleanup — never raises. Skips the current session's
    directory and any entry that doesn't look like a UUID directory.

    Args:
        project_slug: Project identifier (basename of project_dir)
        current_session_id: Current session's UUID (never deleted)
        sessions_dir: Override for base directory (testing)
        max_age_days: Threshold in days (default: 7)
    """
    if not project_slug or not current_session_id:
        return

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    slug_dir = Path(sessions_dir) / project_slug
    if not slug_dir.exists():
        return

    try:
        for entry in slug_dir.iterdir():
            if not entry.is_dir():
                continue
            if not _UUID_PATTERN.match(entry.name):
                continue
            if entry.name == current_session_id:
                continue
            try:
                age_days = (time.time() - entry.stat().st_mtime) / 86400
                if age_days > max_age_days:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        pact_context.init(input_data)
        project_slug = get_project_slug()
        session_dir = get_session_dir()
        current_session_id = get_session_id()

        # Write session_end event to journal (best-effort, before other work)
        team_name = get_team_name()
        if team_name:
            append_event(make_event("session_end"), team_name)

        # Safety-net: warn if open PR detected but pause-mode wasn't run
        tasks = get_task_list()
        check_unpaused_pr(
            tasks=tasks,
            project_slug=project_slug,
            team_name=team_name,
        )

        # Clean up teachback warning markers (no longer needed after session)
        cleanup_teachback_markers(
            project_slug=project_slug,
            session_dir=session_dir,
        )

        # Clean up stale session directories (older than 7 days)
        cleanup_old_sessions(
            project_slug=project_slug,
            current_session_id=current_session_id,
        )

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (session_end): {e}", file=sys.stderr)
        print(hook_error_json("session_end", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
