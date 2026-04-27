"""
Location: pact-plugin/skills/pact-memory/scripts/pact_session.py
Summary: Shared session context helpers for pact-memory skill scripts.
Used by: memory_api.py, memory_init.py

Provides a single implementation of the context file reader so that
memory_api.py and memory_init.py don't each define their own copy.

The context file is written once per session by session_init.py and
read by all subsequent hooks and skill scripts.

Note: hooks/shared/pact_context.py has the authoritative implementation.
This module mirrors the path logic (using Path(project_dir).name for the
slug) because skill scripts can't import from the hooks package (different
Python package boundary).
"""

import json
import os
from pathlib import Path


def _context_file_path(session_id: str, project_dir: str) -> Path | None:
    """Return the path to the PACT session context file.

    Computed dynamically (not cached at import time) so that tests can
    monkeypatch Path.home() before calling get_session_id_from_context_file().

    Returns the session-scoped path when both identifiers are provided:
        ~/.claude/pact-sessions/{project-slug}/{session-id}/pact-session-context.json
    where project-slug is Path(project_dir).name (e.g., "PACT-Plugin").

    Returns None when either identifier is missing — callers should treat
    this as "no context file available" and return a safe default.

    Must match hooks/shared/pact_context.py path logic.

    Note: hooks/shared/pact_context.py uses init() + _context_path for the
    same purpose (testable there because hooks call init() after parsing stdin).
    """
    if session_id and project_dir:
        slug = Path(project_dir).name
        return (
            Path.home() / ".claude" / "pact-sessions"
            / slug / session_id / "pact-session-context.json"
        )
    return None


def get_session_id_from_context_file(
    session_id: str = "",
    project_dir: str = "",
) -> str:
    """
    Read session_id from the PACT session context file.

    The context file is written at session start by session_init.py.
    This is the primary source for session ID in skill scripts that
    run outside the hooks package.

    Args:
        session_id: If known, used to locate the session-scoped context file.
                    Required for path computation (no legacy fallback).
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
        return ""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("session_id", "")
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError, AttributeError):
        return ""
