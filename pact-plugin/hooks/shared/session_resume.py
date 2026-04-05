"""
Location: pact-plugin/hooks/shared/session_resume.py
Summary: Session resume and snapshot management for cross-session continuity.
Used by: session_init.py during SessionStart hook to write session info,
         restore previous session snapshots, check for resumable tasks,
         and detect paused work from previous sessions.

Manages:
1. Writing session resume info (team name, resume command) to project CLAUDE.md
2. Restoring last session context from session journal
3. Checking for in-progress tasks that indicate resumable work
4. Detecting paused state from session journal
"""

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.session_journal import read_events, read_last_event

# Maximum characters for decision summaries in journal resume output
_DECISION_TRUNCATION_LIMIT = 80


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
    prev_team_name: str | None = None,
) -> str | None:
    """
    Restore the last session context for cross-session continuity.

    Reads the previous session's journal (located by prev_team_name) and
    constructs a resume summary from agent_handoff, phase_transition, and
    checkpoint events.

    Args:
        prev_team_name: Previous session's team name (from CLAUDE.md).
            When provided, reads that team's journal for resume context.

    Returns:
        Resume context string if available, None otherwise
    """
    if not prev_team_name:
        return None

    return _build_journal_resume(prev_team_name)


def _build_journal_resume(team_name: str) -> str | None:
    """
    Build resume context from a previous session's journal events.

    Reads agent_handoff events (completed work), phase_transition events
    (progress), and checkpoint events (state snapshot) to produce a
    concise resume summary.

    Args:
        team_name: The previous session's team name

    Returns:
        Formatted resume string, or None if journal is empty/missing
    """
    all_events = read_events(team_name)
    if not all_events:
        return None

    lines = ["Previous session summary (from journal -- read-only reference):", ""]

    # Extract completed handoffs
    handoffs = [e for e in all_events if e.get("type") == "agent_handoff"]
    if handoffs:
        lines.append("## Completed Work")
        for h in handoffs:
            agent = h.get("agent", "unknown")
            subject = h.get("task_subject", "")
            handoff_data = h.get("handoff", {})
            decisions = handoff_data.get("decisions", [])
            summary = decisions[0] if decisions else ""
            if len(summary) > _DECISION_TRUNCATION_LIMIT:
                summary = summary[:_DECISION_TRUNCATION_LIMIT - 3] + "..."
            if summary:
                lines.append(f"- {agent}: {subject} -> {summary}")
            else:
                lines.append(f"- {agent}: {subject}")
        lines.append("")

    # Extract phase progress
    phases = [e for e in all_events if e.get("type") == "phase_transition"]
    if phases:
        completed = [p["phase"] for p in phases if p.get("status") == "completed"]
        in_progress = [p["phase"] for p in phases if p.get("status") == "started"]
        if completed:
            lines.append(f"Completed phases: {', '.join(completed)}")
        if in_progress:
            lines.append(f"Last active phase: {in_progress[-1]}")
        lines.append("")

    # Check for warnings in session_end events
    end_events = [e for e in all_events if e.get("type") == "session_end"]
    for end_event in end_events:
        warning = end_event.get("warning")
        if warning:
            lines.append(f"**Warning**: {warning}")
            lines.append("")

    # Minimal output check
    if len(lines) <= 2:
        return None

    return "\n".join(lines)


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


def check_paused_state(
    prev_team_name: str | None = None,
) -> str | None:
    """
    Detect paused work from a previous session's /PACT:pause invocation.

    Reads the previous session's journal for session_paused events.
    The event contains pr_number, pr_url, branch, worktree_path,
    consolidation_completed, and team_name.

    Validation pipeline (ordered cheapest-first):
    1. TTL check: timestamp older than 14 days → return informational message
    2. Active PR validation via `gh pr view`: if MERGED/CLOSED → return info

    The journal is immutable — no file deletion is performed.

    Args:
        prev_team_name: Previous session's team name (from CLAUDE.md).
            When provided, reads that team's journal for pause state.

    Returns:
        Formatted context string if paused state exists, None otherwise
    """
    if not prev_team_name:
        return None

    return _check_journal_paused_state(prev_team_name)


def _check_journal_paused_state(team_name: str) -> str | None:
    """Check for paused state in the previous session's journal."""
    event = read_last_event(team_name, "session_paused")
    if not event:
        return None

    pr_number = event.get("pr_number")
    branch = event.get("branch", "unknown")
    worktree_path = event.get("worktree_path", "unknown")

    if pr_number is None:
        return None

    # TTL check: ts older than 14 days
    ts_str = event.get("ts", "")
    if ts_str:
        try:
            paused_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - paused_at).days
            if age_days > 14:
                paused_date = paused_at.strftime("%Y-%m-%d")
                return (
                    f"Stale paused state from {paused_date} "
                    f"(older than 14 days). PR #{pr_number} on {branch}."
                )
        except (ValueError, TypeError, OverflowError):
            pass

    # Active PR validation
    pr_state = _check_pr_state(pr_number)
    if pr_state in ("MERGED", "CLOSED"):
        return (
            f"Previously paused PR #{pr_number} has been "
            f"{pr_state.lower()}."
        )

    consolidation = event.get("consolidation_completed", False)
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


def _check_pr_state(pr_number: int | str) -> str:
    """
    Check PR state via gh CLI. Returns uppercase state string or empty on error.

    Fail-open: returns "" if gh is unavailable, network fails, or timeout.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().upper()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""
