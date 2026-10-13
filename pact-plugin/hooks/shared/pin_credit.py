"""
Location: pact-plugin/hooks/shared/pin_credit.py

Summary: The pin-cap credit record: per agent, the pins its allowed changes to
the project CLAUDE.md removed and has not yet added back, so a pin moved by two
Edits is not counted as added. Keyed by the hash of each file text an allowed
change produced: a change from any other route leaves a text with no entry,
which breaks the chain, and the next change gets the plain verdict.

Used by: pin_caps_gate (`read` before the decision, `record` after an ALLOW),
claude_md_drift (`confirm` once a write lands, `state` for the baseline) and
pin_staleness_gate (`read` in its add check).

THE RECORD. `<session dir>/claude-md-pin-credit/<key>.json`, with the session
dir and key the drift's last-seen records use (`claude_md_drift._session_dir`,
`_record_key`): one per agent, none for a teammate with no key. It holds the
base directory and, per file text hash, the credit left (`credit`), whether the
count needed it (`credited`), whether the write reached the file (`landed`,
set by `confirm`), snapshots of the pins removed and not yet back (`removed`)
and the indented and below-marker heading lines this change added
(`added_indented`, `added_below`). Written through `state_file.locked_update`
with a short lock wait; read with no lock. Never CLAUDE.md.

BOUNDS, on every write: 32 states, oldest first out; per state 24 snapshots and
32 KB of snapshot text, oldest first out; 64 keys per added list. A state older
than 12 hours is dropped on write and ignored on read. A record file over 1 MB
reads as none. A change whose pins before and after would need more than 2,500
comparisons to tell which pins it removed records nothing, which breaks the
chain.

FAILS TOWARD NO CREDIT. Every entry point catches its own failures: `read`
returns `Credit.none()`, `state` returns None, and `record` and `confirm` write
nothing. Credit only turns a refusal into an allow, so no failure here can
refuse a change the gate would allow without it.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

_CREDIT_DIR = "claude-md-pin-credit"
_VERSION = 1
_MAX_STATES = 32
_MAX_SNAPSHOTS = 24
_MAX_SNAPSHOT_CHARS = 32 * 1024
_MAX_KEYS = 64
_EXPIRY_SECONDS = 12 * 3600
_MAX_RECORD_BYTES = 1024 * 1024
_LOCK_TIMEOUT_SECONDS = 1.0
_MAX_COMPARISONS = 2_500


class Exclusions(NamedTuple):
    """Heading lines this agent's own landed changes added this session: keys
    of indented `### ` lines in the Pinned section and of `### ` lines below
    its end marker. A reveal of one of them earns no credit."""

    indented: frozenset
    below: frozenset


@dataclass(frozen=True)
class Credit:
    """What this agent's chain allows the change being decided: `count` pins
    it removed and has not added back, `removed` their snapshots as `Pin`s
    for the size rule's second reading, and `exclude`."""

    count: int
    removed: tuple
    exclude: Exclusions

    @staticmethod
    def none() -> "Credit":
        return Credit(0, (), Exclusions(frozenset(), frozenset()))


def read(input_data: dict, frame_kind: str, base: Path, before_digest: str) -> Credit:
    """The credit this frame's chain holds for the file text hashed
    `before_digest`, with this agent's exclusions. `Credit.none()` when there
    is no record, no key or no session dir, and on any failure."""
    try:
        location = _location(input_data, frame_kind)
        if location is None:
            return Credit.none()
        states = _live_states(_load(*location), base)
        entry = states.get(before_digest)
        count, removed = (0, ()) if entry is None else (entry["credit"], _pins(entry["removed"]))
        return Credit(count, removed, _exclusions(states))
    except Exception:  # noqa: BLE001 — a failed read gives no credit
        return Credit.none()


def record(input_data: dict, frame_kind: str, base: Path, before_digest: str,
           detail: dict, decision, credit: Credit) -> None:
    """Store the chain's state for the file text an allowed change produces.

    Called after an ALLOW only. `detail` holds the gate's text after (`after`),
    both parses, both located sections, both sections' pins and the rule's
    growth before credit; a missing key records nothing. Writes nothing when
    the new state would carry nothing and the text before had no state."""
    try:
        location = _location(input_data, frame_kind)
        if location is None or decision.verdict != "ALLOW":
            return
        entry = _entry(detail, decision, credit)
        if entry is None:
            return
        from .claude_md_drift import _digest

        after_digest = _digest(detail["after"])
        if after_digest == before_digest:
            return
        _write(*location, base, before_digest, after_digest, entry)
    except Exception:  # noqa: BLE001 — a failed record leaves the chain as it was
        return


def state(session_dir: Path, key: str, base: Path, digest: str) -> dict | None:
    """The live state of `key`'s record for the file text hashed `digest`, or
    None when there is none, and on any failure."""
    try:
        return _live_states(_load(Path(session_dir), key), base).get(digest)
    except Exception:  # noqa: BLE001 — a failed read is no state
        return None


def confirm(session_dir: Path, key: str, base: Path, digest: str) -> None:
    """Mark that the file text hashed `digest` reached the file, so the
    heading lines its change added exclude a later reveal. A no-op when the
    record holds no live state for it; never raises."""
    try:
        def apply(text):
            record_data = _parsed(text)
            entry = _live_states(record_data, base).get(digest)
            if entry is None or entry["landed"]:
                return text, False, None
            record_data["states"][digest]["landed"] = True
            return json.dumps(record_data), True, None

        _update(Path(session_dir), key, apply)
    except Exception:  # noqa: BLE001 — an unconfirmed state only keeps no exclusion
        return


def _location(input_data: dict, frame_kind: str) -> tuple[Path, str] | None:
    """The session dir and record key for this frame, or None for a frame
    that keeps no record."""
    from .claude_md_drift import _record_key, _session_dir

    key = _record_key(input_data, frame_kind)
    session_dir = _session_dir(input_data)
    if key is None or session_dir is None:
        return None
    return session_dir, key


def _record_path(session_dir: Path, key: str) -> Path:
    return session_dir / _CREDIT_DIR / f"{key}.json"


def _root() -> Path:
    from .paths import get_claude_config_dir

    return get_claude_config_dir() / "pact-sessions"


def _base_key(base) -> str:
    """One spelling for a base directory, so two spellings of it share one
    record."""
    return os.path.realpath(str(base))


def _load(session_dir: Path, key: str) -> dict | None:
    """The record's parsed content, or None when absent, over 1 MB or not a
    JSON object."""
    from . import state_file

    path = _record_path(session_dir, key)
    try:
        if path.stat().st_size > _MAX_RECORD_BYTES:
            return None
        text = state_file.read_text(path, _root())
    except OSError:
        return None
    return _parsed(text) or None


def _parsed(text: str) -> dict:
    """The record in `text`, or an empty dict when it is not a JSON object."""
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _live_states(record_data: dict | None, base) -> dict:
    """The record's well-formed states younger than the expiry, by hash; none
    for another version or another base."""
    if (not record_data or record_data.get("version") != _VERSION
            or record_data.get("base") != _base_key(base)):
        return {}
    states = record_data.get("states")
    if not isinstance(states, dict):
        return {}
    now = time.time()
    return {digest: entry for digest, entry in states.items()
            if isinstance(digest, str) and _well_formed(entry) and now - entry["at"] < _EXPIRY_SECONDS}


def _well_formed(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    at, credit = entry.get("at"), entry.get("credit")
    return (isinstance(at, (int, float)) and not isinstance(at, bool)
            and isinstance(credit, int) and not isinstance(credit, bool) and credit >= 0
            and isinstance(entry.get("credited"), bool) and isinstance(entry.get("landed"), bool)
            and isinstance(entry.get("removed"), list) and all(_snapshot_ok(s) for s in entry["removed"])
            and all(isinstance(entry.get(name), list) and all(isinstance(k, str) for k in entry[name])
                    for name in ("added_indented", "added_below")))


def _snapshot_ok(snapshot) -> bool:
    return (isinstance(snapshot, dict) and isinstance(snapshot.get("heading"), str)
            and isinstance(snapshot.get("lines"), list) and all(isinstance(x, str) for x in snapshot["lines"])
            and isinstance(snapshot.get("body_chars"), int)
            and (snapshot.get("override") is None or isinstance(snapshot["override"], str)))


def _pins(snapshots: list) -> tuple:
    """The size rule's `Pin`s for stored snapshots."""
    from pin_caps import Pin

    return tuple(Pin(heading=s["heading"], body="\n".join(s["lines"]), body_chars=s["body_chars"],
                     date_comment=None, override_rationale=s["override"], is_stale=False,
                     lines=tuple(s["lines"]))
                 for s in snapshots)


def _snapshot(pin) -> dict:
    return {"heading": pin.heading, "lines": list(pin.lines), "body_chars": pin.body_chars,
            "override": pin.override_rationale}


def _exclusions(states: dict) -> Exclusions:
    """The union of the added heading lines over the landed states."""
    landed = [entry for entry in states.values() if entry["landed"]]
    return Exclusions(frozenset(k for entry in landed for k in entry["added_indented"]),
                      frozenset(k for entry in landed for k in entry["added_below"]))


def _entry(detail: dict, decision, credit: Credit) -> dict | None:
    """The new state's fields for an allowed change, or None when `detail`
    lacks what it needs."""
    from pin_caps import PIN_COUNT_CAP, continues
    from .pin_growth import reveal_candidates

    needed = ("after", "before_doc", "after_doc", "before_located", "after_located",
              "pins_before", "pins_after")
    if not all(name in detail for name in needed) or detail["pins_after"] is None:
        return None
    growth = detail.get("growth")
    delta = growth if growth is not None else decision.pins_after - decision.pins_before
    pins_before, pins_after = detail["pins_before"] or [], detail["pins_after"]
    if (len(credit.removed) + len(pins_before)) * len(pins_after) > _MAX_COMPARISONS:
        return None

    def back(pin) -> bool:
        return any(continues(pin, after_pin) for after_pin in pins_after)

    removed = [s for s in credit.removed if not back(s)] + [p for p in pins_before if not back(p)]
    indented_before, below_before = reveal_candidates(detail["before_doc"], detail["before_located"])
    indented_after, below_after = reveal_candidates(detail["after_doc"], detail["after_located"])
    return {
        "credit": max(0, credit.count - delta),
        "credited": bool(growth is not None and growth > 0 and decision.pins_after > PIN_COUNT_CAP
                         and growth <= credit.count),
        "removed": _bounded_snapshots([_snapshot(p) for p in removed]),
        "added_indented": list((Counter(indented_after) - Counter(indented_before)).elements())[-_MAX_KEYS:],
        "added_below": list((Counter(below_after) - Counter(below_before)).elements())[-_MAX_KEYS:],
    }


def _bounded_snapshots(snapshots: list) -> list:
    """The newest snapshots within the count and text bounds."""
    kept, chars = [], 0
    for snapshot in reversed(snapshots[-_MAX_SNAPSHOTS:]):
        chars += len(snapshot["heading"]) + sum(len(line) for line in snapshot["lines"])
        if chars > _MAX_SNAPSHOT_CHARS:
            break
        kept.append(snapshot)
    return kept[::-1]


def _write(session_dir: Path, key: str, base, before_digest: str, after_digest: str, entry: dict) -> None:
    """Add the state for `after_digest`, when it carries something or the
    text before had a state, within the bounds."""
    def apply(text):
        record_data = _parsed(text)
        states = _live_states(record_data, base)
        carries = (entry["credit"] > 0 or entry["removed"] or entry["added_indented"]
                   or entry["added_below"] or before_digest in states)
        if not carries:
            return text, False, None
        states[after_digest] = {"at": time.time(), "landed": False, **entry}
        newest = sorted(states.items(), key=lambda item: item[1]["at"])[-_MAX_STATES:]
        return json.dumps({"version": _VERSION, "base": _base_key(base), "states": dict(newest)}), True, None

    _update(session_dir, key, apply)


def _update(session_dir: Path, key: str, apply) -> None:
    from . import state_file

    state_file.locked_update(_record_path(session_dir, key), apply, _root(), timeout=_LOCK_TIMEOUT_SECONDS)
