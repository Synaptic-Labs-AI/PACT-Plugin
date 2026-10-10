#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/claude_md_drift.py

Summary: Reports pin growth in the project CLAUDE.md that the pin-cap gate does
not see. The gate decides Edit and Write; this module notices growth that
arrives any other way (a shell command, a git operation, another tool, an edit
outside any tool) and reports it. It never refuses, writes, restores or locks
the CLAUDE.md, and it never sets a permission decision. It writes only its own
records, under the PACT sessions root.

Used by: track_files (`report_after_bash` on its Bash legs, PostToolUse and
PostToolUseFailure; `record_after_write` on its Edit/Write leg) and
missed_wake_scan (`drift_advisory` at each prompt and session start). Both
import it lazily, inside a try of its own, so a failure here changes nothing
else they do. Each entry point also catches its own failures and never raises.

THREE RECORDS.
- The base cache, `<session dir>/claude-md-base.json`: the resolver's base
  directory, or None when no file resolved. Resolving can start git, so it runs
  once per session; the two-file check under the cached base runs on every
  call, so a new `.claude/CLAUDE.md` is picked up at once.
- The last-seen record, `<session dir>/claude-md-last-seen/<key>.json`, one per
  agent (its `agent_id`; else `lead` for the lead and `session` for a frame
  alone in its own session; a teammate in the lead's session without an
  `agent_id` keeps none): the base, the resolved path, the hash
  and the text, as of the last Bash call, or Edit or Write the gate checked. A
  record for another base counts as none.
- The baseline, `claude-md-baseline.json` in the project's directory under the
  sessions root, one per project, written by lead frames only: the base, the
  path, the hash, the fence-aware pin count (None when the pins could not be
  counted) and the Pinned section's state.

Keying on the base directory rather than the file makes a move from
`./CLAUDE.md` to `.claude/CLAUDE.md` compare with the old text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from . import pact_context, state_file
from .claude_md_markers import State, parse
from .paths import get_claude_config_dir

_BASE_CACHE = "claude-md-base.json"
_LAST_SEEN_DIR = "claude-md-last-seen"
_BASELINE = "claude-md-baseline.json"
_RECORD_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")
_LEAD_KEY = "lead"
_SESSION_KEY = "session"

# The Pinned section's state in a baseline.
_FOUND = "found"
_NO_MARKERS = "no_markers"  # no memory block, but a prose `## Pinned Context`
_NOT_FOUND = "not_found"


def report_after_bash(frame: dict) -> str | None:
    """After a Bash call (PostToolUse or PostToolUseFailure), in a frame the
    pin-cap gate checks (`claude_md_manager.gate_frame`): compare the project
    CLAUDE.md with this agent's last-seen record and return a report when the
    change since then would be refused by the pin cap, else None. Always
    stores the file as the new last-seen record, so a cancelled and rerun hook
    finds no change. Any other frame (a session with no PACT role) gets no
    record and no report. Never raises."""
    try:
        return _report_after_bash(frame)
    except Exception:  # noqa: BLE001 - a report job must never disturb its host
        return None


def record_after_write(frame: dict) -> None:
    """After an Edit or Write the pin-cap gate checked (a frame it checks, on
    the project CLAUDE.md): store the file as the new last-seen record, and
    in a lead frame, when the Pinned section is FOUND after it, update the
    baseline, unless the file is now over the cap with more pins than the
    baseline holds (or the baseline could not count them): growth past the
    cap that the gate allowed is reported at the next prompt. Never raises."""
    try:
        _record_after_write(frame)
    except Exception:  # noqa: BLE001
        return


def drift_advisory(frame: dict) -> str | None:
    """At a prompt or a session start (never a compaction's), in lead frames
    only: one advisory per growth past the pin cap since the baseline, and one
    when PACT's memory markers are missing so the pins cannot be counted.
    Updates the baseline whenever the file changed, except at a session start
    while the file has no PACT_MANAGED block: session_init's migration may be
    adding the markers then, so nothing is reported or recorded and the next
    prompt checks the file. Never raises."""
    try:
        return _drift_advisory(frame)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# The three jobs
# ---------------------------------------------------------------------------

def _report_after_bash(frame: dict) -> str | None:
    from .claude_md_manager import gate_frame

    kind = gate_frame(frame)
    if kind is None:
        return None  # a frame the pin-cap gate does not check: no record, no report
    key = _record_key(frame, kind)
    session_dir = _session_dir(frame)
    if key is None or session_dir is None:
        return None
    path, base = _resolve(session_dir)
    if path is None or base is None:
        return None
    text = _read(path)
    if text is None:
        return None
    digest = _digest(text)
    record_path = session_dir / _LAST_SEEN_DIR / f"{key}.json"
    record = _read_json(record_path)
    report = None
    if record is not None and record.get("base") == str(base) and isinstance(record.get("text"), str):
        if record.get("hash") == digest:
            return None
        from .pin_growth import pin_cap_decision

        decision = pin_cap_decision(record["text"], text)
        if decision.verdict == "DENY":
            report = _bash_report(path, decision, kind)
    _write_json(record_path, {"base": str(base), "path": str(path), "hash": digest, "text": text})
    return report


def _record_after_write(frame: dict) -> None:
    tool_input = frame.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or Path(file_path).name.casefold() != "claude.md":
        return
    from .claude_md_manager import gate_frame, gate_target

    # Only an edit the gate checked moves a record. Another file named
    # CLAUDE.md would otherwise absorb growth made outside any tool.
    kind = gate_frame(frame)
    if kind is None or gate_target(file_path) is None:
        return
    key = _record_key(frame, kind)
    session_dir = _session_dir(frame)
    if key is None or session_dir is None:
        return
    path, base = _resolve(session_dir)
    if path is None or base is None:
        return
    text = _read(path)
    if text is None:
        return
    digest = _digest(text)
    _write_json(session_dir / _LAST_SEEN_DIR / f"{key}.json",
                {"base": str(base), "path": str(path), "hash": digest, "text": text})
    if not pact_context.is_lead(frame):
        return
    state, count = _pin_state(text)
    if count is None:
        # Allowed on the not-FOUND advisory path: leave the baseline, so the
        # next prompt reports any growth past the cap once.
        return
    from pin_caps import PIN_COUNT_CAP

    baseline = _read_json(session_dir.parent / _BASELINE)
    if count > PIN_COUNT_CAP and baseline is not None and _valid_baseline(baseline, base):
        stored = baseline["count"]
        if stored is None or stored < count:
            # Growth past the cap that the gate allowed (a revealed pin, a
            # Write over a file it could not read): leave the baseline, so the
            # next prompt reports it once.
            return
    _write_baseline(session_dir, base, path, digest, count, state)


def _drift_advisory(frame: dict) -> str | None:
    if not pact_context.is_lead(frame):
        return None
    if frame.get("hook_event_name") == "SessionStart" and frame.get("source") == "compact":
        return None
    session_dir = _session_dir(frame)
    if session_dir is None:
        return None
    path, base = _resolve(session_dir)
    if path is None or base is None:
        return None
    text = _read(path)
    if text is None:
        return None
    digest = _digest(text)
    baseline = _read_json(session_dir.parent / _BASELINE)
    if baseline is not None and not _valid_baseline(baseline, base):
        baseline = None
    if baseline is not None and baseline["hash"] == digest:
        return None  # unchanged: one read and one hash, nothing parsed
    if frame.get("hook_event_name") == "SessionStart" and _migration_pending(text):
        # session_init's migration runs beside this hook and may be adding the
        # markers: a baseline taken now could record the file before it and
        # report markers missing that the migration adds. The first prompt
        # takes the baseline instead.
        return None
    state, count = _pin_state(text)
    if baseline is None:
        _write_baseline(session_dir, base, path, digest, count, state)
        return _no_markers_advisory(path) if state == _NO_MARKERS else None
    advisory = None
    stored = baseline["count"]
    if count is not None:
        from pin_caps import PIN_COUNT_CAP

        if count > PIN_COUNT_CAP and (stored is None or count > stored):
            if stored is None:  # no growth was measured
                then = "PACT could not count them at the last check"
                step = "To bring it under the cap, run /PACT:prune-memory to demote pins."
            else:
                then = f"it held {stored} at the last check"
                step = "If the growth was not intended, run /PACT:prune-memory to demote pins."
            advisory = (
                f"The project CLAUDE.md ({path}) now holds {_pin_count(count)}, over the cap "
                f"of {PIN_COUNT_CAP}; {then}. Nothing was refused or changed. {step}"
            )
        stored = count
    elif state == _NO_MARKERS and baseline.get("state") != _NO_MARKERS:
        advisory = _no_markers_advisory(path)
    _write_baseline(session_dir, base, path, digest, stored, state)
    return advisory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bash_report(path: Path, decision, kind: str) -> str:
    """The per-Bash report: the file, which cap the change crossed, and the
    pin counts. It never quotes pin text, and it never says the command grew
    the file: the record may predate other changes. A team member cannot run
    the pin commands, so its report gives the gate's member instruction
    instead of naming one."""
    from .claude_md_manager import MEMBER_PIN_INSTRUCTION

    if decision.cause == "count":
        caps = "count and size caps" if decision.size_reason is not None else "count cap"
    else:
        caps = "size cap"
    if kind == "member":
        step = MEMBER_PIN_INSTRUCTION
    else:
        step = "If the growth was not intended, run /PACT:prune-memory to demote pins."
    return (
        f"The project CLAUDE.md ({path}) has grown past the pin {caps} since the last "
        f"check: {_pin_count(decision.pins_before)} then, {decision.pins_after} now. The file was "
        f"left as it is; nothing was refused or reverted. {step}"
    )


def _pin_count(count: int) -> str:
    return f"{count} pin" if count == 1 else f"{count} pins"


def _no_markers_advisory(path: Path) -> str:
    return (
        f"The pins in the project CLAUDE.md ({path}) cannot be counted: PACT's memory "
        "markers are missing from it, so the pin cap does not apply to that file "
        "until they return. Nothing was refused or changed."
    )


def _migration_pending(text: str) -> bool:
    """Whether session_init's migration acts on this text: it does exactly
    when the PACT_MANAGED pair is absent."""
    from .claude_md_manager import MANAGED_END_MARKER, MANAGED_START_MARKER

    return parse(text).find_block(MANAGED_START_MARKER, MANAGED_END_MARKER).state is State.ABSENT


def _pin_state(text: str) -> tuple[str, int | None]:
    """(state, fence-aware pin count). The count is None unless FOUND."""
    from pin_caps import section_pins
    from staleness import locate_pinned

    from .claude_md_manager import MEMORY_END_MARKER, MEMORY_START_MARKER

    doc = parse(text)
    located = locate_pinned(doc, unique=True)
    if located.state is State.FOUND:
        return _FOUND, len(section_pins(doc, located))
    memory = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER)
    if memory.state is State.ABSENT and locate_pinned(doc).state is State.FOUND:
        return _NO_MARKERS, None
    return _NOT_FOUND, None


def _valid_baseline(baseline: dict, base: Path) -> bool:
    """A baseline for this base directory, with a hash and a count that is an
    int, or None when the pins could not be counted."""
    count = baseline.get("count", False)
    return (
        baseline.get("base") == str(base)
        and isinstance(baseline.get("hash"), str)
        and (count is None or (isinstance(count, int) and not isinstance(count, bool)))
    )


def _write_baseline(session_dir: Path, base: Path, path: Path, digest: str,
                    count: int | None, state: str) -> None:
    _write_json(session_dir.parent / _BASELINE,
                {"base": str(base), "path": str(path), "hash": digest, "count": count,
                 "state": state})


def _resolve(session_dir: Path) -> tuple[Path | None, Path | None]:
    """The project CLAUDE.md and its base directory, the resolver's answer,
    starting git at most once per session.

    The resolver's first step, CLAUDE_PROJECT_DIR, needs no git, so it is
    checked on every call and wins whatever is cached. Otherwise the cached
    base is used with the two-file check, and a cached "no file" stays the
    answer until a file appears under CLAUDE_PROJECT_DIR."""
    from staleness import _find_existing_claude_md, _resolve_project_claude_md_with_base

    cache_path = session_dir / _BASE_CACHE
    cached = _read_json(cache_path)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project_dir:
        found = _find_existing_claude_md(Path(project_dir))
        if found is not None:
            if cached is None or cached.get("base") != project_dir:
                _write_json(cache_path, {"base": project_dir})
            return found, Path(project_dir)
    if cached is not None and "base" in cached:
        base = cached["base"]
        if not isinstance(base, str):
            return None, None
        found = _find_existing_claude_md(Path(base))
        return (found, Path(base)) if found is not None else (None, None)
    path, base = _resolve_project_claude_md_with_base()
    _write_json(cache_path, {"base": str(base) if base is not None else None})
    return path, base


def _session_dir(frame: dict) -> Path | None:
    """This session's directory: the lead's context, else rebuilt from
    CLAUDE_PROJECT_DIR and the frame's session id (a separate-process
    teammate has no context file)."""
    session_dir = pact_context.get_session_dir()
    if not session_dir:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        session_id = frame.get("session_id")
        if project_dir and isinstance(session_id, str) and session_id:
            session_dir = pact_context.reconstruct_session_dir(project_dir, session_id)
    return Path(session_dir) if session_dir else None


def _record_key(frame: dict, kind: str) -> str | None:
    """The frame's last-seen record name, or None when it may keep none.

    A valid agent_id names the record. Without one, the lead's frame is
    "lead", and a frame alone in its own session (a separate-process teammate,
    a solo specialist) is "session". A teammate frame without an agent_id in
    the lead's session gets None: any name would be shared with the lead or
    another teammate. The lead's prompt-time advisory still sees that growth."""
    agent_id = frame.get("agent_id")
    if (isinstance(agent_id, str) and _RECORD_KEY.fullmatch(agent_id)
            and agent_id not in (_LEAD_KEY, _SESSION_KEY)):
        return agent_id
    if kind == "lead":
        return _LEAD_KEY
    if pact_context.get_session_dir():
        return None  # the lead's session (its context file resolved)
    return _SESSION_KEY


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    """Every record goes through the state-file writer: a sidecar lock on the
    record (never on CLAUDE.md), an atomic replace, mode 0600 under 0700."""
    state_file.write_text(path, json.dumps(data), get_claude_config_dir() / "pact-sessions")
