"""
Location: pact-plugin/hooks/shared/session_journal.py
Summary: Append-only JSONL event store for GC-proof workflow state persistence.
Used by: session_init.py, session_end.py, handoff_gate.py (hooks);
         orchestrate.md, comPACT.md, peer-review.md, pause.md (commands via Bash).

Write path: POSIX O_APPEND atomic append. Safe for concurrent writers
(hooks + orchestrator commands). O_APPEND on regular files guarantees
atomic seek+write regardless of write size on POSIX systems. This is NOT
governed by PIPE_BUF — that limit applies only to pipes and FIFOs.
The kernel serializes O_APPEND writes at the VFS level, making each
append atomic regardless of entry size.

Read path: Sequential scan with type filtering. For typical sessions
(<200 events, <80KB), full scan completes in <5ms. For crash recovery,
read from end to find last checkpoint, then replay forward.

File location: ~/.claude/teams/{team_name}/session-journal.jsonl
Permissions: 0o600 (owner read/write only)
Directory permissions: 0o700 (owner only)
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Schema version for journal events.
_SCHEMA_VERSION = 1


# --- Write API ---


def make_event(event_type: str, **fields: Any) -> dict[str, Any]:
    """
    Construct a journal event dict with common fields pre-filled.

    Sets v=1 and ts=current UTC time. Caller provides type-specific fields.

    Args:
        event_type: Event type string (e.g., "agent_handoff", "session_start")
        **fields: Type-specific fields to include in the event

    Returns:
        Complete event dict ready for append_event()
    """
    event: dict[str, Any] = {
        "v": _SCHEMA_VERSION,
        "type": event_type,
    }
    event.update(fields)
    event["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return event


def append_event(event: dict[str, Any], team_name: str) -> bool:
    """
    Append a single event to the session journal.

    Creates the teams directory if it doesn't exist (mkdir -p, 0o700).
    Serializes event to JSON, appends newline, writes atomically via
    O_WRONLY | O_APPEND | O_CREAT with 0o600 permissions.

    Args:
        event: Event dict. Must include 'v' (int) and 'type' (non-empty str).
            'ts' is auto-set if missing. Invalid events cause a silent
            return False (fail-open).
        team_name: Team name for journal path resolution.

    Returns:
        True if write succeeded, False on any error (fail-open).
    """
    try:
        # Validate required fields
        if not isinstance(event.get("v"), int):
            return False
        if not isinstance(event.get("type"), str) or not event["type"]:
            return False
        if not team_name:
            return False

        # Auto-set timestamp if missing
        if "ts" not in event:
            event["ts"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        # Ensure directory exists (mkdir -p with 0o700)
        journal = _journal_path(team_name)
        journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Serialize and write atomically via O_APPEND
        entry = json.dumps(event, separators=(",", ":")) + "\n"
        fd = os.open(
            str(journal),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        try:
            os.write(fd, entry.encode("utf-8"))
        finally:
            os.close(fd)
        return True

    except Exception as e:
        print(
            f"session_journal: append_event failed: {e}",
            file=sys.stderr,
        )
        return False


# --- Read API ---


def read_events(
    team_name: str,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read events from the session journal, optionally filtered by type.

    Reads the full journal file, parses each line as JSON, and returns
    events matching the filter. Malformed lines are silently skipped
    (each event is self-contained — one bad line doesn't affect others).

    Args:
        team_name: Team name for journal path resolution.
        event_type: If provided, only return events with this type.
            If None, return all events.

    Returns:
        List of event dicts, in chronological order (oldest first).
        Empty list if journal doesn't exist or on any error.
    """
    try:
        journal = _journal_path(team_name)
        if not journal.exists():
            return []

        events: list[dict[str, Any]] = []
        for line in journal.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if event_type and event.get("type") != event_type:
                    continue
                events.append(event)
            except (json.JSONDecodeError, ValueError):
                continue  # Skip malformed lines
        return events

    except Exception:
        return []


def read_last_event(
    team_name: str,
    event_type: str,
) -> dict[str, Any] | None:
    """
    Read the most recent event of a given type from the journal.

    Reads the full journal and returns the last matching event.
    For 'checkpoint' events during recovery, this is the primary
    entry point (O(n) scan but typically <200 lines).

    Args:
        team_name: Team name for journal path resolution.
        event_type: Event type to search for.

    Returns:
        The last matching event dict, or None if not found.
    """
    events = read_events(team_name, event_type=event_type)
    return events[-1] if events else None


def get_journal_path(team_name: str) -> str:
    """
    Return the absolute path to the journal file for a given team.

    Does not check existence. Used by callers that need the path
    for logging or external tooling.

    Returns:
        Absolute path string: ~/.claude/teams/{team_name}/session-journal.jsonl
    """
    return str(_journal_path(team_name))


# --- Internal ---


def _journal_path(team_name: str) -> Path:
    """Compute the journal file path for a given team name."""
    return Path.home() / ".claude" / "teams" / team_name / "session-journal.jsonl"
