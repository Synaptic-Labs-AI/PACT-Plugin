"""Location: pact-plugin/tests/visualizer_census.py
Summary: Builders for journal events, task files, team configs and cloud
         roster records in the shapes the real PACT writers emit, for the
         visualizer tests. Each builder sets every field its writer always
         sets and nothing a writer never sets, so a fixture cannot pass on a
         field name or value that real data never carries.
Used by: test_visualizer_data.py, test_visualizer_server.py,
         test_visualizer_events_and_handoffs.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LOCAL_LEAD = "team-lead"
CLOUD_LEAD = "main"


def event(event_type: str, ts: str, **fields: Any) -> dict:
    """The journal envelope (v, type, ts) plus the given fields."""
    return {"v": 1, "type": event_type, "ts": ts, **fields}


def session_start(
    ts: str,
    *,
    session_id: str = "sess-1",
    project_dir: str = "/work/PACT-prompt",
    team: str = "session-abc12345",
    source: str = "startup",
    lead_address: str | None = LOCAL_LEAD,
) -> dict:
    """SessionStart hook record. lead_address=None models an older start
    that carries no lead address at all."""
    fields: dict[str, Any] = {
        "team": team,
        "session_id": session_id,
        "project_dir": project_dir,
        "worktree": "",
        "source": source,
    }
    if lead_address is not None:
        fields["lead_address"] = lead_address
    return event("session_start", ts, **fields)


def bare_session_end(ts: str) -> dict:
    """SessionEnd hook record: written on every process exit, envelope only."""
    return event("session_end", ts)


def cleanup_summary(ts: str) -> dict:
    """What the SessionEnd hook writes right after its own session_end."""
    return event(
        "cleanup_summary",
        ts,
        teams_reaped=0,
        teams_skipped=0,
        tasks_reaped=0,
        tasks_skipped=0,
        teams_ttl_days=7,
        tasks_ttl_days=7,
        teams_ran=True,
        tasks_ran=True,
    )


def wrapup_end(
    ts: str, *, pr_state: str = "MERGED", pr_number: int | None = 7, pr_source: str = "cwd"
) -> dict:
    """Wrap-up record. NONE/UNKNOWN records carry no pr_number."""
    fields: dict[str, Any] = {"source": "wrap-up", "pr_state": pr_state, "pr_source": pr_source}
    if pr_number is not None:
        fields["pr_number"] = pr_number
    return event("session_end", ts, **fields)


def session_paused(ts: str, *, pr_number: int = 7) -> dict:
    return event(
        "session_paused",
        ts,
        pr_number=pr_number,
        pr_url=f"https://example.invalid/pull/{pr_number}",
        branch="feature",
        worktree_path="/work/.worktrees/feature",
        consolidation_completed=True,
        team_name="session-abc12345",
    )


def session_refreshed(ts: str) -> dict:
    return event(
        "session_refreshed",
        ts,
        consolidation_completed=True,
        halt_active=False,
        worktrees=["/work/.worktrees/feature"],
        next_phase="CODE",
    )


def phase_transition(ts: str, phase: str, status: str, *, skip_reason: str = "") -> dict:
    return event(
        "phase_transition", ts, phase=phase, status=status, skip_reason=skip_reason, metadata={}
    )


def agent_dispatch(ts: str, agent: str, task_id: str, phase: str) -> dict:
    return event("agent_dispatch", ts, agent=agent, task_id=task_id, phase=phase, scope=[])


def teachback_ack(ts: str, gate_task_id: str, *, verdict: str = "yes") -> dict:
    """The ack names the TEACHBACK gate task and never an agent."""
    return event(
        "teachback_ack", ts, task_id=gate_task_id, rationale_articulates_this_dispatch=verdict
    )


def task_snapshot(
    ts: str,
    task_id: str,
    *,
    subject: str,
    occupant: str,
    owner: str | None = None,
    metadata: dict | None = None,
    task_type: str | None = None,
    team: str | None = None,
) -> dict:
    """task_metadata_snapshot. `occupant` separates tasks sharing a reused id;
    snapshots never carry a status."""
    fields: dict[str, Any] = {
        "task_id": task_id,
        "metadata": metadata or {},
        "subject": subject,
        "occupant": occupant,
    }
    if owner is not None:
        fields["owner"] = owner
    if task_type is not None:
        fields["task_type"] = task_type
    if team is not None:
        fields["team"] = team
    return event("task_metadata_snapshot", ts, **fields)


def agent_handoff(ts: str, agent: str, task_id: str, task_subject: str, handoff: dict) -> dict:
    return event(
        "agent_handoff", ts, agent=agent, task_id=task_id, task_subject=task_subject,
        handoff=handoff,
    )


def full_handoff() -> dict:
    return {
        "produced": "docs/spec.md",
        "decisions": ["narrow scope"],
        "reasoning_chain": "because",
        "uncertainty": "[LOW] none",
        "integration": "architect consumes the doc",
        "open_questions": [],
    }


def review_finding(ts: str, severity: str, finding: str, reviewer: str, task_id: str) -> dict:
    return event(
        "review_finding", ts, severity=severity, finding=finding, reviewer=reviewer,
        task_id=task_id,
    )


def review_dispatch(ts: str, reviewers: list[str], pr_number: int = 7) -> dict:
    return event(
        "review_dispatch", ts, pr_number=pr_number,
        pr_url=f"https://example.invalid/pull/{pr_number}", reviewers=reviewers,
    )


def dispatch_decision(ts: str, name: str | None, decision: str = "ALLOW") -> dict:
    return event(
        "dispatch_decision", ts, decision=decision, rule=None, subagent_type="pact-architect",
        name=name, team_name="session-abc12345", resolved_team="session-abc12345", reason=None,
    )


def write_lines(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def write_task_file(
    config_root: Path,
    team: str,
    task_id: str,
    *,
    subject: str,
    status: str = "pending",
    owner: str | None = None,
    metadata: dict | None = None,
    blocks: list[str] | None = None,
) -> Path:
    """A platform task file: id is a string, status is the platform vocabulary."""
    payload: dict[str, Any] = {
        "id": task_id,
        "subject": subject,
        "description": "",
        "status": status,
        "blocks": blocks or [],
        "blockedBy": [],
    }
    if owner is not None:
        payload["owner"] = owner
    if metadata is not None:
        payload["metadata"] = metadata
    path = config_root / "tasks" / team / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_team_config(config_root: Path, team: str, specialists: list[tuple[str, str]]) -> Path:
    """A local team config: the lead is a member named team-lead with
    agentType team-lead; specialists are (name, agentType)."""
    members = [
        {"agentId": f"{LOCAL_LEAD}@{team}", "name": LOCAL_LEAD, "agentType": "team-lead",
         "joinedAt": 0, "tmuxPaneId": "", "cwd": "/work", "subscriptions": [],
         "backendType": "in-process"},
    ]
    for name, agent_type in specialists:
        members.append(
            {"agentId": f"{name}@{team}", "name": name, "agentType": agent_type,
             "joinedAt": 0, "tmuxPaneId": "", "cwd": "/work", "subscriptions": [],
             "backendType": "in-process", "color": "blue", "model": "m", "prompt": "",
             "planModeRequired": False}
        )
    payload = {"name": team, "createdAt": 0, "leadAgentId": f"{LOCAL_LEAD}@{team}",
               "leadSessionId": "sess-1", "members": members}
    path = config_root / "teams" / team / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def roster_intent(tu: str, name: str, agent_type: str, ts: str) -> dict:
    return {"v": 1, "k": "intent", "tu": tu, "name": name, "type": agent_type, "ts": ts}


def roster_bind(tu: str, aid: str, agent_type: str, ts: str) -> dict:
    return {"v": 1, "k": "bind", "tu": tu, "aid": aid, "type": agent_type, "via": "meta",
            "meta_present": True, "ts": ts}


def roster_confirm(tu: str, aid: str, ts: str) -> dict:
    return {"v": 1, "k": "confirm", "tu": tu, "aid": aid, "ts": ts}
