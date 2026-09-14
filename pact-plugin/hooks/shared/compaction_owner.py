"""
Location: pact-plugin/hooks/shared/compaction_owner.py
Summary: Stages each compaction summary, then settles whose compaction it was
         once the compacting agent's transcript has been written.
Used by: postcompact_archive.py (stage_summary, after a settle),
         session_init.py (settle on every source) and bootstrap_gate.py
         (settle before a Read or Bash that names the summary).

An in-process teammate compacts inside the lead's process, and its compaction
frames carry the lead's agent_type, session_id and transcript_path, so no frame
field tells the two apart. The platform writes the compacting agent's summary
record into that agent's own transcript only after the compaction hooks return,
so no hook in the chain can see it. PostCompact therefore stages the summary as
a pending file and never writes compact-summary.txt; settle() decides later,
from a hook that runs after the record has landed.

The transcript first drops the <analysis> block, then renders the text inside
<summary> stripped, after a "Summary:" line, with runs of newlines collapsed to
one blank line, so both sides are compared in that form. An analysis that
quotes its own closing tag has no single end, so the body is taken under both
the shortest and the longest reading of the block, and a record holding either
one matches. A body counts only in a record marked isCompactSummary and only
directly after its first "Summary:" line, so text quoted in an ordinary message,
or part-way through another summary, never matches. Each transcript is read
from its size at stage time, so a summary recorded before the compaction never
matches.

The lead's transcript is the staged frame's transcript_path, and a teammate's is
<transcript dir>/<session_id>/subagents/agent-*.jsonl. A match in the lead's
promotes the summary to compact-summary.txt. A match in a teammate's keeps it
beside that file, which stays as it is. A summary no transcript records within
EXPIRE_S becomes compact-summary.txt only when there is none, because a
teammate's summary must never replace the lead's.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from . import session_journal
from .constants import COMPACT_SUMMARY_NAME
from .paths import get_claude_config_dir
from .session_state import is_safe_path_component

# ponytail: timing constants from one machine's live window. A teammate's
# summary record landed 7.8-10.4 s after its compaction boundary, and the lead's
# earliest tool call after a compaction came 8.5 s after its boundary. Re-measure
# if the journal records an expired compaction_attributed for a real compaction.
READ_WAIT_S = 5.0
FRESH_S = 60.0
EXPIRE_S = 120.0

POLL_S = 0.25
READ_CAP_BYTES = 8 * 1024 * 1024
MIN_BODY_CHARS = 200
KEEP_SETTLED = 10

TEAMMATE, LEAD, UNKNOWN = "teammate", "lead", "unknown"
CONTENT, EXPIRED = "content", "expired"

_PENDING_PREFIX = "compact-summary.pending-"
_TEAMMATE_PREFIX = "compact-summary.teammate-"
_UNATTRIBUTED_PREFIX = "compact-summary.unattributed-"
_STOP = object()

_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
# The shortest and the longest reading of the analysis block.
_ANALYSIS_RES = (
    re.compile(r"<analysis>.*?</analysis>", re.DOTALL),
    re.compile(r"<analysis>.*</analysis>", re.DOTALL),
)
_NEWLINE_RUN_RE = re.compile(r"\n\n+")


def _collapse_newlines(text: str) -> str:
    """Runs of newlines as one blank line, the form the transcript stores."""
    return _NEWLINE_RUN_RE.sub("\n\n", text)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stage_summary(frame: Any, session_dir: str, *, now: Callable[[], datetime] = _utc_now) -> bool:
    """Write the frame's compaction summary to a pending file. Never raises.

    Reads only compact_summary, session_id and transcript_path. Records each
    transcript's size now, so settle() reads only what is written after the
    compaction. Never touches compact-summary.txt. True when the file was written.
    """
    try:
        summary = frame.get("compact_summary")
        if not isinstance(summary, str) or not summary:
            return False
        transcript = frame.get("transcript_path")
        session_id = frame.get("session_id")
        located = _locate(transcript, session_id)
        offsets = {str(path): status.st_size for path, status in _transcripts(*located)} if located else {}
        record = {
            "summary": summary,
            "transcript_path": transcript,
            "session_id": session_id,
            "staged_at": now().isoformat(),
            "offsets": offsets,
        }
        folder = Path(session_dir)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_atomic(folder / f"{_PENDING_PREFIX}{time.time_ns()}.json", json.dumps(record))
        return True
    except Exception:
        return False


def settle(
    session_dir: str,
    *,
    wait_s: float = 0.0,
    now: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> "list[tuple[str, str]]":
    """Resolve the staged summaries in session_dir, oldest first. Never raises.

    Returns the (verdict, basis) of each summary resolved in this pass. A pass
    stops at the first summary that is unmatched and younger than EXPIRE_S, so a
    newer one is never resolved ahead of it. wait_s bounds a poll for a summary
    younger than FRESH_S whose record has not landed yet. Keeps the newest
    KEEP_SETTLED teammate and unattributed summaries and removes older ones.
    On an exception, the files stay where they are.
    """
    resolved: "list[tuple[str, str]]" = []
    if not session_dir:
        return resolved
    try:
        folder = Path(session_dir)
        for staged_ns, pending in _stamped(folder, _PENDING_PREFIX, ".json"):
            outcome = _settle_one(folder, pending, staged_ns, wait_s, now, monotonic, sleep)
            if outcome is _STOP:
                break
            if outcome is not None:
                resolved.append(outcome)
        for prefix in (_TEAMMATE_PREFIX, _UNATTRIBUTED_PREFIX):
            for _, path in _stamped(folder, prefix, ".txt")[:-KEEP_SETTLED]:
                with contextlib.suppress(OSError):
                    path.unlink()
    except Exception:
        pass
    return resolved


def _settle_one(folder, pending, staged_ns, wait_s, now, monotonic, sleep):
    claim = pending.with_name(f"{pending.name}.claimed-{os.getpid()}")
    try:
        os.replace(pending, claim)
    except FileNotFoundError:
        return None
    record = _load(claim)
    verdict = _owner(record)
    if verdict is None and record is not None and wait_s > 0 and _age(record, now) < FRESH_S:
        start = monotonic()
        while verdict is None and monotonic() - start < wait_s:
            sleep(POLL_S)
            verdict = _owner(record)
    age = _age(record, now)
    if verdict is None and age < EXPIRE_S:
        os.replace(claim, pending)
        return _STOP
    basis = EXPIRED if verdict is None else CONTENT
    verdict = verdict or UNKNOWN
    canonical = folder / COMPACT_SUMMARY_NAME
    if record is None:
        os.replace(claim, folder / f"{_UNATTRIBUTED_PREFIX}{staged_ns}.txt")
    elif verdict == TEAMMATE:
        _write_atomic(folder / f"{_TEAMMATE_PREFIX}{staged_ns}.txt", record["summary"])
    elif verdict == LEAD or not canonical.exists():
        _write_atomic(canonical, record["summary"])
    else:
        _write_atomic(folder / f"{_UNATTRIBUTED_PREFIX}{staged_ns}.txt", record["summary"])
    fields = {"verdict": verdict, "basis": basis}
    if math.isfinite(age):
        fields["latency_s"] = round(age, 3)
    try:
        session_journal.append_event(
            session_journal.make_event("compaction_attributed", **fields), session_dir=str(folder)
        )
    except Exception:
        pass
    with contextlib.suppress(OSError):
        claim.unlink()
    return verdict, basis


def _stamped(folder: Path, prefix: str, suffix: str) -> "list[tuple[int, Path]]":
    """(stamp, path) for each file named <prefix><digits><suffix>, oldest first."""
    found = []
    for path in folder.glob(f"{prefix}*{suffix}"):
        stamp = path.name[len(prefix):-len(suffix)]
        if stamp.isdigit():
            found.append((int(stamp), path))
    return sorted(found)


def _load(path: Path) -> "dict | None":
    """The staged record, or None when it is unreadable or holds no summary."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("summary"), str):
        return None
    return record


def _age(record: "dict | None", now: Callable[[], datetime]) -> float:
    """Seconds since the record was staged; infinite when that is unknowable.

    A stamp more than EXPIRE_S in the future is unknowable too, so it can never
    hold the queue.
    """
    try:
        staged = datetime.fromisoformat(record["staged_at"])
        if staged.tzinfo is None:
            return math.inf
        age = now().timestamp() - staged.timestamp()
    except Exception:
        return math.inf
    return age if age > -EXPIRE_S else math.inf


def _owner(record: "dict | None") -> "str | None":
    """lead or teammate when a transcript holds the record's summary, else None."""
    if record is None:
        return None
    bodies = _summary_bodies(record.get("summary"))
    located = _locate(record.get("transcript_path"), record.get("session_id"))
    if not bodies or located is None:
        return None
    offsets = record.get("offsets")
    offsets = offsets if isinstance(offsets, dict) else {}
    for index, (path, status) in enumerate(_transcripts(*located)):
        if _holds_summary(path, status.st_size, offsets.get(str(path)), bodies):
            return LEAD if index == 0 else TEAMMATE
    return None


def _summary_bodies(summary: Any) -> "tuple[str, ...]":
    """The text inside <summary>...</summary> once the analysis block is removed.

    One body per reading of the analysis block, without duplicates or bodies too
    short to match on; empty when there is none.
    """
    if not isinstance(summary, str):
        return ()
    bodies: list[str] = []
    for analysis in _ANALYSIS_RES:
        match = _SUMMARY_RE.search(analysis.sub("", summary))
        if match is None:
            continue
        body = _collapse_newlines(match.group(1).strip())
        if len(body) >= MIN_BODY_CHARS and body not in bodies:
            bodies.append(body)
    return tuple(bodies)


def _locate(transcript: Any, session_id: Any) -> "tuple[Path, Path] | None":
    """(lead transcript, subagents folder) inside projects/, or None.

    The lead transcript must exist. The subagents folder need not: a session
    with no teammate transcripts has none.
    """
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
    if not os.path.isfile(lead):
        return None
    return Path(lead), Path(subagents)


def _transcripts(lead: Path, subagents: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """The lead transcript, then each readable teammate transcript, with its stat."""
    yield lead, os.stat(lead)
    for path in sorted(subagents.glob("agent-*.jsonl")):
        status = _candidate_stat(path, subagents)
        if status is not None:
            yield path, status


def _candidate_stat(path: Path, folder: Path) -> "os.stat_result | None":
    """The stat of a regular file that resolves inside folder, or None.

    A link out of the folder is not read, and neither is a FIFO or device,
    which would block the open.
    """
    try:
        real = os.path.realpath(path)
        if real == str(folder) or os.path.commonpath([real, str(folder)]) != str(folder):
            return None
        status = os.stat(real)
    except (OSError, ValueError):
        return None
    return status if stat.S_ISREG(status.st_mode) else None


def _holds_summary(path: Path, size: int, offset: Any, bodies: "tuple[str, ...]") -> bool:
    """True when a summary record holding one of bodies follows offset in path.

    Reads at most READ_CAP_BYTES from offset. An offset that is missing, or past
    the file's current size, reads from the start.
    """
    start = offset if isinstance(offset, int) and not isinstance(offset, bool) and 0 <= offset <= size else 0
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read(READ_CAP_BYTES)
    except OSError:
        return False
    shortest = min(map(len, bodies))
    for line in data.split(b"\n"):
        if len(line) < shortest or b'"isCompactSummary"' not in line:
            continue
        record = _record(line)
        if record.get("type") == "user" and record.get("isCompactSummary") is True:
            _, sep, rest = _text(record).partition("Summary:\n")
            if sep and any(rest.startswith(body) for body in bodies):
                return True
    return False


def _write_atomic(path: Path, text: str) -> None:
    """Write text to path through a 0600 temporary file in the same folder."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="compact-summary.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _record(line: bytes) -> dict:
    try:
        record = json.loads(line)
    except (ValueError, RecursionError):
        return {}
    return record if isinstance(record, dict) else {}


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
