"""
Location: pact-plugin/tests/test_actor_identity.py
Summary: Unit tests for shared/actor_identity.resolve_actor: one test per
         resolution step (design doc section 3.3), the _meta_type precedence,
         the "a type is never derived from a name" rule, totality, and
         isolation from the session registry.
Used by: the pact-plugin test suite.
"""

from __future__ import annotations

import pytest

from fixtures.actor_frames import (
    SUBAGENT_ID,
    at_id_frame,
    cloud_meta,
    cloud_subagent_frame,
    inprocess_frame,
    lead_frame,
    local_meta,
    plain_frame,
    tmux_frame,
    use_config,
    write_subagent_meta,
    write_team_config,
)
from shared import actor_identity
from shared.actor_identity import (
    ROLE_LEAD,
    ROLE_TEAMMATE,
    ROLE_UNKNOWN,
    SOURCE_AT_ID,
    SOURCE_FRAME_ONLY,
    SOURCE_INPROCESS_ID,
    SOURCE_LEAD_FRAME,
    SOURCE_NONE,
    SOURCE_SUBAGENT_META,
    SOURCE_SUBAGENT_NO_META,
    ActorIdentity,
    resolve_actor,
)

TEAM = "test-team"


@pytest.fixture
def config(tmp_path, monkeypatch):
    return use_config(monkeypatch, tmp_path / "cfg")


# ─── step 0-2: role ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [None, "frame", 3, ["agent_type"]])
def test_non_dict_input_is_unknown(bad):
    assert resolve_actor(bad, TEAM) == ActorIdentity(ROLE_UNKNOWN, None, None, SOURCE_NONE)


def test_lead_frame_is_lead():
    assert resolve_actor(lead_frame(), TEAM) == ActorIdentity(
        ROLE_LEAD, None, None, SOURCE_LEAD_FRAME
    )


def test_plain_frame_is_unknown():
    assert resolve_actor(plain_frame(), TEAM).role == ROLE_UNKNOWN


# ─── step 3 / 3': in-process ─────────────────────────────────────────────────


def test_inprocess_member_resolves_name_and_config_type(config):
    write_team_config(config, TEAM, [{"name": "coder", "agentType": "PACT:pact-backend-coder"}])
    actor = resolve_actor(inprocess_frame(config, "coder"), TEAM)
    assert actor == ActorIdentity(ROLE_TEAMMATE, "coder", "pact-backend-coder", SOURCE_INPROCESS_ID)


def test_inprocess_member_named_like_an_exempt_type_takes_its_config_type(config):
    """A type is never derived from a name: the frame's agent_type is the NAME."""
    write_team_config(config, TEAM, [{"name": "pact-secretary", "agentType": "pact-backend-coder"}])
    actor = resolve_actor(inprocess_frame(config, "pact-secretary"), TEAM)
    assert actor.name == "pact-secretary"
    assert actor.agent_type == "pact-backend-coder"


def test_inprocess_member_without_config_type_falls_back_to_local_meta(config):
    write_team_config(config, TEAM, [{"name": "coder"}])
    write_subagent_meta(
        config, "acoder-0123456789abcdef", local_meta("coder", "pact-backend-coder", TEAM)
    )
    actor = resolve_actor(inprocess_frame(config, "coder"), TEAM)
    assert actor.agent_type == "pact-backend-coder"


def test_inprocess_shape_without_membership_is_frame_only_with_no_type(config):
    write_team_config(config, TEAM, [{"name": "someone-else", "agentType": "pact-auditor"}])
    actor = resolve_actor(inprocess_frame(config, "pact-secretary"), TEAM)
    assert actor == ActorIdentity(ROLE_TEAMMATE, None, None, SOURCE_FRAME_ONLY)


def test_inprocess_shape_with_no_config_is_frame_only(config):
    actor = resolve_actor(inprocess_frame(config, "coder"), TEAM)
    assert actor == ActorIdentity(ROLE_TEAMMATE, None, None, SOURCE_FRAME_ONLY)


# ─── step 4: subagent / cloud ────────────────────────────────────────────────


def test_cloud_subagent_with_meta_resolves_name_and_type(config):
    write_subagent_meta(config, SUBAGENT_ID, cloud_meta("secretary", "pact-secretary"))
    actor = resolve_actor(cloud_subagent_frame(config, "pact-secretary"), TEAM)
    assert actor == ActorIdentity(ROLE_TEAMMATE, "secretary", "pact-secretary", SOURCE_SUBAGENT_META)


def test_cloud_subagent_strips_pact_namespace(config):
    write_subagent_meta(config, SUBAGENT_ID, cloud_meta("secretary", "PACT:pact-secretary"))
    actor = resolve_actor(cloud_subagent_frame(config, "PACT:pact-secretary"), TEAM)
    assert actor.agent_type == "pact-secretary"


def test_cloud_subagent_without_meta_takes_frame_type(config):
    actor = resolve_actor(cloud_subagent_frame(config, "pact-secretary"), TEAM)
    assert actor == ActorIdentity(ROLE_TEAMMATE, None, "pact-secretary", SOURCE_SUBAGENT_NO_META)


def test_cloud_subagent_whose_frame_type_is_its_name_does_not_become_a_type(config):
    """C5 guard: if the frame's agent_type turns out to be the NAME, the
    skip-if-equals-name rule keeps it from being used as a type."""
    write_subagent_meta(config, SUBAGENT_ID, {"name": "pact-secretary"})
    actor = resolve_actor(cloud_subagent_frame(config, "pact-secretary"), TEAM)
    assert actor.name == "pact-secretary"
    assert actor.agent_type is None


@pytest.mark.parametrize("bad_name", ["", "x" * 129, "bad\nname", 42])
def test_cloud_subagent_invalid_meta_name_is_none(config, bad_name):
    write_subagent_meta(config, SUBAGENT_ID, {"name": bad_name, "agentType": "pact-auditor"})
    actor = resolve_actor(cloud_subagent_frame(config, "pact-auditor"), TEAM)
    assert actor.name is None
    assert actor.agent_type == "pact-auditor"


def test_meta_path_that_is_a_directory_degrades_to_no_meta(config):
    path = write_subagent_meta(config, SUBAGENT_ID, {})
    path.unlink()
    path.mkdir()
    actor = resolve_actor(cloud_subagent_frame(config, "pact-auditor"), TEAM)
    assert actor.source == SOURCE_SUBAGENT_NO_META


# ─── _meta_type precedence ───────────────────────────────────────────────────


def test_meta_type_local_shape_skips_agenttype_that_is_the_name():
    meta = {"agentType": "coder", "name": "coder", "spawnedAgentType": "PACT:pact-backend-coder"}
    assert actor_identity._meta_type(meta, name="coder") == "pact-backend-coder"


def test_meta_type_prefers_custom_over_spawned_over_agenttype():
    meta = {"customAgentType": "a-type", "spawnedAgentType": "b-type", "agentType": "c-type"}
    assert actor_identity._meta_type(meta) == "a-type"
    del meta["customAgentType"]
    assert actor_identity._meta_type(meta) == "b-type"
    del meta["spawnedAgentType"]
    assert actor_identity._meta_type(meta) == "c-type"


def test_meta_type_empty_meta_falls_back_to_frame_type():
    assert actor_identity._meta_type({}, frame_type="PACT:pact-auditor") == "pact-auditor"
    assert actor_identity._meta_type({}, frame_type=None) is None


# ─── step 5: name@team ───────────────────────────────────────────────────────


def test_at_id_with_matching_team_resolves(config):
    write_team_config(config, TEAM, [{"name": "coder", "agentType": "pact-backend-coder"}])
    actor = resolve_actor(at_id_frame("coder", "TEST-TEAM"), TEAM)
    assert actor == ActorIdentity(ROLE_TEAMMATE, "coder", "pact-backend-coder", SOURCE_AT_ID)


def test_at_id_with_other_team_falls_through_to_frame_only(config):
    actor = resolve_actor(at_id_frame("coder", "other-team"), TEAM)
    assert actor.source == SOURCE_FRAME_ONLY
    assert actor.name is None


# ─── step 6: frame only ──────────────────────────────────────────────────────


def test_tmux_frame_is_teammate_with_frame_type():
    actor = resolve_actor(tmux_frame("PACT:pact-test-engineer"), TEAM)
    assert actor == ActorIdentity(ROLE_TEAMMATE, None, "pact-test-engineer", SOURCE_FRAME_ONLY)


def test_frame_only_agent_type_that_names_a_member_is_not_a_type(config):
    write_team_config(config, TEAM, [{"name": "pact-secretary", "agentType": "pact-backend-coder"}])
    actor = resolve_actor(tmux_frame("pact-secretary"), TEAM)
    assert actor.agent_type is None


# ─── totality and journal view ───────────────────────────────────────────────


@pytest.mark.parametrize("fields", [
    {"agent_type": "x", "agent_id": 5},
    {"agent_type": ["x"]},
    {"agent_type": "x", "agent_id": SUBAGENT_ID, "transcript_path": 7, "session_id": None},
])
def test_odd_field_types_never_raise(fields):
    actor = resolve_actor(fields, TEAM)
    assert isinstance(actor, ActorIdentity)


def test_internal_error_returns_unknown(monkeypatch):
    def boom(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(actor_identity.pact_context, "classify_session_role", boom)
    assert resolve_actor(lead_frame(), TEAM).role == ROLE_UNKNOWN


def test_journal_view_has_four_fields_and_no_ids():
    view = ActorIdentity(ROLE_TEAMMATE, "n", "t", SOURCE_FRAME_ONLY).journal_view()
    assert view == {"role": ROLE_TEAMMATE, "source": SOURCE_FRAME_ONLY, "name": "n", "agent_type": "t"}


# ─── registry isolation ──────────────────────────────────────────────────────


def test_resolution_never_reaches_the_registry(config, monkeypatch):
    from shared import background_work, pact_context, session_registry

    def forbidden(*_a, **_k):
        raise AssertionError("registry-reaching helper called")

    monkeypatch.setattr(background_work, "frame_team_and_name", forbidden)
    monkeypatch.setattr(pact_context, "resolve_agent_name", forbidden, raising=False)
    # session_registry's resolver is `resolve`; it must exist, so this patch
    # fails loudly if the registry API is renamed rather than going vacuous.
    monkeypatch.setattr(session_registry, "resolve", forbidden)
    # Make the totality wrapper visible: a swallowed AssertionError would
    # otherwise read as ROLE_UNKNOWN.
    write_team_config(config, TEAM, [{"name": "coder", "agentType": "pact-backend-coder"}])
    write_subagent_meta(config, SUBAGENT_ID, cloud_meta("sec", "pact-secretary"))
    assert resolve_actor(inprocess_frame(config, "coder"), TEAM).source == SOURCE_INPROCESS_ID
    assert resolve_actor(cloud_subagent_frame(config, "pact-secretary"), TEAM).source == SOURCE_SUBAGENT_META
    assert resolve_actor(tmux_frame(), TEAM).source == SOURCE_FRAME_ONLY
    assert resolve_actor(at_id_frame("coder", TEAM), TEAM).source == SOURCE_AT_ID
    assert resolve_actor(lead_frame(), TEAM).source == SOURCE_LEAD_FRAME
