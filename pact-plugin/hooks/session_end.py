#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_end.py
Summary: SessionEnd hook that writes a session_end journal event and performs
         session directory cleanup.
Used by: hooks.json SessionEnd hook

Actions:
1. Write session_end event to the session journal
2. Detect open PRs that were not paused (append warning to journal)
3. Clean up teachback warning markers (session-scoped + legacy slug-level)
4. Clean up stale session directories older than 30 days (preserving paused sessions)

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
from shared.session_journal import (
    append_event,
    make_event,
    read_events,
    read_last_event_from,
)

from shared.task_utils import get_task_list

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def get_project_slug() -> str:
    """Derive project slug from session context (basename of project_dir)."""
    project_dir = get_project_dir()
    if project_dir:
        return Path(project_dir).name
    return ""


def check_unpaused_pr(
    tasks: list[dict] | None,
    project_slug: str,
    sessions_dir: str | None = None,
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
    """
    if not project_slug:
        return

    # Check journal for pause state: if session_paused event exists, no warning needed
    paused_events = read_events("session_paused")
    if paused_events:
        return

    # Check journal for PR creation
    pr_number = None
    review_events = read_events("review_dispatch")
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

    # Write warning to journal
    append_event(
        make_event(
            "session_end",
            warning=(
                f"Session ended without memory consolidation. "
                f"PR #{pr_number} is open but pause-mode was not run. "
                f"Run /PACT:pause or /PACT:wrap-up in next session."
            ),
        ),
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

# Default threshold for stale session directory cleanup.
# 30 days balances disk usage (~50KB × 30 sessions = ~1.5MB) against
# cross-session recovery value. Paused sessions are preserved regardless.
_SESSION_MAX_AGE_DAYS = 30


def _is_paused_session(session_dir: str) -> bool:
    """
    Check if a session directory contains a paused session that hasn't ended.

    A session is considered "paused" when the most recent session_paused
    event is strictly newer than the most recent session_end event (or no
    session_end event exists). Sessions that were paused, then resumed and
    completed are NOT preserved. Sessions that were ended and then paused
    again (a paused→ended→paused sequence) ARE preserved.

    Timestamp comparison uses string ordering. Journal timestamps are
    written in ISO `%Y-%m-%dT%H:%M:%SZ` format (see session_journal.py),
    which sorts lexicographically in chronological order — no datetime
    parsing needed. Missing `ts` fields default to "" (oldest), which
    biases toward preserving the session.

    Fail-open: if the journal is unreadable, returns False (allow cleanup).

    Args:
        session_dir: Absolute path to the session directory.

    Returns:
        True if the session is paused and should be preserved.
    """
    paused = read_last_event_from(session_dir, "session_paused")
    if not paused:
        return False

    # Compare timestamps: paused must be strictly newer than ended.
    ended = read_last_event_from(session_dir, "session_end")
    if ended and ended.get("ts", "") >= paused.get("ts", ""):
        return False

    return True


def cleanup_old_sessions(
    project_slug: str,
    current_session_id: str,
    sessions_dir: str | None = None,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
) -> None:
    """
    Remove session directories older than max_age_days.

    Best-effort cleanup — never raises. Skips the current session's
    directory, any entry that doesn't look like a UUID directory, and
    paused sessions (session_paused without session_end).

    Args:
        project_slug: Project identifier (basename of project_dir)
        current_session_id: Current session's UUID (never deleted)
        sessions_dir: Override for base directory (testing)
        max_age_days: Threshold in days (default: 30)
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
                    # Preserve paused sessions (no session_end after pause)
                    if _is_paused_session(str(entry)):
                        continue
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
        append_event(make_event("session_end"))

        # Safety-net: warn if open PR detected but pause-mode wasn't run
        tasks = get_task_list()
        check_unpaused_pr(
            tasks=tasks,
            project_slug=project_slug,
        )

        # Clean up teachback warning markers (no longer needed after session)
        cleanup_teachback_markers(
            project_slug=project_slug,
            session_dir=session_dir,
        )

        # Clean up stale session directories (older than 30 days, preserving paused)
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
