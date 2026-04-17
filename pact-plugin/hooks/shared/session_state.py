"""
Location: pact-plugin/hooks/shared/session_state.py
Summary: Single-session state summarizer for PACT compaction hooks.
         Replaces the cross-session task_scanner.py (#411 root cause).
Used by: precompact_state_reminder.py

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
import re
from pathlib import Path
from typing import Any

from shared import pact_context
from shared.constants import SYSTEM_TASK_PREFIXES
from shared.session_journal import read_events_from


# Maximum length for sanitized render-bound strings. Matches the
# institutional `_ERROR_MAX_CHARS = 200` bound used by the failure
# ring buffer, so every string we surface to the compaction model has
# the same length cap regardless of source.
_NAME_MAX_CHARS = 200


# Positive allowlist for path components (team_name, feature_id). Any
# input containing anything outside [A-Za-z0-9_-] is rejected. This
# matches the shape of legitimate values:
#   - team_name: "pact-" + hex-with-hyphens (per generate_team_name
#     at session_init.py:173, which uses `re.sub(r"[^a-f0-9-]", "",
#     session_id[:8])`).
#   - feature_id: numeric task IDs or UUIDs (hex + hyphens).
# Rejects ".", "..", "../etc", path separators, control chars, null,
# whitespace, and all shell/URL metacharacters by construction.
# See security-engineer memory patterns_path_name_fallback_escape.md:
# positive regex allowlist is the recommended defense (option 1 over
# Path.name identity checks, which admit ".." as .name returns ".."
# verbatim rather than the expected empty string).
SAFE_PATH_COMPONENT_RE = re.compile(r"[A-Za-z0-9_-]+")
# Back-compat alias for callers that imported the pre-cycle-8 private name.
# The underscore-prefixed binding remains a module-local alias so existing
# test imports (`from shared.session_state import _SAFE_PATH_COMPONENT_RE`)
# and any external consumer that grandfathered in keeps working. New code
# should import the public name via shared/__init__.py.
_SAFE_PATH_COMPONENT_RE = SAFE_PATH_COMPONENT_RE


# Characters stripped from every string that flows into model-visible
# output (custom_instructions, systemMessage). Covers:
#   - C0 control chars 0x00-0x1F (includes NUL, BEL, tab 0x09,
#     LF 0x0A, CR 0x0D, ESC 0x1B, etc.)
#   - DEL (0x7F)
#   - NEL (U+0085), LINE SEPARATOR (U+2028), PARAGRAPH SEPARATOR
#     (U+2029) — Unicode line terminators recognized by
#     `str.splitlines()` and by LLM tokenizers; crafted names
#     containing these survive a naive C0-only filter and can inject
#     new lines into the rendered output.
# Mirrors the regex used by sibling `peer_inject._sanitize_agent_name`
# for symmetric defense (see security-engineer memory
# patterns_symmetric_sanitization.md — asymmetric strip sets across
# interpolation sinks become the attacker's entry point).
_RENDER_STRIP_RE = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]")


def _sanitize_member_name(name: str) -> str:
    """
    Return a sanitized copy of `name` safe to surface into
    model-visible output (custom_instructions, systemMessage).

    Despite the historical name, this helper now sanitizes every
    render-bound string (teammate names, feature subjects, phase
    names, team names, task IDs). The name is retained for call-site
    stability — cycle-2 tests import it by this symbol.

    Defense-in-depth against prompt-injection via:
    - Crafted `~/.claude/teams/{team_name}/config.json` members[].name
      values (security review cycle 1, Finding 1).
    - Crafted `agent_handoff.task_subject` events in the session
      journal (security review cycle 3, F31 — render-facing fields
      from the journal were previously unsanitized, creating
      asymmetric defense).

    Filtering behavior:
    - Strip C0 control chars (0x00-0x1F) INCLUDING tab (0x09). The
      broader application to `feature_subject` / `current_phase` /
      `team_names` means a tab embedded in any of those fields would
      be garbage display — consumers format these with spaces, not
      tabs.
    - Strip DEL (0x7F) and Unicode line terminators NEL / U+2028 /
      U+2029 — aligns with `peer_inject._sanitize_agent_name`.
    - Cap length at _NAME_MAX_CHARS.
    - Return empty string if nothing survives or input isn't a string;
      caller treats empty as "skip this value" (e.g., drop from list).

    This is a filter, not a strict validator: the goal is to neuter
    injection vectors, not RFC-compliance. An empty return signals
    pathological input.
    """
    if not isinstance(name, str):
        return ""
    cleaned = _RENDER_STRIP_RE.sub("", name)
    if len(cleaned) > _NAME_MAX_CHARS:
        cleaned = cleaned[:_NAME_MAX_CHARS]
    return cleaned


def is_safe_path_component(value: str) -> bool:
    """
    Return True if `value` is safe to use as a single-segment path
    component.

    Defense-in-depth against path-traversal via tampered session
    context (see security review Finding 2). The upstream allowlist
    lives at `session_init.py:173` — `re.sub(r"[^a-f0-9-]", "",
    session_id[:8])` — which already filters path separators, `..`,
    nulls, and controls at team-name generation time. This guard is a
    second line of defense at the I/O boundary.

    Uses a positive regex allowlist (`SAFE_PATH_COMPONENT_RE`) instead
    of the tempting `Path(value).name == value` identity check. The
    identity check is BROKEN: `Path("..").name == ".."` is True, so
    the check admits `..` as "safe" and permits one-level directory
    escape when composed against a trusted root (e.g.
    `~/.claude/teams/../something`). The positive allowlist sidesteps
    this by requiring every character to belong to a known-safe
    alphabet; `..`, `.`, `../etc`, path separators, controls, and
    whitespace all reject.

    See security-engineer agent memory
    `patterns_path_name_fallback_escape.md` for the authoritative
    write-up of this anti-pattern.
    """
    if not isinstance(value, str) or not value:
        return False
    return SAFE_PATH_COMPONENT_RE.fullmatch(value) is not None


# Back-compat alias for callers that imported the pre-cycle-8 private
# name. See the matching `_SAFE_PATH_COMPONENT_RE` alias above.
_is_safe_path_component = is_safe_path_component


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

    Mirrors session_resume._build_journal_resume_inner lines 428-469.
    Tied-timestamp correctness (a started+completed pair sharing one
    `ts`) relies on the combination of (a) stable sort in `sorted()`
    preserving append order, (b) journal append-only semantics that
    put `completed` after its matching `started`, and (c) the `>=`
    comparison that lets the later-seen event overwrite the earlier.
    The `>=` alone is insufficient: if a reordered journal ever landed
    `started` after `completed` at the same ts, the `>=` would flip
    the phase back to active. In practice the journal writer stamps
    `ts` monotonically and only appends, so the combined invariant
    holds.

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

    Primary source: the `task_id` of the first variety_assessed event.
    The orchestrator tags the feature task with variety exactly once
    per session, so this is the unambiguous feature marker whenever
    present.

    Fallback (no variety_assessed yet): the chronologically-first
    `agent_dispatch.task_id` that does NOT resolve to a system task
    (algedonic markers, Phase tasks). Without this filter, the
    secretary's briefing task — typically dispatch #1 in a PACT
    session — would be mis-identified as the feature. Because
    agent_dispatch events do not carry `task_subject`, we cross-
    reference each candidate dispatch against `agent_handoff`
    events for the same task_id and reject if the handoff subject
    starts with a `SYSTEM_TASK_PREFIXES` entry. Dispatches without a
    matching handoff yet (pre-first-handoff) are accepted
    provisionally — the disk fallback in summarize_session_state
    can still reject at render time via its own prefix filter.

    feature_subject is sourced from the first agent_handoff event
    whose task_id matches feature_id. agent_dispatch events do NOT
    carry task_subject, so a feature_id with no matching handoff yet
    has no journal-derived subject — returned as None so the disk
    fallback in summarize_session_state can pick it up.

    Returns (None, None) if the journal has no usable variety_assessed
    OR non-system agent_dispatch — correctly reflects "no feature task
    yet declared".
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

    # Pre-compute handoffs once so we can both (a) reject system-prefix
    # dispatches and (b) look up the feature_subject below without
    # re-scanning.
    handoffs = sorted(
        [e for e in events if e.get("type") == "agent_handoff"],
        key=lambda e: e.get("ts", ""),
    )

    if feature_id is None:
        # No variety_assessed event found — fall back to the first
        # non-system agent_dispatch. The secretary's briefing dispatch
        # (typically task #1 of any session) has a system-prefixed
        # subject like "Phase: PREPARE" or similar; rejecting it keeps
        # the feature_subject from surfacing as the secretary task.
        dispatch_events = sorted(
            [e for e in events if e.get("type") == "agent_dispatch"],
            key=lambda e: e.get("ts", ""),
        )
        for d in dispatch_events:
            raw_id = d.get("task_id")
            if not (isinstance(raw_id, str) and raw_id):
                continue
            # Reject if any handoff for this task_id has a system
            # prefix subject. If no handoff exists yet, accept
            # provisionally — the disk fallback in summarize_session_state
            # applies its own prefix filter before rendering.
            handoff_subject: str | None = None
            for h in handoffs:
                if h.get("task_id") == raw_id:
                    cand = h.get("task_subject")
                    if isinstance(cand, str) and cand:
                        handoff_subject = cand
                    break
            if handoff_subject and any(
                handoff_subject.startswith(p)
                for p in SYSTEM_TASK_PREFIXES
            ):
                continue  # System task; skip.
            feature_id = raw_id
            break

    if feature_id is None:
        return (None, None)

    # Look up subject from the chronologically-first handoff matching
    # this feature_id. agent_handoff carries task_subject as a required
    # field, so we trust the schema here.
    for h in handoffs:
        if h.get("task_id") == feature_id:
            subject = h.get("task_subject")
            if isinstance(subject, str) and subject:
                return (feature_id, subject)

    return (feature_id, None)


def _derive_variety_from_journal(
    events: list[dict[str, Any]],
) -> Any:
    """
    Return the variety dict from the first variety_assessed event, or
    None if no such event exists. Opaque passthrough — callers only
    check `is not None` and/or read `.get("total")`. Keeping the full
    dict preserves future flexibility (novelty/scope/uncertainty/risk
    dimensions are rendered by consumers that want them; the default
    compaction-hook render uses `.get("total")`).
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
        order. Names are sanitized (C0 controls stripped, length
        capped) before inclusion — defense-in-depth against
        prompt-injection via crafted config. Empty list on any error.
    """
    if not team_name or not is_safe_path_component(team_name):
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
                    sanitized = _sanitize_member_name(name)
                    if sanitized:
                        names.append(sanitized)
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

    if not team_name or not is_safe_path_component(team_name):
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

    Both team_name and feature_id are validated as single-segment path
    components before use — defense-in-depth against path-traversal
    via tampered session context or journal events.
    """
    if not team_name or not feature_id:
        return None
    if not is_safe_path_component(team_name):
        return None
    if not is_safe_path_component(feature_id):
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
            variety_score (Any), teammates (list[str]),
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
            # Prefix check runs on the RAW fallback (before sanitize)
            # so a crafted subject like "Phase:\u2028FAKE" is still
            # caught by the prefix filter before sanitization could
            # strip the U+2028 and let the rest through.
            if fallback and not any(
                fallback.startswith(p) for p in SYSTEM_TASK_PREFIXES
            ):
                state["feature_subject"] = fallback

        # Render-boundary sanitization (F31 — security review cycle 3):
        # Every string field that flows to the compaction model's
        # custom_instructions / systemMessage must pass through
        # _sanitize_member_name. `teammates` is already sanitized
        # inside _read_team_members per-entry. The remaining string
        # fields — feature_id, feature_subject, current_phase, and
        # each entry in team_names — are sanitized here at the module
        # boundary so a single-point-of-defense applies no matter
        # which helper produced the string. See security-engineer
        # memory patterns_symmetric_sanitization.md for the pattern
        # rationale (asymmetric strip sets across interpolation sinks
        # = no defense). Non-string values pass through untouched
        # (None stays None); _sanitize_member_name returns "" for
        # non-strings, so we guard the call site to preserve None.
        for field in ("feature_id", "feature_subject", "current_phase"):
            value = state[field]
            if isinstance(value, str) and value:
                state[field] = _sanitize_member_name(value)
        state["team_names"] = [
            sanitized
            for sanitized in (
                _sanitize_member_name(n) if isinstance(n, str) else ""
                for n in state["team_names"]
            )
            if sanitized
        ]
    except Exception:
        # Last-resort fail-open: return pristine defaults. Should not
        # be reachable given per-helper try/excepts, but the two
        # callers fire on the compaction hot path — a raise here would
        # block compaction.
        return _default_state(team_name)

    return state
