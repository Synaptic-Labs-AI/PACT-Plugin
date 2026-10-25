"""
Location: pact-plugin/tests/test_cloud_roster_hardening.py
Summary: Regression tests for the security-rev #70 findings on the cloud
         roster and the shadow check: the symlink provenance bypass, the bind
         steal by an orphan intent, the torn-line merge, an unloadable cloud
         roster, and BOM-prefixed frontmatter.
Used by: the pact-plugin test suite.
"""

from __future__ import annotations

import json
import os

import pytest

import roster_writer
from fixtures.actor_frames import (
    SUBAGENT_ID,
    cloud_meta,
    cloud_subagent_frame,
    write_subagent_meta,
    write_team_config,
)
from fixtures.roster_frames import (
    agent_id,
    bind,
    confirm,
    intent,
    read_roster,
    roster_path,
    subagent_start_frame,
    tool_use_id,
    write_roster,
)
from shared import actor_identity, cloud_roster
from shared.actor_identity import (
    SOURCE_ROSTER,
    SOURCE_ROSTER_VERIFIED,
    SOURCE_SUBAGENT_META,
    SOURCE_SUBAGENT_NO_META,
    SOURCE_UNATTRIBUTED,
    resolve_actor,
)

TEAM = "test-team"
TU1, TU2 = tool_use_id(1), tool_use_id(2)
_DEFINITION = "---\nname: pact-secretary\n---\nbody\n"


@pytest.fixture
def cloud(pact_context):
    pact_context(team_name=TEAM, session_id="test-session")
    from shared.paths import get_claude_config_dir

    root = get_claude_config_dir()
    (root / "teams" / TEAM).mkdir(parents=True, exist_ok=True)
    return root


def _coder(root, aid=SUBAGENT_ID):
    return resolve_actor(cloud_subagent_frame(root, "pact-backend-coder", agent_id=aid), TEAM)


# ─── 1. symlink provenance bypass ────────────────────────────────────────────


def _fake_plugin(base, name="PACT"):
    agents = base / "agents"
    agents.mkdir(parents=True)
    (agents / "pact-secretary.md").write_text(_DEFINITION)
    (base / ".claude-plugin").mkdir()
    (base / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name}))
    return agents / "pact-secretary.md"


def test_project_symlink_to_a_fake_pact_plugin_is_a_shadow(tmp_path):
    """The #70 probe shape: a cloned repo commits a symlinked definition and a
    vendored manifest that names PACT. It is the repo's own agent."""
    project = tmp_path / "proj"
    target = _fake_plugin(project / "vendor" / "x")
    (project / ".claude" / "agents").mkdir(parents=True)
    os.symlink(os.path.relpath(target, project / ".claude" / "agents"),
               project / ".claude" / "agents" / "pact-secretary.md")
    assert actor_identity.typed_without_frame("pact-secretary", str(project)) is None


def test_symlink_into_the_config_plugin_cache_is_plugin_owned(tmp_path):
    from shared.paths import get_claude_config_dir

    target = _fake_plugin(get_claude_config_dir() / "plugins" / "cache" / "p" / "PACT" / "1")
    user_agents = get_claude_config_dir() / "agents"
    user_agents.mkdir(parents=True)
    os.symlink(target, user_agents / "pact-secretary.md")
    assert actor_identity.typed_without_frame("pact-secretary") == "pact-secretary"


def test_symlink_into_the_config_plugin_cache_needs_a_pact_manifest(tmp_path):
    from shared.paths import get_claude_config_dir

    target = _fake_plugin(get_claude_config_dir() / "plugins" / "cache" / "o" / "Other" / "1",
                          name="Other")
    user_agents = get_claude_config_dir() / "agents"
    user_agents.mkdir(parents=True)
    os.symlink(target, user_agents / "pact-secretary.md")
    assert actor_identity.typed_without_frame("pact-secretary") is None


def test_symlink_into_this_plugins_agents_dir_is_plugin_owned(tmp_path):
    from shared import subagent_meta
    from shared.paths import get_claude_config_dir

    user_agents = get_claude_config_dir() / "agents"
    user_agents.mkdir(parents=True)
    os.symlink(subagent_meta.plugin_agents_dir() / "pact-secretary.md",
               user_agents / "pact-secretary.md")
    assert actor_identity.typed_without_frame("pact-secretary") == "pact-secretary"


# ─── 5. BOM frontmatter ──────────────────────────────────────────────────────


def test_bom_prefixed_frontmatter_is_still_a_shadow(tmp_path):
    project = tmp_path / "proj"
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "agents" / "renamed.md").write_text(
        "\ufeff" + _DEFINITION, encoding="utf-8")
    assert actor_identity.typed_without_frame("pact-secretary", str(project)) is None


# ─── 2. bind steal by an orphan intent ───────────────────────────────────────


def test_orphan_intent_plus_nested_start_does_not_name_the_nested_agent(cloud):
    """#70 probe: the lead's Agent call was denied after dispatch_gate wrote
    the intent; a nested same-type start takes the type join. Its bind is
    never confirmed, so the roster name alone must not name it."""
    nested = agent_id(7)
    write_roster(cloud, TEAM, [intent(TU1, "researcher", "general-purpose")])
    assert roster_writer.handle_subagent_start(
        subagent_start_frame(cloud, nested, "general-purpose"))
    assert [r["aid"] for r in read_roster(cloud, TEAM) if r["k"] == "bind"] == [nested]
    actor = resolve_actor(cloud_subagent_frame(cloud, "general-purpose", agent_id=nested), TEAM)
    assert actor.name is None and actor.source == SOURCE_SUBAGENT_NO_META


def test_bind_only_with_disagreeing_meta_is_unattributed(cloud):
    write_roster(cloud, TEAM, [intent(TU1, "researcher", "pact-backend-coder"),
                               bind(TU1, SUBAGENT_ID, "pact-backend-coder")])
    write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("nested", "pact-backend-coder"))
    actor = _coder(cloud)
    assert actor.name is None and actor.source == SOURCE_UNATTRIBUTED
    assert actor.dispute == ("researcher", "nested")


def test_bind_only_with_agreeing_meta_is_verified(cloud):
    write_roster(cloud, TEAM, [intent(TU1, "coder", "pact-backend-coder"),
                               bind(TU1, SUBAGENT_ID, "pact-backend-coder")])
    write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("coder", "pact-backend-coder"))
    actor = _coder(cloud)
    assert (actor.name, actor.source) == ("coder", SOURCE_ROSTER_VERIFIED)


def test_confirmed_binding_with_meta_missing_still_names(cloud):
    write_roster(cloud, TEAM, [intent(TU1, "coder", "pact-backend-coder"),
                               confirm(TU1, SUBAGENT_ID, "pact-backend-coder")])
    actor = _coder(cloud)
    assert (actor.name, actor.source) == ("coder", SOURCE_ROSTER)


# ─── 3. torn line ────────────────────────────────────────────────────────────


def test_append_after_a_torn_line_keeps_the_new_record(cloud):
    path = write_roster(cloud, TEAM, [intent(TU1, "a", "t")])
    path.write_text(path.read_text() + '{"v":1,"k":"int')  # torn tail, no newline
    assert cloud_roster.append_record(TEAM, {"k": "intent", "tu": TU2, "name": "b",
                                             "type": "t"})
    roster = cloud_roster.load_roster(TEAM)
    assert set(roster.intents) == {TU1, TU2}


def test_append_to_a_clean_file_adds_no_blank_line(cloud):
    write_roster(cloud, TEAM, [intent(TU1, "a", "t")])
    cloud_roster.append_record(TEAM, {"k": "intent", "tu": TU2, "name": "b", "type": "t"})
    assert "\n\n" not in roster_path(cloud, TEAM).read_text()


# ─── 4. unloadable cloud roster ──────────────────────────────────────────────


def _meta_victim(root):
    write_subagent_meta(root, SUBAGENT_ID, cloud_meta("victim", "pact-backend-coder"))


def test_oversized_roster_resolves_unattributed(cloud, monkeypatch):
    write_roster(cloud, TEAM, [intent(TU1, "coder", "pact-backend-coder")])
    monkeypatch.setattr(cloud_roster, "MAX_ROSTER_BYTES", 10)
    _meta_victim(cloud)
    actor = _coder(cloud)
    assert actor.name is None and actor.source == SOURCE_UNATTRIBUTED
    assert actor.dispute is None


def test_symlinked_roster_resolves_unattributed(cloud, tmp_path):
    target = tmp_path / "elsewhere.jsonl"
    target.write_text(json.dumps(intent(TU1, "coder", "pact-backend-coder")) + "\n")
    os.symlink(target, roster_path(cloud, TEAM))
    _meta_victim(cloud)
    assert _coder(cloud).source == SOURCE_UNATTRIBUTED


def test_roster_with_no_valid_record_resolves_unattributed(cloud):
    roster_path(cloud, TEAM).write_text("x" * 50 + "\n{broken\n")
    _meta_victim(cloud)
    assert _coder(cloud).source == SOURCE_UNATTRIBUTED


def test_missing_roster_stays_meta_only(cloud):
    _meta_victim(cloud)
    actor = _coder(cloud)
    assert (actor.name, actor.source) == ("victim", SOURCE_SUBAGENT_META)


def test_empty_roster_file_stays_meta_only(cloud):
    roster_path(cloud, TEAM).write_text("")
    _meta_victim(cloud)
    assert _coder(cloud).source == SOURCE_SUBAGENT_META


def test_local_mode_ignores_an_unloadable_roster(cloud):
    roster_path(cloud, TEAM).write_text("garbage\n")
    write_team_config(cloud, TEAM, [])
    _meta_victim(cloud)
    assert _coder(cloud).source == SOURCE_SUBAGENT_META


def test_unloadable_roster_advisory_names_no_one(cloud, monkeypatch):
    import task_lifecycle_gate as tlg

    monkeypatch.setattr(tlg, "_writeback_dispute", lambda _t: True)
    roster_path(cloud, TEAM).write_text("garbage\n")
    _meta_victim(cloud)
    frame = cloud_subagent_frame(cloud, "pact-backend-coder")
    frame["tool_response"] = {"task": {"id": "7", "subject": "s", "owner": "victim",
                                       "metadata": {}}}
    messages = [m for r, m in tlg.evaluate_lifecycle(frame) if r == "non_lead_completion"]
    assert len(messages) == 1
    assert "a teammate (name unresolved: unattributed)" in messages[0]


def test_deeply_nested_line_cannot_disable_the_roster(cloud):
    """#96 probe: a line that makes json.loads raise RecursionError is skipped
    on its own; it must not turn the whole roster into 'no roster' and so
    restore a forged meta name."""
    write_roster(cloud, TEAM, [intent(TU1, "coder", "pact-backend-coder"),
                               confirm(TU1, SUBAGENT_ID, "pact-backend-coder")],
                 raw_lines=["[" * 200000])
    _meta_victim(cloud)
    actor = _coder(cloud)
    assert actor.name != "victim"
    assert actor.source == SOURCE_UNATTRIBUTED and actor.dispute == ("coder", "victim")


def test_unexpected_parse_failure_reads_as_unloadable(cloud, monkeypatch):
    write_roster(cloud, TEAM, [intent(TU1, "coder", "pact-backend-coder")])

    def _boom(_text):
        raise MemoryError("parse blew up")

    monkeypatch.setattr(cloud_roster, "_parse_lines", _boom)
    assert cloud_roster.load_roster_state(TEAM) == (None, True)
    _meta_victim(cloud)
    assert _coder(cloud).source == SOURCE_UNATTRIBUTED
