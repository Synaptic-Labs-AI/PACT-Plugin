"""
Location: pact-plugin/hooks/shared/s2_state.py
Summary: Atomic read/write/update primitives for .pact/s2-state.json, the shared
         S2 coordination state file that agents read at startup and update as they work.
Used by: orchestrate.md and comPACT.md (seeding), SKILL.md (agent self-coordination),
         s2_conflict_check.py (Phase B), s2_drift_check.py (Phase C).

Concurrency model: fcntl.flock on a SEPARATE sentinel lock file (.pact/s2-state.lock)
+ atomic rename for writes. The lock file is never replaced, so all writers contend
on the same inode — unlike locking the data file directly, which breaks when
os.rename replaces the inode. Atomic rename ensures readers never see partial writes.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    import fcntl
    HAS_FLOCK = True
except ImportError:
    HAS_FLOCK = False


# Maximum number of drift alerts to retain (oldest trimmed on overflow)
_MAX_DRIFT_ALERTS = 50


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
S2_LOCK_FILENAME = "s2-state.lock"
S2_DIR_NAME = ".pact"


def _discover_worktree_path() -> str | None:
    """Discover the worktree root path.

    Checks the PACT_WORKTREE_ROOT env var first to avoid a subprocess call
    (~5ms savings per invocation). Falls back to git rev-parse --show-toplevel.
    Returns None if not in a git repository or on error.
    """
    env_root = os.environ.get("PACT_WORKTREE_ROOT")
    if env_root:
        return env_root

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _s2_state_path(worktree_path: str) -> Path:
    """Return the path to s2-state.json within a worktree."""
    return Path(worktree_path) / S2_DIR_NAME / S2_STATE_FILENAME


def _s2_lock_path(worktree_path: str) -> Path:
    """Return the path to the sentinel lock file (.pact/s2-state.lock).

    This file is used exclusively for flock serialization and is never
    replaced or renamed. All writers contend on the same inode, avoiding
    the inode-mismatch problem that occurs when locking a file that gets
    atomically replaced via os.rename.
    """
    return Path(worktree_path) / S2_DIR_NAME / S2_LOCK_FILENAME


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

    Returns the parsed state dict, or None if the file doesn't exist,
    is unreadable, or contains non-dict JSON (graceful degradation —
    callers fall back to current behavior when S2 state is unavailable).

    No lock is acquired: atomic rename on write ensures readers never
    see partial content — they get the old file or the new file, never
    a half-written one.
    """
    state_path = _s2_state_path(worktree_path)
    if not state_path.exists():
        return None

    try:
        content = state_path.read_text(encoding="utf-8")
        if not content.strip():
            return None
        data = json.loads(content)
        if not isinstance(data, dict):
            print(
                f"Warning: S2 state is not a dict (got {type(data).__name__}), ignoring",
                file=sys.stderr,
            )
            return None
        return data
    except (json.JSONDecodeError, IOError, OSError):
        return None


def write_s2_state(worktree_path: str, state: dict) -> bool:
    """Write a complete S2 state to .pact/s2-state.json atomically.

    Intended for single-writer initial seed (orchestrator creates the file
    before any agents are dispatched). Does NOT acquire a lock — for
    concurrent read-modify-write after agents are running, use
    update_s2_state() instead.

    Uses atomic rename (temp file + os.rename) to prevent readers from
    seeing partially-written files. The temp file is created in the same
    directory (.pact/) to guarantee same-filesystem rename atomicity on POSIX.

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
    updater: Callable[[dict], dict],
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
    updater: Callable[[dict], dict],
) -> bool:
    """Update with fcntl.flock on a sentinel lock file + atomic rename.

    The lock is acquired on a SEPARATE sentinel file (.pact/s2-state.lock)
    that is never replaced. This avoids the inode-mismatch problem: if we
    locked the data file and then os.rename'd over it, the next writer would
    open the new inode and get a lock on a different file — defeating
    serialization. The sentinel file's inode is stable, so all writers
    contend on the same lock.
    """
    lock_path = pact_dir / S2_LOCK_FILENAME

    # Open sentinel lock file with a+ to create if missing
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            # Read current state from the data file (not the lock file)
            state = dict(_DEFAULT_STATE)
            if state_path.exists():
                try:
                    content = state_path.read_text(encoding="utf-8")
                    if content.strip():
                        state = json.loads(content)
                except (json.JSONDecodeError, IOError):
                    pass

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
    updater: Callable[[dict], dict],
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


def _normalize_scope(path: str) -> str:
    """Ensure a scope path ends with '/' for safe prefix matching.

    Without trailing-slash normalization, 'src/server' would match
    'src/server_backup/foo.py' via bare startswith(). Appending '/'
    ensures only true subdirectory relationships match.
    """
    if path and not path.endswith("/"):
        return path + "/"
    return path


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
            owns_a = {_normalize_scope(p) for p in boundaries[agent_a].get("owns", [])}
            owns_b = {_normalize_scope(p) for p in boundaries[agent_b].get("owns", [])}

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

    Scope paths are directory prefixes. Paths are normalized to end with '/'
    before matching to prevent false positives (e.g., 'src/server' matching
    'src/server_backup/foo.py').
    """
    for scope_path in scope_paths:
        if file_path.startswith(_normalize_scope(scope_path)):
            return True
    return False
