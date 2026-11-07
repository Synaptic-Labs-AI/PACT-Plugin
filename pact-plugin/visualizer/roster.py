"""Location: pact-plugin/visualizer/roster.py
Summary: Read-only adapters for the non-journal PACT state the visualizer can
         join against: the team config (teams/<team>/config.json), the cloud
         roster intents (teams/<team>/pact-roster.jsonl), the task store
         (tasks/<team>/*.json), and the session context file that names the
         team/project for a session directory.
Used by: visualizer.model (agent/task derivation), visualizer.server, tests.

Every loader is total: a missing, malformed, oversized or unsafe file reads as
"nothing here" and never raises. The visualizer only READS these locations; it
never writes team, task or memory state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .classify import as_text
from .discovery import contained, valid_component
from .settings import (
    ROSTER_FILENAME,
    TASKS_DIRNAME,
    TEAMS_DIRNAME,
    MAX_FIELD_CHARS,
    TEAM_CONFIG_FILENAME,
    normalize_variety,
)

# Bound for names, agent types, owners and other short identity strings.
_NAME_CHARS = 200

MAX_STATE_FILE_BYTES = 512 * 1024
MAX_TEAM_MEMBERS = 200
MAX_ROSTER_INTENTS = 400
MAX_STORED_TASKS = 600

# Role -> sprite/crew colour token. Mirrors the palette names in the contract
# tokens and the dev-demo exemplar so the stage/crew colour the same agent the
# same way from /api/demo and from a real journal.
ROLE_COLORS: dict[str, str] = {
    "lead": "yellow",
    "preparer": "green",
    "secretary": "red",
    "architect": "blue",
    "auditor": "purple",
    "backend-coder": "purple",
    "frontend-coder": "pink",
    "database-engineer": "teal",
    "devops-engineer": "blue",
    "test-engineer": "orange",
    "qa-engineer": "orange",
    "security-engineer": "red",
    "n8n": "cyan",
}

_STATUS_MAP = {
    "in_progress": "active",
    "in-progress": "active",
    "inprogress": "active",
    "in progress": "active",
    "pending": "pending",
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "failed": "failed",
    "blocked": "blocked",
    "deleted": "deleted",
    "handoff": "handoff",
    "dispatched": "dispatched",
    "active": "active",
}


@dataclass(frozen=True)
class MemberSeed:
    """A declared team member or cloud rosters intent.

    Location: roster.MemberSeed.
    Summary: ``name`` is the teammate's display name; ``role``/``agent_type``
             come from the declared spawn type (``PACT:pact-backend-coder`` ->
             role ``backend-coder``); ``cloud`` marks roster-derived members,
             ``model`` stays empty when the declaration does not carry one.
    Used by: model.derive_agents, demo.
    """

    name: str
    role: str = ""
    agent_type: str = ""
    color: str = ""
    model: str = ""
    cloud: bool = False
    kind: str = "specialist"
    source: str = "team-config"


@dataclass(frozen=True)
class StoredTask:
    """One task-store record (tasks/<team>/<id>.json).

    Location: roster.StoredTask.
    Summary: Normalized view of the platform task file: id/subject/owner/phase,
             lower-case status, optional variety stamp and the flags the model
             needs to enrich derived tasks.
    Used by: model.derive_tasks, tests.
    """

    id: str
    subject: str = ""
    owner: str = ""
    phase: str = ""
    status: str = ""
    kind: str = "task"
    variety: dict | None = None
    teachback: bool = False
    handoff: bool = False
    updated_at: str = ""


@dataclass
class TeamInfo:
    """Everything the visualizer knows about a session's team, or an empty stub.

    Location: roster.TeamInfo.
    Summary: ``name`` is the resolved team directory name ("" when unknown);
             ``members`` the local config members; ``intents`` the cloud roster
             spawn intents; ``tasks`` the task-store records; ``cloud`` is True
             when the team directory exists without a config.json (the cloud
             topology switch); ``config_present``/``roster_present`` are kept
             for diagnostics and tests.
    Used by: model, server, demo.
    """

    name: str = ""
    members: list[MemberSeed] = field(default_factory=list)
    intents: list[MemberSeed] = field(default_factory=list)
    tasks: list[StoredTask] = field(default_factory=list)
    cloud: bool = False
    config_present: bool = False
    roster_present: bool = False
    team_dir_present: bool = False

    @property
    def known(self) -> bool:
        """True when a team name resolved to an existing team on disk."""
        return bool(self.name) and (
            self.team_dir_present
            or self.config_present
            or self.roster_present
            or bool(self.tasks)
        )


def _read_json(path: Path, max_bytes: int = MAX_STATE_FILE_BYTES) -> Any:
    """Read one JSON file with a size cap; None on any failure."""
    try:
        if path.is_symlink():
            return None
        stat = path.stat()
        if stat.st_size > max_bytes or not path.is_file():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        return json.loads(text)
    except (OSError, json.JSONDecodeError, ValueError, RecursionError):
        return None


def _read_text_lines(path: Path, max_bytes: int = MAX_STATE_FILE_BYTES) -> list[str]:
    """Read a small text file's lines, or [] on any failure."""
    try:
        if path.is_symlink():
            return []
        stat = path.stat()
        if stat.st_size > max_bytes or not path.is_file():
            return []
        data = path.read_bytes()
    except OSError:
        return []
    if len(data) > max_bytes:
        return []
    try:
        text = data.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, ValueError):
        return []
    return text.splitlines()


def strip_agent_type(agent_type: str) -> str:
    """Normalize a declared agent type to its bare role spelling.

    Location: roster.strip_agent_type.
    Summary: Strips a ``PACT:`` namespace and a ``pact-`` role prefix:
             ``PACT:pact-backend-coder`` -> ``backend-coder``.
    Used by: roster loaders, model, tests.
    """
    text = agent_type.strip()
    if text.startswith("PACT:"):
        text = text[len("PACT:"):]
    if text.startswith("pact-"):
        text = text[len("pact-"):]
    return text


def role_from_agent_type(agent_type: str, fallback: str = "") -> str:
    """The display role for a declared agent type (or the fallback)."""
    role = strip_agent_type(agent_type)
    return role or fallback


def color_for_role(role: str) -> str:
    """Palette token for a role, defaulting to the neutral ``slate``."""
    return ROLE_COLORS.get(role.lower(), "slate")


def normalize_status(value: object) -> str:
    """Map a platform task status spelling to the visualizer's vocabulary."""
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    return _STATUS_MAP.get(text, text)


def _member_from_config(entry: object) -> MemberSeed | None:
    """Build a MemberSeed from one config.json members[] entry."""
    if not isinstance(entry, dict):
        return None
    name = as_text(entry.get("name"), _NAME_CHARS) if isinstance(entry.get("name"), str) else ""
    if not name:
        return None
    agent_type_text = (
        as_text(entry.get("agentType"), _NAME_CHARS)
        if isinstance(entry.get("agentType"), str)
        else ""
    )
    role = role_from_agent_type(agent_type_text, fallback=name)
    color = as_text(entry.get("color"), 40) if isinstance(entry.get("color"), str) else ""
    model = as_text(entry.get("model"), _NAME_CHARS) if isinstance(entry.get("model"), str) else ""
    return MemberSeed(
        name=name,
        role=role,
        agent_type=agent_type_text,
        color=color or color_for_role(role),
        model=model,
        cloud=False,
        kind="specialist",
        source="team-config",
    )


def _intent_from_record(record: object) -> MemberSeed | None:
    """Build a MemberSeed from one cloud roster ``intent`` record."""
    if not isinstance(record, dict) or record.get("kind") != "intent":
        return None
    name = as_text(record.get("name"), _NAME_CHARS) if isinstance(record.get("name"), str) else ""
    if not name:
        return None
    declared_type = record.get("type")
    type_text = as_text(declared_type, _NAME_CHARS) if isinstance(declared_type, str) else ""
    role = role_from_agent_type(type_text, fallback=name)
    return MemberSeed(
        name=name,
        role=role,
        agent_type=type_text,
        color=color_for_role(role),
        model="",
        cloud=True,
        kind="specialist",
        source="cloud-roster",
    )


def _load_roster_intents(path: Path) -> tuple[list[MemberSeed], bool]:
    """Parse the cloud roster's intent records; first record per tool_use_id wins."""
    lines = _read_text_lines(path)
    present = bool(lines)
    intents: list[MemberSeed] = []
    seen_tool_use_ids: set[str] = set()
    for line in lines:
        if not line.strip() or len(intents) >= MAX_ROSTER_INTENTS:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError, RecursionError):
            continue
        seed = _intent_from_record(record)
        if seed is None:
            continue
        tool_use_id = record.get("tu")
        if isinstance(tool_use_id, str) and tool_use_id:
            if tool_use_id in seen_tool_use_ids:
                continue
            seen_tool_use_ids.add(tool_use_id)
        if any(existing.name == seed.name for existing in intents):
            continue
        intents.append(seed)
    return intents, present


def _task_from_file(path: Path, payload: object) -> StoredTask | None:
    """Build a StoredTask from one task-store JSON file."""
    if not isinstance(payload, dict):
        return None
    raw_id = payload.get("id")
    if raw_id is None:
        raw_id = path.stem
    task_id = str(raw_id).strip()
    if not task_id:
        return None
    metadata = payload.get("metadata")
    meta = metadata if isinstance(metadata, dict) else {}
    subject = payload.get("subject")
    owner = payload.get("owner")
    variety = normalize_variety(meta.get("variety"))
    teachback = bool(meta.get("teachback_submit")) or bool(meta.get("teachback"))
    handoff = bool(meta.get("handoff"))
    completion_type = meta.get("type")
    kind = completion_type if isinstance(completion_type, str) and completion_type else "task"
    phase = meta.get("phase")
    phase_text = phase if isinstance(phase, str) else ""
    return StoredTask(
        id=task_id,
        subject=as_text(subject, MAX_FIELD_CHARS) if isinstance(subject, str) else "",
        owner=as_text(owner, _NAME_CHARS) if isinstance(owner, str) else "",
        phase=as_text(phase_text, 40).upper(),
        status=normalize_status(payload.get("status")),
        kind=kind,
        variety=variety,
        teachback=teachback,
        handoff=handoff,
    )


def _load_tasks(tasks_root: Path, team_name: str) -> list[StoredTask]:
    """Read one team's task-store JSON files in sorted file order, bounded.

    Only files ``discovery.contained`` accepts under ``<root>/tasks`` are
    read, so a symlinked team task dir or task file is skipped.
    """
    tasks: list[StoredTask] = []
    tasks_dir = tasks_root / team_name
    if not contained(tasks_root, tasks_dir):
        return tasks
    try:
        entries = sorted(tasks_dir.iterdir(), key=lambda item: item.name)
    except OSError:
        return tasks
    for entry in entries:
        if len(tasks) >= MAX_STORED_TASKS:
            break
        if not contained(tasks_root, entry):
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        if entry.name.startswith(".") or entry.suffix != ".json":
            continue
        if not valid_component(entry.stem):
            continue
        payload = _read_json(entry)
        task = _task_from_file(entry, payload)
        if task is not None:
            tasks.append(task)
    return tasks


def load_team(config_root: Path, team_name: str) -> TeamInfo:
    """Load the team config, cloud roster intents and task store for a team.

    Location: roster.load_team.
    Summary: Resolves ``<root>/teams/<team>/config.json``,
             ``<root>/teams/<team>/pact-roster.jsonl`` and
             ``<root>/tasks/<team>/*.json``. Invalid team names and missing
             files yield an empty TeamInfo; ``cloud`` is set when the team
             directory exists but has no config.json (the same switch
             hooks/shared/cloud_roster.py uses). Never raises.
    Used by: model.build_model, tests.
    """
    info = TeamInfo(name=team_name if isinstance(team_name, str) else "")
    if not info.name or not valid_component(info.name):
        return TeamInfo()
    teams_dir = Path(config_root) / TEAMS_DIRNAME
    team_dir = teams_dir / info.name
    try:
        team_dir_present = contained(teams_dir, team_dir) and team_dir.is_dir()
    except OSError:
        team_dir_present = False
    config_path = team_dir / TEAM_CONFIG_FILENAME
    roster_path = team_dir / ROSTER_FILENAME
    config = _read_json(config_path) if contained(teams_dir, config_path) else None
    info.config_present = isinstance(config, dict)
    info.team_dir_present = bool(team_dir_present)
    if isinstance(config, dict):
        members_raw = config.get("members")
        if isinstance(members_raw, list):
            for entry in members_raw[:MAX_TEAM_MEMBERS]:
                seed = _member_from_config(entry)
                if seed is not None:
                    info.members.append(seed)
    intents, roster_present = (
        _load_roster_intents(roster_path)
        if contained(teams_dir, roster_path)
        else ([], False)
    )
    info.intents = intents
    info.roster_present = roster_present
    info.cloud = team_dir_present and not info.config_present
    # Load the task store even when the team directory itself is gone: the
    # platform reaper can remove teams/ before tasks/, and losing the tasks
    # because of an unrelated directory is silent data loss.
    info.tasks = _load_tasks(Path(config_root) / TASKS_DIRNAME, info.name)
    return info


def read_session_context(session_dir: Path) -> dict:
    """Read a session directory's pact-session-context.json, or {}.

    Location: roster.read_session_context.
    Summary: The context file is the frame-independent record of
             project_dir/team_name for a session; it is used only to resolve
             the team whose config/tasks should be joined. Never raises.
    Used by: model.build_model.
    """
    payload = _read_json(Path(session_dir) / "pact-session-context.json")
    return payload if isinstance(payload, dict) else {}
