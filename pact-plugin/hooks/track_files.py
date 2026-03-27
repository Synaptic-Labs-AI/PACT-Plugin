#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/track_files.py
Summary: PostToolUse hook that tracks files modified during the session.
Used by: Claude Code settings.json PostToolUse hook (Edit, Write tools)

Extracts file paths from Edit/Write tool usage and records them
for the memory system's graph network.

Input: JSON from stdin with tool_name, tool_input, tool_output
Output: None (writes to tracking file for later memory association)
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared.error_output import hook_error_json

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

try:
    import fcntl
    HAS_FLOCK = True
except ImportError:
    HAS_FLOCK = False


# Directory for tracking data
TRACKING_DIR = Path.home() / ".claude" / "pact-memory" / "session-tracking"


def ensure_tracking_dir():
    """Ensure the tracking directory exists."""
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)


def get_session_tracking_file() -> Path:
    """Get the tracking file for the current session."""
    session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")
    return TRACKING_DIR / f"{session_id}.json"


def load_tracked_files() -> dict:
    """Load existing tracked files for this session.

    Uses shared (LOCK_SH) file locking on platforms that support fcntl
    to prevent reading while another process is mid-write.
    """
    default = {"files": [], "session_id": os.environ.get("CLAUDE_SESSION_ID", "unknown")}
    tracking_file = get_session_tracking_file()
    if not tracking_file.exists():
        return default

    if HAS_FLOCK:
        try:
            with open(tracking_file, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    content = f.read()
                    return json.loads(content) if content.strip() else default
                except json.JSONDecodeError:
                    return default
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except IOError:
            return default
    else:
        try:
            with open(tracking_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return default


def save_tracked_files(data: dict):
    """Save tracked files for this session.

    Uses exclusive (LOCK_EX) file locking on platforms that support fcntl
    to prevent concurrent write corruption. Unlock is in a finally block
    to ensure release even on exceptions.
    """
    ensure_tracking_dir()
    tracking_file = get_session_tracking_file()

    if HAS_FLOCK:
        try:
            with open(tracking_file, "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, indent=2)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except IOError as e:
            print(f"Warning: Could not save tracking data: {e}", file=sys.stderr)
    else:
        try:
            with open(tracking_file, "w") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save tracking data: {e}", file=sys.stderr)


def extract_file_path(tool_input: dict) -> str:
    """Extract file path from tool input."""
    # Both Edit and Write use file_path parameter
    return tool_input.get("file_path", "")


def _update_data(data: dict, file_path: str, tool_name: str) -> dict:
    """Update tracking data with a file entry (pure function, no I/O).

    Separated from I/O so that the read-modify-write cycle can be done
    under a single lock.
    """
    existing_paths = [f["path"] for f in data["files"]]
    if file_path in existing_paths:
        for f in data["files"]:
            if f["path"] == file_path:
                f["last_modified"] = datetime.now(timezone.utc).isoformat()
                f["tool"] = tool_name
                break
    else:
        data["files"].append({
            "path": file_path,
            "tool": tool_name,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_modified": datetime.now(timezone.utc).isoformat(),
        })
    return data


def track_file(file_path: str, tool_name: str):
    """Add a file to the tracking list.

    Uses a single exclusive lock for the entire read-modify-write cycle
    to prevent TOCTOU race conditions between concurrent hook invocations.
    """
    if not file_path:
        return

    ensure_tracking_dir()
    tracking_file = get_session_tracking_file()
    default = {"files": [], "session_id": os.environ.get("CLAUDE_SESSION_ID", "unknown")}

    if HAS_FLOCK:
        try:
            with open(tracking_file, "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    content = f.read()
                    try:
                        data = json.loads(content) if content.strip() else default
                    except json.JSONDecodeError:
                        data = default
                    data = _update_data(data, file_path, tool_name)
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, indent=2)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except IOError as e:
            print(f"Warning: Could not track file: {e}", file=sys.stderr)
    else:
        data = load_tracked_files()
        data = _update_data(data, file_path, tool_name)
        save_tracked_files(data)


def main():
    """Main entry point for the PostToolUse hook."""
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Only track Edit and Write tools
        if tool_name not in ("Edit", "Write"):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Extract and track the file path
        file_path = extract_file_path(tool_input)
        if file_path:
            track_file(file_path, tool_name)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Don't block on errors
        print(f"Hook warning (track_files): {e}", file=sys.stderr)
        print(hook_error_json("track_files", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
