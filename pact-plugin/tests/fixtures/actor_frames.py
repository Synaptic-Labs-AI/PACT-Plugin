"""
Location: pact-plugin/tests/fixtures/actor_frames.py
Summary: Frame builders and side-file writers for actor resolution
         (shared/actor_identity.py) and the self-completion rule in
         task_lifecycle_gate.py.
Used by: tests/test_actor_identity.py, tests/test_task_lifecycle_gate.py,
         tests/test_task_lifecycle_gate_smoke.py.

Every frame here is SYNTHESIZED from captured shapes, never captured itself
(`_meta.capture_method` says so). The shapes per mode:

  - lead:        agent_type = lead spelling, no agent_id.
  - in-process:  agent_type = the member NAME, agent_id = a<name>-<16hex>,
                 plus a team-config member {name, agentType}.
  - cloud:       agent_type = a TYPE, agent_id = a<16hex>, plus
                 <config>/projects/-proj/<LEAD_SID>/subagents/agent-<id>.meta.json
                 in the cloud shape, and NO team config.
  - tmux:        agent_type = a TYPE, no agent_id, a session id of its own.
  - plain:       no agent_type, no agent_id.

`subagent_metadata` only reads under `<config>/projects`, so callers must
point CLAUDE_CONFIG_DIR at the same root they pass here (see `use_config`).
"""

from __future__ import annotations

import json
from pathlib import Path

LEAD_SID = "11111111-2222-3333-4444-555555555555"
TMUX_SID = "99999999-8888-7777-6666-555555555555"
SUBAGENT_ID = "a0123456789abcdef"
_SYNTHESIZED = {"capture_method": "synthesized-from-captured-shape"}


def use_config(monkeypatch, config_root: Path) -> Path:
    """Point the config root (teams/, projects/) at `config_root`."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_root))
    return config_root


def write_team_config(config_root: Path, team: str, members: list[dict]) -> Path:
    """Write <config>/teams/<team>/config.json with the given members."""
    team_dir = config_root / "teams" / team
    team_dir.mkdir(parents=True, exist_ok=True)
    path = team_dir / "config.json"
    path.write_text(json.dumps({"members": members}), encoding="utf-8")
    return path


def lead_transcript(config_root: Path) -> str:
    """The lead's transcript path; meta files sit in <parent>/<LEAD_SID>/subagents."""
    return str(config_root / "projects" / "-proj" / f"{LEAD_SID}.jsonl")


def write_subagent_meta(config_root: Path, agent_id: str, meta: dict) -> Path:
    """Write the platform's meta file where subagent_metadata looks for it."""
    subagents = config_root / "projects" / "-proj" / LEAD_SID / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    path = subagents / f"agent-{agent_id}.meta.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    return path


def cloud_meta(name: str, agent_type: str) -> dict:
    """The cloud meta shape observed on CC 2.1.310: agentType is a TYPE."""
    return {
        "agentType": agent_type, "isBuiltIn": False,
        "description": f"Spawn {name}", "name": name,
        "toolUseId": "toolu_synthetic", "spawnDepth": 1,
        "requestShape": "foreground", "requestNonInteractive": True,
    }


def local_meta(name: str, agent_type: str, team: str) -> dict:
    """The local in-process meta shape: agentType is the NAME."""
    return {
        "agentType": name, "name": name, "taskKind": "in_process_teammate",
        "teamName": team, "customAgentType": agent_type,
    }


def completion_frame(task_id: str = "7", **fields) -> dict:
    """A PostToolUse(TaskUpdate, status=completed) frame; fields override."""
    frame = {
        "hook_event_name": "PostToolUse",
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": str(task_id), "status": "completed"},
        "tool_response": {},
        "_meta": dict(_SYNTHESIZED),
    }
    frame.update(fields)
    return frame


def lead_frame(task_id: str = "7") -> dict:
    return completion_frame(
        task_id, agent_type="PACT:pact-orchestrator", session_id=LEAD_SID,
    )


def inprocess_frame(config_root: Path, name: str, task_id: str = "7") -> dict:
    return completion_frame(
        task_id, agent_type=name, agent_id=f"a{name}-0123456789abcdef",
        session_id=LEAD_SID, transcript_path=lead_transcript(config_root),
    )


def cloud_subagent_frame(
    config_root: Path, agent_type: str, task_id: str = "7",
    agent_id: str = SUBAGENT_ID,
) -> dict:
    return completion_frame(
        task_id, agent_type=agent_type, agent_id=agent_id,
        session_id=LEAD_SID, transcript_path=lead_transcript(config_root),
    )


def tmux_frame(agent_type: str = "pact-backend-coder", task_id: str = "7") -> dict:
    return completion_frame(task_id, agent_type=agent_type, session_id=TMUX_SID)


def plain_frame(task_id: str = "7") -> dict:
    return completion_frame(task_id, session_id=LEAD_SID)


def at_id_frame(name: str, team: str, task_id: str = "7") -> dict:
    return completion_frame(
        task_id, agent_type=name, agent_id=f"{name}@{team}", session_id=LEAD_SID,
    )
