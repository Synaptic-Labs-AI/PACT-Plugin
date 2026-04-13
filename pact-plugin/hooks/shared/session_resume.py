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
import sys
from datetime import datetime, timezone
from typing import Any

from shared.claude_md_manager import (
    ensure_dot_claude_parent,
    file_lock,
    resolve_project_claude_md_path,
)
from shared.session_journal import read_events_from, read_last_event_from

# Maximum characters for decision summaries in journal resume output
_DECISION_TRUNCATION_LIMIT = 80

# Maximum characters for phase strings rendered into journal resume output.
# Phases are nominally short uppercase identifiers (CODE, TEST, etc.) but the
# consumer must defend against historical or hand-crafted events that stashed
# a long free-form string or a non-string type in the `phase` field.
_PHASE_TRUNCATION_LIMIT = 80


def update_session_info(
    session_id: str,
    team_name: str,
    session_dir: str | None = None,
    plugin_root: str | None = None,
) -> str | None:
    """
    Write the Current Session section to the project's CLAUDE.md.

    Inserts (or overwrites) a managed section containing the session resume
    command, team name, session directory, plugin root, and start timestamp.
    Uses <!-- SESSION_START --> / <!-- SESSION_END --> comment markers for
    reliable replacement across sessions.

    Args:
        session_id: Full session UUID (e.g. "93cf3da0-c792-4daa-888e-...")
        team_name: Generated team name (e.g. "PACT-93cf3da0")
        session_dir: Absolute path to the session directory (optional).
            When provided, written as "- Session dir:" line for next-session
            journal access.
        plugin_root: Absolute path to the installed plugin directory (optional).
            When provided, written as "- Plugin root:" line so the orchestrator
            can locate hook scripts without symlink traversal.

    Returns:
        Status message or None if no action taken.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    # Honor both supported project CLAUDE.md locations.
    # Existing files take precedence (.claude/CLAUDE.md > legacy ./CLAUDE.md);
    # if neither exists, the resolver returns the new default
    # ($project_dir/.claude/CLAUDE.md) so we create at the preferred path.
    target_file, _source = resolve_project_claude_md_path(project_dir)

    SESSION_START = "<!-- SESSION_START -->"
    SESSION_END = "<!-- SESSION_END -->"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Build session dir line. MUST be written as an absolute path — command
    # files read this value via bash single-quoted expansion which does NOT
    # perform tilde expansion, and `session_journal._validate_cli_session_dir`
    # rejects non-absolute paths via `Path(session_dir).is_absolute()`. A
    # tilde-abbreviated path would break every journal write from command
    # files (R4 regression). Mirrors `plugin_root` below.
    session_dir_line = ""
    if session_dir:
        session_dir_line = f"- Session dir: `{session_dir}`\n"

    # Build plugin root line (no abbreviation — needs to be usable as-is in Bash)
    plugin_root_line = ""
    if plugin_root:
        plugin_root_line = f"- Plugin root: `{plugin_root}`\n"

    session_block = (
        f"{SESSION_START}\n"
        f"## Current Session\n"
        f"<!-- Auto-managed by session_init hook. Overwritten each session. -->\n"
        f"- Resume: `claude --resume {session_id}`\n"
        f"- Team: `{team_name}`\n"
        f"{session_dir_line}"
        f"{plugin_root_line}"
        f"- Started: {timestamp}\n"
        f"{SESSION_END}"
    )

    # Create the `.claude/` parent directory (with 0o700) BEFORE acquiring
    # the file lock. `file_lock` internally creates the target's parent
    # directory as a side effect of opening the sidecar lock file, but it
    # uses `mkdir(parents=True, exist_ok=True)` with no explicit mode —
    # which defaults to 0o755 under umask. Running `ensure_dot_claude_parent`
    # first guarantees the parent gets the intended 0o700 mode. The call is
    # idempotent, so concurrent callers are safe: whichever thread creates
    # the directory wins with 0o700, others see `parent.exists()` and no-op.
    ensure_dot_claude_parent(target_file)

    # Concurrency guard (#366 F1): serialize read-mutate-write so two
    # concurrent session_init hooks on the same project CLAUDE.md cannot
    # interleave 5b (update_session_info) with 5c (update_pact_routing) in
    # another session and clobber each other's managed blocks. Fail-open
    # on timeout — next session start will retry.
    try:
        with file_lock(target_file):
            # Symlink guard INSIDE the lock (#366 R5 M1, TOCTOU defense):
            # same defensive check as remove_stale_kernel_block and
            # update_pact_routing. is_symlink uses lstat (does not follow
            # the link). Inside the lock so an attacker cannot swap the
            # target between an outside-lock check and the write.
            if target_file.is_symlink():
                return "Session info skipped: path precondition not met."

            try:
                # Case 0: File doesn't exist -- create it with a minimal template
                # so the orchestrator has a stable Current Session block to read on
                # the very first session in a project. The .claude/ parent was
                # created above (before the lock) with mode 0o700.
                if not target_file.exists():
                    new_content = (
                        "# PACT Framework for Agentic Orchestration\n"
                        "\n"
                        "<!-- PACT auto-creates this file on first session. "
                        "Safe to add your own content outside the PACT_MANAGED block; "
                        "the SESSION_START/SESSION_END markers are auto-updated each session. -->\n"
                        "\n"
                        f"{session_block}\n"
                    )
                    target_file.write_text(new_content, encoding="utf-8")
                    os.chmod(str(target_file), 0o600)
                    return "Session info created in new project CLAUDE.md"

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
                return f"Session info failed: {str(e)[:50]}"
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Session info update skipped; will retry on next session start."
        )


def restore_last_session(
    prev_session_dir: str | None = None,
) -> str | None:
    """
    Restore the last session context for cross-session continuity.

    Reads the previous session's journal (located by prev_session_dir) and
    constructs a resume summary from agent_handoff, phase_transition, and
    checkpoint events.

    Args:
        prev_session_dir: Previous session's directory path (from CLAUDE.md).
            When provided, reads that session's journal for resume context.

    Returns:
        Resume context string if available, None otherwise
    """
    if not prev_session_dir:
        return None

    return _build_journal_resume(prev_session_dir)


def _coerce_decision_summary(decisions: Any) -> str:
    """
    Extract a short summary string from a handoff's `decisions` field.

    The `decisions` field is nominally a list of strings, but historical
    journal data and future schema drift can produce:
    - non-list values (dict, None, scalar)
    - empty lists
    - lists whose first element is not a string (dict, list, None)

    This helper returns an empty string for any of those shapes rather
    than raising IndexError/TypeError. When the first element is a
    non-string, it is stringified via str() so callers still get a
    useful, bounded summary.

    Truncation to _DECISION_TRUNCATION_LIMIT happens here so every caller
    gets consistent behavior.
    """
    if not isinstance(decisions, list) or not decisions:
        return ""
    first = decisions[0]
    if isinstance(first, str):
        summary = first
    elif first is None:
        return ""
    else:
        # Best-effort stringify for dict/list/number/other — bounded by
        # truncation below so even a giant dict becomes a readable stub.
        summary = str(first)
    if len(summary) > _DECISION_TRUNCATION_LIMIT:
        summary = summary[:_DECISION_TRUNCATION_LIMIT - 3] + "..."
    return summary


def _coerce_phase_string(phase: Any) -> str:
    """
    Stringify and bound a `phase` value drawn from a phase_transition event.

    Parallel to `_coerce_decision_summary`: the per-type validator rejects
    new writes that lack `phase`, but the defensive consumer backstop must
    still handle:
    - non-string phase values from older schema versions or hand-crafted
      journal files (dict, list, None, number)
    - pathologically long strings from a misconfigured writer that stashed
      a whole error message in `phase`

    None is handled explicitly (returns ``""``), matching
    ``_coerce_decision_summary``'s convention. Other non-string values
    are stringified via ``str()`` and truncated at
    ``_PHASE_TRUNCATION_LIMIT`` so a bad event can produce at worst a
    readable 80-character stub in the resume output instead of flooding
    the SessionStart hook context or raising a TypeError downstream.
    """
    if phase is None:
        return ""
    rendered = str(phase)
    if len(rendered) > _PHASE_TRUNCATION_LIMIT:
        rendered = rendered[:_PHASE_TRUNCATION_LIMIT - 3] + "..."
    return rendered


def _build_journal_resume(session_dir: str) -> str | None:
    """
    Build resume context from a previous session's journal events.

    Reads agent_handoff events (completed work), phase_transition events
    (progress), and checkpoint events (state snapshot) to produce a
    concise resume summary.

    Defensive consumer: this function MUST NOT raise on malformed events.
    Any KeyError/IndexError/TypeError propagates through restore_last_session
    into session_init.main()'s outer except, which replaces the entire
    constructed hook output dict (team-create instructions, working memory,
    retrieved context) with an error JSON — losing critical SessionStart
    context for one bad journal line. Per-type schema validation at write
    time (see session_journal._validate_event_schema) is the primary
    defense; this consumer is the backstop for events that slipped past
    an older validator, prior schema versions, or hand-crafted files.

    Failure mode: on any unexpected exception, log to stderr and return
    None so the caller continues with an empty resume.

    Args:
        session_dir: The previous session's directory path

    Returns:
        Formatted resume string, or None if journal is empty/missing/unreadable
    """
    try:
        return _build_journal_resume_inner(session_dir)
    except Exception as e:
        # Last-resort catch so one malformed event cannot nuke session_init's
        # hook output. Log so the bug is visible but fail-open.
        print(
            f"session_resume: _build_journal_resume failed "
            f"(fail-open, returning None): {e}",
            file=sys.stderr,
        )
        return None


def _build_journal_resume_inner(session_dir: str) -> str | None:
    """
    Inner implementation of _build_journal_resume.

    Separated so the outer wrapper can catch any unexpected exception
    without cluttering the main flow. Each field access uses `.get()`
    with safe defaults so normal missing-field cases don't raise —
    the outer try/except is defense-in-depth for unforeseen shapes.
    """
    all_events = read_events_from(session_dir)
    if not all_events:
        return None

    lines = ["Previous session summary (from journal -- read-only reference):", ""]

    # Extract completed handoffs. Every field access is guarded with
    # .get() and type checks — `decisions[0]` is the single historical
    # crash site (BugF1 secondary), now funneled through the helper.
    handoffs = [e for e in all_events if e.get("type") == "agent_handoff"]
    if handoffs:
        lines.append("## Completed Work")
        for h in handoffs:
            agent = h.get("agent", "unknown")
            subject = h.get("task_subject", "")
            handoff_data = h.get("handoff")
            if not isinstance(handoff_data, dict):
                handoff_data = {}
            summary = _coerce_decision_summary(handoff_data.get("decisions"))
            if summary:
                lines.append(f"- {agent}: {subject} -> {summary}")
            else:
                lines.append(f"- {agent}: {subject}")
        lines.append("")

    # Extract phase progress. Use .get("phase") with a safe default so a
    # malformed phase_transition event (missing `phase`) does not raise
    # KeyError — this is the BugF1 primary crash site. The filter requires
    # `phase` to be a non-empty string so dict/list/number/empty-string
    # shapes from older schema versions do not render as garbled trailers
    # (e.g. "Completed phases: " or "Completed phases: 0").
    #
    # Sort defensively by `ts` so we don't depend on the (currently true
    # but undocumented) chronological-order contract of read_events_from.
    phases = sorted(
        [e for e in all_events if e.get("type") == "phase_transition"],
        key=lambda e: e.get("ts", ""),
    )
    if phases:
        completed = [
            phase
            for p in phases
            if p.get("status") == "completed"
            and (phase := p.get("phase")) and isinstance(phase, str)
        ]

        # Track the latest event per phase name so we only report a phase
        # as "active" if its most recent transition was `started` — a
        # phase that started and then completed should not appear in the
        # active list.
        latest_per_phase: dict[str, tuple[str, str]] = {}
        for p in phases:
            name = p.get("phase")
            status = p.get("status")
            ts = p.get("ts", "")
            if isinstance(name, str) and name and isinstance(status, str):
                prev = latest_per_phase.get(name)
                # Use `>=` so the later-seen event wins on ties: when two
                # events for the same phase share the identical `ts`, the
                # strict `>` comparator would keep the first-seen record
                # and drop the second, causing a
                # `started` + `completed` pair at the same timestamp to
                # leave the phase visible as "active" (BugF2 territory).
                if prev is None or ts >= prev[0]:
                    latest_per_phase[name] = (ts, status)
        # Pick the active phase by max ts among still-started entries.
        # Dict insertion order does not match latest-ts order when multiple
        # phases are concurrently active, so scanning `latest_per_phase` and
        # taking the last insertion (R1 regression) could surface a stale
        # phase on the "Last active phase:" line.
        active_entries = [
            (ts, name)
            for name, (ts, status) in latest_per_phase.items()
            if status == "started"
        ]

        if completed:
            lines.append(
                "Completed phases: "
                + ", ".join(_coerce_phase_string(c) for c in completed)
            )
        if active_entries:
            latest_active = max(active_entries)[1]
            lines.append(
                f"Last active phase: {_coerce_phase_string(latest_active)}"
            )
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
    prev_session_dir: str | None = None,
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
        prev_session_dir: Previous session's directory path (from CLAUDE.md).
            When provided, reads that session's journal for pause state.

    Returns:
        Formatted context string if paused state exists, None otherwise
    """
    if not prev_session_dir:
        return None

    return _check_journal_paused_state(prev_session_dir)


def _check_journal_paused_state(session_dir: str) -> str | None:
    """Check for paused state in the previous session's journal."""
    event = read_last_event_from(session_dir, "session_paused")
    if not event:
        return None

    pr_number = event.get("pr_number")
    branch = event.get("branch", "unknown")
    worktree_path = event.get("worktree_path", "unknown")

    # Defensive type narrowing: bool is a subclass of int, so we must
    # exclude it explicitly. 0/False/""/dict/list/None all fall through
    # to None so the formatter never sees a junk PR number.
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number <= 0:
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
