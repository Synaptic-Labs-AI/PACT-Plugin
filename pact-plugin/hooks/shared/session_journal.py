"""
Location: pact-plugin/hooks/shared/session_journal.py
Summary: Append-only JSONL event store for GC-proof workflow state persistence.
Used by: session_init.py, session_end.py, handoff_gate.py (hooks);
         orchestrate.md, comPACT.md, peer-review.md, wrap-up.md, pause.md
         (commands invoke via CLI: python3 session_journal.py write|read|read-last).

Write path: O_APPEND append, safe for concurrent writers (hooks +
orchestrator commands) on Linux local filesystems. Linux O_APPEND on
regular files atomically advances the file offset and writes up to
PIPE_BUF bytes (typically 4096) in one kernel call; POSIX itself only
guarantees that the seek+write PAIR is not interleaved with other
appenders, not that the write of arbitrary sizes is atomic. NFS does
not honor this at all. Current event sizes are well under PIPE_BUF
(events are small JSONL lines), so interleaving is not a practical
concern on the supported platforms — but maintainers should not rely
on the broader "POSIX atomic regardless of size" claim if event sizes
grow or the journal ever lands on NFS. The short-write loop in
`_atomic_write` is the belt-and-braces guard against partial writes
from signal interruption even within the PIPE_BUF bound.

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


# Per-type required fields, derived from actual writer call sites. Every
# entry here reflects what a production writer ACTUALLY produces (grep
# `make_event("{type}"` in hooks/ and `write --type {type}` in commands/),
# not an aspirational schema. Unknown types (e.g. "test" in unit tests)
# bypass per-type validation and only get baseline v/type/ts checks — this
# preserves test ergonomics without loosening production safety.
#
# When adding a new event type, add it here with its required fields AND
# add a test to TestValidateEventSchemaPerType in test_session_journal.py.
_REQUIRED_FIELDS_BY_TYPE: dict[str, tuple[str, ...]] = {
    # hooks/session_init.py:326 writes session_start with team, session_id,
    # project_dir, worktree. Of these, session_id and project_dir are the
    # load-bearing fields downstream consumers depend on; team is redundant
    # with CLAUDE.md and worktree is empty at write time.
    "session_start": ("session_id", "project_dir"),
    # commands/orchestrate.md writes variety_assessed with task_id + variety.
    "variety_assessed": ("task_id", "variety"),
    # commands/orchestrate.md + comPACT.md write phase_transition with
    # phase + status. session_resume._build_journal_resume subscripts
    # `p["phase"]` — this schema check is the defensive bulwark against F1.
    "phase_transition": ("phase", "status"),
    # commands/orchestrate.md writes checkpoint with phase + completed_phases
    # + active_agents + variety + pending_phases + safe_to_retry. Only
    # `phase` is universally required; the rest vary per checkpoint context.
    "checkpoint": ("phase",),
    # commands/orchestrate.md + comPACT.md write agent_dispatch with agent,
    # task_id, phase, scope.
    "agent_dispatch": ("agent", "task_id", "phase"),
    # hooks/handoff_gate.py:261 writes agent_handoff with agent, task_id,
    # task_subject, handoff. All four are load-bearing for the secretary.
    "agent_handoff": ("agent", "task_id", "task_subject", "handoff"),
    # commands/orchestrate.md writes s2_state_seeded with worktree, agents,
    # boundaries. No hook-based writer; CLI-only event.
    "s2_state_seeded": ("worktree", "agents", "boundaries"),
    # commands/orchestrate.md + comPACT.md write commit with sha, message, phase.
    "commit": ("sha", "message", "phase"),
    # commands/peer-review.md writes review_dispatch with pr_number, pr_url, reviewers.
    "review_dispatch": ("pr_number", "pr_url", "reviewers"),
    # commands/peer-review.md writes review_finding with severity, finding,
    # reviewer, task_id.
    "review_finding": ("severity", "finding", "reviewer"),
    # commands/peer-review.md writes remediation with cycle, items, fixer.
    "remediation": ("cycle", "items", "fixer"),
    # commands/peer-review.md writes pr_ready with pr_number, pr_url, commits.
    "pr_ready": ("pr_number", "pr_url", "commits"),
    # commands/pause.md writes session_paused with pr_number, pr_url, branch,
    # worktree_path, consolidation_completed, team_name.
    "session_paused": (
        "pr_number",
        "pr_url",
        "branch",
        "worktree_path",
        "consolidation_completed",
    ),
    # hooks/session_end.py writes session_end with NO required fields — one
    # writer passes an optional `warning` (line 119), the other passes
    # nothing (line 291). commands/wrap-up.md CLI also writes session_end
    # with no --data. Baseline v/type/ts validation is the only requirement.
    "session_end": (),
}


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


def _validate_event_schema(event: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate that an event dict has the required schema fields.

    Baseline (all event types):
    - 'v' is an int (and NOT a bool — Python bool is a subclass of int,
      so it must be rejected explicitly)
    - 'type' is a non-empty str (whitespace-only is rejected)

    Per-type (only for types in _REQUIRED_FIELDS_BY_TYPE):
    - Every required field is present and not None. Unknown event types
      (e.g. free-form "test" used in unit tests) pass per-type validation
      by default — the whitelist is opt-in enforcement for known types.

    This is the bulwark that prevents BugF1: a malformed `phase_transition`
    event (missing `phase` field) from any writer causes `append_event` or
    the CLI write path to return False BEFORE the bad line reaches disk,
    so `_build_journal_resume` in the next session never has to deal with
    a missing-field event. The defensive consumer is still a backstop for
    anything that slips past this (e.g. events from prior schema versions).

    Returns:
        A `(ok, reason)` tuple. `ok` is True only when every check passes;
        `reason` is a short human-readable string. On success `reason` is
        "ok". On failure `reason` identifies the first failing check so
        callers (notably the CLI write path) can surface a precise error
        to stderr instead of a generic "invalid event schema" line.
    """
    v = event.get("v")
    if not isinstance(v, int) or isinstance(v, bool):
        return False, "v must be int"
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return False, "type must be non-empty str"
    required = _REQUIRED_FIELDS_BY_TYPE.get(event_type)
    if required is None:
        # Unknown event type — opt-in enforcement, pass through.
        return True, "ok"
    for field in required:
        if field not in event or event[field] is None:
            return (
                False,
                f"missing required field '{field}' for type '{event_type}'",
            )
    return True, "ok"


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
        # Validate required schema fields (shared with CLI write path).
        # In-process API is fail-open: the caller gets a bool and the
        # reason is intentionally discarded — hooks never surface per-type
        # validator messages to end users. The CLI write path below has a
        # symmetric call site that DOES print the reason.
        ok, _reason = _validate_event_schema(event)
        if not ok:
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
    Append *data* to *path* using O_APPEND, looping over short writes.

    Returns True on success, False on OSError or a non-progressing write.
    File is created with 0o600 if it does not exist. The caller is
    responsible for ensuring the parent directory exists before calling.

    Short-write loop rationale: `os.write` can return fewer bytes than
    requested when (a) the write is interrupted by a signal, or (b) the
    request exceeds PIPE_BUF on platforms where only the first PIPE_BUF
    bytes of an O_APPEND write are guaranteed atomic. The caller handles
    (a) by retrying from where we left off; (b) is not our concern for
    current event sizes but the loop makes the primitive correct in
    principle. A non-positive return from os.write indicates a failure we
    cannot recover from — bail out and let the caller see False.
    """
    try:
        fd = os.open(
            str(path),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        try:
            view = memoryview(data)
            total = 0
            while total < len(view):
                n = os.write(fd, view[total:])
                if n <= 0:
                    # Non-progressing write — treat as failure so the
                    # caller can log and return False up the stack rather
                    # than spin forever.
                    return False
                total += n
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

        # Apply the same schema validation as append_event() — extra fields
        # in --data may shadow defaults from make_event() (e.g., a caller
        # passing {"v": true} would overwrite the default v=1 with a bool).
        # Unlike append_event (which is fail-open), the CLI surfaces the
        # exact failure reason so operators see precisely which check fired
        # instead of a generic "v must be int" line that may not apply.
        ok, reason = _validate_event_schema(event)
        if not ok:
            print(
                f"session_journal: invalid event schema ({reason})",
                file=sys.stderr,
            )
            return 1

        # AdvF4: mirror the empty-session-dir guard from read_events_from /
        # read_last_event_from. Without this, an empty `--session-dir`
        # silently resolves to "./session-journal.jsonl" and creates a
        # stray journal file in the caller's CWD. Argparse's `required=True`
        # catches a missing flag, but the empty-string case slips past it.
        if not args.session_dir:
            print(
                "session_journal: --session-dir must be non-empty",
                file=sys.stderr,
            )
            return 1

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
