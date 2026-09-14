"""
Location: pact-plugin/hooks/shared/compaction_owner.py
Summary: Decides whether a compaction hook frame belongs to the lead or to an
         in-process teammate, by reading the transcripts on disk.
Used by: postcompact_archive.py, session_init.py (SessionStart source=compact)
         and missed_wake_scan.py (SessionStart source=compact).

An in-process teammate compacts inside the lead's process, and its PreCompact,
SessionStart and PostCompact frames carry the lead's agent_type, session_id and
transcript_path. No frame field tells the two apart. What differs is the
compacting agent's own transcript, which gains a compact_boundary record: the
lead's is the frame's transcript_path, and a teammate's is
<transcript dir>/<session_id>/subagents/agent-*.jsonl.

PostCompact carries the summary, and the text inside its <summary> block is
written into the compacting agent's transcript, so it is attributed by content.
The transcript renders that text stripped, after a "Summary:" line, with runs of
newlines collapsed to one blank line, so both sides are compared in that form.
The summary record can sit after another agent's message and be stamped before
its boundary, so the match ignores order and the record's timestamp; freshness
applies to boundaries only. SessionStart carries no content and is attributed
by timing: a fresh lead boundary means the lead, and a fresh subagent boundary
with no lead boundary by LEAD_GUARD_S means a teammate.

Only a teammate verdict should change a caller's behaviour. unknown keeps
today's behaviour, because suppressing a real lead's summary or directive is
worse than a teammate receiving lead-shaped output.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .paths import get_claude_config_dir
from .session_state import is_safe_path_component

# ponytail: timing constants from one machine's captures (the lead's boundary
# visible within 0.5 s of hook entry; a teammate's boundary stamped up to
# 0.96 s before entry). Re-measure if compaction_attributed records unknown for
# a real compaction.
POLL_S = 0.25
BACK_S = 3.0
LEAD_GUARD_S = 5.0
T_SESSIONSTART = 5.0
T_POSTCOMPACT = 3.0
READ_BACK_BYTES = 2 * 1024 * 1024
MIN_BODY_CHARS = 200

TEAMMATE, LEAD, UNKNOWN = "teammate", "lead", "unknown"
CONTENT, TIMING, DEADLINE, NO_SIGNAL = "content", "timing", "deadline", "no_signal"

_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_NEWLINE_RUN_RE = re.compile(r"\n\n+")


def _collapse_newlines(text: str) -> str:
    """Runs of newlines as one blank line, the form the transcript stores."""
    return _NEWLINE_RUN_RE.sub("\n\n", text)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def attribute_compaction(
    frame: Any,
    *,
    now: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> "tuple[str, str]":
    """(verdict, basis) for a compaction frame. Never raises.

    verdict is teammate, lead or unknown; basis is content, timing, deadline or
    no_signal. Reads only hook_event_name, source, session_id, transcript_path
    and compact_summary. Returns unknown with no sleep when the frame is not a
    PostCompact or SessionStart(compact), or when the transcripts cannot be
    located inside the config root's projects folder.
    """
    try:
        if not isinstance(frame, dict):
            return UNKNOWN, NO_SIGNAL
        event = frame.get("hook_event_name")
        if event == "PostCompact":
            body = _summary_body(frame.get("compact_summary"))
        elif event == "SessionStart" and frame.get("source") == "compact":
            body = None
        else:
            return UNKNOWN, NO_SIGNAL
        located = _locate(frame.get("transcript_path"), frame.get("session_id"))
        if located is None:
            return UNKNOWN, NO_SIGNAL
        reader = _Reader(*located, since=now().timestamp() - BACK_S, body=body)
        start = monotonic()
        if event == "PostCompact" and body is not None:
            return _by_content(reader, start, monotonic, sleep)
        if event == "PostCompact":
            return _by_timing(reader, start, T_POSTCOMPACT, T_POSTCOMPACT, monotonic, sleep)
        return _by_timing(reader, start, LEAD_GUARD_S, T_SESSIONSTART, monotonic, sleep)
    except Exception:
        return UNKNOWN, NO_SIGNAL


def teammate_compaction(
    frame: Any,
    *,
    now: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True iff attribute_compaction gives the teammate verdict."""
    return attribute_compaction(frame, now=now, monotonic=monotonic, sleep=sleep)[0] == TEAMMATE


def _summary_body(summary: Any) -> "str | None":
    """The text inside <summary>...</summary>, or None when absent or too short to match on."""
    if not isinstance(summary, str):
        return None
    match = _SUMMARY_RE.search(summary)
    if match is None:
        return None
    body = match.group(1).strip()
    body = _collapse_newlines(body)
    return body if len(body) >= MIN_BODY_CHARS else None


def _locate(transcript: Any, session_id: Any) -> "tuple[Path, Path] | None":
    """(lead transcript, subagents folder), both existing and inside projects/, or None."""
    if not isinstance(transcript, str) or not transcript:
        return None
    if not isinstance(session_id, str) or not is_safe_path_component(session_id):
        return None
    root = os.path.realpath(get_claude_config_dir() / "projects")
    lead = os.path.realpath(transcript)
    subagents = os.path.realpath(Path(transcript).parent / session_id / "subagents")
    for path in (lead, subagents):
        if path == root or os.path.commonpath([path, root]) != root:
            return None
    if not os.path.isfile(lead) or not os.path.isdir(subagents):
        return None
    return Path(lead), Path(subagents)


def _by_content(reader, start, monotonic, sleep) -> "tuple[str, str]":
    while True:
        reader.poll()
        if reader.content_match(LEAD):
            return LEAD, CONTENT
        if reader.content_match(TEAMMATE):
            return TEAMMATE, CONTENT
        if monotonic() - start >= T_POSTCOMPACT:
            return UNKNOWN, DEADLINE
        sleep(POLL_S)


def _by_timing(reader, start, guard, deadline, monotonic, sleep) -> "tuple[str, str]":
    while True:
        reader.poll()
        if reader.fresh_boundary(LEAD):
            return LEAD, TIMING
        elapsed = monotonic() - start
        if elapsed >= guard:
            return (TEAMMATE, TIMING) if reader.fresh_boundary(TEAMMATE) else (UNKNOWN, DEADLINE)
        if elapsed >= deadline:
            return UNKNOWN, DEADLINE
        sleep(POLL_S)


class _Reader:
    """Reads the candidate transcripts forward from a seek point set at entry."""

    def __init__(self, lead: Path, subagents: Path, *, since: float, body: "str | None"):
        self._lead = lead
        self._subagents = subagents
        self._since = since
        self._body = body
        self._offset: dict[Path, int] = {lead: _seek_point(lead)}
        for path in subagents.glob("agent-*.jsonl"):
            self._offset[path] = _seek_point(path)
        self._partial: dict[Path, bytes] = {}
        self._boundary: set[Path] = set()
        self._has_body: set[Path] = set()

    def poll(self) -> None:
        self._read(self._lead)
        for path in self._subagents.glob("agent-*.jsonl"):
            try:
                if os.stat(path).st_mtime < self._since:
                    continue
            except OSError:
                continue
            self._read(path)

    def fresh_boundary(self, side: str) -> bool:
        return any(self._side(path) == side for path in self._boundary)

    def content_match(self, side: str) -> bool:
        return any(self._side(path) == side for path in self._boundary & self._has_body)

    def _side(self, path: Path) -> str:
        return LEAD if path == self._lead else TEAMMATE

    def _read(self, path: Path) -> None:
        offset = self._offset.setdefault(path, 0)
        try:
            with open(path, "rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            return
        if not chunk:
            return
        self._offset[path] = offset + len(chunk)
        data = self._partial.pop(path, b"") + chunk
        lines = data.split(b"\n")
        if lines[-1]:
            self._partial[path] = lines[-1]
        for line in lines[:-1]:
            self._scan(path, line)

    def _scan(self, path: Path, line: bytes) -> None:
        if b'"compact_boundary"' in line:
            record = _record(line)
            if (
                record.get("type") == "system"
                and record.get("subtype") == "compact_boundary"
                and _stamp(record.get("timestamp")) >= self._since
            ):
                self._boundary.add(path)
        if self._body is not None and len(line) >= len(self._body) and b'"user"' in line:
            record = _record(line)
            if record.get("type") == "user" and self._body in _text(record):
                self._has_body.add(path)


def _seek_point(path: Path) -> int:
    try:
        return max(0, os.stat(path).st_size - READ_BACK_BYTES)
    except OSError:
        return 0


def _record(line: bytes) -> dict:
    try:
        record = json.loads(line)
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def _stamp(value: Any) -> float:
    """Epoch seconds of an ISO-8601 timestamp, or -inf when unusable."""
    if not isinstance(value, str):
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        return float("-inf")
    return parsed.timestamp()


def _text(record: dict) -> str:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return _collapse_newlines(content)
    if isinstance(content, list):
        return _collapse_newlines("\n".join(
            item["text"] for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        ))
    return ""
