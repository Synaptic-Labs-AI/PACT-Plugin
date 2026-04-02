"""
Location: pact-plugin/hooks/shared/s2_state.py
Summary: Atomic read/write/update primitives for .pact/s2-state.json, the shared
         S2 coordination state file that agents read at startup and update as they work.
Used by: orchestrate.md and comPACT.md (seeding), SKILL.md (agent self-coordination),
         s2_conflict_check.py (Phase B), s2_drift_check.py (Phase C).

Concurrency model: fcntl.flock (advisory exclusive lock) + atomic rename for writes.
Follows the proven pattern from file_tracker.py and track_files.py.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
    HAS_FLOCK = True
except ImportError:
    HAS_FLOCK = False


# Default empty state matching the v1 schema
_DEFAULT_STATE = {
    "version": 1,
    "session_team": "",
    "worktree": "",
    "created_at": "",
    "last_updated": "",
    "created_by": "",
    "boundaries": {},
    "conventions": [],
    "scope_claims": {},
    "drift_alerts": [],
}

S2_STATE_FILENAME = "s2-state.json"
S2_DIR_NAME = ".pact"


def _s2_state_path(worktree_path: str) -> Path:
    """Return the path to s2-state.json within a worktree."""
    return Path(worktree_path) / S2_DIR_NAME / S2_STATE_FILENAME


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _ensure_pact_dir(worktree_path: str) -> Path:
    """Ensure .pact/ directory exists and return its path."""
    pact_dir = Path(worktree_path) / S2_DIR_NAME
    pact_dir.mkdir(parents=True, exist_ok=True)
    return pact_dir


def read_s2_state(worktree_path: str) -> dict | None:
    """Read S2 state from .pact/s2-state.json.

    Returns the parsed state dict, or None if the file doesn't exist
    or is unreadable (graceful degradation — callers fall back to
    current behavior when S2 state is unavailable).
    """
    state_path = _s2_state_path(worktree_path)
    if not state_path.exists():
        return None

    try:
        content = state_path.read_text(encoding="utf-8")
        if not content.strip():
            return None
        return json.loads(content)
    except (json.JSONDecodeError, IOError, OSError):
        return None


def write_s2_state(worktree_path: str, state: dict) -> bool:
    """Write a complete S2 state to .pact/s2-state.json atomically.

    Uses fcntl.flock for advisory locking and atomic rename to prevent
    readers from seeing partially-written files. The temp file is created
    in the same directory (.pact/) to guarantee same-filesystem rename
    atomicity on POSIX.

    Returns True on success, False on failure. Failures are logged to
    stderr but never raise — hooks must not crash.
    """
    try:
        pact_dir = _ensure_pact_dir(worktree_path)
        state_path = _s2_state_path(worktree_path)

        # Update timestamp
        state["last_updated"] = _now_iso()
        if not state.get("created_at"):
            state["created_at"] = state["last_updated"]

        # Write to temp file in the same directory, then atomic rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(pact_dir), suffix=".tmp", prefix="s2-state-"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.rename(tmp_path, str(state_path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return True
    except (IOError, OSError) as e:
        print(f"Warning: Could not write S2 state: {e}", file=sys.stderr)
        return False


def update_s2_state(
    worktree_path: str,
    updater: "callable[[dict], dict]",
) -> bool:
    """Read-lock-modify-write-unlock S2 state atomically.

    The updater function receives the current state dict and must return
    the modified state dict. The entire read-modify-write cycle runs under
    a single exclusive lock to prevent TOCTOU races.

    If the file doesn't exist, updater receives a copy of _DEFAULT_STATE.

    Returns True on success, False on failure.
    """
    try:
        pact_dir = _ensure_pact_dir(worktree_path)
        state_path = _s2_state_path(worktree_path)

        if HAS_FLOCK:
            return _update_with_flock(state_path, pact_dir, updater)
        else:
            return _update_without_flock(state_path, pact_dir, updater)
    except (IOError, OSError) as e:
        print(f"Warning: Could not update S2 state: {e}", file=sys.stderr)
        return False


def _update_with_flock(
    state_path: Path,
    pact_dir: Path,
    updater: "callable[[dict], dict]",
) -> bool:
    """Update with fcntl.flock — single lock for the read-modify-write cycle."""
    # Open with a+ to create if missing, then lock
    with open(state_path, "a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            # Read current state
            lock_file.seek(0)
            content = lock_file.read()
            try:
                state = json.loads(content) if content.strip() else dict(_DEFAULT_STATE)
            except json.JSONDecodeError:
                state = dict(_DEFAULT_STATE)

            # Apply update
            state = updater(state)
            state["last_updated"] = _now_iso()

            # Write to temp file, then atomic rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(pact_dir), suffix=".tmp", prefix="s2-state-"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
                os.rename(tmp_path, str(state_path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)

    return True


def _update_without_flock(
    state_path: Path,
    pact_dir: Path,
    updater: "callable[[dict], dict]",
) -> bool:
    """Fallback update without file locking (non-POSIX systems)."""
    # Read
    state = dict(_DEFAULT_STATE)
    if state_path.exists():
        try:
            content = state_path.read_text(encoding="utf-8")
            if content.strip():
                state = json.loads(content)
        except (json.JSONDecodeError, IOError):
            pass

    # Modify
    state = updater(state)
    state["last_updated"] = _now_iso()

    # Write atomically via temp + rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(pact_dir), suffix=".tmp", prefix="s2-state-"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.rename(tmp_path, str(state_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return True


# --- Convenience helpers for common S2 state operations ---


def resolve_convention(conventions: list, key: str) -> str | None:
    """Resolve a convention by key using last-per-key semantics.

    Conventions is an append-only array. Multiple entries may share the
    same key. This function returns the value from the most recent entry
    for the given key, or None if the key hasn't been established.
    """
    result = None
    for entry in conventions:
        if entry.get("key") == key:
            result = entry.get("value")
    return result


def check_boundary_overlap(boundaries: dict) -> list[dict]:
    """Check all boundary pairs for overlapping 'owns' scopes.

    Returns a list of overlap descriptions, each with 'agent_a', 'agent_b',
    and 'overlapping_paths' (directory prefixes where both agents have
    'owns' claims). Empty list means no overlaps.

    Used by the conflict check hook (Phase B) to detect scope collisions.
    """
    overlaps = []
    agents = list(boundaries.keys())

    for i, agent_a in enumerate(agents):
        for agent_b in agents[i + 1:]:
            owns_a = set(boundaries[agent_a].get("owns", []))
            owns_b = set(boundaries[agent_b].get("owns", []))

            # Check if any owned path is a prefix of the other or identical
            shared = set()
            for path_a in owns_a:
                for path_b in owns_b:
                    if path_a.startswith(path_b) or path_b.startswith(path_a):
                        shared.add(path_a if len(path_a) <= len(path_b) else path_b)

            if shared:
                overlaps.append({
                    "agent_a": agent_a,
                    "agent_b": agent_b,
                    "overlapping_paths": sorted(shared),
                })

    return overlaps


def file_in_scope(file_path: str, scope_paths: list[str]) -> bool:
    """Check if a file path falls within any of the given scope paths.

    Scope paths are directory prefixes ending with '/'. A file is in scope
    if its path starts with any scope path.
    """
    for scope_path in scope_paths:
        if file_path.startswith(scope_path):
            return True
    return False
