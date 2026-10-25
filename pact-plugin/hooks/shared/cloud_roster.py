"""
Location: pact-plugin/hooks/shared/cloud_roster.py
Summary: The cloud roster: an append-only record, written by hooks on the
         lead's side of a spawn, that ties each spawned agent's NAME (from the
         lead's own Agent call) to its harness `agent_id`. Owns the file
         format, record validation, the append, and the fold that reads it.
Used by: dispatch_gate.py (writes `intent`), roster_writer.py (writes `bind`
         and `confirm`), shared/actor_identity.py (reads a name for an
         agent_id: R1) and shared/intentional_wait.py (reads the types an owner
         name was spawned with: R2). Design: docs/architecture/cloud-roster.md.

WHY. A cloud session has no team config, so a teammate's NAME exists only in
its platform meta.json, which the teammate's own OS user can rewrite or
delete. The lead's PreToolUse(Agent) frame carries the name and type the lead
asked for, and the teammate cannot touch it, but it has no agent_id. This
module joins the two through the harness's own `tool_use_id`.

THE SWITCH. Every reader and writer calls `applies(team)` first: True only
when the team directory exists and its `config.json` does NOT. Local sessions
have a config, so they never read or write the roster, and their behaviour is
unchanged by construction.

RECORDS (one compact JSON object per line, `v: 1`):
  intent  {tu, name, type}            dispatch_gate, lead PreToolUse(Agent),
                                      after a non-DENY decision.
  bind    {tu, aid, type, via,        roster_writer, SubagentStart, before
           meta_present}              the teammate's first turn.
  confirm {tu, aid, type?}            roster_writer, lead PostToolUse(Agent):
                                      the harness pairs tool_use_id and agentId.

FOLD (pure, in `load_roster`):
  1. The FIRST intent per tool_use_id wins; a later duplicate is ignored.
  2. A bind or confirm with no matching intent is ignored.
  3. A bind's type, and a confirm's type when present, must equal the
     intent's type once `PACT:` is stripped.
  4. A confirm beats a bind for the same tool_use_id.
  5. An agent_id attached to two tool_use_ids at the same strength is unbound.
     A confirmed attachment beats a bind-only one for the same agent_id.
  6. `types_for_name(n)` is the set of stripped types over ALL intents named n.

TAMPER RESISTANCE, STATED HONESTLY. Hooks and teammates run as the same OS
user and no secret exists that a teammate cannot read, so nothing here
PREVENTS a teammate from writing this file. The roster raises the cost of
framing another member from "edit your own meta.json" to "forge this file AND
meta.json consistently". ACCEPTED RESIDUALS (user decision Q1, design §7):
  - a same-uid two-file forge (roster + meta.json) can still put a victim's
    name on an action, or add an intent that makes a fresh owner name exempt;
  - meta.json does not exist yet at SubagentStart (measured, WP0), so two
    same-type spawns whose SubagentStarts overlap stay unbound, and resolve
    meta-only as before, until the lead's PostToolUse confirm;
  - unnamed and nested spawns are not recorded.
The real control is harness-side (a cloud TaskCompleted `teammate_name`, or a
harness-held roster).

TRUST BOUNDARY: an AUTHORITY module. It never imports the session registry,
background_work or turn_end_gate (pinned by
tests/test_session_registry_trust_partition.py), and only dispatch_gate and
roster_writer may call `append_record` (pinned there too).

Every function here is total: errors read as "no roster" or "not written".
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import state_file, subagent_meta
from .paths import get_claude_config_dir

ROSTER_FILENAME = "pact-roster.jsonl"
MAX_ROSTER_BYTES = 1_048_576   # reader cap, and the size at which writers stop
MAX_RECORD_BYTES = 1024        # one os.write, below PIPE_BUF
RECORD_VERSION = 1

KIND_INTENT = "intent"
KIND_BIND = "bind"
KIND_CONFIRM = "confirm"

BIND_VIA_META = "meta"
BIND_VIA_TYPE = "type"
_BIND_VIAS = frozenset({BIND_VIA_META, BIND_VIA_TYPE})

STRENGTH_BOUND = "bound"
STRENGTH_CONFIRMED = "confirmed"

TOOL_USE_ID = re.compile(r"toolu_[A-Za-z0-9_-]{1,128}")
TYPE_VALUE = re.compile(r"(?:PACT:)?[A-Za-z0-9_.-]{1,128}")
_TIMESTAMP_MAX_CHARS = 40
_PACT_PREFIX = "PACT:"


@dataclass(frozen=True)
class Intent:
    """One spawn the lead asked for: its tool_use_id, name and type spelling."""

    tool_use_id: str
    name: str
    type: str
    ts: str | None


@dataclass(frozen=True)
class Binding:
    """An agent_id joined to the lead-written intent it was spawned from.

    strength: STRENGTH_BOUND (SubagentStart bind) or STRENGTH_CONFIRMED (the
    lead's PostToolUse confirm).
    """

    agent_id: str
    tool_use_id: str
    name: str
    type: str
    strength: str


@dataclass(frozen=True)
class Roster:
    """The folded roster. Build it with `load_roster`, not directly."""

    intents: dict = field(default_factory=dict)        # tool_use_id -> Intent
    bindings: dict = field(default_factory=dict)       # agent_id -> Binding
    attached: frozenset = frozenset()                  # tool_use_ids with any valid bind/confirm

    def binding_for(self, agent_id: Any) -> Binding | None:
        """The binding for `agent_id`, or None (unknown, or in conflict)."""
        if not isinstance(agent_id, str):
            return None
        return self.bindings.get(agent_id)

    def types_for_name(self, name: Any) -> frozenset:
        """Every type (`PACT:` stripped) any intent recorded for `name`."""
        if not isinstance(name, str) or not name:
            return frozenset()
        return frozenset(
            strip_type(i.type) for i in self.intents.values() if i.name == name
        )

    def intent(self, tool_use_id: Any) -> Intent | None:
        if not isinstance(tool_use_id, str):
            return None
        return self.intents.get(tool_use_id)

    def unattached_intents(self) -> list:
        """Intents no valid bind or confirm has attached an agent_id to yet."""
        return [i for tu, i in self.intents.items() if tu not in self.attached]


def strip_type(value: str) -> str:
    """`value` without a leading `PACT:`."""
    return value[len(_PACT_PREFIX):] if value.startswith(_PACT_PREFIX) else value


def now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_timestamp(value: Any) -> datetime | None:
    """A tz-aware datetime from a record `ts`, or None."""
    if not isinstance(value, str) or len(value) > _TIMESTAMP_MAX_CHARS:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def teams_root(teams_dir: str | None = None) -> Path:
    return Path(teams_dir) if teams_dir else get_claude_config_dir() / "teams"


def applies(team_name: Any, teams_dir: str | None = None) -> bool:
    """True iff `team_name`'s directory exists and holds no `config.json`.

    The one switch every roster reader and writer checks. A config present (or
    a symlink at its path) means a local team: no roster read or write.
    Never raises.
    """
    try:
        if not isinstance(team_name, str) or not subagent_meta.SAFE_ID.match(team_name):
            return False
        team_dir = teams_root(teams_dir) / team_name
        return team_dir.is_dir() and not os.path.lexists(team_dir / "config.json")
    except Exception:  # noqa: BLE001 — totality is the contract
        return False


def append_record(team_name: str, record: dict, teams_dir: str | None = None) -> bool:
    """Validate `record` and append it as one line. Never raises.

    Adds `v` and, when absent, `ts`. Returns False, writing nothing, when the
    roster does not apply, the record is invalid or over MAX_RECORD_BYTES, the
    file has reached MAX_ROSTER_BYTES, or on any I/O error. The team directory
    is never created.
    """
    try:
        if not applies(team_name, teams_dir) or not isinstance(record, dict):
            return False
        full = {"v": RECORD_VERSION, **record}
        full.setdefault("ts", now_timestamp())
        if _validated(full) is None:
            return False
        data = (json.dumps(full, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        if len(data) > MAX_RECORD_BYTES:
            return False
        root = teams_root(teams_dir)
        return state_file.append_line(
            root / team_name / ROSTER_FILENAME, data, root,
            max_file_bytes=MAX_ROSTER_BYTES,
        )
    except Exception:  # noqa: BLE001 — a roster write never breaks a hook
        return False


def load_roster(team_name: str, teams_dir: str | None = None) -> Roster | None:
    """The folded roster, or None when it does not apply, is missing,
    unreadable, a symlink, or over MAX_ROSTER_BYTES. Lines that fail JSON or
    field validation are skipped. Never raises."""
    try:
        if not applies(team_name, teams_dir):
            return None
        root = teams_root(teams_dir)
        text = state_file.read_text(
            root / team_name / ROSTER_FILENAME, root, max_bytes=MAX_ROSTER_BYTES
        )
    except (OSError, state_file.TooLarge):
        return None
    except Exception:  # noqa: BLE001
        return None
    try:
        return fold(_parse_lines(text))
    except Exception:  # noqa: BLE001
        return None


def _parse_lines(text: str) -> list:
    records = []
    for line in text.splitlines():
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        record = _validated(raw)
        if record is not None:
            records.append(record)
    return records


def _validated(raw: Any) -> dict | None:
    """`raw` when it is a well-formed record of a known kind, else None."""
    if not isinstance(raw, dict) or raw.get("v") != RECORD_VERSION:
        return None
    kind = raw.get("k")
    tu = raw.get("tu")
    if not (isinstance(tu, str) and TOOL_USE_ID.fullmatch(tu)):
        return None
    ts = raw.get("ts")
    if ts is not None and parse_timestamp(ts) is None:
        return None
    type_value = raw.get("type")
    type_ok = isinstance(type_value, str) and bool(TYPE_VALUE.fullmatch(type_value))
    if kind == KIND_INTENT:
        if subagent_meta.valid_name(raw.get("name")) is None or not type_ok:
            return None
        return raw
    aid = raw.get("aid")
    if not (isinstance(aid, str) and subagent_meta.SUBAGENT_ID.fullmatch(aid)):
        return None
    if kind == KIND_BIND:
        if not type_ok or raw.get("via") not in _BIND_VIAS:
            return None
        if not isinstance(raw.get("meta_present"), bool):
            return None
        return raw
    if kind == KIND_CONFIRM:
        if type_value is not None and not type_ok:
            return None
        return raw
    return None


def fold(records: list) -> Roster:
    """Apply the fold rules (module docstring) to validated records in file order."""
    intents: dict = {}
    for r in records:
        if r["k"] == KIND_INTENT and r["tu"] not in intents:
            intents[r["tu"]] = Intent(r["tu"], r["name"], r["type"], r.get("ts"))

    binds: dict = {}      # tu -> aid (first valid bind)
    confirms: dict = {}   # tu -> aid (first valid confirm)
    for r in records:
        intent = intents.get(r["tu"])
        if intent is None or r["k"] == KIND_INTENT:
            continue
        record_type = r.get("type")
        if isinstance(record_type, str) and strip_type(record_type) != strip_type(intent.type):
            continue
        target = binds if r["k"] == KIND_BIND else confirms
        target.setdefault(r["tu"], r["aid"])

    # Rule 4: per tool_use_id, the confirm beats the bind.
    attachment: dict = {}  # tu -> (aid, strength)
    for tu, aid in binds.items():
        attachment[tu] = (aid, STRENGTH_BOUND)
    for tu, aid in confirms.items():
        attachment[tu] = (aid, STRENGTH_CONFIRMED)

    # Rule 5: per agent_id, the strongest attachments; two of them -> unbound.
    by_aid: dict = {}
    for tu, (aid, strength) in attachment.items():
        by_aid.setdefault(aid, []).append((tu, strength))
    bindings: dict = {}
    for aid, entries in by_aid.items():
        confirmed = [tu for tu, s in entries if s == STRENGTH_CONFIRMED]
        chosen = confirmed or [tu for tu, _ in entries]
        if len(chosen) != 1:
            continue
        tu = chosen[0]
        intent = intents[tu]
        strength = STRENGTH_CONFIRMED if confirmed else STRENGTH_BOUND
        bindings[aid] = Binding(aid, tu, intent.name, intent.type, strength)
    return Roster(intents, bindings, frozenset(attachment))
