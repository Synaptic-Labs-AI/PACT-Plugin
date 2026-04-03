"""
Location: pact-plugin/hooks/shared/pact_context.py

Shared session context module for PACT hooks.

Provides session identity (team_name, session_id, project_dir) and agent
name resolution for all hooks. Context is written once at SessionStart
by session_init.py and read by subsequent hooks via init() + accessors.

See: docs/architecture/pact-context-module.md for full design rationale.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Session-scoped context file path, set by init().
# When None, get_pact_context() returns _EMPTY_CONTEXT (no file to read).
# Note: pact_session.py (in skills/pact-memory/scripts/) mirrors this logic
# with a dynamic _context_file_path() function because skill scripts can't
# import from hooks/shared/.
_context_path: Path | None = None

# Module-level cache: populated on first get_pact_context() call.
# Safe because the context file is write-once and each hook invocation
# is a fresh Python process (new module state = clean cache).
_cache: dict | None = None

# Default context dict returned on any error
_EMPTY_CONTEXT = {
    "team_name": "",
    "session_id": "",
    "project_dir": "",
    "started_at": "",
}


def _get_context_file_path() -> Path | None:
    """Return the session-scoped context file path, or None if init() not called.

    When None, get_pact_context() returns _EMPTY_CONTEXT without attempting
    any file I/O. There is no fallback to a global path — all reads require
    a session-scoped path established by init().
    """
    return _context_path


def init(input_data: dict) -> None:
    """
    Initialize the context module with session-scoped path.

    Must be called by each hook after parsing stdin JSON. Extracts session_id
    from input_data and CLAUDE_PROJECT_DIR from environment to construct the
    session-scoped context file path:
        ~/.claude/pact-sessions/{project-slug}/{session-id}/pact-session-context.json

    Where project-slug is Path(project_dir).name (e.g., "PACT-prompt").

    If session_id or project_dir is unavailable, leaves _context_path as None.
    Readers will return _EMPTY_CONTEXT without attempting any file I/O.

    No-op if _context_path is already set (e.g., by a test fixture or a prior
    init() call within the same process).

    Args:
        input_data: Parsed stdin JSON from the hook
    """
    global _context_path, _cache

    # Skip if already initialized (test fixtures pre-set _context_path)
    if _context_path is not None:
        return

    session_id = ""
    raw_id = input_data.get("session_id")
    if raw_id:
        session_id = str(raw_id)

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")

    if session_id and project_dir:
        slug = Path(project_dir).name
        _context_path = (
            Path.home() / ".claude" / "pact-sessions"
            / slug / session_id / "pact-session-context.json"
        )
        # Clear cache so subsequent reads use the new path
        _cache = None
    # else: leave _context_path as None — readers return _EMPTY_CONTEXT


def get_pact_context() -> dict:
    """
    Read session context from the context file.

    Returns dict with keys: team_name, session_id, project_dir, started_at.
    All values are strings. Returns empty strings for all keys on any error
    (file missing, malformed JSON, permission denied).

    Caching: Result is cached in a module-level variable after first read.
    The file is write-once/read-many, so caching is safe within a single
    hook process lifetime.
    """
    global _cache
    if _cache is not None:
        return _cache

    ctx_path = _get_context_file_path()
    if ctx_path is None:
        # init() was not called or session_id/project_dir unavailable —
        # no file to read. Return empty context without logging (this is
        # normal for hooks that run before session_init writes the file).
        _cache = dict(_EMPTY_CONTEXT)
        return _cache

    try:
        data = json.loads(ctx_path.read_text(encoding="utf-8"))
        _cache = {
            "team_name": str(data.get("team_name", "")),
            "session_id": str(data.get("session_id", "")),
            "project_dir": str(data.get("project_dir", "")),
            "started_at": str(data.get("started_at", "")),
        }
        return _cache
    except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
        print(
            f"pact_context: could not read context file: {e}",
            file=sys.stderr,
        )
        _cache = dict(_EMPTY_CONTEXT)
        return _cache


def get_team_name() -> str:
    """Convenience: return team_name from context, lowercased. Empty string on error."""
    return get_pact_context().get("team_name", "").lower()


def get_session_id() -> str:
    """Convenience: return session_id from context. Empty string on error."""
    return get_pact_context().get("session_id", "")


def get_project_dir() -> str:
    """Convenience: return project_dir from context. Empty string on error."""
    return get_pact_context().get("project_dir", "")


def get_session_dir() -> str:
    """Return the session-scoped directory path, or '' if unavailable.

    Constructs: ~/.claude/pact-sessions/{slug}/{session_id}/

    Uses get_session_id() and get_project_dir() from the cached context.
    Returns "" if either is unavailable.

    The returned path may not exist on disk — callers must create it
    (mkdir -p) before writing files.
    """
    session_id = get_session_id()
    project_dir = get_project_dir()
    if not session_id or not project_dir:
        return ""
    slug = Path(project_dir).name
    return str(
        Path.home() / ".claude" / "pact-sessions" / slug / session_id
    )


def resolve_agent_name(
    input_data: dict,
    team_name: str | None = None,
    teams_dir: str | None = None,
) -> str:
    """
    Resolve the human-readable agent name from hook stdin JSON.

    Resolution chain:
    1. input_data["agent_name"] — if present, use directly
    2. input_data["agent_id"] string split — if contains "@", split and
       return the name part (format: "name@team_name")
    3. input_data["agent_id"] → lookup in team config members array
    4. input_data["agent_type"] → strip "pact-" prefix as fallback name
    5. "" — unknown agent (main process, non-PACT context)

    Args:
        input_data: Parsed stdin JSON from the hook
        team_name: Override team name (defaults to get_team_name())
        teams_dir: Override teams directory path (for testing)

    Returns:
        Agent name string, or "" if unresolvable
    """
    # Step 1: direct agent_name field
    agent_name = input_data.get("agent_name")
    if agent_name:
        return str(agent_name)

    # Step 2: agent_id string split (common case — avoids file I/O)
    agent_id = input_data.get("agent_id")
    if agent_id and "@" in str(agent_id):
        return str(agent_id).split("@")[0]

    # Step 3: agent_id → team config lookup (fallback for non-@ formats)
    if agent_id:
        resolved_team = team_name if team_name else get_team_name()
        if resolved_team:
            name = _lookup_agent_in_team_config(
                str(agent_id), resolved_team, teams_dir
            )
            if name:
                return name

    # Step 4: agent_type → strip "pact-" prefix
    agent_type = input_data.get("agent_type")
    if agent_type:
        type_str = str(agent_type)
        if type_str.startswith("pact-"):
            return type_str[len("pact-"):]
        return type_str

    # Step 5: unresolvable
    return ""


def _lookup_agent_in_team_config(
    agent_id: str,
    team_name: str,
    teams_dir: str | None = None,
) -> str:
    """
    Look up agent name from team config file.

    Reads ~/.claude/teams/{team_name}/config.json and scans the members[]
    array for an entry where member["id"] == agent_id.

    Args:
        agent_id: The agent UUID to look up
        team_name: Team name for config path
        teams_dir: Override teams directory (for testing)

    Returns:
        Agent name if found, empty string otherwise
    """
    try:
        if teams_dir:
            config_path = Path(teams_dir) / team_name / "config.json"
        else:
            config_path = (
                Path.home() / ".claude" / "teams" / team_name / "config.json"
            )

        data = json.loads(config_path.read_text(encoding="utf-8"))
        members = data.get("members", [])
        if not isinstance(members, list):
            return ""

        for member in members:
            if isinstance(member, dict) and member.get("id") == agent_id:
                return str(member.get("name", ""))

        return ""
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
        print(
            f"pact_context: could not read team config: {e}",
            file=sys.stderr,
        )
        return ""


def write_context(
    team_name: str,
    session_id: str,
    project_dir: str,
) -> None:
    """
    Write the session context file. Called ONLY by session_init.py.

    Computes the session-scoped path from session_id and project_dir:
        ~/.claude/pact-sessions/{project-slug}/{session-id}/pact-session-context.json
    Requires session_id and project_dir — returns without writing if either
    is missing (the fail-open read behavior handles the no-file case).

    Uses atomic write (write to temp file, then os.rename) for crash safety.
    File permissions: 0o600 (user-only read/write).

    Also sets _context_path so subsequent reads in the same process use the
    correct path (relevant for session_init.py which may read context after writing).

    Args:
        team_name: The generated team name (e.g., "pact-0001639f")
        session_id: Session ID from stdin JSON or env var
        project_dir: CLAUDE_PROJECT_DIR value
    """
    global _context_path, _cache

    context = {
        "team_name": team_name,
        "session_id": session_id,
        "project_dir": project_dir,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # Use _context_path if already set (from init() or test fixture),
    # otherwise compute from session_id and project_dir.
    if _context_path is not None:
        target = _context_path
    elif session_id and project_dir:
        slug = Path(project_dir).name
        target = (
            Path.home() / ".claude" / "pact-sessions"
            / slug / session_id / "pact-session-context.json"
        )
    else:
        # Cannot compute session-scoped path — skip writing.
        # Readers fall back to empty context via _EMPTY_CONTEXT.
        print(
            "pact_context: skipping write — session_id or project_dir unavailable",
            file=sys.stderr,
        )
        return

    # Update module state so reads in the same process find the file
    _context_path = target
    _cache = None

    context_dir = target.parent
    try:
        context_dir.mkdir(parents=True, exist_ok=True)

        # Write to temp file in the same directory (required for atomic rename)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(context_dir),
            prefix=".pact-session-context-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(context, f)
            os.chmod(tmp_path, 0o600)
            os.rename(tmp_path, str(target))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(
            f"pact_context: could not write context file: {e}",
            file=sys.stderr,
        )
