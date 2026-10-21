"""
Location: pact-plugin/hooks/shared/task_utils.py
Summary: Shared Task system integration utilities for PACT hooks.
Used by: PACT lifecycle/wake hooks (via get_task_list), the lifecycle gate
and handoff machinery (via read_task_json), and dispatch helpers (via
iter_team_task_jsons).

This module provides common functions for reading and analyzing Tasks from
the Claude Task system. Tasks live under one of two storage topologies, keyed
on a structural signal (team_name): a TEAM session stores tasks at
~/.claude/tasks/{team_name}/*.json, a SOLO/no-team session at
~/.claude/tasks/{session_id}/*.json. They survive context compaction, making
them the primary state source for workflow recovery.

Functions:
    get_task_list: Read all tasks from the Task system
    find_feature_task: Identify the main Feature task
    find_current_phase: Find the currently active phase task
    find_active_agents: Find all active agent tasks
    find_blockers: Find blocker/algedonic tasks
    build_post_compaction_checkpoint: Build [POST-COMPACTION CHECKPOINT] message from Task state
    task_dir_for: Containment-validated join of one team name under the tasks root
    resolve_team_dir: Resolve the live teams/{team} dir from context candidates
    resolve_task_dir: Resolve the live tasks/{team} dir from context candidates
    iter_team_task_jsons: Yield parsed task JSONs from a team dir (path-traversal safe)
    read_task_json: Read the raw task JSON by id + team_name (path-traversal safe)
    is_teachback_subject: True iff a subject matches the canonical Teachback shape
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

from shared.pact_context import get_session_id, get_team_name
import shared.pact_context as _pact_context_module
from shared.session_state import is_safe_path_component
from shared.paths import get_claude_config_dir


def get_task_list(tasks_base_dir: str | None = None) -> list[dict[str, Any]] | None:
    """
    Read TaskList from the Claude Task system.

    Two live storage topologies, keyed on a structural signal (team_name):
      - TEAM session -> tasks under ~/.claude/tasks/{team_name}/   (Agent Teams)
      - SOLO session -> tasks under ~/.claude/tasks/{session_id}/  (no team)
    team_name is mode-independent (resolves identically under in-process and
    tmux), so the team branch needs no teammateMode detection. This function
    reads directly from the filesystem since hooks cannot call Task tools.

    Args:
        tasks_base_dir: Override for the ~/.claude/tasks base dir (testing only;
            production default None resolves the real home path). Mirrors the
            institutional test seam in _read_task_counts / read_task_json.

    Returns:
        List of task dicts, or None if tasks unavailable
    """
    # TEAM branch (the team-dir resolution fix): a team session stores tasks
    # under {team_name}, NOT the bare session_id. Reuse the path-traversal-safe
    # SSOT reader. An empty/absent team dir returns None and MUST NOT fall
    # through to the solo session_id dir: a team session with no tasks yet is
    # still a team session (branch key = "is this a team session?", not "did the
    # team dir have tasks?"). The return lives INSIDE the `if` to guarantee that
    # invariant — control never reaches the solo branch when team_name is truthy.
    team_name = get_team_name()
    if team_name:
        tasks = list(iter_team_task_jsons(team_name, tasks_base_dir=tasks_base_dir))
        return tasks if tasks else None

    # SOLO / no-team branch: the CLAUDE_CODE_TASK_LIST_ID-or-session_id key
    # choice is preserved, and the dir is read by this branch's own direct glob
    # (NOT routed through iter_team_task_jsons) — but with the SAME FIVE
    # path-safety + content-hygiene defenses applied INLINE, in FULL PARITY with
    # the team branch (iter_team_task_jsons): (1) is_safe_path_component on the
    # name (F2 guard below), (2) a resolve/relative_to base-anchor on the dir,
    # (3) a per-file is_symlink skip, (4) a dotfile-prefixed-file skip, and
    # (5) an isinstance(dict) parse guard. The solo branch is therefore NOT
    # raw/unvalidated: it has FULL 5/5 defense parity with the team branch, just
    # implemented inline. Surgical-inline duplication (NOT a shared-helper
    # refactor) is the accepted trade-off to avoid touching the working/tested
    # team path. Only the base root is parameterized (test seam).
    session_id = get_session_id()
    # Also check for multi-session task list ID
    task_list_id = os.environ.get("CLAUDE_CODE_TASK_LIST_ID", session_id)

    if not task_list_id:
        return None

    # F2 (security): task_list_id is USER-CONTROLLED via the
    # CLAUDE_CODE_TASK_LIST_ID env var, then used as a path component. Validate
    # it with the same positive allowlist the team branch already applies to
    # team_name (is_safe_path_component, inside iter_team_task_jsons) so a
    # traversal value ("../secret") cannot escape the tasks base and read *.json
    # outside it. Legitimate task-list IDs (pact-<slug>, UUIDs, multi-session
    # pointers) are single [A-Za-z0-9_-] components and pass unchanged — no solo
    # behavior change for real inputs (the env var must name a ~/.claude/tasks/
    # dir, which the platform generates within that alphabet). Validating BEFORE
    # the path-join also rejects an embedded-NUL value before the .exists() call
    # (which could otherwise raise ValueError on some Python versions). Fail
    # CLOSED (return None) to preserve the None-on-unavailable contract.
    if not is_safe_path_component(task_list_id):
        return None

    base = Path(tasks_base_dir) if tasks_base_dir else (get_claude_config_dir() / "tasks")
    tasks_dir = base / task_list_id

    # R3 (security): the {task_list_id} dir could itself be a SYMLINK that
    # escapes the tasks base. is_safe_path_component (above) rejects '../' in the
    # NAME, but not a safe-NAME dir that resolves out-of-base via a symlink.
    # Mirror the team branch's anchor (iter_team_task_jsons): resolve the dir and
    # assert it stays under the resolved base. A legit (non-symlink) dir resolves
    # to itself and passes unchanged; only an escaping symlink-dir is rejected.
    try:
        tasks_dir.resolve().relative_to(base.resolve())
    except (OSError, ValueError):
        return None

    if not tasks_dir.exists():
        return None

    tasks = []
    try:
        for task_file in tasks_dir.glob("*.json"):
            # R4 (hygiene parity): skip dotfile-prefixed JSON. pathlib glob
            # INCLUDES them, but the platform task system never writes them; an
            # attacker dropping one into the user's own tasks dir could otherwise
            # inflate the task count. Mirrors the team branch's dotfile skip.
            if task_file.name.startswith("."):
                continue
            # R3 (security): skip a *.json that is a SYMLINK — it could point
            # outside the tasks base. Mirrors the team branch's per-file
            # is_symlink skip; real task files are regular files, unaffected.
            try:
                if task_file.is_symlink():
                    continue
            except OSError:
                continue
            try:
                content = task_file.read_text(encoding='utf-8')
                task = json.loads(content)
                # R4 (hygiene parity): skip a parse that is not a dict (e.g.
                # [1,2,3]/42/"x" from a malformed-but-valid JSON). Mirrors the
                # team branch's isinstance(dict) yield-guard; downstream readers
                # (find_feature_task etc.) call .get() and need a dict.
                if not isinstance(task, dict):
                    continue
                tasks.append(task)
            except (IOError, json.JSONDecodeError):
                continue
    except Exception:
        return None

    return tasks if tasks else None


def find_feature_task(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Find the main Feature task from the task list.

    Feature tasks are top-level tasks that represent the overall work item.
    They can be identified by:
    - Having no blockedBy (top-level)
    - Subject starting with a verb (e.g., "Implement user auth")
    - OR having phase tasks as children

    Args:
        tasks: List of all tasks

    Returns:
        Feature task dict, or None if not found
    """
    # Feature task is one that blocks others but isn't blocked itself
    # (or has status in_progress at top level)
    for task in tasks:
        task_id = task.get("id")
        if not task_id:
            continue

        # Skip if this task is blocked by something
        if task.get("blockedBy"):
            continue

        # Check if it's a feature-like task (not a phase task)
        subject = task.get("subject", "")
        # Phase tasks start with phase names
        phase_prefixes = ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:", "Review:")
        if any(subject.startswith(p) for p in phase_prefixes):
            continue

        # This looks like a feature task
        if task.get("status") in ("in_progress", "pending"):
            return task

    return None


def find_current_phase(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Find the currently active phase task.

    Phase tasks follow the pattern: "{PHASE}: {feature-slug}"
    The current phase is the one with status "in_progress".

    Args:
        tasks: List of all tasks

    Returns:
        Phase task dict, or None if not found
    """
    phase_prefixes = ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:")

    for task in tasks:
        subject = task.get("subject", "")
        if any(subject.startswith(p) for p in phase_prefixes):
            if task.get("status") == "in_progress":
                return task

    return None


def find_active_agents(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find all currently active agent tasks.

    Agent tasks follow the pattern: "{agent-type}: {work-description}"
    and have status "in_progress".

    Args:
        tasks: List of all tasks

    Returns:
        List of agent task dicts
    """
    agent_prefixes = (
        "preparer:",
        "architect:",
        "backend-coder:",
        "frontend-coder:",
        "database-engineer:",
        "devops-engineer:",
        "n8n:",
        "test-engineer:",
        "security-engineer:",
        "qa-engineer:",
        "auditor:",
        "secretary:",
    )

    active = []
    for task in tasks:
        subject = task.get("subject", "").lower()
        if any(subject.startswith(p) for p in agent_prefixes):
            if task.get("status") == "in_progress":
                active.append(task)

    return active


def find_blockers(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find any blocker or algedonic tasks.

    These are signal tasks created by agents when they hit blockers
    or detect viability threats.

    Args:
        tasks: List of all tasks

    Returns:
        List of blocker/algedonic task dicts
    """
    blockers = []
    for task in tasks:
        metadata = task.get("metadata", {})
        task_type = metadata.get("type", "")
        if task_type in ("blocker", "algedonic"):
            if task.get("status") != "completed":
                blockers.append(task)

    return blockers


def build_post_compaction_checkpoint(
    feature: dict[str, Any] | None,
    phase: dict[str, Any] | None,
    agents: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> str:
    """Build [POST-COMPACTION CHECKPOINT] message from Task state.

    Generates a concise message describing the workflow state for
    the orchestrator to resume from.

    Args:
        feature: Feature task dict or None
        phase: Current phase task dict or None
        agents: List of active agent tasks
        blockers: List of active blocker tasks

    Returns:
        Formatted refresh message string
    """
    lines = ["[POST-COMPACTION CHECKPOINT]"]
    lines.append("Prior conversation auto-compacted. Resume from Task state below:")

    # Feature context
    if feature:
        feature_subject = feature.get("subject", "unknown feature")
        feature_id = feature.get("id", "")
        if feature_id:
            lines.append(f"Feature: {feature_subject} (id: {feature_id})")
        else:
            lines.append(f"Feature: {feature_subject}")
    else:
        lines.append("Feature: Unable to identify feature task")

    # Phase context
    if phase:
        phase_subject = phase.get("subject", "unknown phase")
        lines.append(f"Current Phase: {phase_subject}")
    else:
        lines.append("Current Phase: None detected")

    # Active agents
    if agents:
        agent_names = [a.get("subject", "unknown") for a in agents]
        lines.append(f"Active Agents ({len(agents)}): {', '.join(agent_names)}")
    else:
        lines.append("Active Agents: None")

    # Blockers (critical info)
    if blockers:
        lines.append("")
        lines.append("**BLOCKERS DETECTED:**")
        for blocker in blockers:
            subj = blocker.get("subject", "unknown blocker")
            meta = blocker.get("metadata", {})
            level = meta.get("level", "")
            if level:
                lines.append(f"  - {level}: {subj}")
            else:
                lines.append(f"  - {subj}")

    # Next step guidance
    lines.append("")
    if blockers:
        lines.append("Next Step: **Address blockers before proceeding.**")
    elif agents:
        lines.append("Next Step: Monitor active agents via TaskList, then proceed.")
    elif phase:
        lines.append("Next Step: Continue current phase or check agent completion.")
    else:
        lines.append("Next Step: **Check TaskList and ask user how to proceed.**")

    return "\n".join(lines)


# ─── team/task path resolution (the one seam every hook builds paths through) ─
#
# Two layers:
#   * task_dir_for / _team_dir_under — CONSTRUCTION: validate one name, join it
#     under one root, assert containment. No existence claim. This is the
#     containment SSOT: the R3 symlink-escape anchor (resolve + relative_to)
#     and the is_safe_path_component allowlist live HERE and nowhere else, so
#     migrating a caller to these helpers RETIRES that caller's local copy
#     rather than adding a second one.
#   * resolve_task_dir / resolve_team_dir — RESOLUTION: pick the live store
#     from context candidates (aligned-first, one-directional), existence-only.
#     Non-TTY harnesses key the store by the full session UUID while the
#     persisted context name says "session-<id8>"; the resolver absorbs that
#     divergence so raw reads of get_pact_context()["team_name"] cannot address
#     the wrong (absent) store — the #1020 silent-HANDOFF-loss class.


def _team_dir_under(base: Path, name: object) -> Path | None:
    """Containment SSOT for ONE team-keyed name under ONE root.

    Validates and joins the name AS GIVEN (whitespace-stripped, NEVER
    case-folded), then asserts the resolved dir stays under the resolved base
    (symlink-escape defense). Returns the JOINED (unresolved) dir on success,
    ``None`` for any unsafe name — never raises, so callers fail open exactly
    as they did with inline copies.

    WHY NO CASE-FOLD (OS-invariance, the Linux-CI lesson): folding the join
    made the addressed path a DIFFERENT name on case-SENSITIVE filesystems —
    a symlinked tasks/EVIL was bypassed entirely by joining tasks/evil (a
    fresh, clean dir inside the base), so the escape guard never engaged on
    Linux, while on case-INSENSITIVE macOS the folded lookup traversed the
    symlink anyway and the guard fired — the test passed locally by
    filesystem accident. Joining the name AS GIVEN makes the guard depend on
    the symlink alone, identical on every filesystem. (The allowlist is
    charset-only, so validation is case-free and unchanged.) Comparison-level
    folding survives where it belongs: the resolver's CANDIDATE comparison in
    _context_team_candidates, whose names are platform-minted lowercase.
    """
    try:
        if not isinstance(name, str):
            return None
        stripped = name.strip()
        if not stripped or not is_safe_path_component(stripped):
            return None
        base_resolved = base.resolve()
        team_dir = base / stripped
        team_dir.resolve().relative_to(base_resolved)
        return team_dir
    except (OSError, ValueError, TypeError):
        return None


def task_dir_for(team_name: object, tasks_base_dir: str | Path | None = None) -> Path | None:
    """Construction-only join of ``team_name`` under the tasks root.

    The explicit-name form of the seam for callers that hold their OWN
    team name from a non-context SSOT (task_claim_gate resolves the registry
    @team half, which may legitimately differ from the context candidates).
    Validates + joins + anchors via ``_team_dir_under``; makes NO existence
    claim — writers mkdir on top of the returned dir exactly as they did with
    their local builds. ``None`` when the name is unusable (fail-open).
    """
    try:
        base = (
            Path(tasks_base_dir)
            if tasks_base_dir
            else (get_claude_config_dir() / "tasks")
        )
        return _team_dir_under(base, team_name)
    except (OSError, ValueError):
        return None


def _context_team_candidates() -> tuple[str, str]:
    """The aligned and persisted team names from the context cache, folded.

    Returns ``(aligned, persisted)`` — aligned from get_team_name() (the
    identity-matched resolver; "" on its fail-closed paths), persisted from
    get_pact_context()["team_name"]. Both lowercased/stripped; "" when the
    cache is uninitialised. Never raises. Candidate ORDER (aligned first) is
    one-directional: resolution may only UPGRADE from the persisted name to
    the identity-matched one, never pick a name the resolver distrusts.

    Accesses the context through the MODULE (late-bound
    ``shared.pact_context.<name>``), not from-imported names: the test
    harnesses patch the pact_context module's attributes (e.g.
    ``patch.object(emitter.pact_context, "get_pact_context", ...)``), and a
    from-imported binding here would silently dodge every such patch. The
    explicit-name paths (get_task_list) keep their from-imported bindings —
    those are patched at the task_utils attribute in tests, by design.
    """
    try:
        aligned = (_pact_context_module.get_team_name() or "").strip().lower()
    except Exception:
        aligned = ""
    try:
        raw = _pact_context_module.get_pact_context().get("team_name", "")
        persisted = raw.strip().lower() if isinstance(raw, str) else ""
    except Exception:
        persisted = ""
    return aligned, persisted


def _resolve_live_team_dir(root: Path) -> Path | None:
    """Resolve the LIVE team dir under ``root`` from context candidates.

    Resolution order (existence-only — first existing dir wins):
      1. the aligned name (get_team_name()),
      2. the persisted context name,
      3. the session-id dir (the config-less UUID-keyed store), if it exists.
    BOTH-DISAGREE fails CLOSED: when the aligned and persisted names differ
    and BOTH name existing dirs under ``root``, the substrate is ambiguous
    (a stale team lingers beside the live one) and picking either would
    silently address the wrong store — return None and let consumers
    suppress, mirroring bootstrap_marker_writer's ambiguity suppression.
    Never raises; None when no candidate resolves.
    """
    try:
        aligned, persisted = _context_team_candidates()
        if aligned and persisted and aligned != persisted:
            first = _team_dir_under(root, aligned)
            second = _team_dir_under(root, persisted)
            if (
                first is not None
                and second is not None
                and first.is_dir()
                and second.is_dir()
            ):
                return None  # ambiguous substrate — fail closed, consumers suppress
        seen: set[str] = set()
        for candidate in (aligned, persisted, _pact_context_module.get_session_id()):
            if not isinstance(candidate, str):
                continue
            folded = candidate.strip().lower()
            if not folded or folded in seen:
                continue
            seen.add(folded)
            team_dir = _team_dir_under(root, folded)
            if team_dir is not None and team_dir.is_dir():
                return team_dir
        return None
    except Exception:
        return None


def resolve_team_dir(teams_dir: str | None = None) -> Path | None:
    """Resolve the live ``teams/{team}`` dir from context candidates.

    Aligned-first, one-directional, existence-only, never raises. ``None``
    when nothing resolves (callers fail open — absence of a team dir is the
    existing "no team state" signal, not an error). ``teams_dir`` mirrors the
    ``_resolve_aligned_team_name`` override seam (testing only).
    """
    try:
        base = Path(teams_dir) if teams_dir else (get_claude_config_dir() / "teams")
        return _resolve_live_team_dir(base)
    except (OSError, ValueError):
        return None


def resolve_task_dir(tasks_base_dir: str | Path | None = None) -> Path | None:
    """Resolve the live ``tasks/{team}`` dir from context candidates.

    The #1020 fix's read side: replaces raw ``get_pact_context()["team_name"]``
    reads that addressed ``tasks/session-<id8>/`` while the store lived under
    the full session UUID. Same resolution order and BOTH-DISAGREE fail-closed
    cell as ``resolve_team_dir``; ``tasks_base_dir`` mirrors task_utils' own
    override idiom (``Path(tasks_base_dir) if tasks_base_dir else ...``).
    Existence-only: writability is never probed (an explicit opt-in would be
    added only when a writer wants fail-fast — none does today).
    """
    try:
        base = (
            Path(tasks_base_dir)
            if tasks_base_dir
            else (get_claude_config_dir() / "tasks")
        )
        return _resolve_live_team_dir(base)
    except (OSError, ValueError):
        return None


def iter_team_task_jsons(
    team_name: str,
    tasks_base_dir: str | None = None,
) -> Iterator[dict]:
    """
    Yield parsed task JSON dicts from ~/.claude/tasks/{team_name}/*.json.

    Path-traversal defense (single source of truth for per-team task
    iteration), re-expressed on the task_dir_for containment seam:
      - team_name validated via is_safe_path_component (positive
        allowlist; rejects empty, non-string, traversal fragments,
        separators, controls, whitespace) INSIDE task_dir_for, with the
        resolve/relative_to symlink-escape anchor — one containment
        implementation, not a per-caller copy.
    NO candidate fallthrough: this iterator addresses exactly
    tasks/{team_name}/ as passed (callers holding their own name keep their
    semantics); context-candidate resolution is resolve_task_dir's job.

    Args:
        team_name: Team identifier; the {team_name} path component.
        tasks_base_dir: Override for the ~/.claude/tasks base dir (testing
            only; production default None resolves the real home path). The
            symlink-escape relative_to assertion still anchors to this base,
            so the path-traversal defense holds for any base. Mirrors the
            institutional test seam in _read_task_counts / read_task_json.

    Pure generator; never raises. Yields nothing on any error
    (unsafe team_name, missing dir, symlink escape, IO error). Individual
    unreadable / unparseable task files are skipped silently. Callers
    treat "no tasks" as fail-open — wake mechanism degrades to baseline
    idle-poll rather than crashing the hook.

    Yields:
        dict per successfully-parsed task JSON file.
    """
    team_dir = task_dir_for(team_name, tasks_base_dir=tasks_base_dir)
    if team_dir is None or not team_dir.is_dir():
        return
    try:
        for task_file in team_dir.glob("*.json"):
            # Exclude dotfile-prefixed JSON files: pathlib glob includes
            # them, but the platform's task system never writes them.
            # An attacker dropping one into the team's tasks dir could
            # otherwise inflate the active-task count.
            if task_file.name.startswith("."):
                continue
            # Per-file symlink defense: glob() returns symlink entries
            # as their raw paths. Even though resolved_team_dir is
            # asserted under tasks_root, a symlink inside it could
            # resolve outside. Skip symlinks silently — the platform
            # task system writes only regular files.
            try:
                if task_file.is_symlink():
                    continue
            except OSError:
                continue
            try:
                content = task_file.read_text(encoding="utf-8")
                task = json.loads(content)
            except (IOError, OSError, json.JSONDecodeError):
                continue
            if isinstance(task, dict):
                yield task
    except (IOError, OSError):
        return


def read_task_json(
    task_id: str,
    team_name: str | None,
    tasks_base_dir: str | None = None,
) -> dict:
    """
    Read the raw task JSON from disk, safe against path traversal.

    Locates the task file in the team directory first, then falls back to
    the base directory. Hoisted from handoff_gate.py so non-hook callers
    (agent_handoff_emitter.py) can consume the same reader.

    Args:
        task_id: Task identifier. Sanitized to strip `/`, `\\`, and `..`.
        team_name: Team name for scoped task lookup (may be None).
        tasks_base_dir: Override for tasks base directory (testing).

    Returns:
        Full task dict from the JSON file, or empty dict on any failure
        (missing file, malformed JSON, IO error). Fail-open by design —
        callers treat "no task data" as a signal to bypass, not crash.
    """
    if not task_id:
        return {}

    task_id = re.sub(r'[/\\]|\.\.', '', task_id)
    if not task_id:
        return {}

    if tasks_base_dir is None:
        tasks_base_dir = str(get_claude_config_dir() / "tasks")

    base = Path(tasks_base_dir)

    task_dirs = []
    # Team-dir lookup routes through the task_dir_for containment seam: the
    # is_safe_path_component allowlist (guard 1) and the resolve/relative_to
    # symlink-escape anchor (guard 2) all live inside the seam — one
    # containment implementation — joining the name AS GIVEN (no case-fold;
    # see _team_dir_under's OS-invariance note: a folded join addressed a
    # DIFFERENT store on case-sensitive filesystems). A mixed-case name now
    # reads the dir it names, where the historical pre-seam fold read the
    # lowercased one — divergence only for mixed-case names, which
    # generate_team_name never mints. An unsafe team_name yields None here,
    # skipping the team dir and falling through to the bare-base read,
    # preserving the fail-open {} contract. Only the single-named-file read
    # semantics stay local (this reader reads ONE file, never globs, so the
    # glob-result hygiene guards are correctly absent).
    team_dir = task_dir_for(team_name, tasks_base_dir=tasks_base_dir)
    if team_dir is not None:
        task_dirs.append(team_dir)
    task_dirs.append(base)

    for task_dir in task_dirs:
        task_file = task_dir / f"{task_id}.json"
        # M2 (security): exists() is INSIDE the try and ValueError is caught.
        # A NUL byte in task_id that slips past the sanitizer raises
        # ValueError('embedded null byte') from the path syscall in exists();
        # catching it degrades to the fail-open {} (same as a missing/malformed
        # file) instead of propagating to a caller's catch-all — e.g. the
        # lifecycle gate, where an uncaught exception skips rule enforcement for
        # the turn (advisory-suppression DoS).
        try:
            if task_file.exists():
                return json.loads(task_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError, ValueError):
            return {}

    return {}


# Canonical Teachback Task subject pattern: `<teammate-name>: TEACHBACK
# <mission descriptor>`, normally `TEACHBACK for …`. The leading
# `[a-z0-9-]+:` is the canonical teammate-prefix shape used across the
# plugin (matches names like `backend-coder-2`, `secretary`,
# `architect-1`); `TEACHBACK` is the canonical mission-framing marker per
# pact-completion-authority.md.
#
# WHY a structural match instead of substring `"teachback" in subject`:
# the substring form fires on ANY task subject containing the word —
# including planning/discussion subjects like `"Plan: wake-lifecycle
# teachback re-arm fix"` — and produces benign-but-noisy false-positive
# advisories. The structural match pins the pattern to the canonical
# Teachback-Gated Dispatch shape so only actual teachback tasks trip
# the rules.
#
# HOISTED here (from task_lifecycle_gate.py) so a second hook surface — the
# handoff_ordering_gate PreToolUse WARN — can reuse the same predicate without
# importing the sibling PostToolUse gate module (a coupling smell + import-time
# side-effect risk). SINGLE definition: the lifecycle gate re-imports it from
# here, so the regex is never duplicated (duplication would reintroduce the
# drift class the structural match was introduced to close).
#
# The tail is a WORD BOUNDARY, not the literal `TEACHBACK for `. The stricter
# trailing-space form silently missed real gates that qualify the marker before
# the mission — `preparer: TEACHBACK (respawn) for …` escaped the carve-out
# entirely. Widening keeps every subject the strict form matched (it is a
# superset) and admits qualified spellings; a work task titled
# `<name>: TEACHBACK…` now reads as a gate, which drops it from the dispatch
# population — the benign direction, and the reason a lossy-but-safe rule is
# preferred here over a precise one that can fail harmfully. `\b` still refuses
# `TEACHBACKS`/`TEACHBACKING`, so the marker must stand as its own word.
_TEACHBACK_SUBJECT_PATTERN = re.compile(r"^[a-z0-9-]+: TEACHBACK\b")


def is_teachback_subject(subject: str) -> bool:
    """Return True iff `subject` matches the canonical Teachback Task
    shape (`<teammate-name>: TEACHBACK <mission>`, normally
    `TEACHBACK for <mission>`; a qualifier between the marker and the
    mission — `TEACHBACK (respawn) for …` — still matches).

    Pure function; never raises. Returns False on non-string input or
    any subject that does not match the anchored pattern. Replaces the
    legacy `"TEACHBACK" in subject_upper` substring check across the
    gate's TaskCreate and TaskUpdate rule paths.
    """
    if not isinstance(subject, str):
        return False
    return _TEACHBACK_SUBJECT_PATTERN.match(subject) is not None
