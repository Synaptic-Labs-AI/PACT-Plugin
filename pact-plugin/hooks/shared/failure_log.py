"""
Location: pact-plugin/hooks/shared/failure_log.py
Summary: Bounded ring buffer log for session_init failures that cannot use
         the normal per-session journal path.
Used by: session_init.py (R3 malformed-stdin gate) to record failures that
         leave no per-session directory, giving post-hoc observability across
         ALL affected sessions (lead and teammates) without risking an
         unreapable `unknown-{hex}/` directory leak.

The normal session_journal.py writes into a per-session directory keyed by
session_id. When session_init.py hits a malformed-stdin path it has no
valid session_id: creating a synthetic `unknown-{hex}/` directory would leak
indefinitely because session_end.cleanup_old_sessions filters on
_UUID_PATTERN and will never reap an "unknown-*" slug. PR #390 R3 chose to
drop the session_start anchor entirely rather than leak disk — at the cost
of leaving no persistent record that the hook even fired. That loss is
especially painful for teammate sessions, whose first-message context is
never seen by the user; a silent teammate degradation would only surface as
downstream wrongness whose cause could not be traced back to the hook.

This file is that post-hoc record. A small bounded ring buffer at a fixed
global path — underscore-prefixed so cleanup_old_sessions never matches it —
captures one JSONL line per failure with just enough fields to detect
patterns (timestamp, classification, truncated error, cwd, source) without
leaking sensitive payload content.

Fail-open invariant (SACROSANCT): append_failure MUST NEVER raise. The log
exists to observe session_init failures, not create new ones. Every write
path is wrapped in a top-level try/except that swallows ALL exceptions and
returns cleanly — lock timeouts, OSError, JSON encode failures, any unknown
exception class. The call site in session_init.py adds its own
belt-and-suspenders try/except for defense in depth.

Rotation: read all → keep last (MAX_ENTRIES - 1) → append new → write back,
under an exclusive `file_lock` from claude_md_manager. The file is small
(~10KB max at 100 entries × ~100 bytes/entry), so a full rewrite is cheap
and the semantics are trivially correct. This is structurally different
from session_journal's O_APPEND path — a ring buffer cannot append blindly
without losing the oldest entry.

File location: ~/.claude/pact-sessions/_session_init_failures.log
Permissions: 0o600 (owner read/write only); parent directory 0o700.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .claude_md_manager import file_lock


# Locked decision: global log at the pact-sessions root. Underscore prefix
# prevents cleanup_old_sessions from reaping it (that reaper filters on
# _UUID_PATTERN, which "_session_init_failures.log" never matches).
LOG_PATH = (
    Path.home() / ".claude" / "pact-sessions" / "_session_init_failures.log"
)

# Bounded ring buffer cap: large enough to see a multi-day failure pattern,
# small enough that read-truncate-write stays fast (~10KB file at steady state).
MAX_ENTRIES = 100

# Error field truncation limit. Bounds entry size and prevents a pathological
# exception message (e.g., a huge stdin echoed back) from blowing up the log.
_ERROR_MAX_CHARS = 200


def append_failure(
    classification: str,
    error: str,
    cwd: str | None = None,
    source: str | None = None,
) -> None:
    """
    Append a session_init failure record to the ring buffer log.

    Record fields:
        ts             — ISO 8601 UTC timestamp (matches session_journal)
        classification — enum string identifying the failure kind
                         (e.g. "malformed_json", "missing_session_id",
                         "non_string_session_id", "sentinel_session_id")
        error          — free-form error detail, truncated to 200 chars
        cwd            — current working directory (optional)
        source         — session source hint (optional, e.g. "startup",
                         "resume", "compact")

    Rotation: reads the existing log, keeps the most recent
    (MAX_ENTRIES - 1) entries, appends the new record, and writes the full
    result back under an exclusive `file_lock`. The file stays bounded.

    Fail-open contract: returns None on success AND on any failure. Never
    raises. Lock timeouts, missing directories, disk errors, JSON encode
    failures, and malformed existing content are all swallowed silently.
    The log exists to observe session_init failures, not create new ones.

    Args:
        classification: Short enum string identifying the failure kind.
        error: Human-readable error detail. Truncated to 200 chars.
        cwd: Current working directory at the time of failure (optional).
        source: Session source string from stdin input (optional).
    """
    try:
        # Construct the record up front so any formatting failure (unlikely
        # with these field types, but defensive) is caught by the outer
        # except and drops the append cleanly rather than propagating.
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "classification": classification,
            "error": (error or "")[:_ERROR_MAX_CHARS],
            "cwd": cwd,
            "source": source,
        }

        # Ensure parent dir exists. 0o700 matches the rest of PACT's
        # secure-by-default permission scheme.
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        # file_lock serializes the read-mutate-write critical section.
        # Timeout fails open via the outer try/except below.
        with file_lock(LOG_PATH):
            existing_lines: list[str] = []
            if LOG_PATH.exists():
                try:
                    # errors="replace" matches session_journal read conventions:
                    # a single bad byte substitutes U+FFFD instead of raising,
                    # so a corrupt byte in one line cannot poison the whole read.
                    existing_lines = LOG_PATH.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                except OSError:
                    existing_lines = []

            # Keep only well-formed existing lines. Malformed lines are
            # silently dropped on rotation — fail-open read semantics match
            # session_journal's _read_events_at.
            kept: list[str] = []
            for line in existing_lines:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    continue
                kept.append(stripped)

            # Ring buffer cap: keep the most recent (MAX_ENTRIES - 1) so the
            # new record lands within the cap.
            if len(kept) > MAX_ENTRIES - 1:
                kept = kept[-(MAX_ENTRIES - 1):]

            new_line = json.dumps(record, separators=(",", ":"))
            kept.append(new_line)

            # Single write under the lock — read-truncate-write is correct
            # for a bounded ring buffer and avoids O_APPEND's "lose oldest"
            # impossibility.
            LOG_PATH.write_text(
                "\n".join(kept) + "\n", encoding="utf-8"
            )
            try:
                LOG_PATH.chmod(0o600)
            except OSError:
                # Permission bump is best-effort; the write already succeeded.
                pass
    except Exception:
        # SACROSANCT: never raise. A logging failure must not crash
        # session_init. Silent swallow matches the fail-open invariant.
        return


def read_failures() -> list[dict[str, Any]]:
    """
    Read the ring buffer log and return parsed entries in chronological order.

    Fail-open contract: returns [] on any failure — missing file, lock
    timeout, OSError, malformed JSON. Malformed individual lines are
    silently skipped (matching session_journal's _read_events_at).

    Returns:
        List of parsed record dicts, oldest first. Empty list on any error
        or when the log does not exist.
    """
    try:
        if not LOG_PATH.exists():
            return []
        # Read does not require the exclusive lock — JSONL lines are
        # self-contained and _atomic_write style integrity guarantees
        # we never see a partially-written line from a concurrent writer
        # because the writer holds the lock across the full rewrite.
        # errors="replace" prevents one bad byte from poisoning the scan.
        entries: list[dict[str, Any]] = []
        for line in LOG_PATH.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except (json.JSONDecodeError, ValueError):
                continue  # Skip malformed lines
        return entries
    except Exception:
        return []
