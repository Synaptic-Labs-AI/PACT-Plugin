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
# directory on sys.path, so a bare `from shared.paths import ...` raises
# ModuleNotFoundError. parents[3] is the plugin root; its hooks/ dir holds the
# ONE config-root resolver. Importing it beats re-implementing it: a local copy
# has drifted from the authoritative one before.
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

from shared.paths import get_claude_config_dir  # noqa: E402  # requires the sys.path bootstrap above
from shared.pact_context import (  # noqa: E402  # requires the sys.path bootstrap above
    _UNSAFE_SLUG_CHARS_RE,
    _build_session_path,
    project_slug,
)
from shared.project_scope import WORKTREE_IDENTITY_FILE  # noqa: E402  # requires the sys.path bootstrap above


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


def _context_record_on_disk(
    env_session: str, filename: str = "pact-session-context.json"
) -> dict:
    """Find the one session record `filename` under this session id's folder, and parse it.

    The shared glob+parse half of discovery, split out so the session-id
    reader and the project_dir reader share one derivation (and one set of
    failure modes) instead of drifting into two.

    Returns the parsed mapping, or {} on any failure: the glob raised, the
    match count was not exactly one (uniqueness was measured on one machine,
    not guaranteed, so picking the first would be a coin toss over which
    project's session this is), the file did not parse, or the payload was
    not a mapping. Fail-open by posture: callers land on their own fallback.
    """
    try:
        sessions_root = get_claude_config_dir() / "pact-sessions"
        # The writers collapsed unsafe characters in the id; match that name.
        safe_session = _UNSAFE_SLUG_CHARS_RE.sub("_", env_session)
        matches = list(sessions_root.glob(f"*/{safe_session}/{filename}"))
    except OSError:
        return {}

    if len(matches) != 1:
        return {}

    try:
        data = json.loads(matches[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}

    return data if isinstance(data, dict) else {}


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


_discovered_record = _DISCOVERY_UNSET
_discovered_record_for_env = None


def _discover_context_record() -> dict:
    """Return this session's parsed context record, discovered from the env id.

    The record route of the same discovery `_discover_session_id` performs,
    with the SAME two guards (a test process is refused before any read; no
    env id means no answer) and the same cache discipline: the filesystem
    lookup is cached, the guards are re-run on every call. The guards are
    duplicated here rather than shared through `_discover_session_id` because
    that function's cache is pinned by tests that count its calls to
    `_resolve_context_on_disk` — routing id discovery through this record
    cache would let a warm record cache starve that call count.

    Returns {} whenever the answer is not unambiguous.
    """
    # Twin guard pair of _discover_session_id's — keep in sync.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {}
    env_session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not env_session:
        return {}

    global _discovered_record, _discovered_record_for_env
    if _discovered_record is _DISCOVERY_UNSET or _discovered_record_for_env != env_session:
        _discovered_record = _context_record_on_disk(env_session)
        _discovered_record_for_env = env_session
    return _discovered_record


def get_project_dir_from_session_record() -> str:
    """Return this session's recorded project_dir, or "" when unavailable.

    The session-record rung of the project-scope read contract: below
    CLAUDE_PROJECT_DIR (a present declaration wins), ABOVE any git/cwd
    derivation (in a multi-repo workspace the cwd's git root can be the WRONG
    scope; the record is the session's own resolved identity, written by
    session_init at SessionStart).

    Existence is deliberately NOT checked: the value is a scope ANCHOR, and a
    recorded directory deleted between sessions is the caller-resolver's
    fall-through case, not a discovery failure.

    A NON-ABSOLUTE recorded value is rejected. Records written before the
    resolve-once fix could hold "." — resolving that HERE would alias this
    rung to the reader's cwd ABOVE the git rung, inverting the precedence the
    rung exists to establish.

    The record's session_id field is cross-checked against the env id that
    LOCATED the file: a MISMATCH means the globbed record is not this
    session's own (a misfiled or planted record), so it reads as no record.
    An ABSENT field is accepted — legacy records predate the always-written
    field, and the locating glob already matched the env id's directory.

    Returns "" on every failure: no env id, no unique context file, corrupt
    JSON, non-mapping payload, non-string/non-absolute field, session_id
    mismatch. Never raises.
    """
    record = _discover_context_record()
    found = record.get("project_dir", "")
    if not isinstance(found, str) or not os.path.isabs(found):
        return ""
    record_session = record.get("session_id")
    if record_session is not None and (
        not isinstance(record_session, str)
        or record_session != os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    ):
        return ""
    return found


_WORKTREE_IDENTITY_PATHS = ("declared", "worktree", "common_dir")


def get_worktree_identity_from_session_record() -> dict:
    """Return the worktree identity session_init recorded for this session, or {}.

    session_init writes it into the session's own folder, for every role, when
    the session starts inside a linked worktree. The working-memory write guard
    passes it to `stays_in_declared_project`, which reads it only when the
    declared directory no longer exists.

    Discovery is `_context_record_on_disk`'s, behind the same two guards as
    `_discover_context_record`. Nothing is cached.

    Returns {} unless the record's `session_id` equals CLAUDE_CODE_SESSION_ID
    and `declared`, `worktree` and `common_dir` are all absolute path strings.
    Never raises.
    """
    # Twin guard pair of _discover_session_id's -- keep in sync.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {}
    env_session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not env_session:
        return {}
    record = _context_record_on_disk(env_session, WORKTREE_IDENTITY_FILE)
    if record.get("session_id") != env_session:
        return {}
    for key in _WORKTREE_IDENTITY_PATHS:
        value = record.get(key)
        if not isinstance(value, str) or not os.path.isabs(value):
            return {}
    return record


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
    """
    env_value = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not env_value:
        return None
    record_value = get_project_dir_from_session_record()
    if not record_value:
        return None
    env_norm = os.path.normcase(os.path.normpath(env_value))
    record_norm = os.path.normcase(os.path.normpath(record_value))
    if env_norm == record_norm:
        return None
    return env_value, record_value


def format_project_dir_disagreement(env_value: str, record_value: str) -> str:
    """The ONE refusal text every write path raises on an env/record
    disagreement — both values and the remedy, so the refusal is a visible
    decision instead of a silent winner."""
    return (
        f"CLAUDE_PROJECT_DIR ({env_value}) disagrees with this session's "
        f"recorded project directory ({record_value}); the write was refused "
        f"rather than scoped silently. Nothing was written. Run without the "
        f"override, or re-export CLAUDE_PROJECT_DIR to the recorded value. "
        f"The comparison is textual (normcase/normpath, not resolved): a "
        f"symlinked or case-differing spelling of the same directory refuses "
        f"although identical, and re-exporting the recorded value is the "
        f"remedy for that spelling."
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
