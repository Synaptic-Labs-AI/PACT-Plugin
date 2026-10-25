"""
Location: pact-plugin/tests/test_roster_writer.py
Summary: Tests for hooks/roster_writer.py: the SubagentStart `bind` (meta
         join, type join, ambiguity, depth and name checks, meta_present), the
         lead PostToolUse(Agent) `confirm` (foreground and background
         tool_response shapes), the local no-op, and fail-open output.
Used by: the pact-plugin test suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import roster_writer
from fixtures.actor_frames import use_config, write_subagent_meta, write_team_config
from fixtures.roster_frames import (
    agent_id,
    confirm,
    intent,
    iso,
    post_agent_frame,
    read_roster,
    subagent_start_frame,
    tool_use_id,
    write_roster,
)

TEAM = "cloud-team"
TU1, TU2 = tool_use_id(1), tool_use_id(2)
A1, A2 = agent_id(1), agent_id(2)
HOOK = Path(__file__).parent.parent / "hooks" / "roster_writer.py"


@pytest.fixture
def config(tmp_path, monkeypatch, pact_context):
    root = use_config(monkeypatch, tmp_path / "cfg")
    pact_context(team_name=TEAM, session_id="s1")
    return root


def _binds(config):
    return [r for r in read_roster(config, TEAM) if r["k"] == "bind"]


def _confirms(config):
    return [r for r in read_roster(config, TEAM) if r["k"] == "confirm"]


def _meta(name, tu, depth=1, agent_type="pact-backend-coder"):
    return {"agentType": agent_type, "name": name, "toolUseId": tu,
            "spawnDepth": depth, "isBuiltIn": False}


# ─── SubagentStart ───────────────────────────────────────────────────────────


def test_type_join_binds_the_single_same_type_intent(config):
    write_roster(config, TEAM, [intent(TU1, "coder", "pact-backend-coder")])
    assert roster_writer.handle_subagent_start(
        subagent_start_frame(config, A1, "pact-backend-coder"))
    (record,) = _binds(config)
    assert record["tu"] == TU1 and record["aid"] == A1
    assert record["via"] == "type" and record["meta_present"] is False


def test_meta_join_binds_when_meta_exists_at_start(config):
    write_roster(config, TEAM, [intent(TU1, "c1", "pact-backend-coder"),
                                intent(TU2, "c2", "pact-backend-coder")])
    write_subagent_meta(config, A2, _meta("c2", TU2))
    assert roster_writer.handle_subagent_start(
        subagent_start_frame(config, A2, "pact-backend-coder"))
    (record,) = _binds(config)
    assert record["tu"] == TU2 and record["via"] == "meta"
    assert record["meta_present"] is True


def test_ambiguous_same_type_intents_bind_nothing(config):
    write_roster(config, TEAM, [intent(TU1, "c1", "pact-backend-coder"),
                                intent(TU2, "c2", "pact-backend-coder")])
    assert not roster_writer.handle_subagent_start(
        subagent_start_frame(config, A1, "pact-backend-coder"))
    assert _binds(config) == []


def test_meta_name_mismatch_does_not_meta_join(config):
    write_roster(config, TEAM, [intent(TU1, "c1", "pact-backend-coder"),
                                intent(TU2, "c2", "pact-backend-coder")])
    write_subagent_meta(config, A2, _meta("someone-else", TU2))
    assert not roster_writer.handle_subagent_start(
        subagent_start_frame(config, A2, "pact-backend-coder"))


def test_nested_spawn_depth_does_not_meta_join(config):
    write_roster(config, TEAM, [intent(TU1, "c1", "pact-backend-coder"),
                                intent(TU2, "c2", "pact-backend-coder")])
    write_subagent_meta(config, A2, _meta("c2", TU2, depth=2))
    assert not roster_writer.handle_subagent_start(
        subagent_start_frame(config, A2, "pact-backend-coder"))


def test_type_join_ignores_other_types_and_stale_intents(config):
    write_roster(config, TEAM, [
        intent(TU1, "old", "pact-backend-coder", ts=iso(minutes_ago=30)),
        intent(TU2, "new", "pact-backend-coder"),
        intent(tool_use_id(3), "sec", "pact-secretary"),
    ])
    assert roster_writer.handle_subagent_start(
        subagent_start_frame(config, A1, "PACT:pact-backend-coder"))
    assert _binds(config)[0]["tu"] == TU2


def test_type_join_skips_intents_already_confirmed(config):
    """Measured: a background spawn's confirm lands BEFORE its SubagentStart."""
    write_roster(config, TEAM, [intent(TU1, "bg", "general-purpose"),
                                confirm(TU1, A1),
                                intent(TU2, "fg", "general-purpose")])
    assert roster_writer.handle_subagent_start(
        subagent_start_frame(config, A2, "general-purpose"))
    assert _binds(config)[0]["tu"] == TU2


def test_already_bound_agent_is_not_bound_again(config):
    write_roster(config, TEAM, [intent(TU1, "c", "t"), confirm(TU1, A1),
                                intent(TU2, "d", "t")])
    assert not roster_writer.handle_subagent_start(subagent_start_frame(config, A1, "t"))


def test_no_roster_file_writes_nothing(config):
    (config / "teams" / TEAM).mkdir(parents=True, exist_ok=True)
    assert not roster_writer.handle_subagent_start(subagent_start_frame(config, A1, "t"))
    assert read_roster(config, TEAM) == []


def test_inprocess_id_shape_is_ignored(config):
    write_roster(config, TEAM, [intent(TU1, "coder", "coder")])
    frame = subagent_start_frame(config, "acoder-0123456789abcdef", "coder")
    assert not roster_writer.handle_subagent_start(frame)


def test_local_team_writes_nothing(config):
    write_team_config(config, TEAM, [])
    (config / "teams" / TEAM / "pact-roster.jsonl").write_text(
        json.dumps(intent(TU1, "c", "t")) + "\n")
    assert not roster_writer.handle_subagent_start(subagent_start_frame(config, A1, "t"))
    assert _binds(config) == []


# ─── PostToolUse(Agent) ─────────────────────────────────────────────────────


def test_foreground_confirm_carries_agent_type(config):
    write_roster(config, TEAM, [intent(TU1, "c", "pact-backend-coder")])
    assert roster_writer.handle_post_agent(
        post_agent_frame(config, TU1, A1, "pact-backend-coder"))
    (record,) = _confirms(config)
    assert record == {**record, "tu": TU1, "aid": A1, "type": "pact-backend-coder"}


def test_background_confirm_has_no_type(config):
    write_roster(config, TEAM, [intent(TU1, "c", "general-purpose")])
    assert roster_writer.handle_post_agent(
        post_agent_frame(config, TU1, A1, background=True))
    (record,) = _confirms(config)
    assert "type" not in record


def test_non_lead_frame_writes_no_confirm(config):
    write_roster(config, TEAM, [intent(TU1, "c", "t")])
    frame = post_agent_frame(config, TU1, A1, "t", frame_agent_type="pact-backend-coder")
    assert not roster_writer.handle_post_agent(frame)


@pytest.mark.parametrize("bad_aid", ["", "agent", "A0123456789ABCDEF", None, 7])
def test_malformed_agent_id_writes_nothing(config, bad_aid):
    write_roster(config, TEAM, [intent(TU1, "c", "t")])
    assert not roster_writer.handle_post_agent(post_agent_frame(config, TU1, bad_aid, "t"))


def test_confirm_needs_a_matching_intent(config):
    write_roster(config, TEAM, [intent(TU1, "c", "t")])
    assert not roster_writer.handle_post_agent(post_agent_frame(config, TU2, A1, "t"))


def test_confirm_local_writes_nothing(config):
    write_team_config(config, TEAM, [])
    assert not roster_writer.handle_post_agent(post_agent_frame(config, TU1, A1, "t"))


# ─── main: fail-open ────────────────────────────────────────────────────────


@pytest.mark.parametrize("stdin", ["not json", "[]", "{}",
                                   '{"hook_event_name": "SubagentStart"}',
                                   '{"hook_event_name": "PostToolUse", "tool_name": "Agent"}'])
def test_main_always_suppresses_and_exits_zero(stdin, tmp_path):
    result = subprocess.run(
        [sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True,
        cwd=str(tmp_path), timeout=30,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"suppressOutput": True}


def test_main_swallows_handler_exceptions(monkeypatch, capsys):
    def _boom(_frame, teams_dir=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(roster_writer, "handle_subagent_start", _boom)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(
        json.dumps({"hook_event_name": "SubagentStart"})))
    with pytest.raises(SystemExit) as exc:
        roster_writer.main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out) == {"suppressOutput": True}
