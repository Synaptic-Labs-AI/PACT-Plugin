"""
Location: pact-plugin/hooks/shared/session_resume.py
Summary: Session resume and snapshot management for cross-session continuity.
Used by: session_init.py during SessionStart hook to write session info,
         restore previous session snapshots, check for resumable tasks,
         and detect paused work from previous sessions.

Manages:
1. Writing session resume info (team name, resume command) to project CLAUDE.md
2. Restoring last session snapshots for cross-session continuity
3. Checking for in-progress tasks that indicate resumable work
4. Detecting paused state from /PACT:pause for multi-session resume
"""

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def update_session_info(session_id: str, team_name: str) -> str | None:
    """
    Write the Current Session section to the project's CLAUDE.md.

    Inserts (or overwrites) a managed section containing the session resume
    command, team name, and start timestamp. Uses <!-- SESSION_START --> /
    <!-- SESSION_END --> comment markers for reliable replacement across
    sessions.

    Args:
        session_id: Full session UUID (e.g. "93cf3da0-c792-4daa-888e-...")
        team_name: Generated team name (e.g. "PACT-93cf3da0")

    Returns:
        Status message or None if no action taken.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file = Path(project_dir) / "CLAUDE.md"
    if not target_file.exists():
        return None

    SESSION_START = "<!-- SESSION_START -->"
    SESSION_END = "<!-- SESSION_END -->"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    session_block = (
        f"{SESSION_START}\n"
        f"## Current Session\n"
        f"<!-- Auto-managed by session_init hook. Overwritten each session. -->\n"
        f"- Resume: `claude --resume {session_id}`\n"
        f"- Team: `{team_name}`\n"
        f"- Started: {timestamp}\n"
        f"{SESSION_END}"
    )

    try:
        content = target_file.read_text(encoding="utf-8")

        # Case 1: Markers already exist -- replace the block
        if SESSION_START in content and SESSION_END in content:
            new_content = re.sub(
                re.escape(SESSION_START) + r".*?" + re.escape(SESSION_END),
                session_block,
                content,
                count=1,
                flags=re.DOTALL,
            )
            if new_content != content:
                target_file.write_text(new_content, encoding="utf-8")
                os.chmod(str(target_file), 0o600)
                return "Session info updated in project CLAUDE.md"
            return None

        # Case 2: No markers -- insert before "## Retrieved Context" if present
        insert_marker = "## Retrieved Context"
        if insert_marker in content:
            new_content = content.replace(
                insert_marker,
                session_block + "\n\n" + insert_marker,
                1,
            )
        else:
            # Fallback: append at end
            if not content.endswith("\n"):
                content += "\n"
            new_content = content + "\n" + session_block + "\n"

        target_file.write_text(new_content, encoding="utf-8")
        os.chmod(str(target_file), 0o600)
        return "Session info added to project CLAUDE.md"

    except Exception as e:
        return f"Session info failed: {str(e)[:30]}"


def restore_last_session(
    project_slug: str,
    sessions_dir: str | None = None,
) -> str | None:
    """
    Restore the last session snapshot for cross-session continuity.

    Checks if ~/.claude/pact-sessions/{project_slug}/last-session.md exists.
    If found, reads the content, rotates it to last-session.prev.md, and returns
    the content with a header for injection as additionalContext.

    Args:
        project_slug: Project identifier for the session directory
        sessions_dir: Override for sessions base directory (for testing)

    Returns:
        Snapshot content with header if file exists, None otherwise
    """
    if not project_slug:
        return None

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    snapshot_file = Path(sessions_dir) / project_slug / "last-session.md"
    if not snapshot_file.exists():
        return None

    try:
        content = snapshot_file.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return None

    if not content.strip():
        return None

    # Rotate: move last-session.md to last-session.prev.md
    prev_file = snapshot_file.parent / "last-session.prev.md"
    try:
        # Overwrite any existing prev file
        prev_file.write_text(content, encoding="utf-8")
        os.chmod(str(prev_file), 0o600)
        snapshot_file.unlink()
    except (IOError, OSError):
        pass  # Best-effort rotation; don't fail the restore

    return (
        "Previous session summary (read-only reference -- not live tasks):\n"
        + content
    )


def check_resumption_context(tasks: list[dict[str, Any]]) -> str | None:
    """
    Check if there are in_progress Tasks indicating work to resume.

    This helps users understand the current state when starting a new session
    with a persistent task list (CLAUDE_CODE_TASK_LIST_ID set).

    Args:
        tasks: List of all tasks

    Returns:
        Status message describing resumption context, or None if nothing to report
    """
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]
    pending = [t for t in tasks if t.get("status") == "pending"]

    if not in_progress and not pending:
        return None

    # Count by type
    feature_tasks = []
    phase_tasks = []
    agent_tasks = []
    blocker_tasks = []

    for task in in_progress:
        subject = task.get("subject", "")
        metadata = task.get("metadata") or {}

        if metadata.get("type") in ("blocker", "algedonic"):
            blocker_tasks.append(task)
        elif any(subject.startswith(p) for p in ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:")):
            phase_tasks.append(task)
        elif any(subject.lower().startswith(p) for p in ("pact-",)):
            agent_tasks.append(task)
        else:
            # Assume it's a feature task
            feature_tasks.append(task)

    parts = []

    if feature_tasks:
        names = [t.get("subject", "unknown")[:30] for t in feature_tasks[:2]]
        if len(feature_tasks) > 2:
            parts.append(f"Features: {', '.join(names)} (+{len(feature_tasks)-2} more)")
        else:
            parts.append(f"Features: {', '.join(names)}")

    if phase_tasks:
        phases = [t.get("subject", "").split(":")[0] for t in phase_tasks]
        parts.append(f"Phases: {', '.join(phases)}")

    if agent_tasks:
        parts.append(f"Active agents: {len(agent_tasks)}")

    if blocker_tasks:
        parts.append(f"**Blockers: {len(blocker_tasks)}**")

    if parts:
        summary = f"Resumption context: {' | '.join(parts)}"
        if pending:
            summary += f" ({len(pending)} pending)"
        return summary

    return None


# paused-state.json schema (written by /PACT:pause command, read here):
# {
#   "pr_number": int,           -- GitHub PR number
#   "pr_url": str,              -- Full GitHub PR URL
#   "branch": str,              -- Git branch name
#   "worktree_path": str,       -- Absolute path to .worktrees/ directory
#   "paused_at": str,           -- ISO 8601 UTC timestamp
#   "consolidation_completed": bool,  -- Whether memory consolidation ran
#   "team_name": str            -- Session team name (e.g. "pact-d7ab1edb")
# }


def check_paused_state(
    project_slug: str,
    sessions_dir: str | None = None,
) -> str | None:
    """
    Detect paused work from a previous session's /PACT:pause invocation.

    Checks if ~/.claude/pact-sessions/{project_slug}/paused-state.json exists.
    If found, validates the paused state and returns a formatted context string
    for the orchestrator describing the paused PR, branch, and worktree so it
    can offer to resume.

    Validation pipeline (ordered cheapest-first):
    1. TTL check: paused_at older than 14 days → clean up stale file, return info
    2. Active PR validation via `gh pr view`: if PR is MERGED/CLOSED → clean up,
       return info. Fail-open: if gh is unavailable or network fails, skip this
       check and fall through to existing behavior.

    Args:
        project_slug: Project identifier for the session directory
        sessions_dir: Override for sessions base directory (for testing)

    Returns:
        Formatted context string if paused state exists, None otherwise
    """
    if not project_slug:
        return None

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    state_file = Path(sessions_dir) / project_slug / "paused-state.json"
    if not state_file.exists():
        return None

    try:
        content = state_file.read_text(encoding="utf-8")
        state = json.loads(content)
    except (IOError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    # Validate required fields
    pr_number = state.get("pr_number")
    branch = state.get("branch", "unknown")
    worktree_path = state.get("worktree_path", "unknown")

    if pr_number is None:
        return None

    # TTL check: clean up paused state older than 14 days (cheaper than gh call)
    # Fail-open: if paused_at is missing or unparseable, skip TTL check
    paused_at_str = state.get("paused_at")
    if paused_at_str:
        try:
            paused_at = datetime.fromisoformat(paused_at_str.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - paused_at).days
            if age_days > 14:
                try:
                    state_file.unlink()
                except OSError:
                    pass
                paused_date = paused_at.strftime("%Y-%m-%d")
                return (
                    f"Stale paused state from {paused_date} cleaned up "
                    f"(older than 14 days). PR #{pr_number} on {branch}."
                )
        except (ValueError, TypeError, OverflowError):
            pass  # Fail-open: unparseable timestamp — skip TTL check

    # Active PR validation: check if the PR is still open.
    # Only runs when paused-state.json exists (rare), so ~1s latency is acceptable.
    # Fail-open: if gh is unavailable or network fails, fall through to existing behavior.
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            pr_state = result.stdout.strip().upper()
            if pr_state in ("MERGED", "CLOSED"):
                # PR is no longer open — clean up stale paused state
                try:
                    state_file.unlink()
                except OSError:
                    pass
                return (
                    f"Previously paused PR #{pr_number} has been "
                    f"{pr_state.lower()}. Cleaned up paused state."
                )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass  # Fail-open: gh not available or network error — skip validation

    consolidation = state.get("consolidation_completed", False)
    consolidation_note = ""
    if not consolidation:
        consolidation_note = (
            " Memory consolidation did NOT complete — "
            "run /PACT:pause or /PACT:wrap-up to capture session knowledge."
        )

    return (
        f"Paused work detected: PR #{pr_number} ({branch}) — awaiting merge. "
        f"Worktree at {worktree_path}. "
        f"Run /PACT:peer-review to resume review/merge.{consolidation_note}"
    )
