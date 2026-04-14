"""
Location: pact-plugin/hooks/shared/session_state.py
Summary: Single-session state summarizer for PACT compaction hooks.
         Replaces the cross-session task_scanner.py (#411 root cause).
Used by: precompact_state_reminder.py, postcompact_verify.py

Produces a 10-key state dict that is a superset of the legacy
analyze_task_state (8 keys) + scan_team_members (2 keys) outputs. Every
legacy key is preserved with the same type and null-semantics so
downstream render logic in the two consumer hooks is unchanged.

Source mix (hybrid per architecture §3):
- Journal-sourced fields (authoritative event-sourced state for THIS
  session): feature_subject, feature_id, current_phase, variety_score.
  Read via session_journal.read_events_from(session_dir).
- Disk-sourced fields (session-scoped snapshot, keyed by team_name —
  never iterates a parent directory): completed/in_progress/pending/
  total task counts (from ~/.claude/tasks/{team_name}/*.json) and the
  teammates/team_names roster (from ~/.claude/teams/{team_name}/
  config.json).

Fail-open invariant (SACROSANCT): every error path returns the 10-key
defaults dict. summarize_session_state never raises. Compaction hooks
call this on the hot path; a crash here would block compaction.

Architecture reference: docs/architecture/journal-based-task-scanner.md
in the fix/journal-based-task-scanner worktree.
"""

import json
from pathlib import Path
from typing import Any

from shared import pact_context
from shared.session_journal import read_events_from


# Prefixes that indicate system tasks (not feature tasks). Used
# internally by _read_task_counts when classifying the feature subject
# from the disk snapshot as a fallback. Relocated from task_scanner.py
# (the only other consumer, now deleted).
_SYSTEM_TASK_PREFIXES = ("Phase:", "BLOCKER:", "ALERT:", "HALT:")


# --- Default dict factory -------------------------------------------------


def _default_state(team_name: str) -> dict[str, Any]:
    """
    Build the 10-key defaults dict. Used both as the initial accumulator
    and as the last-resort fail-open return value.

    team_names is derived from the team_name arg (single-entry list if
    non-empty, else []) — it is not a disk read.
    """
    return {
        "completed": 0,
        "in_progress": 0,
        "pending": 0,
        "total": 0,
        "feature_subject": None,
        "feature_id": None,
        "current_phase": None,
        "variety_score": None,
        "teammates": [],
        "team_names": [team_name] if team_name else [],
    }


# --- Journal-sourced helpers ---------------------------------------------


def _derive_phase_from_journal(
    events: list[dict[str, Any]],
) -> str | None:
    """
    Return the latest-started phase that is not yet completed.

    Mirrors session_resume._build_journal_resume_inner lines 428-469 —
    latest-by-ts wins on ties via `>=` so a started+completed pair with
    the same timestamp correctly resolves as completed.

    Returns None if no active phase (empty journal, or every started
    phase has a matching completed).
    """
    phases = sorted(
        [e for e in events if e.get("type") == "phase_transition"],
        key=lambda e: e.get("ts", ""),
    )
    if not phases:
        return None

    latest_per_phase: dict[str, tuple[str, str]] = {}
    for p in phases:
        name = p.get("phase")
        status = p.get("status")
        ts = p.get("ts", "")
        if isinstance(name, str) and name and isinstance(status, str):
            prev = latest_per_phase.get(name)
            if prev is None or ts >= prev[0]:
                latest_per_phase[name] = (ts, status)

    active_entries = [
        (ts, name)
        for name, (ts, status) in latest_per_phase.items()
        if status == "started"
    ]
    if not active_entries:
        return None
    return max(active_entries)[1]


def _derive_feature_from_journal(
    events: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    """
    Return (feature_id, feature_subject) derived from the session
    journal.

    feature_id is the task_id of the first variety_assessed event (the
    orchestrator tags the feature task with variety once per session).
    Falls back to the chronologically-first agent_dispatch.task_id if
    no variety_assessed event exists.

    feature_subject is sourced from the first agent_handoff event whose
    task_id matches feature_id. agent_dispatch events do NOT carry
    task_subject, so a feature_id with no matching handoff yet has no
    journal-derived subject — returned as None so the disk fallback in
    summarize_session_state can pick it up.

    Returns (None, None) if the journal has no variety_assessed and no
    agent_dispatch events — correctly reflects "no feature task yet
    declared".
    """
    variety_events = sorted(
        [e for e in events if e.get("type") == "variety_assessed"],
        key=lambda e: e.get("ts", ""),
    )
    feature_id: str | None = None
    if variety_events:
        raw_id = variety_events[0].get("task_id")
        if isinstance(raw_id, str) and raw_id:
            feature_id = raw_id

    if feature_id is None:
        dispatch_events = sorted(
            [e for e in events if e.get("type") == "agent_dispatch"],
            key=lambda e: e.get("ts", ""),
        )
        for d in dispatch_events:
            raw_id = d.get("task_id")
            if isinstance(raw_id, str) and raw_id:
                feature_id = raw_id
                break

    if feature_id is None:
        return (None, None)

    # Look up subject from the chronologically-first handoff matching
    # this feature_id. agent_handoff carries task_subject as a required
    # field, so we trust the schema here.
    handoffs = sorted(
        [e for e in events if e.get("type") == "agent_handoff"],
        key=lambda e: e.get("ts", ""),
    )
    for h in handoffs:
        if h.get("task_id") == feature_id:
            subject = h.get("task_subject")
            if isinstance(subject, str) and subject:
                return (feature_id, subject)

    return (feature_id, None)


def _derive_variety_from_journal(
    events: list[dict[str, Any]],
) -> Any | None:
    """
    Return the variety dict from the first variety_assessed event, or
    None if no such event exists. Opaque passthrough — callers only
    check `is not None`.
    """
    variety_events = sorted(
        [e for e in events if e.get("type") == "variety_assessed"],
        key=lambda e: e.get("ts", ""),
    )
    for v in variety_events:
        variety = v.get("variety")
        if variety is not None:
            return variety
    return None


# --- Disk-sourced helpers (session-scoped) -------------------------------


def _read_team_members(
    team_name: str,
    teams_base_dir: str | None = None,
) -> list[str]:
    """
    Read member names from ~/.claude/teams/{team_name}/config.json.

    Session-scoped by construction: only the {team_name}/config.json
    file is read — the parent ~/.claude/teams/ directory is never
    iterated. This is the #411 fix.

    Args:
        team_name: Current team identifier from pact_context.
        teams_base_dir: Override for ~/.claude/teams/ (testing).

    Returns:
        List of member names from config.json `members[]` in file
        order. Duplicates preserved (mirrors current behavior — config
        is authoritative). Empty list on any error.
    """
    if not team_name:
        return []

    try:
        if teams_base_dir:
            config_path = Path(teams_base_dir) / team_name / "config.json"
        else:
            config_path = (
                Path.home() / ".claude" / "teams" / team_name / "config.json"
            )

        if not config_path.exists():
            return []

        data = json.loads(
            config_path.read_text(encoding="utf-8", errors="replace")
        )
        members = data.get("members", []) if isinstance(data, dict) else []
        if not isinstance(members, list):
            return []

        names: list[str] = []
        for member in members:
            if isinstance(member, dict):
                name = member.get("name", "")
                if isinstance(name, str) and name:
                    names.append(name)
        return names
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return []


def _read_task_counts(
    team_name: str,
    tasks_base_dir: str | None = None,
) -> dict[str, int]:
    """
    Count tasks by status under ~/.claude/tasks/{team_name}/*.json.

    Session-scoped: iterates only the {team_name} subdirectory, never
    the parent ~/.claude/tasks/. This is the other #411 fix.

    Args:
        team_name: Current team identifier from pact_context.
        tasks_base_dir: Override for ~/.claude/tasks/ (testing).

    Returns:
        Dict with keys {completed, in_progress, pending, total}. Counts
        are ints; total is the sum. Malformed task JSON files are
        skipped silently. All-zero dict on any outer error.
    """
    counts = {"completed": 0, "in_progress": 0, "pending": 0, "total": 0}

    if not team_name:
        return counts

    try:
        if tasks_base_dir:
            team_dir = Path(tasks_base_dir) / team_name
        else:
            team_dir = Path.home() / ".claude" / "tasks" / team_name

        if not team_dir.exists():
            return counts

        for task_file in team_dir.glob("*.json"):
            try:
                data = json.loads(
                    task_file.read_text(encoding="utf-8", errors="replace")
                )
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue

            status = data.get("status", "pending")
            if status in ("completed", "in_progress", "pending"):
                counts[status] += 1
            counts["total"] += 1
    except OSError:
        pass  # Fail-open: return whatever we tallied
    return counts


def _read_feature_subject_from_disk(
    team_name: str,
    feature_id: str,
    tasks_base_dir: str | None = None,
) -> str | None:
    """
    Fallback subject lookup for feature_id when the journal has no
    matching agent_handoff yet (pre-first-handoff case — see
    architecture §4.2 I4).

    Reads ~/.claude/tasks/{team_name}/{feature_id}.json and returns its
    subject. Returns None on any error (file missing, malformed, empty
    subject).
    """
    if not team_name or not feature_id:
        return None

    try:
        if tasks_base_dir:
            task_path = (
                Path(tasks_base_dir) / team_name / f"{feature_id}.json"
            )
        else:
            task_path = (
                Path.home()
                / ".claude"
                / "tasks"
                / team_name
                / f"{feature_id}.json"
            )

        if not task_path.exists():
            return None

        data = json.loads(
            task_path.read_text(encoding="utf-8", errors="replace")
        )
        if not isinstance(data, dict):
            return None
        subject = data.get("subject")
        if isinstance(subject, str) and subject:
            return subject
        return None
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


# --- Public API ----------------------------------------------------------


def summarize_session_state(
    session_dir: str | None = None,
    team_name: str | None = None,
    tasks_base_dir: str | None = None,
    teams_base_dir: str | None = None,
) -> dict[str, Any]:
    """
    Single-session state summary for PACT compaction hooks.

    Hybrid source model: event-sourced fields come from the session
    journal (authoritative for what happened THIS session); snapshot
    fields come from session-scoped disk reads of the current team's
    config and task files (authoritative for the "now" state).

    Args:
        session_dir: Journal directory. Defaults to
            pact_context.get_session_dir(). Empty string → journal
            fields fall back to defaults (None / [] / None).
        team_name: Team identifier for disk scoping. Defaults to
            pact_context.get_team_name(). Empty string → disk fields
            fall back to defaults (0 counts, [] lists).
        tasks_base_dir: Override for ~/.claude/tasks/ root (testing).
            None uses the home-dir default.
        teams_base_dir: Override for ~/.claude/teams/ root (testing).
            None uses the home-dir default.

    Returns:
        10-key dict:
            completed (int), in_progress (int), pending (int),
            total (int), feature_subject (str|None),
            feature_id (str|None), current_phase (str|None),
            variety_score (Any|None), teammates (list[str]),
            team_names (list[str]).

    Fail-open: any exception reaching the outer try/except resolves to
    the 10-key defaults dict. Never raises.
    """
    # Resolve defaults from pact_context ONLY when the caller did not
    # pass an explicit value. Tests pass explicit strings (possibly
    # empty) to avoid any pact_context dependency; the None-default
    # branch is for the two compaction hooks.
    if session_dir is None:
        try:
            session_dir = pact_context.get_session_dir()
        except Exception:
            session_dir = ""
    if team_name is None:
        try:
            team_name = pact_context.get_team_name()
        except Exception:
            team_name = ""

    state = _default_state(team_name)

    try:
        # Journal-sourced fields ------------------------------------
        events: list[dict[str, Any]] = []
        if session_dir:
            try:
                events = read_events_from(session_dir)
            except Exception:
                events = []

        state["current_phase"] = _derive_phase_from_journal(events)
        feature_id, feature_subject = _derive_feature_from_journal(events)
        state["feature_id"] = feature_id
        state["feature_subject"] = feature_subject
        state["variety_score"] = _derive_variety_from_journal(events)

        # Disk-sourced fields ---------------------------------------
        counts = _read_task_counts(team_name, tasks_base_dir)
        state["completed"] = counts["completed"]
        state["in_progress"] = counts["in_progress"]
        state["pending"] = counts["pending"]
        state["total"] = counts["total"]

        state["teammates"] = _read_team_members(team_name, teams_base_dir)

        # Feature-subject disk fallback: if the journal identified a
        # feature_id but no matching agent_handoff yet exists, look up
        # the subject from ~/.claude/tasks/{team_name}/{feature_id}.json.
        # Preserves the pre-refactor behavior where analyze_task_state
        # surfaced the subject directly from disk. Still session-scoped
        # — same team_name path.
        if feature_id and not feature_subject:
            fallback = _read_feature_subject_from_disk(
                team_name, feature_id, tasks_base_dir
            )
            # Only accept the fallback if it is a real feature subject,
            # not a system task subject (Phase:/BLOCKER:/etc.). If the
            # orchestrator's feature task really did start with a system
            # prefix that would be a different bug; preserving the
            # prefix filter matches analyze_task_state's behavior.
            if fallback and not any(
                fallback.startswith(p) for p in _SYSTEM_TASK_PREFIXES
            ):
                state["feature_subject"] = fallback
    except Exception:
        # Last-resort fail-open: return pristine defaults. Should not
        # be reachable given per-helper try/excepts, but the two
        # callers fire on the compaction hot path — a raise here would
        # block compaction.
        return _default_state(team_name)

    return state
