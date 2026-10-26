"""
Location: pact-plugin/tests/fixtures/roster_frames.py
Summary: Frame builders and roster writers for the cloud roster
         (shared/cloud_roster.py, roster_writer.py, dispatch_gate's intent
         write, and the roster consumers in actor_identity, intentional_wait
         and task_claim_gate).
Used by: tests/test_cloud_roster.py, tests/test_roster_writer.py,
         tests/test_cloud_roster_consumers.py,
         tests/test_cloud_roster_adversarial.py.

Every frame is SYNTHESIZED from the key sets measured by the WP0 probe (CC
2.1.310, a child `claude -p` session, docs/architecture/cloud-roster.md §8):

  - PreToolUse Agent (lead): cwd, effort, hook_event_name, permission_mode,
    prompt_id, scratchpad_dir, session_id, tool_input{description, name,
    prompt, run_in_background, subagent_type}, tool_name, tool_use_id,
    transcript_path. No agent_id.
  - SubagentStart: agent_id (a<16hex>), agent_type (the TYPE), cwd,
    hook_event_name, prompt_id, scratchpad_dir, session_id, transcript_path.
    No tool_use_id, no name. meta.json did NOT exist yet.
  - PostToolUse Agent (lead), foreground: tool_response{agentId, agentType,
    status: "completed", ...}; background: tool_response{agentId, status:
    "async_launched", isAsync, outputFile, ...} with no agentType.

The lead frames here carry the lead's agent_type (`PACT:pact-orchestrator`),
as a PACT lead launched with `--agent` does; the probe's plain child session
had none.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fixtures.actor_frames import LEAD_SID, lead_transcript

LEAD_AGENT_TYPE = "PACT:pact-orchestrator"
_SYNTHESIZED = {"capture_method": "synthesized-from-wp0-key-sets"}


def tool_use_id(n: int) -> str:
    return f"toolu_01Synthetic{n:04d}"


def agent_id(n: int) -> str:
    return f"a{n:016x}"


def make_cloud_team(config_root: Path, team: str) -> Path:
    """A team directory with NO config.json: the cloud shape."""
    team_dir = config_root / "teams" / team
    team_dir.mkdir(parents=True, exist_ok=True)
    return team_dir


def roster_path(config_root: Path, team: str) -> Path:
    return config_root / "teams" / team / "pact-roster.jsonl"


def iso(minutes_ago: float = 0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.isoformat(timespec="seconds")


def intent(tu: str, name: str, agent_type: str, ts: str | None = None) -> dict:
    return {"v": 1, "k": "intent", "tu": tu, "name": name, "type": agent_type,
            "ts": ts or iso()}


def bind(tu: str, aid: str, agent_type: str, via: str = "type",
         meta_present: bool = False) -> dict:
    return {"v": 1, "k": "bind", "tu": tu, "aid": aid, "type": agent_type,
            "via": via, "meta_present": meta_present, "ts": iso()}


def confirm(tu: str, aid: str, agent_type: str | None = None) -> dict:
    record = {"v": 1, "k": "confirm", "tu": tu, "aid": aid, "ts": iso()}
    if agent_type is not None:
        record["type"] = agent_type
    return record


def write_roster(config_root: Path, team: str, records: list, raw_lines=()) -> Path:
    """Write records (and any raw text lines) as the team's roster file."""
    make_cloud_team(config_root, team)
    path = roster_path(config_root, team)
    lines = [json.dumps(r) for r in records] + list(raw_lines)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def read_roster(config_root: Path, team: str) -> list:
    path = roster_path(config_root, team)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def pre_agent_frame(config_root: Path, tu: str, name: str | None,
                    subagent_type: str, agent_type: str | None = LEAD_AGENT_TYPE) -> dict:
    tool_input = {"description": "spawn", "prompt": "Check TaskList.",
                  "run_in_background": False, "subagent_type": subagent_type}
    if name is not None:
        tool_input["name"] = name
    frame = {
        "hook_event_name": "PreToolUse", "tool_name": "Agent",
        "tool_input": tool_input, "tool_use_id": tu,
        "session_id": LEAD_SID, "transcript_path": lead_transcript(config_root),
        "cwd": str(config_root), "_meta": dict(_SYNTHESIZED),
    }
    if agent_type is not None:
        frame["agent_type"] = agent_type
    return frame


def subagent_start_frame(config_root: Path, aid: str, agent_type: str) -> dict:
    return {
        "hook_event_name": "SubagentStart", "agent_id": aid,
        "agent_type": agent_type, "session_id": LEAD_SID,
        "transcript_path": lead_transcript(config_root),
        "cwd": str(config_root), "prompt_id": "p", "scratchpad_dir": "/tmp/s",
        "_meta": dict(_SYNTHESIZED),
    }


def post_agent_frame(config_root: Path, tu: str, aid: str,
                     agent_type: str | None = None, *, background: bool = False,
                     frame_agent_type: str | None = LEAD_AGENT_TYPE) -> dict:
    if background:
        response = {"agentId": aid, "status": "async_launched", "isAsync": True,
                    "outputFile": "/tmp/out", "canReadOutputFile": True}
    else:
        response = {"agentId": aid, "status": "completed", "content": [],
                    "totalDurationMs": 1}
        if agent_type is not None:
            response["agentType"] = agent_type
    frame = {
        "hook_event_name": "PostToolUse", "tool_name": "Agent",
        "tool_input": {"name": "x", "subagent_type": agent_type or "general-purpose"},
        "tool_response": response, "tool_use_id": tu,
        "session_id": LEAD_SID, "transcript_path": lead_transcript(config_root),
        "_meta": dict(_SYNTHESIZED),
    }
    if frame_agent_type is not None:
        frame["agent_type"] = frame_agent_type
    return frame
