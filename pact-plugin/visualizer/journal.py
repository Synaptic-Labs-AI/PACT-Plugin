"""Location: pact-plugin/visualizer/journal.py
Summary: Tolerant, read-only JSONL access for PACT session journals. Owns the
         full-file parse (stable file-order seq, malformed/partial/non-UTF-8
         lines skipped with bounded warnings) and the bounded head/tail reads
         used by the cheap session list.
Used by: visualizer.model (full model + session summaries), visualizer.server
         (ETag/since identity), visualizer.cli (--list).

Nothing here writes. A journal is an append-only file that may end in a
partially flushed line; the reader must never crash on it, must keep the file
line index as the event seq, and must report what it skipped in `warnings`
instead of silently dropping data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .settings import (
    JOURNAL_FILENAME,
    LIVE_WINDOW_S,
    MAX_WARNINGS,
    SUMMARY_HEAD_BYTES,
    SUMMARY_TAIL_BYTES,
)


@dataclass(frozen=True)
class RawEvent:
    """One parsed journal line.

    Location: journal.RawEvent.
    Summary: ``seq`` is the zero-based PHYSICAL line index in the file (stable
             across polls); ``data`` is the decoded JSON object. Only objects
             with a non-empty string ``type`` reach this type.
    Used by: classify, model, demo, tests.
    """

    seq: int
    data: dict


@dataclass
class JournalRead:
    """The full tolerant read of one journal file.

    Location: journal.JournalRead.
    Summary: ``events`` in file order, ``warnings`` bounded to
             settings.MAX_WARNINGS, line/event counters for the model's
             metrics, and the stat identity used for the ETag.
    Used by: model, server, tests.
    """

    events: list[RawEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lines_total: int = 0
    valid_events: int = 0
    skipped_lines: int = 0
    mtime: float = 0.0
    size: int = 0

    @property
    def etag(self) -> str:
        """The journal identity token: ``<int mtime>-<size>``."""
        return make_etag(self.mtime, self.size)


@dataclass
class JournalSummary:
    """The cheap session-list summary built from bounded head/tail reads.

    Location: journal.JournalSummary.
    Summary: No full parse: only the first/last windows of the file are read,
             so /api/sessions stays fast on multi-megabyte journals. Fields
             that cannot be recovered from those windows are left empty.
    Used by: model.build_sessions_list, server, cli.
    """

    started_at: str | None = None
    updated_at: str | None = None
    last_event_type: str = ""
    last_phase: str = ""
    team: str = ""
    project_dir: str = ""
    run_kind: str = "session"
    status: str = "ended"
    is_live: bool = False
    mtime: float = 0.0
    size: int = 0
    warnings: list[str] = field(default_factory=list)


def make_etag(mtime: float, size: int) -> str:
    """Build the opaque ETag from a journal's mtime and size.

    Location: journal.make_etag.
    Summary: ``<int(mtime)>-<size>`` matches the contract's example identity
             and changes on any append or metadata update.
    Used by: server (/api/session, /api/sessions), tests.
    """
    return f"{int(mtime)}-{int(size)}"


def journal_stat(path: Path) -> tuple[float, int] | None:
    """Stat a journal, returning (mtime, size) or None when unreadable.

    Location: journal.journal_stat.
    Summary: Total helper used for cheap change detection before a parse.
    Used by: model, server.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return float(stat.st_mtime), int(stat.st_size)


def _iter_lines(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (line_index, raw_line) for every physical line in file order.

    Location: journal._iter_lines.
    Summary: Splits on b"\\n" and keeps the trailing empty fragment only when
             the file does not end in a newline; a mid-write final line is
             still attempted and reported as malformed/partial if it fails.
    Used by: read_journal.
    """
    if not data:
        return
    start = 0
    index = 0
    length = len(data)
    while start <= length:
        end = data.find(b"\n", start)
        if end == -1:
            fragment = data[start:]
            if fragment:
                yield index, fragment
            return
        yield index, data[start:end]
        index += 1
        start = end + 1


def decode_line(raw: bytes) -> str | None:
    """Decode one JSONL line as UTF-8, or None when it is not valid UTF-8.

    Location: journal.decode_line.
    Summary: Decoding is strict on purpose: a non-UTF-8 line is data we cannot
             trust, so it is skipped with a warning instead of being repaired
             with replacement characters that would look like real content.
    Used by: read_journal, summary readers.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _parse_object(line: str) -> dict | None:
    """Parse one line into a JSON object with a usable ``type`` field."""
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    event_type = value.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return None
    return value


def _add_warning(warnings: list[str], message: str) -> None:
    """Append a warning while keeping the list bounded."""
    if len(warnings) < MAX_WARNINGS:
        warnings.append(message)


def read_journal(path: Path) -> JournalRead:
    """Full tolerant read of a session journal.

    Location: journal.read_journal.
    Summary: Every physical line gets a stable seq equal to its file line
             index. Blank lines are skipped silently; malformed JSON,
             non-object values, missing/blank type, non-UTF-8 bytes and a
             partially written final line are skipped with one bounded
             warning each (counted, not enumerated forever). A missing or
             unreadable file yields an empty read with the stat zeroed.
    Used by: model.build_model, server, tests.
    """
    read = JournalRead()
    stat = journal_stat(path)
    if stat is not None:
        read.mtime, read.size = stat
    try:
        data = path.read_bytes()
    except OSError:
        _add_warning(read.warnings, "journal could not be read")
        return read

    for index, raw_line in _iter_lines(data):
        read.lines_total += 1
        if not raw_line.strip():
            continue
        text = decode_line(raw_line)
        if text is None:
            read.skipped_lines += 1
            _add_warning(read.warnings, f"line {index}: skipped non-UTF-8 content")
            continue
        value = _parse_object(text)
        if value is None:
            read.skipped_lines += 1
            _add_warning(read.warnings, f"line {index}: skipped malformed event")
            continue
        read.events.append(RawEvent(seq=index, data=value))
        read.valid_events += 1
    return read


def _read_window(path: Path, start: int | None, end: int | None) -> bytes:
    """Read a byte window from a file without loading the whole file.

    Location: journal._read_window.
    Summary: Bounded helper for the summary readers; any OSError reads as
             b"". ``start=None`` reads the final ``end`` bytes; ``end=None``
             reads from ``start`` to EOF.
    Used by: read_head_events, read_tail_events, summarize_journal.
    """
    try:
        with path.open("rb") as handle:
            if start is None:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                begin = max(0, size - int(end or 0))
                handle.seek(begin)
                return handle.read()
            handle.seek(int(start))
            if end is None:
                return handle.read()
            return handle.read(int(end))
    except OSError:
        return b""


def read_head_events(path: Path, limit: int = 40) -> list[RawEvent]:
    """Parse up to ``limit`` valid events from the start of a journal.

    Location: journal.read_head_events.
    Summary: Uses settings.SUMMARY_HEAD_BYTES so a huge journal is never read
             whole; the first window's lines are parsed with the same tolerant
             rules (a boundary-truncated final line is skipped).
    Used by: summarize_journal, tests.
    """
    data = _read_window(path, 0, SUMMARY_HEAD_BYTES)
    events: list[RawEvent] = []
    for index, raw_line in _iter_lines(data):
        if not raw_line.strip():
            continue
        text = decode_line(raw_line)
        if text is None:
            continue
        value = _parse_object(text)
        if value is None:
            continue
        events.append(RawEvent(seq=index, data=value))
        if len(events) >= limit:
            break
    return events


def read_tail_events(path: Path, limit: int = 80) -> list[RawEvent]:
    """Parse up to ``limit`` valid events from the end of a journal.

    Location: journal.read_tail_events.
    Summary: Reads settings.SUMMARY_TAIL_BYTES and returns valid events in
             file order (ascending seq). The first physical line of the window
             may be a fragment; it is skipped like any malformed line. seq is
             NOT the true file line index for tail reads because the byte
             offset can fall mid-file; callers use it only for ordering. The
             full model always uses read_journal for real seq values.
    Used by: summarize_journal, tests.
    """
    stat = journal_stat(path)
    size = 0 if stat is None else stat[1]
    window = min(size, SUMMARY_TAIL_BYTES)
    data = _read_window(path, size - window if window else 0, None)
    events: list[RawEvent] = []
    for index, raw_line in _iter_lines(data):
        if not raw_line.strip():
            continue
        text = decode_line(raw_line)
        if text is None:
            continue
        value = _parse_object(text)
        if value is None:
            continue
        events.append(RawEvent(seq=index, data=value))
    if len(events) > limit:
        events = events[-limit:]
    return events


_PHASE_START_TYPES = frozenset(
    {
        "phase_transition",
        "agent_dispatch",
        "checkpoint",
        "dispatch_decision",
        "dispatch_site",
        "dispatch_variety",
        "variety_assessed",
        "session_end",
    }
)


def _timestamp_of(value: object) -> str | None:
    """Return a non-empty event ts string, or None."""
    return value if isinstance(value, str) and value else None


def summarize_journal(path: Path, now: datetime | None = None) -> JournalSummary:
    """Build the cheap /api/sessions summary from bounded head/tail reads.

    Location: journal.summarize_journal.
    Summary: Provides started_at/updated_at/last_event_type/last_phase/team/
             project_dir/run_kind/status without parsing the whole journal.
             ``status`` is "live" only when no session_end is visible in the
             tail window and the file mtime is within settings.LIVE_WINDOW_S
             of ``now``; otherwise "ended". All failures read as empty fields.
    Used by: model.build_sessions_list, cli (--list), tests.
    """
    summary = JournalSummary()
    stat = journal_stat(path)
    if stat is None:
        return summary
    summary.mtime, summary.size = stat
    current = now if now is not None else datetime.now(timezone.utc)

    head = read_head_events(path)
    if head:
        first = head[0]
        summary.started_at = _timestamp_of(first.data.get("ts"))
        for event in head:
            data = event.data
            if data.get("type") == "session_start":
                team = data.get("team")
                if isinstance(team, str) and team:
                    summary.team = team
                project_dir = data.get("project_dir")
                if isinstance(project_dir, str) and project_dir:
                    summary.project_dir = project_dir
                if summary.started_at is None:
                    summary.started_at = _timestamp_of(data.get("ts"))
                break

    tail = read_tail_events(path)
    if tail:
        last = tail[-1]
        summary.updated_at = _timestamp_of(last.data.get("ts")) or summary.updated_at
        summary.last_event_type = str(last.data.get("type", ""))
        ended = summary.last_event_type == "session_end"
        for event in reversed(tail):
            data = event.data
            event_type = data.get("type")
            if event_type in _PHASE_START_TYPES:
                summary.run_kind = "orchestrate"
                if event_type == "session_end":
                    ended = True
                break
        for event in reversed(tail):
            data = event.data
            if data.get("type") == "phase_transition":
                phase = data.get("phase")
                if isinstance(phase, str) and phase:
                    summary.last_phase = phase
                break
        if ended:
            summary.status = "ended"
        else:
            age = max(0.0, current.timestamp() - summary.mtime)
            summary.status = "live" if age <= LIVE_WINDOW_S else "ended"
    else:
        age = max(0.0, current.timestamp() - summary.mtime)
        summary.status = "live" if age <= LIVE_WINDOW_S else "ended"

    if summary.updated_at is None:
        summary.updated_at = _iso_from_mtime(summary.mtime)
    summary.is_live = summary.status == "live"
    return summary


def _iso_from_mtime(mtime: float) -> str | None:
    """Render a stat mtime as an ISO-8601 UTC timestamp, or None."""
    if mtime <= 0:
        return None
    try:
        return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OverflowError, OSError, ValueError):
        return None


def journal_relative_path(slug: str, session_id: str) -> str:
    """The journal path as shown in the model's source block.

    Location: journal.journal_relative_path.
    Summary: ``pact-sessions/<slug>/<session_id>/session-journal.jsonl`` — a
             root-relative POSIX path, never an absolute one, so the payload
             does not leak the operator's home directory.
    Used by: model.build_model.
    """
    return f"pact-sessions/{slug}/{session_id}/{JOURNAL_FILENAME}"
