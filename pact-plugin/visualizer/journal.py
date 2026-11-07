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
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .settings import (
    JOURNAL_FILENAME,
    LIVE_WINDOW_S,
    MAX_JOURNAL_BYTES,
    MAX_WARNINGS,
    ORCHESTRATION_MARKER_TYPES,
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
    tail_only: bool = False

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
    ended_by: str | None = "idle"
    is_live: bool = False
    mtime: float = 0.0
    mtime_ns: int = 0
    size: int = 0
    lifecycle: Lifecycle | None = None
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
             warning each (counted, not enumerated forever). A journal larger
             than MAX_JOURNAL_BYTES is parsed from its LAST MAX_JOURNAL_BYTES
             only (``tail_only``, with a warning): the partial first line is
             dropped and the skipped prefix's newlines are counted, so seq
             stays the true file line index. A missing or unreadable file
             yields an empty read.
    Used by: model.build_model, server, tests.
    """
    read = JournalRead()
    try:
        status = os.stat(path)
    except OSError:
        status = None
    stat = None if status is None else (float(status.st_mtime), int(status.st_size))
    if stat is not None:
        read.mtime, read.size = stat
    if status is not None and stat[1] <= MAX_JOURNAL_BYTES:
        try:
            return _read_incremental(path, status, read)
        except OSError:
            _add_warning(read.warnings, "journal could not be read")
            return read
    first_index = 0
    try:
        if stat is not None and stat[1] > MAX_JOURNAL_BYTES:
            read.tail_only = True
            data, first_index = _read_tail_lines(path, stat[1] - MAX_JOURNAL_BYTES)
            _add_warning(
                read.warnings,
                f"journal is {stat[1]} bytes (over the {MAX_JOURNAL_BYTES} byte cap); "
                f"showing only its last {MAX_JOURNAL_BYTES} bytes",
            )
        else:
            data = path.read_bytes()
    except OSError:
        _add_warning(read.warnings, "journal could not be read")
        return read

    for offset, raw_line in _iter_lines(data):
        _fold_line(read, first_index + offset, raw_line)
    return read


def _fold_line(read: JournalRead, index: int, raw_line: bytes) -> None:
    """Parse one physical line into ``read`` (event, or a counted skip)."""
    read.lines_total += 1
    if not raw_line.strip():
        return
    text = decode_line(raw_line)
    if text is None:
        read.skipped_lines += 1
        _add_warning(read.warnings, f"line {index}: skipped non-UTF-8 content")
        return
    value = _parse_object(text)
    if value is None:
        read.skipped_lines += 1
        _add_warning(read.warnings, f"line {index}: skipped malformed event")
        return
    read.events.append(RawEvent(seq=index, data=value))
    read.valid_events += 1


@dataclass
class _ParseState:
    """The cached parse of a journal's complete lines (incremental reads)."""

    device: int
    inode: int
    offset: int
    next_index: int
    read: JournalRead


# Per-journal parse of every COMPLETE line read so far, so a poll of a growing
# journal parses only its new lines. Bounded: whole-journal event lists are
# large, and only the runs a page has open are read repeatedly.
_PARSE_CACHE: "OrderedDict[str, _ParseState]" = OrderedDict()
_PARSE_CACHE_MAX = 4
_PARSE_LOCK = threading.Lock()


def _copy_read(source: JournalRead) -> JournalRead:
    """A JournalRead whose lists can be extended without touching ``source``."""
    return JournalRead(
        events=list(source.events),
        warnings=list(source.warnings),
        lines_total=source.lines_total,
        valid_events=source.valid_events,
        skipped_lines=source.skipped_lines,
        mtime=source.mtime,
        size=source.size,
        tail_only=source.tail_only,
    )


def _read_incremental(path: Path, status: os.stat_result, result: JournalRead) -> JournalRead:
    """read_journal for a journal under the cap, parsing only new lines.

    Location: journal._read_incremental.
    Summary: The cached state holds the parse of every complete line up to a
             byte offset. A journal with the same device and inode that has
             not shrunk resumes from that offset; anything else (a new file,
             a rotation, a truncation) is parsed in full. A trailing fragment
             without its newline is parsed for this read only, never cached,
             because the writer may still be completing it.
    Used by: read_journal.
    """
    key = str(path)
    with _PARSE_LOCK:
        state = _PARSE_CACHE.get(key)
        if state is not None:
            _PARSE_CACHE.move_to_end(key)
    same_file = (
        state is not None
        and state.device == int(status.st_dev)
        and state.inode == int(status.st_ino)
        and int(status.st_size) >= state.offset
    )
    if same_file:
        base = _copy_read(state.read)
        offset, next_index = state.offset, state.next_index
    else:
        base, offset, next_index = JournalRead(), 0, 0
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    cut = data.rfind(b"\n") + 1
    complete, fragment = data[:cut], data[cut:]
    for line_offset, raw_line in _iter_lines(complete):
        _fold_line(base, next_index + line_offset, raw_line)
    next_index += complete.count(b"\n")
    with _PARSE_LOCK:
        _PARSE_CACHE[key] = _ParseState(
            int(status.st_dev), int(status.st_ino), offset + cut, next_index, _copy_read(base)
        )
        _PARSE_CACHE.move_to_end(key)
        while len(_PARSE_CACHE) > _PARSE_CACHE_MAX:
            _PARSE_CACHE.popitem(last=False)
    if fragment:
        _fold_line(base, next_index, fragment)
    base.mtime, base.size = result.mtime, result.size
    return base


def _read_tail_lines(path: Path, start: int) -> tuple[bytes, int]:
    """The whole lines from byte ``start`` to EOF, and the first one's line index.

    The bytes before ``start`` are only scanned for newlines (in chunks), so
    the returned data's first line keeps its true file line index. A line cut
    by ``start`` is dropped whole.
    """
    newlines = 0
    with path.open("rb") as handle:
        remaining = start
        last = b"\n"
        while remaining > 0:
            chunk = handle.read(min(remaining, 1 << 20))
            if not chunk:
                break
            newlines += chunk.count(b"\n")
            last = chunk[-1:]
            remaining -= len(chunk)
        data = handle.read()
    if last != b"\n":
        cut = data.find(b"\n")
        data = b"" if cut == -1 else data[cut + 1:]
        newlines += 1
    return data, newlines


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


_HEAD_CACHE: "OrderedDict[str, tuple[tuple[int, int, int], list[RawEvent]]]" = OrderedDict()
_SUMMARY_CACHE: "OrderedDict[str, tuple[tuple[int, int, int], JournalSummary]]" = OrderedDict()
_SUMMARY_CACHE_MAX = 4096
_SUMMARY_LOCK = threading.Lock()


def _identity(path: Path) -> tuple[tuple[int, int, int], os.stat_result] | None:
    """(inode, mtime_ns, size) and the stat of a journal, or None."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return (int(status.st_ino), int(status.st_mtime_ns), int(status.st_size)), status


def _cached(cache: OrderedDict, key: str, identity: tuple[int, int, int]):
    """A cache value whose identity still matches, else None (thread-safe)."""
    with _SUMMARY_LOCK:
        hit = cache.get(key)
        if hit is not None and hit[0] == identity:
            cache.move_to_end(key)
            return hit[1]
    return None


def _remember(cache: OrderedDict, key: str, identity: tuple[int, int, int], value) -> None:
    """Store a cache value, evicting the least recently used past the bound."""
    with _SUMMARY_LOCK:
        cache[key] = (identity, value)
        cache.move_to_end(key)
        while len(cache) > _SUMMARY_CACHE_MAX:
            cache.popitem(last=False)


def head_events(path: Path) -> list[RawEvent]:
    """read_head_events, cached by (inode, mtime_ns, size)."""
    found = _identity(path)
    if found is None:
        return []
    identity, _status = found
    cached = _cached(_HEAD_CACHE, str(path), identity)
    if cached is not None:
        return cached
    events = read_head_events(path)
    _remember(_HEAD_CACHE, str(path), identity, events)
    return events


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


# THE single marker set for "this run is an orchestration". settings owns the
# definition so the full model (model._ORCHESTRATION_TYPES) and this cheap
# summary can never disagree about which event types count; session_end is
# deliberately absent because every run ends.
_ORCHESTRATION_TYPES = frozenset(ORCHESTRATION_MARKER_TYPES)

# Types the cheap summary records the newest of in its backward walk: the
# marker set (run_kind) plus phase_transition (last_phase).
_SUMMARY_SCAN_TYPES = _ORCHESTRATION_TYPES | {"phase_transition"}


def _timestamp_of(value: object) -> str | None:
    """Return a non-empty event ts string, or None."""
    return value if isinstance(value, str) and value else None


# How many tail-sized windows a backward scan will walk before giving up:
# 4 x SUMMARY_TAIL_BYTES (the 512KB budget CONTRACT.md documents). THE single
# budget for every bounded lookup in this module — the phase scan, the
# orchestration-marker scan and the session_end scan all use it, so one number
# describes what the cheap summary can and cannot see.
_PHASE_SCAN_MAX_WINDOWS = 4


def _iter_back(path: Path, size: int) -> Iterator[dict]:
    """Yield the journal's valid events newest-first within the scan budget.

    Location: journal._iter_back.
    Summary: Walks tail-sized windows from the end of the file toward the
             start (at most _PHASE_SCAN_MAX_WINDOWS of them). Each window's
             LEADING partial line is carried into the next (earlier) window
             instead of being dropped: a line that straddles a window boundary
             is a fragment in both windows, so without the carry an event on
             a boundary would be invisible to every bounded scan. A window that
             begins exactly ON a line boundary keeps its first line whole — one
             byte of look-behind tells the two cases apart, and getting it
             wrong would lose an aligned event at the outermost budget edge.
    Used by: _scan_back, summarize_journal, lifecycle_scan.
    """
    offset = size
    carry = b""
    for _ in range(_PHASE_SCAN_MAX_WINDOWS):
        if offset <= 0:
            return
        start = max(0, offset - SUMMARY_TAIL_BYTES)
        # The probe byte is dropped from `data`: the next (earlier) window
        # already covers it as its last byte.
        if start > 0:
            head = _read_window(path, start - 1, offset - start + 1)
            starts_on_boundary = head[:1] == b"\n"
            data = head[1:] + carry
        else:
            starts_on_boundary = True
            data = _read_window(path, start, offset - start) + carry
        if start > 0 and not starts_on_boundary:
            newline = data.find(b"\n")
            if newline == -1:
                # One line longer than a whole window: keep it whole and let
                # the earlier window supply its head.
                carry, offset = data, start
                continue
            carry = data[:newline]
            data = data[newline + 1:]
        else:
            carry = b""
        for _, raw_line in reversed(list(_iter_lines(data))):
            if not raw_line.strip():
                continue
            text = decode_line(raw_line)
            if text is None:
                continue
            value = _parse_object(text)
            if value is not None:
                yield value
        offset = start


def _scan_back(path: Path, size: int, wanted: frozenset[str]) -> dict[str, dict]:
    """Newest event of each requested type within the scan budget.

    Location: journal._scan_back.
    Summary: ``{type: event data}`` for the first — i.e. newest — event of
             each requested type that _iter_back yields.
    Used by: tests (the summary itself folds the same walk with the
             lifecycle reader in summarize_journal).
    """
    found: dict[str, dict] = {}
    for value in _iter_back(path, size):
        event_type = value.get("type")
        if event_type in wanted and event_type not in found:
            found[event_type] = value
            if len(found) == len(wanted):
                break
    return found


# --- Run status: one rule for the summary and the full model ---------------

LIFECYCLE_TYPES = frozenset({"session_start", "session_end"})
# What the SessionEnd hook may write after its own session_end.
END_TRAILER_TYPES = frozenset({"session_end", "cleanup_summary"})
_PAUSE_STATUS = {"session_paused": "paused", "session_refreshed": "refreshed"}


@dataclass
class Lifecycle:
    """What the newest lifecycle events say about a run.

    Location: journal.Lifecycle.
    Summary: Fed events NEWEST FIRST. ``last`` is the newest lifecycle event
             type (None when none was seen); ``trailers_only`` is True when
             only END_TRAILER_TYPES follow the newest session_end; ``pause``
             is "paused"/"refreshed" when the newest such event lies after the
             newest session_start and before that session_end. The full model
             feeds the whole reversed event list and the cheap summary feeds
             the bounded backward walk, so both derive status with one rule.
    Used by: run_status, summarize_journal, lifecycle_scan, model.
    """

    last: str | None = None
    trailers_only: bool = False
    pause: str | None = None
    done: bool = False
    _saw_other: bool = False

    def feed(self, event_type: object) -> bool:
        """Fold one event (newest first); True once nothing older matters."""
        if self.done:
            return True
        if self.last is None:
            if event_type == "session_start":
                self.last = "session_start"
                self.done = True
            elif event_type == "session_end":
                self.last = "session_end"
                self.trailers_only = not self._saw_other
                self.done = not self.trailers_only
            elif event_type not in END_TRAILER_TYPES:
                self._saw_other = True
            return self.done
        if event_type == "session_start":
            self.done = True
        elif self.pause is None and event_type in _PAUSE_STATUS:
            self.pause = _PAUSE_STATUS[str(event_type)]
        return self.done


def run_status(lifecycle: Lifecycle, activity_ts: float, now_ts: float) -> tuple[str, str | None]:
    """(status, ended_by) for a run.

    Location: journal.run_status.
    Summary: Ended by its own session_end only when that is the newest
             lifecycle event and nothing but end trailers follows it — then
             the status is "paused"/"refreshed" when a pause or refresh was
             recorded since the newest session_start, else "ended". Otherwise
             the activity clock decides: "live" within settings.LIVE_WINDOW_S
             of ``now_ts`` (ended_by None), else ("ended", "idle").
    Used by: summarize_journal, model, server (ETag short-circuit).
    """
    if lifecycle.last == "session_end" and lifecycle.trailers_only:
        return lifecycle.pause or "ended", "session_end"
    if activity_ts and now_ts - activity_ts <= LIVE_WINDOW_S:
        return "live", None
    return "ended", "idle"


def lifecycle_of_events(events_newest_first: Iterable[dict]) -> Lifecycle:
    """Feed event payloads newest-first into a fresh Lifecycle."""
    lifecycle = Lifecycle()
    for data in events_newest_first:
        if lifecycle.feed(data.get("type")):
            break
    return lifecycle


_LIFECYCLE_CACHE: dict[str, tuple[tuple[int, int, int], Lifecycle]] = {}
_LIFECYCLE_CACHE_MAX = 512
_LIFECYCLE_LOCK = threading.Lock()


def lifecycle_scan(path: Path) -> tuple[Lifecycle, os.stat_result | None]:
    """The bounded-walk Lifecycle of a journal plus its stat, cached by identity.

    Location: journal.lifecycle_scan.
    Summary: Keyed by (inode, mtime_ns, size), so an append re-scans and an
             unchanged journal costs one stat. The cache is bounded and
             thread-safe; a stat failure returns an empty Lifecycle.
    Used by: model (ETag token), server (since short-circuit).
    """
    try:
        stat = os.stat(path)
    except OSError:
        return Lifecycle(), None
    identity = (int(stat.st_ino), int(stat.st_mtime_ns), int(stat.st_size))
    key = str(path)
    with _LIFECYCLE_LOCK:
        cached = _LIFECYCLE_CACHE.get(key)
    if cached is not None and cached[0] == identity:
        return cached[1], stat
    lifecycle = lifecycle_of_events(_iter_back(path, int(stat.st_size)))
    with _LIFECYCLE_LOCK:
        if len(_LIFECYCLE_CACHE) >= _LIFECYCLE_CACHE_MAX and key not in _LIFECYCLE_CACHE:
            _LIFECYCLE_CACHE.pop(next(iter(_LIFECYCLE_CACHE)))
        _LIFECYCLE_CACHE[key] = (identity, lifecycle)
    return lifecycle, stat


def summarize_journal(
    path: Path,
    now: datetime | None = None,
    activity_ts: float | None = None,
) -> JournalSummary:
    """Build the cheap /api/sessions summary from bounded head/tail reads.

    Location: journal.summarize_journal.
    Summary: Provides started_at/updated_at/last_event_type/last_phase/team/
             project_dir/run_kind/status/ended_by without parsing the whole
             journal. last_phase, run_kind and the lifecycle all come from ONE
             backward walk over the shared 512KB budget, and status comes from
             run_status — the same rule the full model applies to the whole
             file. ``activity_ts`` (the newest mtime of the run's journal and
             team/task files, when the caller knows it) drives the clock; the
             journal mtime alone is used otherwise. All failures read as empty
             fields.
    Used by: model.build_sessions_list, cli (--list), tests.
    """
    found = _identity(path)
    if found is None:
        return JournalSummary()
    identity, stat = found
    current = now if now is not None else datetime.now(timezone.utc)
    core = _cached(_SUMMARY_CACHE, str(path), identity)
    if core is None:
        core = _summary_core(path, stat)
        _remember(_SUMMARY_CACHE, str(path), identity, core)
    summary = JournalSummary(**{**core.__dict__, "warnings": list(core.warnings)})
    clock = max(summary.mtime, float(activity_ts or 0.0))
    summary.status, summary.ended_by = run_status(
        summary.lifecycle or Lifecycle(), clock, current.timestamp()
    )
    summary.is_live = summary.status == "live"
    return summary


def _summary_core(path: Path, stat: os.stat_result) -> JournalSummary:
    """Every summary field that depends only on the file's bytes (cacheable)."""
    summary = JournalSummary()
    summary.mtime, summary.mtime_ns, summary.size = (
        float(stat.st_mtime), int(stat.st_mtime_ns), int(stat.st_size)
    )

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

    # ONE backward walk over ONE budget answers: the newest phase, whether a
    # marker makes this an orchestration, and the run's lifecycle.
    found: dict[str, dict] = {}
    lifecycle = Lifecycle()
    wanted = _SUMMARY_SCAN_TYPES
    for value in _iter_back(path, summary.size) if summary.size else ():
        event_type = value.get("type")
        if event_type in wanted and event_type not in found:
            found[event_type] = value
        lifecycle.feed(event_type)
        if lifecycle.done and len(found) == len(wanted):
            break
    summary.lifecycle = lifecycle
    phase = (found.get("phase_transition") or {}).get("phase")
    if isinstance(phase, str) and phase:
        summary.last_phase = phase
    if _ORCHESTRATION_TYPES.intersection(found):
        summary.run_kind = "orchestrate"

    if summary.run_kind != "orchestrate" and head:
        # A run's opening markers sit in the first 64KB even when every marker
        # older than the 512KB tail budget has scrolled out of the backward
        # walk. Markers between the two windows stay invisible to the cheap
        # summary — the documented residual.
        for event in head:
            if event.data.get("type") in _ORCHESTRATION_TYPES:
                summary.run_kind = "orchestrate"
                break

    if summary.updated_at is None:
        summary.updated_at = _iso_from_mtime(summary.mtime)
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
