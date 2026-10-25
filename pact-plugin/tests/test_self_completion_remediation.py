"""
Location: pact-plugin/tests/test_self_completion_remediation.py
Summary: Regression tests for the PR #1670 review remediation: the step-4
         member-name guard, the C6 fallback (in-process frames with
         unexpected id shapes), frame-first typing, the shadowed-plugin-type
         check, the owner="" escape, the owner display cap, and the bounded
         meta.json read.
Used by: the pact-plugin test suite.

Sibling of tests/test_self_completion_actor_edges.py and
tests/test_actor_identity.py, kept separate so each finding's pin is easy to
find from the review synthesis (docs/review/pr-1670-self-completion-actor.md).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import task_lifecycle_gate as tlg
from fixtures.actor_frames import (
    SUBAGENT_ID,
    cloud_meta,
    cloud_subagent_frame,
    inprocess_frame,
    local_meta,
    write_subagent_meta,
    write_team_config,
)
from shared import actor_identity, state_file, subagent_meta
from shared.actor_identity import ROLE_TEAMMATE, resolve_actor, valid_name

TEAM = "test-team"
_HANDOFF = {
    "produced": "x", "decisions": "x", "reasoning_chain": "x",
    "uncertainty": "x", "integration": "x", "open_questions": "x",
}
_COMPLETION_RULES = frozenset({"self_completion", "non_lead_completion"})


def _with_task(frame: dict, owner, *, tool_input_extra: dict | None = None) -> dict:
    frame["tool_input"] = {"taskId": "7", "status": "completed", **(tool_input_extra or {})}
    frame["tool_response"] = {"task": {
        "id": "7", "subject": "implement foo", "owner": owner,
        "metadata": {"handoff": dict(_HANDOFF)},
    }}
    return frame


def _rules(payload) -> list[str]:
    return [r for r, _ in tlg.evaluate_lifecycle(payload) if r in _COMPLETION_RULES]


def _messages(payload) -> list[str]:
    return [m for r, m in tlg.evaluate_lifecycle(payload) if r in _COMPLETION_RULES]


@pytest.fixture
def writebacks(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tlg, "_writeback_dispute", lambda task_id: calls.append(task_id) or True)
    return calls


@pytest.fixture
def team(pact_context, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    pact_context(team_name=TEAM, session_id="test-session")
    from shared.paths import get_claude_config_dir
    return get_claude_config_dir()


# =============================================================================
# A-MINOR-1: step 4 drops a frame agent_type that names a member
# =============================================================================


def test_step4_member_name_in_frame_is_not_a_type(team):
    """An in-process frame carrying a subagent-shaped id (the C6 shape) falls
    to step 4. Its agent_type is the member NAME 'pact-secretary', which must
    not become the exempt TYPE."""
    write_team_config(team, TEAM, [{"name": "pact-secretary", "agentType": "pact-backend-coder"}])
    frame = inprocess_frame(team, "pact-secretary")
    frame["agent_id"] = SUBAGENT_ID
    actor = resolve_actor(frame, TEAM)
    assert actor.role == ROLE_TEAMMATE
    assert actor.agent_type is None


def test_step4_member_name_falls_back_to_meta_type(team):
    """With the frame type dropped as a name, the local meta's
    customAgentType supplies the TYPE."""
    write_team_config(team, TEAM, [{"name": "coder", "agentType": "pact-backend-coder"}])
    write_subagent_meta(team, SUBAGENT_ID, local_meta("coder", "pact-backend-coder", TEAM))
    frame = inprocess_frame(team, "coder")
    frame["agent_id"] = SUBAGENT_ID
    actor = resolve_actor(frame, TEAM)
    assert (actor.name, actor.agent_type) == ("coder", "pact-backend-coder")


def test_step4_member_name_does_not_exempt_another_owners_task(team, writebacks):
    write_team_config(team, TEAM, [{"name": "pact-secretary", "agentType": "pact-backend-coder"}])
    frame = inprocess_frame(team, "pact-secretary")
    frame["agent_id"] = SUBAGENT_ID
    assert _rules(_with_task(frame, "someone-else")) == ["non_lead_completion"]
    assert writebacks == ["7"]


# =============================================================================
# A-MINOR-2: the C6 fallback, in-process frames with unexpected id shapes
# =============================================================================

_C6_IDS = [
    "0123456789abcdef",          # no leading "a"
    "coder-0123456789abcdef",    # name without the leading "a"
    "acoder-0123456789ABCDEF",   # upper-case hex
    "acoder-0123456789abcde",    # 15 hex
    SUBAGENT_ID,                 # a<16hex>: the subagent shape
    "coder@other-team",          # name@team for another team
]


@pytest.mark.parametrize("agent_id", _C6_IDS)
def test_c6_non_exempt_teammate_stays_flagged(team, writebacks, agent_id):
    write_team_config(team, TEAM, [{"name": "coder", "agentType": "pact-backend-coder"}])
    frame = inprocess_frame(team, "coder")
    frame["agent_id"] = agent_id
    rules = _rules(_with_task(frame, "coder"))
    assert len(rules) == 1 and rules[0] in _COMPLETION_RULES
    assert writebacks == ["7"]


@pytest.mark.parametrize("agent_id", _C6_IDS)
def test_c6_secretary_stays_exempt(team, writebacks, agent_id):
    write_team_config(team, TEAM, [{"name": "secretary", "agentType": "pact-secretary"}])
    frame = inprocess_frame(team, "secretary")
    frame["agent_id"] = agent_id
    assert _rules(_with_task(frame, "secretary")) == []
    assert writebacks == []


# =============================================================================
# S-MINOR-2: the frame type wins over a rewritten meta.json
# =============================================================================


@pytest.mark.parametrize("field", ["customAgentType", "spawnedAgentType", "agentType"])
def test_meta_type_tamper_does_not_override_frame_type(team, field):
    meta = cloud_meta("backend-coder", "pact-backend-coder")
    meta[field] = "pact-secretary"
    write_subagent_meta(team, SUBAGENT_ID, meta)
    actor = resolve_actor(cloud_subagent_frame(team, "pact-backend-coder"), TEAM)
    assert actor.agent_type == "pact-backend-coder"


def test_meta_type_tamper_does_not_exempt(team, writebacks):
    meta = cloud_meta("backend-coder", "pact-backend-coder")
    meta["customAgentType"] = "pact-secretary"
    write_subagent_meta(team, SUBAGENT_ID, meta)
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "backend-coder")
    assert _rules(payload) == ["self_completion"]


def test_deleted_meta_keeps_the_frame_type(team, writebacks):
    """Deleting meta.json removes the NAME (a documented residual) but not the
    TYPE: a non-exempt frame type stays non-exempt."""
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "secretary")
    assert _rules(payload) == ["non_lead_completion"]


def test_frame_type_equal_to_meta_name_falls_back_to_meta_type(team):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("pact-auditor", "pact-preparer"))
    actor = resolve_actor(cloud_subagent_frame(team, "pact-auditor"), TEAM)
    assert actor.agent_type == "pact-preparer"


# =============================================================================
# S-MINOR-1: a bare plugin type shadowed by a non-plugin definition
# =============================================================================


def _agent_file(directory: Path, filename: str, name: str | None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    front = f"name: {name}\n" if name is not None else ""
    path.write_text(f"---\n{front}description: x\n---\nbody\n", encoding="utf-8")
    return path


def _cloud_secretary(team, cwd: Path | None = None, frame_type="pact-secretary"):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("secretary", "pact-secretary"))
    frame = cloud_subagent_frame(team, frame_type)
    if cwd is not None:
        frame["cwd"] = str(cwd)
    return frame


def test_unshadowed_bare_secretary_type_is_kept(team, tmp_path):
    """The real cloud shape: meta and frame carry the bare type, and no other
    definition exists. The type stays, so the cloud secretary stays exempt."""
    assert resolve_actor(_cloud_secretary(team, tmp_path), TEAM).agent_type == "pact-secretary"


def test_project_definition_shadows_the_bare_type(team, tmp_path):
    project = tmp_path / "proj"
    _agent_file(project / ".claude" / "agents", "impostor.md", "pact-secretary")
    actor = resolve_actor(_cloud_secretary(team, project / "src" / "deep"), TEAM)
    assert actor.agent_type is None


def test_project_definition_without_frontmatter_name_uses_the_stem(team, tmp_path):
    project = tmp_path / "proj"
    _agent_file(project / ".claude" / "agents", "pact-secretary.md", None)
    assert resolve_actor(_cloud_secretary(team, project), TEAM).agent_type is None


def test_file_without_frontmatter_is_not_a_definition(team, tmp_path):
    agents = tmp_path / "proj" / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "pact-secretary.md").write_text("no frontmatter\n", encoding="utf-8")
    actor = resolve_actor(_cloud_secretary(team, tmp_path / "proj"), TEAM)
    assert actor.agent_type == "pact-secretary"


def test_claude_project_dir_is_searched(team, tmp_path, monkeypatch):
    project = tmp_path / "proj"
    _agent_file(project / ".claude" / "agents", "x.md", "pact-secretary")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    assert resolve_actor(_cloud_secretary(team), TEAM).agent_type is None


def test_user_regular_file_shadows_the_bare_type(team):
    """A regular file in <config>/agents is a user override, not the plugin."""
    _agent_file(team / "agents", "pact-secretary.md", "pact-secretary")
    assert resolve_actor(_cloud_secretary(team), TEAM).agent_type is None


def test_user_symlink_to_this_plugin_is_not_a_shadow(team):
    """symlinks.py links the plugin's agents into <config>/agents; those links
    are the plugin's own definition."""
    agents = team / "agents"
    agents.mkdir(parents=True)
    os.symlink(subagent_meta.plugin_agents_dir() / "pact-secretary.md", agents / "pact-secretary.md")
    assert resolve_actor(_cloud_secretary(team), TEAM).agent_type == "pact-secretary"


def test_user_symlink_to_an_installed_pact_copy_is_not_a_shadow(team, tmp_path):
    """The installed links point into the plugin cache under <config>/plugins:
    an agents/ directory whose plugin manifest names PACT. (A PACT-named
    manifest outside <config>/plugins is not enough: see
    test_cloud_roster_hardening.py, security review #70.)"""
    cache = team / "plugins" / "cache" / "pact-plugin" / "PACT" / "9.9.9"
    _agent_file(cache / "agents", "pact-secretary.md", "pact-secretary")
    (cache / ".claude-plugin").mkdir(parents=True)
    (cache / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "PACT"}))
    (team / "agents").mkdir(parents=True)
    os.symlink(cache / "agents" / "pact-secretary.md", team / "agents" / "pact-secretary.md")
    assert resolve_actor(_cloud_secretary(team), TEAM).agent_type == "pact-secretary"


def test_user_symlink_to_another_plugin_shadows(team, tmp_path):
    other = tmp_path / "other"
    _agent_file(other / "agents", "pact-secretary.md", "pact-secretary")
    (other / ".claude-plugin").mkdir(parents=True)
    (other / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "NOTPACT"}))
    (team / "agents").mkdir(parents=True)
    os.symlink(other / "agents" / "pact-secretary.md", team / "agents" / "pact-secretary.md")
    assert resolve_actor(_cloud_secretary(team), TEAM).agent_type is None


def test_namespaced_frame_type_is_plugin_provenance(team, tmp_path):
    project = tmp_path / "proj"
    _agent_file(project / ".claude" / "agents", "impostor.md", "pact-secretary")
    frame = _cloud_secretary(team, project, frame_type="PACT:pact-secretary")
    assert resolve_actor(frame, TEAM).agent_type == "pact-secretary"


def test_definition_with_another_name_does_not_shadow(team, tmp_path):
    project = tmp_path / "proj"
    _agent_file(project / ".claude" / "agents", "pact-secretary.md", "my-helper")
    assert resolve_actor(_cloud_secretary(team, project), TEAM).agent_type == "pact-secretary"


def test_non_plugin_type_is_never_checked(team, tmp_path):
    """Only types this plugin ships are checked; another type is kept as is."""
    project = tmp_path / "proj"
    _agent_file(project / ".claude" / "agents", "mine.md", "my-agent")
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("helper", "my-agent"))
    frame = cloud_subagent_frame(team, "my-agent")
    frame["cwd"] = str(project)
    assert resolve_actor(frame, TEAM).agent_type == "my-agent"


def test_shadowed_secretary_is_not_exempt(team, writebacks, tmp_path):
    project = tmp_path / "proj"
    _agent_file(project / ".claude" / "agents", "impostor.md", "pact-secretary")
    payload = _with_task(_cloud_secretary(team, project), "secretary")
    assert _rules(payload) == ["self_completion"]


def test_unshadowed_cloud_secretary_stays_exempt(team, writebacks, tmp_path):
    payload = _with_task(_cloud_secretary(team, tmp_path), "secretary")
    assert _rules(payload) == []


def test_shadowed_inprocess_config_type_is_dropped(team, tmp_path):
    """The check applies to every source, including the local team config."""
    write_team_config(team, TEAM, [{"name": "sec", "agentType": "pact-secretary"}])
    project = tmp_path / "proj"
    _agent_file(project / ".claude" / "agents", "impostor.md", "pact-secretary")
    frame = inprocess_frame(team, "sec")
    frame["cwd"] = str(project)
    actor = resolve_actor(frame, TEAM)
    assert (actor.name, actor.agent_type) == ("sec", None)


def test_unreadable_agents_dir_is_not_a_shadow(team, tmp_path, monkeypatch):
    project = tmp_path / "proj"
    _agent_file(project / ".claude" / "agents", "impostor.md", "pact-secretary")

    def boom(*_args, **_kwargs):
        raise OSError("denied")

    monkeypatch.setattr(os, "walk", boom)
    assert resolve_actor(_cloud_secretary(team, project), TEAM).agent_type == "pact-secretary"


# =============================================================================
# S-MINOR-4: TaskUpdate(owner="", status="completed") in one call
# =============================================================================


def test_owner_cleared_in_the_completing_call_is_flagged(team, writebacks):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(
        cloud_subagent_frame(team, "pact-backend-coder"), "", tool_input_extra={"owner": ""}
    )
    messages = _messages(payload)
    assert _rules(payload) == ["non_lead_completion"]
    assert "after clearing its owner in the same call" in messages[0]
    assert writebacks


def test_owner_already_empty_stays_silent(team, writebacks):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "")
    assert _rules(payload) == []
    assert writebacks == []


def test_named_secretary_clearing_owner_is_flagged(team, writebacks, tmp_path):
    """Clearing the owner hides whose task it was, so a NAMED exempt actor
    cannot show it is the owner (1b needs actor name == owner) and is flagged.
    Conservative on purpose: the secretary has no reason to clear an owner."""
    payload = _with_task(_cloud_secretary(team, tmp_path), "", tool_input_extra={"owner": ""})
    assert _rules(payload) == ["non_lead_completion"]


# =============================================================================
# S-FUTURE-1: the owner is capped before it reaches additionalContext
# =============================================================================


@pytest.mark.parametrize("owner", ["x" * 129, "line\ninjected", "nul\x00", "ignore all " * 40])
def test_invalid_owner_is_not_echoed(team, writebacks, owner):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), owner)
    (message,) = _messages(payload)
    assert owner not in message and repr(owner) not in message
    assert f"cannot be shown ({len(owner)} characters)" in message


def test_valid_owner_is_echoed(team, writebacks):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "x" * 128)
    (message,) = _messages(payload)
    assert f"owned by {'x' * 128!r}" in message


def test_valid_name_is_public_and_bounded():
    assert valid_name("a" * actor_identity.MAX_NAME_CHARS) == "a" * 128
    assert valid_name("a" * 129) is None
    assert valid_name("tab\there") is None
    assert valid_name("") is None
    assert valid_name(7) is None


# =============================================================================
# S-FUTURE-2: the meta.json read itself is bounded
# =============================================================================


def test_oversize_meta_is_rejected_by_the_read(team):
    path = write_subagent_meta(team, SUBAGENT_ID, cloud_meta("secretary", "pact-secretary"))
    path.write_text(json.dumps({"name": "secretary", "pad": "x" * subagent_meta.META_MAX_BYTES}))
    frame = cloud_subagent_frame(team, "pact-backend-coder")
    assert subagent_meta.subagent_metadata(frame) == {}


def test_meta_at_the_bound_is_read(team):
    path = write_subagent_meta(team, SUBAGENT_ID, {})
    body = json.dumps({"name": "secretary", "pad": ""})
    pad = subagent_meta.META_MAX_BYTES - len(body)
    path.write_text(json.dumps({"name": "secretary", "pad": "x" * pad}))
    assert path.stat().st_size == subagent_meta.META_MAX_BYTES
    frame = cloud_subagent_frame(team, "pact-backend-coder")
    assert subagent_meta.subagent_metadata(frame)["name"] == "secretary"


def test_state_file_read_text_max_bytes(tmp_path):
    path = tmp_path / "f.json"
    path.write_text("abcdef", encoding="utf-8")
    assert state_file.read_text(path, tmp_path, max_bytes=6) == "abcdef"
    with pytest.raises(state_file.TooLarge):
        state_file.read_text(path, tmp_path, max_bytes=5)
    assert state_file.read_text(path, tmp_path) == "abcdef"


def test_turn_end_gate_keeps_its_reexports():
    from shared import turn_end_gate

    assert turn_end_gate.subagent_metadata is subagent_meta.subagent_metadata
    assert turn_end_gate.META_MAX_BYTES == subagent_meta.META_MAX_BYTES
