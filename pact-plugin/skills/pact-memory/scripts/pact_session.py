"""
Location: pact-plugin/skills/pact-memory/scripts/pact_session.py
Summary: Shared session context helpers for pact-memory skill scripts.
Used by: memory_api.py, memory_init.py

Provides a single implementation of the context file reader so that
memory_api.py and memory_init.py don't each define their own copy.

The context file is written once per session by session_init.py and
read by all subsequent hooks and skill scripts.

Note: hooks/shared/pact_context.py has the authoritative implementation.
This module IMPORTS the slug derivation and the config-root resolver rather
than re-implementing them (see the bootstrap below).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# A direct-script invocation (`python3 .../scripts/cli.py`) puts only this
# directory on sys.path, so a bare `from shared... import` raises
# ModuleNotFoundError. parents[3] is the plugin root; its hooks/ dir holds the
# ONE config-root resolver, which this module reaches through the shared session
# helpers below. Importing them beats re-implementing them: a local copy has
# drifted from the authoritative one before.
#
# NO try/except fallback here, deliberately. A fallback would silently rebuild
# the local copy this import replaced, turning a loud startup failure into a
# session-state split across two config roots that nothing reports.
#
# APPEND, not insert(0): this path stays on sys.path for the rest of the
# process, and hooks/ holds ~29 importable names. Prepending would let a future
# hooks/config.py or hooks/database.py shadow this package's own modules of
# those names. Appending cannot — nothing else on the path provides `shared`.
sys.path.append(str(Path(__file__).resolve().parents[3] / "hooks"))

from shared.pact_context import (  # noqa: E402  # requires the sys.path bootstrap above
    _build_session_path,
    project_slug,
)
from shared.project_scope import _session_record_on_disk  # noqa: E402  # requires the sys.path bootstrap above
# Re-exported, never wrapped: the working-memory guard and archive_pin must hold one reader.
from shared.project_scope import get_worktree_identity_from_session_record  # noqa: E402, F401  # re-export
# The session-record rung lives in project_scope, so the anchor every CLAUDE.md
# site uses and this module's readers are one function; re-exported, never wrapped.
from shared.project_scope import (  # noqa: E402  # requires the sys.path bootstrap above
    recorded_project_dir as get_project_dir_from_session_record,
)


def _context_file_path(session_id: str, project_dir: str) -> Path | None:
    """Return the path to the PACT session context file.

    Computed dynamically (not cached at import time) so that tests can
    monkeypatch Path.home() before calling get_session_id_from_context_file().

    Returns the session-scoped path when both identifiers are provided:
        <config-root>/pact-sessions/{project-slug}/{session-id}/pact-session-context.json
    built by the writers' own _build_session_path, so the slug
    (project_slug(project_dir), the resolved directory's basename) and the
    session id carry the same sanitisation the writer applied.

    Returns None when either identifier is missing — callers should treat
    this as "no context file available" and return a safe default.

    Must match hooks/shared/pact_context.py path logic.

    Note: hooks/shared/pact_context.py uses init() + _context_path for the
    same purpose (testable there because hooks call init() after parsing stdin).
    """
    if session_id and project_dir:
        return (
            _build_session_path(project_slug(project_dir), session_id)
            / "pact-session-context.json"
        )
    return None


_DISCOVERY_UNSET = object()
# Cached RESULT OF THE FILESYSTEM LOOKUP only, plus the session id it was
# resolved for. Deliberately NOT a cache of the function's return value: the
# guards below must be re-evaluated on every call, because a cached answer must
# never outlive the conditions that permitted it.
_discovered_session_id = _DISCOVERY_UNSET
_discovered_for_env = None


def _context_record_on_disk(env_session: str) -> dict:
    """Find the one context file naming this session id, and parse it.

    The shared glob+parse half of discovery, split out so the session-id
    reader and the project_dir reader share one derivation (and one set of
    failure modes) instead of drifting into two. The glob is
    `shared.project_scope._session_record_on_disk`, which the worktree record
    reader shares; its docstring lists the failures that return {}.
    """
    return _session_record_on_disk(env_session, "pact-session-context.json")


def _resolve_context_on_disk(env_session: str) -> str:
    """Find the one context file naming this session id, and read the id back.

    The expensive half of session-id discovery, split out so it can be cached
    without the guards being cached with it.
    """
    found = _context_record_on_disk(env_session).get("session_id", "")
    return found if isinstance(found, str) else ""


def _discover_session_id() -> str:
    """Find this session's id without already knowing it.

    The caller normally has no session id, and the context file that holds one
    is stored under a directory named after it — so the id cannot be read from
    the path that requires it. This breaks that circle by taking the id from
    the environment and confirming it against exactly one context file on disk.

    Returns the empty string whenever the answer is not unambiguous. Every
    failure is silent and lands the caller on its own degraded fallback.
    """
    # A test process inherits the developer's real session id from the
    # environment. Resolving it here would let the suite compute paths that
    # belong to a live session and write to them, so refuse before reading it.
    # Bare variable, not the sys.modules narrowing used elsewhere: both spawned
    # children and in-process runs must be caught.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""

    env_session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not env_session:
        return ""

    # Cache the filesystem lookup ONLY, and key it on the id it was resolved
    # for. Both guards above have already run, so a cached value can never be
    # returned to a caller the guards would now refuse.
    global _discovered_session_id, _discovered_for_env
    if _discovered_session_id is _DISCOVERY_UNSET or _discovered_for_env != env_session:
        _discovered_session_id = _resolve_context_on_disk(env_session)
        _discovered_for_env = env_session
    return _discovered_session_id


class ProjectScopeDisagreementError(RuntimeError):
    """A WRITE was refused because CLAUDE_PROJECT_DIR and the session record
    name different project directories.

    Raised on write paths only (backlog set, memory save, working-memory
    sync). READS follow the env value: deliberate per-command cross-scope
    inspection is legitimate, but a write under a disagreed scope is the
    silent mis-scope this family of issues pays for. The message carries BOTH
    values and the remedy.
    """


def env_record_project_dir_disagreement() -> tuple[str, str] | None:
    """Return (env_value, record_value) when both are present AND disagree.

    None when either side is absent (nothing to disagree with) or the two
    match. Comparison is normcase(normpath(...)) string equality, NOT
    resolved-path equality: the record holds the platform's value verbatim so
    textual equality is the invariant by construction, while normpath
    collapses a trailing slash or '.' segments. Resolving would DERIVE (a
    symlinked alias would pass), and derivation is what the verbatim rule
    exists to avoid — a disagreeing write must refuse, not be reconciled.

    ONE ADMITTED DISAGREEMENT, AND IT IS PROVEN BY IDENTITY, NOT BY TEXT. A
    worktree session's writer resolves the MAIN checkout and pins the child's
    CLAUDE_PROJECT_DIR to it; the record holds the worktree path, so the two
    differ for every worktree session. When the worktree-identity record for
    the RECORDED declaration verifies that MAIN root, the two describe one
    project and this returns None. It is not reconciliation: the record must
    name the declared directory exactly and git must verify the candidate
    root, so a symlinked or case-differing spelling still refuses, and a
    foreign or unverified record still refuses.
    """
    env_value = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not env_value:
        return None
    record_value = get_project_dir_from_session_record()
    if not record_value:
        return None
    if project_dir_disagreement_kind(env_value, record_value) is None:
        return None
    return env_value, record_value


def _record_path_is_canonicalisable(value: str) -> bool:
    """Whether the recorded declaration is a path the OS will canonicalise.

    A corrupt session record can carry an embedded NUL, which makes
    `os.path.realpath` raise ValueError. The `os.path.isdir` test below
    swallows that and reports False, so without this check a CORRUPT record
    is classified "removed" and the refusal tells the user the directory no
    longer exists when it was never a usable path.
    """
    try:
        os.path.realpath(value)
        return True
    except (OSError, ValueError):
        return False


def project_dir_disagreement_kind(env_value: str, record_value: str) -> str | None:
    """Classify an env/record pair ONCE, for the refusal and for its text.

    ONE DECISION, TWO RENDERERS. `env_record_project_dir_disagreement`
    refuses on a non-None answer and `format_project_dir_disagreement` picks
    the remedy from the same answer, so the wording cannot congratulate a
    user for a refusal that did not happen, or explain a refusal with another
    cause's advice.

    None       -- one project: textually equal, or a VERIFIED worktree retarget
                  (the writer pinned MAIN, the record names the worktree).
    "removed"  -- they disagree, and the recorded directory no longer exists,
                  so re-exporting it is not a remedy.
    "corrupt"  -- they disagree and the recorded value cannot be
                  canonicalised (a malformed record), so it is neither
                  "removed" nor a live directory and re-exporting it cannot
                  help. Checked BEFORE "removed": `os.path.isdir` reports
                  False for a malformed path, which would otherwise name the
                  wrong cause.
    "textual"  -- they disagree and the recorded directory still exists.
    """
    env_norm = os.path.normcase(os.path.normpath(env_value))
    record_norm = os.path.normcase(os.path.normpath(record_value))
    if env_norm == record_norm:
        return None
    if _env_is_the_recorded_checkouts_main_root(env_norm, record_value):
        return None
    if not _record_path_is_canonicalisable(record_value):
        return "corrupt"
    return "removed" if not os.path.isdir(record_value) else "textual"


def _env_is_the_recorded_checkouts_main_root(
    env_norm: str, record_value: str
) -> bool:
    """Whether `env_norm` is the MAIN root the worktree record proves for the
    recorded declaration.

    The record must be for this declaration and its candidate root must
    verify (`recorded_main_root` checks both), so a planted or stale record
    cannot turn a disagreement into a pass. Any failure is a disagreement.
    """
    try:
        from shared.project_scope import (
            get_worktree_identity_from_session_record,
            recorded_main_root,
        )

        declared = Path(record_value)
        identity = get_worktree_identity_from_session_record()
        main = recorded_main_root(declared, identity)
        if main is None:
            return False
        return os.path.normcase(os.path.normpath(str(main))) == env_norm
    except Exception:  # noqa: BLE001 — cannot verify means disagreement
        return False


def format_project_dir_disagreement(env_value: str, record_value: str) -> str:
    """The ONE refusal text every write path raises on an env/record
    disagreement — both values and the remedy, so the refusal is a visible
    decision instead of a silent winner.

    A REMOVED RECORDED DIRECTORY GETS A DIFFERENT REMEDY. Telling the user to
    re-export the recorded value is a dead end when that directory no longer
    exists — the same class as a directive that sends them to a command which
    refuses. A directory that still exists keeps the actionable advice.

    THE BRANCH IS THE SAME DECISION THE REFUSAL USED (`kind`), so the text and
    the verdict cannot disagree about which case this is.
    """
    kind = project_dir_disagreement_kind(env_value, record_value)
    if kind == "corrupt":
        remedy = (
            "The recorded directory is not a usable path — the session record "
            "is malformed — so re-exporting it cannot help: run without the "
            "override, or start a fresh session so the record is rewritten. "
        )
        spelling_note = (
            "The recorded value is malformed rather than merely spelled "
            "differently, so no re-spelling of it can be compared."
        )
    elif kind == "removed":
        remedy = (
            "The recorded directory no longer exists, so re-exporting it is "
            "not possible: run without the override, or start the session "
            "from the project's main checkout. "
        )
        spelling_note = (
            "The comparison is textual (normcase/normpath, not resolved): a "
            "symlinked or case-differing spelling of the same directory refuses "
            "although identical, and re-exporting the recorded value is the "
            "remedy for that spelling."
        )
    else:
        remedy = (
            "Run without the override, or re-export CLAUDE_PROJECT_DIR to "
            "the recorded value. "
        )
        spelling_note = (
            "The comparison is textual (normcase/normpath, not resolved): a "
            "symlinked or case-differing spelling of the same directory refuses "
            "although identical, and re-exporting the recorded value is the "
            "remedy for that spelling."
        )
    return (
        f"CLAUDE_PROJECT_DIR ({env_value}) disagrees with this session's "
        f"recorded project directory ({record_value}); the write was refused "
        f"rather than scoped silently. Nothing was written. {remedy}"
        f"{spelling_note}"
    )


def get_session_id_from_context_file(
    session_id: str = "",
    project_dir: str = "",
) -> str:
    """
    Read session_id from the PACT session context file.

    The context file is written at session start by session_init.py.
    This is the primary source for session ID in skill scripts that
    run outside the hooks package.

    When the caller supplies both identifiers the named context file is read
    directly. When it supplies neither — which is every production call — the
    id is discovered from the environment instead, because the path of the file
    holding the id is itself named after the id.

    Args:
        session_id: If known, used to locate the session-scoped context file.
        project_dir: If known, used with session_id to locate the context file.
                     When empty, falls back to CLAUDE_PROJECT_DIR env var.

    Returns:
        Session ID string, or empty string if unavailable
    """
    # Resolve project_dir from env var if not provided;
    # session_id comes only from the caller or the context file itself.
    resolved_session = session_id
    resolved_project = project_dir or os.environ.get("CLAUDE_PROJECT_DIR", "")

    # Compute session-scoped path (requires both identifiers)
    path = _context_file_path(resolved_session, resolved_project)
    if path is None:
        # Call discovery EVERY time. Caching here instead would return a stored
        # answer without re-running the guards inside it, so a process that had
        # once resolved an id would keep handing it out after the conditions
        # that permitted it had gone. The expensive filesystem work is cached
        # inside _discover_session_id, below its guards.
        return _discover_session_id()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("session_id", "")
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError, AttributeError):
        return ""
