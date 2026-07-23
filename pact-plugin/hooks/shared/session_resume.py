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
5. Unified resume-claim resolution over paused/refreshed checkpoints
   (check_resume_state — the single seam session_init step 8 calls)
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MANAGED_TITLE,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    _atomic_write_text,
    ensure_dot_claude_parent,
    file_lock,
    resolve_project_claude_md_path,
)
from shared.session_journal import (
    _parse_ts,
    _ts_supersedes,
    read_events_from,
    read_last_event_from,
)

# Maximum characters for decision summaries in journal resume output
_DECISION_TRUNCATION_LIMIT = 80

# Staleness horizon for a session_refreshed checkpoint. A refresh is meant
# to be consumed within minutes (refresh → /compact → bootstrap); past this
# horizon the prompt gets an informational STALE prefix. Downgrade ONLY —
# never suppression: the mid-flight claim (and any HALT line) survives.
_REFRESH_STALE_HOURS = 48

# Bounds for event field values interpolated into the refreshed resume
# prompt. Journal events are written by the refresh command but the journal
# file itself is plain JSONL on disk — a hand-crafted or corrupted event
# must not be able to flood the SessionStart context or smuggle directive
# lines into the prompt. Free-text fields (feature_subject, next_phase,
# task ids) get the tight bound; worktree paths get a wider one because
# legitimate absolute paths can be long.
_REFRESH_FIELD_TRUNCATION_LIMIT = 200
_REFRESH_PATH_TRUNCATION_LIMIT = 512

# Control characters stripped from interpolated refreshed-prompt fields:
# C0 controls (includes \n, \r, \t), DEL plus the full C1 block (which
# includes NEL U+0085 — a str.splitlines boundary), and the Unicode
# line/paragraph separators — anything that could break the prompt onto
# a new line and masquerade as a separate directive.
_PROMPT_CONTROL_CHARS_RE = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]+")

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

    # Concurrency guard: serialize read-mutate-write so two concurrent
    # session_init hooks on the same project CLAUDE.md cannot interleave
    # update_session_info writes and clobber each other's managed blocks.
    # Fail-open on timeout — next session start will retry.
    try:
        with file_lock(target_file):
            # Symlink guard INSIDE the lock (TOCTOU defense): is_symlink
            # uses lstat (does not follow the link). Inside the lock so an
            # attacker cannot swap the target between an outside-lock
            # check and the write.
            if target_file.is_symlink():
                return "Session info skipped: path precondition not met."

            try:
                # Case 0: File doesn't exist -- create it with the full canonical
                # PACT_MANAGED structure so the orchestrator has a stable Current
                # Session block AND a ready PACT_MEMORY container on the very first
                # session in a project. The .claude/ parent was created above
                # (before the lock) with mode 0o700.
                #
                # Structure mirrors `ensure_project_memory_md`'s template — single
                # H1 ("# PACT Framework and Managed Project Memory"), session
                # block, PACT_MEMORY with three default section headings, all
                # wrapped by the PACT_MANAGED outer boundary.
                if not target_file.exists():
                    new_content = (
                        f"{MANAGED_START_MARKER}\n"
                        f"{MANAGED_TITLE}\n"
                        "\n"
                        f"{session_block}\n"
                        "\n"
                        f"{MEMORY_START_MARKER}\n"
                        "## Retrieved Context\n"
                        "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->\n"
                        "\n"
                        "## Pinned Context\n"
                        "\n"
                        "## Working Memory\n"
                        "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. Full history searchable via pact-memory skill. -->\n"
                        f"{MEMORY_END_MARKER}\n"
                        "\n"
                        f"{MANAGED_END_MARKER}\n"
                    )
                    _atomic_write_text(target_file, new_content)
                    return "Session info created in new project CLAUDE.md"

                content = target_file.read_text(encoding="utf-8")

                # Case 1: Markers already exist -- replace the block.
                # Structural guarantee (round 10): SESSION markers are always
                # inside the PACT_MANAGED region (placed by template in both
                # ensure_project_memory_md and update_session_info Case 0).
                # The re.DOTALL regex below scans the full file, but the
                # markers can only appear in plugin-generated content — no
                # user-authored fenced code blocks can contain real SESSION
                # markers, so fence-aware scanning is unnecessary.
                if SESSION_START in content and SESSION_END in content:
                    new_content = re.sub(
                        re.escape(SESSION_START) + r".*?" + re.escape(SESSION_END),
                        session_block,
                        content,
                        count=1,
                        flags=re.DOTALL,
                    )
                    if new_content != content:
                        _atomic_write_text(target_file, new_content)
                        return "Session info updated in project CLAUDE.md"
                    return None

                # Case 2: No SESSION markers. Insertion order matters because
                # the session block must be a SIBLING of PACT_MEMORY inside
                # PACT_MANAGED — never placed inside PACT_MEMORY where it
                # would pollute the memory region and violate the
                # "Current Session is outside PACT_MEMORY" invariant.
                #
                # Ordered preference:
                #   (a) Post-migration file (PACT_MANAGED present): insert
                #       BEFORE MEMORY_START_MARKER so the block stays inside
                #       PACT_MANAGED but outside PACT_MEMORY. This is the
                #       round-4 Item-1 fix — the prior behavior anchored on
                #       "## Retrieved Context" which, after migration, lives
                #       INSIDE PACT_MEMORY.
                #       Structural guarantee (round 10): MEMORY_START_MARKER
                #       is always inside PACT_MANAGED, so the .replace()
                #       below lands the session block in plugin-generated
                #       content — no fence-awareness needed.
                #   (b) Legacy pre-migration file (no PACT_MANAGED): keep
                #       the historical anchor on "## Retrieved Context"
                #       since memory sections were top-level in that shape.
                #   (c) Neither: append at end of file.
                # Both markers are checked (not just MANAGED_START) because a
                # partially-written migration output is theoretically possible
                # under crash: the managed-open marker could be present while
                # memory markers are not yet written. The AND check treats such
                # partial states as pre-migration, routing to the legacy
                # fallback (branch b) rather than attempting a .replace() that
                # would be a no-op producing silent data loss (round 5, item 8).
                if MANAGED_START_MARKER in content and MEMORY_START_MARKER in content:
                    new_content = content.replace(
                        MEMORY_START_MARKER,
                        session_block + "\n\n" + MEMORY_START_MARKER,
                        1,
                    )
                else:
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

                _atomic_write_text(target_file, new_content)
                return "Session info added to project CLAUDE.md"

            except Exception as e:
                return f"Session info failed: {str(e)[:50]}"
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Session info update skipped; will retry on next session start."
        )
    except OSError:
        # #1245: lock ACQUISITION PermissionError escapes `except TimeoutError`;
        # catch it at the same skip-and-retry level (the inner except Exception
        # handles only post-acquisition failures, inside the `with file_lock`).
        # Opaque, matching the sibling TimeoutError message -- no path leak.
        return (
            "Could not acquire lock on project CLAUDE.md "
            "(path precondition not met); session info update skipped."
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
    return _interpret_paused_event(event)


def _interpret_paused_event(event: dict) -> str | None:
    """Interpret an already-read session_paused event into a resume prompt.

    Split from _check_journal_paused_state so check_resume_state can feed
    it the event it already read (one journal read per event type). The
    body is byte-identical to the pre-split logic: pr_number type-narrowing,
    14-day TTL, `gh` PR-state probe, and the silent-None branches.

    Fail direction: PR-GATED SILENT-None is CORRECT here — a paused claim
    whose PR is gone (or that never had a valid PR) has nothing to resume.
    This is the OPPOSITE of the refresh interpreter's fail direction; the
    two interpreters deliberately share no predicate (see
    _interpret_refreshed_event).
    """
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


def check_resume_state(
    prev_session_dir: str | None = None,
) -> str | None:
    """Unified resume-claim resolver over {session_paused, session_refreshed}.

    Single public seam: session_init step 8 calls THIS (and only this).
    Single-dir signature is sufficient for all three paths: post-compact and
    same-session --resume, prev_session_dir self-resolves to the CURRENT
    session dir; the quit path reads one hop back.

    The two per-event-type interpreters stay SEPARATE functions because
    their fail directions are opposite and must never share a predicate:
    the paused interpreter keeps its PR-gated silent-None (correct for
    pause), the refreshed interpreter is fail-safe-toward-surfacing (any
    unspent session_refreshed event yields a prompt). When both claims
    survive interpretation, _arbitrate picks the newer one — the losing
    claim is always mentioned, never silently dropped.

    Args:
        prev_session_dir: Previous session's directory path (from CLAUDE.md).

    Returns:
        The winning resume prompt, or None when no claim survives.
    """
    if not prev_session_dir:
        return None
    paused = read_last_event_from(prev_session_dir, "session_paused")
    refreshed = read_last_event_from(prev_session_dir, "session_refreshed")
    if refreshed is not None and _refresh_is_spent(prev_session_dir, refreshed):
        refreshed = None
    paused_msg = (
        _interpret_paused_event(paused) if paused is not None else None
    )  # may be None (correct for pause)
    refresh_msg = (
        _interpret_refreshed_event(refreshed) if refreshed is not None else None
    )
    if paused_msg and refresh_msg:
        return _arbitrate(paused, paused_msg, refreshed, refresh_msg)
    return refresh_msg or paused_msg


def _sanitize_prompt_field(
    value: str,
    limit: int = _REFRESH_FIELD_TRUNCATION_LIMIT,
) -> str:
    """Sanitize an event field value for interpolation into a resume prompt.

    Collapses control characters (C0, DEL, U+2028/U+2029 — anything that
    could break the prompt onto a new line and masquerade as a separate
    directive) to single spaces, strips, and bounds the length with the
    same ``...`` truncation convention as ``_coerce_decision_summary``.

    Total for any input: an internal failure returns ``""`` so the caller
    drops that field's LINE — content degrades, prompt presence never does
    (a sanitizer error must not become a new suppress path).
    """
    try:
        cleaned = _PROMPT_CONTROL_CHARS_RE.sub(" ", value).strip()
        if len(cleaned) > limit:
            cleaned = cleaned[:limit - 3] + "..."
        return cleaned
    except Exception:
        return ""


def _compose_halt_line(event: dict) -> str | None:
    """Compose the HALT verify-line for a session_refreshed event, or None
    when ``halt_active`` is not exactly True.

    Single composition point shared by ``_interpret_refreshed_event`` and
    ``_arbitrate`` so the interpreter and the losing-claim survival path
    can never render the same event's HALT state differently. Task ids are
    sanitized HERE, once — "verbatim" preservation downstream means this
    sanitized line. Include ids only when ``halt_task_ids`` is a list
    holding non-empty strings; ``halt_active`` malformed/absent ⇒ no line
    (live-task surfacing still covers the union's other leg).
    """
    if event.get("halt_active") is not True:
        return None
    halt_task_ids = event.get("halt_task_ids")
    ids = (
        [_sanitize_prompt_field(i) for i in halt_task_ids if isinstance(i, str)]
        if isinstance(halt_task_ids, list)
        else []
    )
    ids = [i for i in ids if i]
    id_note = f" (tasks: {', '.join(ids)})" if ids else ""
    return (
        f"A HALT/algedonic signal was ACTIVE at refresh{id_note} — "
        f"verify against TaskList before proceeding; do not assume it "
        f"resolved."
    )


def _interpret_refreshed_event(event: dict) -> str:
    """Interpret an already-read session_refreshed event into a prompt.

    MUST return a non-empty prompt for ANY dict input (fail-safe-toward-
    surfacing; return type is str, NOT str | None — totality by signature).
    Malformed fields degrade CONTENT, never PRESENCE. Do NOT copy the paused
    interpreter's early returns: no pr_number gate, no gh probe, no
    silent-None branch of any kind.

    Prompt composition: each absent/malformed field drops its line only.
    The 48h staleness horizon (_REFRESH_STALE_HOURS) prefixes an
    informational downgrade and changes nothing else — a stale prompt
    retains its HALT line. An unparseable/missing ts means no downgrade
    (treat as fresh — fail toward full surfacing).
    """
    ts = event.get("ts")
    # A ts carrying control characters is treated as INVALID for prompt
    # purposes: the consumption key must be echoed VERBATIM to keep the
    # spend-binding exact-match, so it cannot be sanitized — instead the
    # UNAVAILABLE branch renders (fails toward surfacing; the prompt may
    # re-surface once, bounded by the staleness downgrade). Clean ts
    # values keep the byte-exact echo.
    ts_valid = (
        isinstance(ts, str)
        and bool(ts.strip())
        and not _PROMPT_CONTROL_CHARS_RE.search(ts)
    )

    content: list[str] = []

    # String fields are SANITIZED at interpolation (_sanitize_prompt_field:
    # control-char strip + length bound) — the journal is plain JSONL on
    # disk, so a hand-crafted event must not smuggle directive lines or
    # flood the SessionStart context. Sanitization can empty a value
    # (all-control-chars input); an emptied field drops its line only.
    feature_subject = event.get("feature_subject")
    feature_task_id = event.get("feature_task_id")
    subject = (
        _sanitize_prompt_field(feature_subject)
        if isinstance(feature_subject, str)
        else ""
    )
    task_id = (
        _sanitize_prompt_field(feature_task_id)
        if isinstance(feature_task_id, str)
        else ""
    )
    if subject and task_id:
        content.append(f"Feature: {subject} (task {task_id}).")
    elif subject:
        content.append(f"Feature: {subject}.")
    elif task_id:
        content.append(f"Feature task: {task_id}.")

    next_phase = event.get("next_phase")
    if isinstance(next_phase, str):
        phase = _sanitize_prompt_field(next_phase)
        if phase:
            content.append(f"Next phase: {phase}.")

    worktrees = event.get("worktrees")
    if isinstance(worktrees, list):
        # Paths sanitized like every interpolated field (wider bound —
        # legitimate absolute paths can be long); existence checking still
        # happens at bootstrap, not here — the resolver stays a pure
        # journal reader.
        paths = [
            _sanitize_prompt_field(w, _REFRESH_PATH_TRUNCATION_LIMIT)
            for w in worktrees
            if isinstance(w, str)
        ]
        paths = [p for p in paths if p]
        if paths:
            content.append("Worktrees: " + ", ".join(paths) + ".")

    # HALT line (I2): composed by _compose_halt_line (shared with
    # _arbitrate's losing-claim path) — present iff halt_active is True;
    # NEVER omit the prompt itself.
    halt_line = _compose_halt_line(event)
    if halt_line:
        content.append(halt_line)

    # Capture-knowledge warning (wording mirrors the paused interpreter's
    # consolidation note). Trigger is an EXPLICIT False — the field is
    # required + bool-validated at write time, so a missing/malformed
    # value means a malformed event, which keeps the degraded floor below
    # instead of fabricating a warning.
    if event.get("consolidation_completed") is False:
        content.append(
            "Memory consolidation did NOT complete — "
            "run /PACT:pause or /PACT:wrap-up to capture session knowledge."
        )

    # Degraded floor: a dict with no usable field at all still surfaces.
    if not content and not ts_valid:
        return (
            "Refresh detected — run TaskList to recover state, "
            "then /PACT:bootstrap."
        )

    header = (
        "Refreshed workstream detected — mid-flight resume, "
        "not a fresh start."
    )
    if ts_valid:
        try:
            refreshed_at = _parse_ts(ts)
            if refreshed_at.tzinfo is None:
                refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - refreshed_at
            if age > timedelta(hours=_REFRESH_STALE_HOURS):
                header = (
                    f"STALE checkpoint from "
                    f"{refreshed_at.strftime('%Y-%m-%d')} (older than "
                    f"{_REFRESH_STALE_HOURS}h). " + header
                )
        except (ValueError, TypeError):
            pass  # Unparseable ts ⇒ no downgrade — fail toward full surfacing.

    parts = [header]
    parts.extend(content)
    # Consumption key, ALWAYS when ts is a non-empty str — verbatim and
    # machine-copyable; bootstrap's consumption write substitutes this value.
    if ts_valid:
        parts.append(f"refresh_ts={ts}")
    else:
        parts.append(
            "refresh_ts=UNAVAILABLE — consumption cannot be recorded; "
            "prompt may re-surface once."
        )
    parts.append(
        "Run /PACT:bootstrap to respawn the secretary and resume. "
        "Do NOT message any pre-refresh teammate name before bootstrap "
        "respawns it."
    )
    return " ".join(parts)


def _refresh_is_spent(session_dir: str, refreshed: dict) -> bool:
    """True iff a session_refresh_consumed event retires this refresh claim.

    Fire-once via ts-bound consumption: the refresh event's `ts` IS the
    claim id; a consumption's `refresh_ts` must match it exactly (string
    compare — no parsing on the identity axis). Every failure path lands on
    UNSPENT (return False), so a malformed consumption can never suppress a
    prompt. The `>=` conjunct is a belt on the SUPPRESS direction only: the
    consumption must also be temporally sane (written at-or-after its
    refresh). It can never wrongly KEEP a prompt (worst case one duplicate),
    and it blocks the only wrong-spend shape — a consumption record
    predating its claim.
    """
    ts = refreshed.get("ts")
    if not isinstance(ts, str) or not ts:
        return False  # fail toward surfacing
    for consumption in read_events_from(session_dir, "session_refresh_consumed"):
        if consumption.get("refresh_ts") != ts:  # exact string match — the ts IS the claim id
            continue
        try:
            if _parse_ts(consumption.get("ts")) >= _parse_ts(ts):
                return True
        except Exception:
            continue  # fail toward surfacing
    return False


def _both_parse(*timestamps: Any) -> bool:
    """True iff every argument parses via _parse_ts without raising."""
    for value in timestamps:
        try:
            _parse_ts(value)
        except (ValueError, TypeError):
            return False
    return True


def _claim_date(ts: Any) -> str:
    """Render a claim timestamp as YYYY-MM-DD for the superseded-claim
    clause. Callers guarantee parseability via _both_parse."""
    return _parse_ts(ts).strftime("%Y-%m-%d")


def _arbitrate(
    paused_ev: dict,
    paused_msg: str,
    refreshed_ev: dict,
    refresh_msg: str,
) -> str:
    """Pick the newer of two surviving resume claims (newest-ts-wins).

    Never silently drop a resume claim: the losing claim is always
    mentioned in one clause; when either ts is unparseable the claims
    cannot be ordered, so BOTH surface in full with an explicit conflict
    note. Ties go to the refreshed claim (`_ts_supersedes` is `>=`) — the
    mid-flight claim is the more specific.

    HALT survival: a LOSING refreshed claim that carried an active HALT
    keeps its verify-line VERBATIM (the `_compose_halt_line` rendering —
    one composition point with the interpreter) and is labeled
    "superseded", not "stale" — arbitration must never become a suppress
    path for an algedonic signal.
    """
    p_ts, r_ts = paused_ev.get("ts"), refreshed_ev.get("ts")
    if not _both_parse(p_ts, r_ts):
        return (
            refresh_msg + " | " + paused_msg +
            " | CONFLICT: both a paused and a refreshed claim exist with "
            "unordered timestamps — verify via TaskList before resuming."
        )
    if _ts_supersedes(r_ts, p_ts):
        return refresh_msg + (
            f" (A stale paused claim from {_claim_date(p_ts)} also exists.)"
        )
    halt_line = _compose_halt_line(refreshed_ev)
    if halt_line:
        return paused_msg + (
            f" (A superseded refreshed claim from {_claim_date(r_ts)} also "
            f"exists.) {halt_line}"
        )
    return paused_msg + (
        f" (A stale refreshed claim from {_claim_date(r_ts)} also exists.)"
    )


def has_unspent_refresh(session_dir: str | None) -> bool:
    """True iff the dir's latest session_refreshed exists and is unconsumed.

    Presentation-only signal for session_init's compact branch (suppress
    'Re-engage secretary', re-label the agent list). The FULL prompt comes
    only from check_resume_state at step 8 — this helper never composes
    surfacing text. Any internal error returns False (the directive keeps
    its current wording — degraded, not broken).
    """
    try:
        if not session_dir:
            return False
        refreshed = read_last_event_from(session_dir, "session_refreshed")
        if refreshed is None:
            return False
        return not _refresh_is_spent(session_dir, refreshed)
    except Exception:
        return False


def _check_pr_state(pr_number: int | str) -> str:
    """
    Check PR state via ``gh pr view``. Returns uppercase state or empty on error.

    Thin wrapper around ``shared.gh_helpers.check_pr_state`` — kept as a
    module-local function (not a bare re-export) so existing test patches
    of ``shared.session_resume._check_pr_state`` continue to work without
    modification (#453: relocated the implementation, preserved the call
    surface).
    """
    from shared import check_pr_state

    return check_pr_state(pr_number)
