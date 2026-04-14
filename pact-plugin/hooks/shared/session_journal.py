"""
Location: pact-plugin/hooks/shared/session_journal.py
Summary: Append-only JSONL event store for GC-proof workflow state persistence.
Used by: session_init.py, session_end.py, handoff_gate.py (hooks);
         orchestrate.md, comPACT.md, peer-review.md, wrap-up.md, pause.md
         (commands invoke via CLI: python3 session_journal.py write|read|read-last).

Write path: O_APPEND append, protected by an exclusive advisory lock
(`fcntl.flock(LOCK_EX)`) around the short-write loop in `_atomic_write`.
POSIX only guarantees `os.write` atomicity up to PIPE_BUF (512 bytes on
macOS, 4096 on Linux); for larger events the short-write loop would
otherwise open an interleaving window between iterations where another
O_APPEND writer could splice its bytes into the middle of ours. The
flock closes that window, so concurrent writers (hooks + orchestrator
CLI calls) are safe for events of any size on single-host local
filesystems. The guarantee is single-host only — advisory locks do not
cross machine boundaries and NFS flock semantics are unreliable — which
is fine because pact-sessions is per-host already. The short-write
loop itself remains the guard against partial writes from signal
interruption.

Read path: Sequential scan with type filtering. For typical sessions
(<200 events, <80KB), full scan completes in <5ms. For crash recovery,
read from end to find last checkpoint, then replay forward.

Durability: Best-effort. The write+rename+lock cycle protects against
interleaving and partial writes from concurrent writers, but `_atomic_write`
does NOT call `fsync` after the write. After a hard crash (power loss,
kernel panic), the most recent event may be lost even though
`append_event` returned True. This is intentional — the journal lives on
the orchestrate hot path (every checkpoint, phase transition, dispatch)
and a per-write fsync is too expensive there. Durability "to OS buffers"
is the contract; cross-process visibility is immediate after the lock
releases.

Dual API pattern:
- Implicit API (hooks): append_event(), read_events(), read_last_event(),
  get_journal_path() — derive path via pact_context.get_session_dir().
- Explicit API (resume/CLI): read_events_from(session_dir), read_last_event_from(session_dir)
  — caller provides session directory path.

File location: ~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl
Permissions: 0o600 (owner read/write only)
Directory permissions: 0o700 (owner only)
"""

import fcntl
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
# bypass per-type validation by design and only get baseline v/type/ts
# checks — this preserves test ergonomics without loosening production
# safety. Note the trade-off: a typo in `event_type` at a writer call site
# (e.g. `make_event("phse_transition", ...)`) will silently bypass per-type
# validation rather than raise. The mitigation is the test in
# TestValidateEventSchemaPerType — every production type MUST have a test
# entry, which catches typos in tests rather than at runtime.
#
# Each required field maps to its expected Python type. At validate time,
# the validator checks presence AND `isinstance(value, expected_type)` AND
# — for str fields — rejects empty or whitespace-only values. The expected
# types reflect what the writer literally produces: unquoted values in the
# --data JSON become int/bool/list/dict, quoted values become str.
#
# When adding a new event type, add it here with its required field → type
# mapping AND add a test to TestValidateEventSchemaPerType in
# test_session_journal.py.
_REQUIRED_FIELDS_BY_TYPE: dict[str, dict[str, type]] = {
    # hooks/session_init.py writes session_start with team, session_id,
    # project_dir, worktree on the valid-stdin path only (under R3, the event
    # is dropped entirely when stdin lacks session_id to avoid an unreapable
    # `unknown-*` directory leak). Of these, session_id and project_dir are
    # the load-bearing fields downstream consumers depend on; team is
    # redundant with CLAUDE.md and worktree is empty at write time.
    "session_start": {"session_id": str, "project_dir": str},
    # commands/orchestrate.md writes variety_assessed with task_id (quoted
    # string) and variety (nested JSON object → dict).
    "variety_assessed": {"task_id": str, "variety": dict},
    # commands/orchestrate.md + comPACT.md write phase_transition with
    # phase + status (both quoted strings). session_resume._build_journal_resume
    # subscripts `p["phase"]` — this schema check is the defensive bulwark
    # against F1.
    "phase_transition": {"phase": str, "status": str},
    # commands/orchestrate.md writes checkpoint with phase (quoted string) +
    # completed_phases + active_agents + variety + pending_phases + safe_to_retry.
    # Only `phase` is universally required; the rest vary per checkpoint context.
    "checkpoint": {"phase": str},
    # commands/orchestrate.md + comPACT.md write agent_dispatch with agent,
    # task_id, phase (all quoted strings) + scope (list).
    "agent_dispatch": {"agent": str, "task_id": str, "phase": str},
    # hooks/handoff_gate.py:261 writes agent_handoff with agent, task_id,
    # task_subject (all strings) and handoff (dict from task metadata).
    # All four are load-bearing for the secretary.
    "agent_handoff": {
        "agent": str,
        "task_id": str,
        "task_subject": str,
        "handoff": dict,
    },
    # commands/orchestrate.md writes s2_state_seeded with worktree (quoted
    # string), agents (JSON list), and boundaries (JSON object → dict).
    # No hook-based writer; CLI-only event.
    "s2_state_seeded": {"worktree": str, "agents": list, "boundaries": dict},
    # commands/orchestrate.md + comPACT.md write commit with sha, message,
    # phase (all quoted strings).
    "commit": {"sha": str, "message": str, "phase": str},
    # commands/peer-review.md writes review_dispatch with pr_number (unquoted
    # int), pr_url (quoted string), reviewers (JSON list).
    "review_dispatch": {"pr_number": int, "pr_url": str, "reviewers": list},
    # commands/peer-review.md writes review_finding with severity, finding,
    # reviewer, task_id (all quoted strings).
    "review_finding": {"severity": str, "finding": str, "reviewer": str},
    # commands/peer-review.md writes remediation with cycle (unquoted int),
    # items (JSON list), fixer (quoted string).
    "remediation": {"cycle": int, "items": list, "fixer": str},
    # commands/peer-review.md writes pr_ready with pr_number (unquoted int),
    # pr_url (quoted string), commits (unquoted int).
    "pr_ready": {"pr_number": int, "pr_url": str, "commits": int},
    # commands/pause.md writes session_paused with pr_number (unquoted int),
    # pr_url/branch/worktree_path (quoted strings),
    # consolidation_completed (unquoted bool), team_name (quoted string).
    "session_paused": {
        "pr_number": int,
        "pr_url": str,
        "branch": str,
        "worktree_path": str,
        "consolidation_completed": bool,
    },
    # hooks/session_end.py writes session_end with NO required fields — one
    # writer passes an optional `warning` (line 119), the other passes
    # nothing (line 316). commands/wrap-up.md CLI also writes session_end
    # with no --data. Baseline v/type/ts validation is the only requirement.
    "session_end": {},
}


# Per-type optional fields, with expected Python type. Fields listed here
# are NOT required — an event missing them still passes validation — but
# when they ARE present, the validator enforces type. This is the schema
# contract counterpart to runtime clamps (e.g. the `_VALID_SOURCES` clamp
# in session_init.py): if a future writer bypasses the normalization
# path and emits the wrong type directly to `make_event`, the event is
# rejected at validate time rather than landing on disk.
# Same type-symmetry rules as _REQUIRED_FIELDS_BY_TYPE: `int` fields
# reject `bool` because bool subclasses int.
#
# When adding a new optional field, add it here and add a matching
# happy-path + wrong-type case to TestValidateOptionalFieldTypes in
# test_session_journal.py.
_OPTIONAL_FIELDS_BY_TYPE: dict[str, dict[str, type]] = {
    # hooks/session_init.py writes session_start with an optional `source`
    # drawn from stdin. The session_init normalization path clamps non-str
    # inputs to "unknown" before the journal write; this schema contract
    # catches any future writer that bypasses that path.
    "session_start": {"source": str},
}


# --- Write API ---


def make_event(event_type: str, **fields: Any) -> dict[str, Any]:
    """
    Construct a journal event dict with common fields pre-filled.

    Sets v=1 and ts=current UTC time. Caller provides type-specific fields.
    A caller-supplied `ts` (in **fields) is honored — it is only auto-set
    when the caller does not provide one. This lets test fixtures and
    backfill tooling stamp deterministic timestamps without round-tripping
    through the journal.

    Args:
        event_type: Event type string (e.g., "agent_handoff", "session_start")
        **fields: Type-specific fields to include in the event. May include
            an explicit `ts` to override the auto-set timestamp.

    Returns:
        Complete event dict ready for append_event()
    """
    event: dict[str, Any] = {
        "v": _SCHEMA_VERSION,
        "type": event_type,
    }
    event.update(fields)
    # Use setdefault so a caller-supplied ts in **fields is preserved.
    # Without setdefault, the previous unconditional assignment silently
    # discarded any caller ts and contradicted the docstring.
    event.setdefault(
        "ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    return event


def _validate_event_schema(event: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate that an event dict has the required schema fields.

    Baseline (all event types):
    - 'v' is an int (and NOT a bool — Python bool is a subclass of int,
      so it must be rejected explicitly)
    - 'type' is a non-empty str (whitespace-only is rejected)

    Per-type (only for types in _REQUIRED_FIELDS_BY_TYPE):
    - Every required field is present and not None.
    - Every required field has the expected Python type (isinstance check).
      `int` fields reject `bool` explicitly because bool is an int subclass;
      a writer passing `pr_number=True` would otherwise slip through.
    - `str` fields additionally reject empty and whitespace-only values —
      a blank `phase` or `agent` is functionally indistinguishable from
      missing for every downstream consumer, and the baseline `type` check
      already uses the same semantics for consistency.
    - Unknown event types (e.g. free-form "test" used in unit tests) pass
      per-type validation by design — the whitelist is opt-in enforcement
      for known types. The trade-off: a typo in a production
      `make_event("…")` call site silently bypasses per-type checks. The
      TestValidateEventSchemaPerType suite catches that at test time.

    Optional fields (for types in _OPTIONAL_FIELDS_BY_TYPE):
    - Absent fields pass (the field is optional by definition).
    - Present fields must match the declared type, applying the same
      bool-in-int + empty-str rules as required fields. This is the
      schema-level counterpart to runtime clamping paths such as the
      `source` isinstance guard in session_init.py — a future writer
      that bypasses the clamp and emits the wrong type directly to
      `make_event` is rejected at validate time.

    This is the bulwark that prevents BugF1: a malformed `phase_transition`
    event (missing `phase` field, or `phase=""`, or `phase=42`) from any
    writer causes `append_event` or the CLI write path to return False
    BEFORE the bad line reaches disk, so `_build_journal_resume` in the
    next session never has to deal with it. The defensive consumer is
    still a backstop for anything that slips past this (e.g. events from
    prior schema versions).

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
    for field, expected_type in required.items():
        if field not in event or event[field] is None:
            return (
                False,
                f"missing required field '{field}' for type '{event_type}'",
            )
        value = event[field]
        # int fields must reject bool even though bool subclasses int —
        # symmetric with the baseline v check above.
        if expected_type is int and isinstance(value, bool):
            return (
                False,
                f"field '{field}' for type '{event_type}' must be int, "
                f"got bool",
            )
        if not isinstance(value, expected_type):
            return (
                False,
                f"field '{field}' for type '{event_type}' must be "
                f"{expected_type.__name__}, got {type(value).__name__}",
            )
        # Fix B (RG2): str fields additionally reject empty or
        # whitespace-only values — a blank phase/agent/task_id would
        # pass the isinstance check but break every downstream consumer.
        if expected_type is str and not value.strip():
            return (
                False,
                f"field '{field}' for type '{event_type}' must be "
                f"non-empty string",
            )
    # Per-type optional field checks. Absent fields pass (that's what
    # "optional" means); present fields must match the declared type.
    # Symmetric with required-field checks: rejects bool in int fields,
    # rejects empty/whitespace-only str. Event types with no optional
    # declarations (the common case) get a no-op empty dict from .get()
    # and skip the loop entirely.
    optional = _OPTIONAL_FIELDS_BY_TYPE.get(event_type, {})
    for field, expected_type in optional.items():
        if field not in event or event[field] is None:
            continue  # Absent optional field — pass through.
        value = event[field]
        if expected_type is int and isinstance(value, bool):
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be int, got bool",
            )
        if not isinstance(value, expected_type):
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be {expected_type.__name__}, got {type(value).__name__}",
            )
        if expected_type is str and not value.strip():
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be non-empty string",
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
            # AdvF2 Approach 4: warn (but do not fail) when the implicit API
            # is invoked before pact_context.init(). The return value still
            # honors the existing fail-open contract — the warning is purely
            # additive so a missing init() in a hook surfaces as a visible
            # signal during development instead of a silent no-op in
            # production. The is_initialized() check pinpoints the missing
            # init root cause; if pact_context IS initialized but the path is
            # still unavailable, that's a different failure mode (e.g.
            # missing session_id) and we leave the existing silent fail-open
            # in place to avoid noise.
            if not _pact_context_is_initialized():
                print(
                    "session_journal: append_event called before "
                    "pact_context.init() — returning False (this may "
                    "indicate a hook missing session_id)",
                    file=sys.stderr,
                )
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
            # AdvF2 Approach 4: see append_event for rationale. Warns only
            # when the path is unavailable AND pact_context was never
            # initialized — the canonical "hook forgot to call init()" bug.
            if not _pact_context_is_initialized():
                print(
                    "session_journal: read_events called before "
                    "pact_context.init() — returning [] (this may indicate "
                    "a hook missing session_id)",
                    file=sys.stderr,
                )
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
        # AdvF2 Approach 4 (parity with implicit API): emit a stderr
        # warning before the silent fallback so an unset/empty
        # session_dir at the call site surfaces as a visible signal
        # rather than a mute empty result. The empty list is preserved
        # so callers see the same return contract.
        print(
            "session_journal: read_events_from called with empty session_dir",
            file=sys.stderr,
        )
        return []
    journal = _journal_path_from(session_dir)
    return _read_events_at(journal, event_type)


def _read_events_at(
    journal: Path,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Shared read implementation for both implicit and explicit APIs.

    Reads with `errors="replace"` so a single invalid byte sequence
    (e.g., from a botched write or a truncated multibyte character)
    substitutes U+FFFD for the bad bytes instead of raising
    UnicodeDecodeError. A malformed byte range corrupts at most its
    own line; the per-line `json.loads` then drops that line and every
    other event in the file is still returned. Without this, one bad
    byte would cause the outer `except Exception` to drop the whole
    file and hide all of its otherwise-valid events.
    """
    try:
        if not journal.exists():
            return []

        events: list[dict[str, Any]] = []
        for line in journal.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
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
            # AdvF2 Approach 4: see append_event for rationale. Warns only
            # when the path is unavailable AND pact_context was never
            # initialized — the canonical "hook forgot to call init()" bug.
            if not _pact_context_is_initialized():
                print(
                    "session_journal: read_last_event called before "
                    "pact_context.init() — returning None (this may "
                    "indicate a hook missing session_id)",
                    file=sys.stderr,
                )
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
        # AdvF2 Approach 4 (parity with implicit API): emit a stderr
        # warning before the silent fallback so an unset/empty
        # session_dir at the call site surfaces as a visible signal
        # rather than a mute None result. The None is preserved so
        # callers see the same return contract.
        print(
            "session_journal: read_last_event_from called with "
            "empty session_dir",
            file=sys.stderr,
        )
        return None
    journal = _journal_path_from(session_dir)
    return _read_last_event_at(journal, event_type)


def _read_last_event_at(
    journal: Path,
    event_type: str,
) -> dict[str, Any] | None:
    """Shared reverse-scan implementation for both implicit and explicit APIs.

    Reads with `errors="replace"` symmetric with `_read_events_at`. A single
    invalid byte sequence (e.g., from a botched write or truncated multibyte
    character) would otherwise raise `UnicodeDecodeError` and poison the
    entire reverse scan — `read_last_event_from(session_dir, "session_paused")`
    would then return None and `session_end.py` would conclude the session
    was never paused. The replacement substitutes U+FFFD for bad bytes, so
    at most the corrupted line is dropped by the per-line `json.loads`.
    """
    try:
        if not journal.exists():
            return None

        for line in reversed(
            journal.read_text(encoding="utf-8", errors="replace").splitlines()
        ):
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


def _pact_context_is_initialized() -> bool:
    """
    Return True iff pact_context.init() has been called for this process.

    AdvF2 Approach 4 (universal visibility): the implicit-API entry points
    use this to print a stderr warning when a caller invokes them BEFORE
    the surrounding hook has called `pact_context.init(input_data)`. The
    warning is purely additive — the existing fail-open semantics (return
    [], None, or False) are preserved so a missed init() never crashes a
    hook. The signal lets maintainers find the missing init() during
    development instead of debugging silent empty results in production.

    Lazy import mirrors `_get_session_dir` so tests that monkeypatch the
    helper at the session_journal level continue to work.

    Returns:
        True if pact_context._context_path is set, False otherwise.
    """
    try:
        from shared.pact_context import is_initialized
    except ImportError:
        from pact_context import is_initialized  # type: ignore[no-redef]
    return is_initialized()


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


def _validate_cli_session_dir(session_dir: str) -> int:
    """
    Validate the CLI `--session-dir` flag, returning a non-zero exit code
    on failure (and printing the reason to stderr) or 0 on success.

    Two checks, applied to write/read/read-last alike:

    1. Empty string — argparse `required=True` only catches a missing
       flag; an explicit `--session-dir ""` slips past it. Without this
       guard the path silently resolves to `./session-journal.jsonl`
       in the caller's CWD and creates a stray journal file.

    2. Non-absolute path — relative paths are also caller-CWD-relative
       and equally surprising. The journal MUST live under
       `~/.claude/pact-sessions/{slug}/{session_id}/`, so requiring an
       absolute path eliminates an entire class of stray-file bugs.

    Returns exit code 1 (matching the prior empty-string regression test
    contract — non-zero is the load-bearing property; hooks watch for it
    rather than discriminating on the specific code).
    """
    if not session_dir:
        print(
            "session_journal: --session-dir must be non-empty",
            file=sys.stderr,
        )
        return 1
    if not Path(session_dir).is_absolute():
        print(
            "session_journal: --session-dir must be an absolute path",
            file=sys.stderr,
        )
        return 1
    return 0


def _atomic_write(path: Path, data: bytes) -> bool:
    """
    Append *data* to *path* under an exclusive advisory lock.

    Returns True on success, False on OSError or a non-progressing write.
    File is created with 0o600 if it does not exist. The caller is
    responsible for ensuring the parent directory exists before calling.

    Concurrency guarantee: `fcntl.flock(LOCK_EX)` serializes the entire
    write block against other writers that honor the same lock. POSIX
    only guarantees `os.write` atomicity up to PIPE_BUF (512 bytes on
    macOS, 4096 on Linux); for larger payloads the short-write loop
    below would otherwise leave a window between iterations where an
    interleaving O_APPEND from another process could splice bytes into
    the middle of our event and produce a malformed JSONL line. The
    lock closes that window for any event size. Single-host only
    (advisory locks do not cross machines, and NFS flock semantics
    are unreliable) — fine because pact-sessions is per-host.

    Short-write loop rationale: `os.write` can still return fewer bytes
    than requested when interrupted by a signal even while the lock is
    held; the loop retries from where we left off. A non-positive
    return from os.write indicates a failure we cannot recover from —
    bail out and let the caller see False.

    Durability semantics — best-effort, NO fsync. The function returns
    True once the bytes have been handed to the kernel, but does not
    invoke `os.fsync` or `os.fdatasync`. After a hard crash (power loss,
    kernel panic) the most recent event(s) may be lost even though the
    caller saw True. This is intentional: the journal sits on the
    orchestrate hot path (every checkpoint, phase transition, dispatch)
    and a per-write fsync is too expensive — observed write rates would
    drop by 1-2 orders of magnitude on rotational disks. Cross-process
    visibility is immediate after the lock releases; only post-crash
    durability is sacrificed. Callers that need stronger durability
    should fsync at a coarser granularity (e.g., session_end).
    """
    try:
        fd = os.open(
            str(path),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            return False
        try:
            view = memoryview(data)
            total = 0
            while total < len(view):
                try:
                    n = os.write(fd, view[total:])
                except OSError:
                    return False
                if n <= 0:
                    # Non-progressing write — treat as failure so the
                    # caller can log and return False up the stack rather
                    # than spin forever.
                    return False
                total += n
            return True
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


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
    # --data and --stdin are mutually exclusive ways to supply event fields.
    # --data accepts a JSON object as a CLI argument; --stdin reads the same
    # JSON object from standard input. The stdin path exists so that command
    # files can pipe JSON via heredoc — eliminating shell quoting bugs where
    # an apostrophe in a template-substituted value (e.g., a commit message
    # like "fix: don't crash") would otherwise close the bash single-quoted
    # --data argument and silently drop the journal event under set -e + ERR
    # trap. See r9 HIGH "template injection" finding (PR #350).
    data_group = write_p.add_mutually_exclusive_group()
    data_group.add_argument("--data", default=None,
                            help="JSON object of extra event fields "
                                 "(mutually exclusive with --stdin)")
    data_group.add_argument("--stdin", action="store_true",
                            help="Read the JSON object of extra event "
                                 "fields from standard input "
                                 "(mutually exclusive with --data)")

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
        # Resolve the JSON payload from either --stdin or --data. The
        # mutually-exclusive group above guarantees both cannot be set
        # simultaneously; if neither is set we default to "{}" so writers
        # that pass no extra fields (e.g., wrap-up's session_end) keep
        # working unchanged.
        if args.stdin:
            data_source = "stdin"
            try:
                raw = sys.stdin.read()
            except OSError as exc:
                print(f"session_journal: failed to read --stdin: {exc}",
                      file=sys.stderr)
                return 1
        else:
            data_source = "--data"
            raw = args.data if args.data is not None else "{}"

        try:
            extra = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"session_journal: invalid {data_source} JSON: {exc}",
                  file=sys.stderr)
            return 1

        if not isinstance(extra, dict):
            print(f"session_journal: {data_source} must be a JSON object",
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

        # AdvF4 + AdvF5: validate --session-dir up front. Helper enforces
        # both the empty-string guard and the absolute-path requirement,
        # mirroring the read/read-last subcommands so all three share one
        # contract.
        rc = _validate_cli_session_dir(args.session_dir)
        if rc != 0:
            return rc

        journal = _journal_path_from(args.session_dir)
        journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        entry = json.dumps(event, separators=(",", ":")) + "\n"
        if not _atomic_write(journal, entry.encode("utf-8")):
            print("session_journal: write failed", file=sys.stderr)
            return 1
        return 0

    elif args.command == "read":
        rc = _validate_cli_session_dir(args.session_dir)
        if rc != 0:
            return rc
        events = read_events_from(args.session_dir, event_type=args.event_type)
        print(json.dumps(events))
        return 0

    elif args.command == "read-last":
        rc = _validate_cli_session_dir(args.session_dir)
        if rc != 0:
            return rc
        event = read_last_event_from(args.session_dir, args.event_type)
        if event is None:
            print("null")
        else:
            print(json.dumps(event))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
