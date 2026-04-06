"""
Location: pact-plugin/hooks/shared/session_journal.py
Summary: Append-only JSONL event store for GC-proof workflow state persistence.
Used by: session_init.py, session_end.py, handoff_gate.py (hooks);
         orchestrate.md, comPACT.md, peer-review.md, wrap-up.md, pause.md
         (commands invoke via CLI: python3 session_journal.py write|read|read-last).

Write path: POSIX O_APPEND atomic append. Safe for concurrent writers
(hooks + orchestrator commands). O_APPEND on regular files guarantees
atomic seek+write regardless of write size on POSIX systems. This is NOT
governed by PIPE_BUF — that limit applies only to pipes and FIFOs.
The kernel serializes O_APPEND writes at the VFS level, making each
append atomic regardless of entry size.

Read path: Sequential scan with type filtering. For typical sessions
(<200 events, <80KB), full scan completes in <5ms. For crash recovery,
read from end to find last checkpoint, then replay forward.

Dual API pattern:
- Implicit API (hooks): append_event(), read_events(), read_last_event(),
  get_journal_path() — derive path via pact_context.get_session_dir().
- Explicit API (resume/CLI): read_events_from(session_dir), read_last_event_from(session_dir)
  — caller provides session directory path.

File location: ~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl
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


def append_event(event: dict[str, Any]) -> bool:
    """
    Append a single event to the current session's journal.

    Path is derived implicitly via pact_context.get_session_dir().
    Creates the session directory if it doesn't exist (mkdir -p, 0o700).
    Serializes event to JSON, appends newline, writes atomically via
    O_WRONLY | O_APPEND | O_CREAT with 0o600 permissions.

    Args:
        event: Event dict. Must include 'v' (int) and 'type' (non-empty str).
            'ts' is auto-set if missing. Invalid events cause a silent
            return False (fail-open).

    Returns:
        True if write succeeded, False on any error (fail-open).
    """
    try:
        # Validate required fields.
        # Reject bool explicitly — Python bool is a subclass of int.
        v = event.get("v")
        if not isinstance(v, int) or isinstance(v, bool):
            return False
        if not isinstance(event.get("type"), str) or not event["type"]:
            return False

        # Auto-set timestamp if missing
        if "ts" not in event:
            event["ts"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        # Derive path from session context (implicit — current session)
        journal = _journal_path()
        if journal is None:
            return False

        # Ensure directory exists (mkdir -p with 0o700)
        journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Serialize and write atomically via O_APPEND
        entry = json.dumps(event, separators=(",", ":")) + "\n"
        if not _atomic_write(journal, entry.encode("utf-8")):
            print(
                "session_journal: append_event failed: write error",
                file=sys.stderr,
            )
            return False
        return True

    except Exception as e:
        print(
            f"session_journal: append_event failed: {e}",
            file=sys.stderr,
        )
        return False


# --- Read API ---


def read_events(
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read events from the current session's journal, optionally filtered by type.

    Path is derived implicitly via pact_context.get_session_dir().
    Reads the full journal file, parses each line as JSON, and returns
    events matching the filter. Malformed lines are silently skipped
    (each event is self-contained — one bad line doesn't affect others).

    Args:
        event_type: If provided, only return events with this type.
            If None, return all events.

    Returns:
        List of event dicts, in chronological order (oldest first).
        Empty list if journal doesn't exist or on any error.
    """
    try:
        journal = _journal_path()
        if journal is None:
            return []
        return _read_events_at(journal, event_type)
    except Exception:
        return []


def read_events_from(
    session_dir: str,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read events from a specific session's journal (explicit path).

    Used for cross-session reads (resume, CLI) where the caller knows
    the session directory path.

    Args:
        session_dir: Absolute path to the session directory.
        event_type: If provided, only return events with this type.
            If None, return all events.

    Returns:
        List of event dicts, in chronological order (oldest first).
        Empty list if journal doesn't exist or on any error.
    """
    if not session_dir:
        return []
    journal = _journal_path_from(session_dir)
    return _read_events_at(journal, event_type)


def _read_events_at(
    journal: Path,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Shared read implementation for both implicit and explicit APIs."""
    try:
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
    event_type: str,
) -> dict[str, Any] | None:
    """
    Read the most recent event of a given type from the current session's journal.

    Path is derived implicitly via pact_context.get_session_dir().
    Scans lines in reverse for efficiency — returns as soon as the
    first (most recent) match is found.

    Args:
        event_type: Event type to search for.

    Returns:
        The last matching event dict, or None if not found.
    """
    try:
        journal = _journal_path()
        if journal is None:
            return None
        return _read_last_event_at(journal, event_type)
    except Exception:
        return None


def read_last_event_from(
    session_dir: str,
    event_type: str,
) -> dict[str, Any] | None:
    """
    Read the most recent event of a given type from a specific session's journal.

    Used for cross-session reads (resume, CLI) where the caller knows
    the session directory path.

    Args:
        session_dir: Absolute path to the session directory.
        event_type: Event type to search for.

    Returns:
        The last matching event dict, or None if not found.
    """
    if not session_dir:
        return None
    journal = _journal_path_from(session_dir)
    return _read_last_event_at(journal, event_type)


def _read_last_event_at(
    journal: Path,
    event_type: str,
) -> dict[str, Any] | None:
    """Shared reverse-scan implementation for both implicit and explicit APIs."""
    try:
        if not journal.exists():
            return None

        for line in reversed(journal.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if event.get("type") == event_type:
                    return event
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    except Exception:
        return None


def get_journal_path() -> str:
    """
    Return the absolute path to the journal file for the current session.

    Path is derived implicitly via pact_context.get_session_dir().
    Does not check existence. Used by callers that need the path
    for logging or external tooling.

    Returns:
        Absolute path string, or empty string if session dir unavailable.
    """
    journal = _journal_path()
    if journal is None:
        return ""
    return str(journal)


# --- Internal ---


def _get_session_dir() -> str:
    """
    Resolve session directory via pact_context.get_session_dir().

    Uses lazy import to avoid module-level coupling. Tries the package
    path first (shared.pact_context — works from hooks/ and tests), then
    falls back to bare module (pact_context — works when CLI runs
    session_journal.py directly, where shared/ is on sys.path).

    Separated into its own function so tests can monkeypatch it at
    `session_journal._get_session_dir` without dealing with import paths.

    Returns:
        Session directory path string, or "" if unavailable.
    """
    try:
        from shared.pact_context import get_session_dir
    except ImportError:
        from pact_context import get_session_dir  # type: ignore[no-redef]
    return get_session_dir()


def _journal_path() -> Path | None:
    """
    Derive the journal file path from the current session context.

    Returns:
        Path to session-journal.jsonl, or None if session dir unavailable.
    """
    session_dir = _get_session_dir()
    if not session_dir:
        return None
    return Path(session_dir) / "session-journal.jsonl"


def _journal_path_from(session_dir: str) -> Path:
    """Compute the journal file path from an explicit session directory."""
    return Path(session_dir) / "session-journal.jsonl"


def _atomic_write(path: Path, data: bytes) -> bool:
    """
    Append *data* to *path* using POSIX O_APPEND for atomic writes.

    Returns True on success, False on OSError.  File is created with 0o600
    if it does not exist.  The caller is responsible for ensuring the parent
    directory exists before calling.
    """
    try:
        fd = os.open(
            str(path),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


# --- CLI ---


def main() -> int:
    """
    CLI entry point for session journal operations.

    Subcommands:
        write  — Append an event via make_event() + append_event()
        read   — Read events, optionally filtered by type, output JSON
        read-last — Read the most recent event of a given type, output JSON

    Returns:
        0 on success, 1 on error.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Session journal CLI — append and query JSONL events.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # --- write ---
    write_p = sub.add_parser("write", help="Append an event to the journal")
    write_p.add_argument("--type", required=True, dest="event_type",
                         help="Event type string (e.g. phase_transition)")
    write_p.add_argument("--session-dir", required=True,
                         help="Session directory path")
    write_p.add_argument("--data", default="{}",
                         help="JSON object of extra event fields")

    # --- read ---
    read_p = sub.add_parser("read", help="Read events (JSON array to stdout)")
    read_p.add_argument("--session-dir", required=True,
                        help="Session directory path")
    read_p.add_argument("--type", default=None, dest="event_type",
                        help="Filter by event type")

    # --- read-last ---
    last_p = sub.add_parser("read-last",
                            help="Read the most recent event of a type")
    last_p.add_argument("--session-dir", required=True,
                        help="Session directory path")
    last_p.add_argument("--type", required=True, dest="event_type",
                        help="Event type to find")

    args = parser.parse_args()

    if args.command == "write":
        try:
            extra = json.loads(args.data)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"session_journal: invalid --data JSON: {exc}",
                  file=sys.stderr)
            return 1

        if not isinstance(extra, dict):
            print("session_journal: --data must be a JSON object",
                  file=sys.stderr)
            return 1

        event = make_event(args.event_type, **extra)
        journal = _journal_path_from(args.session_dir)
        journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        entry = json.dumps(event, separators=(",", ":")) + "\n"
        if not _atomic_write(journal, entry.encode("utf-8")):
            print("session_journal: write failed", file=sys.stderr)
            return 1
        return 0

    elif args.command == "read":
        events = read_events_from(args.session_dir, event_type=args.event_type)
        print(json.dumps(events))
        return 0

    elif args.command == "read-last":
        event = read_last_event_from(args.session_dir, args.event_type)
        if event is None:
            print("null")
        else:
            print(json.dumps(event))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
