"""
Location: pact-plugin/tests/test_cloud_roster_adversarial.py
Summary: One pinned test per row of the cloud roster's tamper table
         (docs/architecture/cloud-roster.md §5.2): what a cloud teammate,
         running as the same OS user as every hook, achieves by editing its
         own meta.json or appending to the roster. The two ACCEPTED RESIDUAL
         rows are asserted as CURRENT behaviour, so a future fix flips them
         visibly.
Used by: the pact-plugin test suite.
"""

from __future__ import annotations

import pytest

from fixtures.actor_frames import SUBAGENT_ID, cloud_meta, cloud_subagent_frame, write_subagent_meta
from fixtures.roster_frames import bind, confirm, intent, tool_use_id, write_roster
from shared import cloud_roster, intentional_wait
from shared.actor_identity import (
    SOURCE_ROSTER,
    SOURCE_ROSTER_VERIFIED,
    SOURCE_SUBAGENT_NO_META,
    SOURCE_UNATTRIBUTED,
    resolve_actor,
)

TEAM = "test-team"
TU1, TU2, TU_FORGED = tool_use_id(1), tool_use_id(2), tool_use_id(99)


@pytest.fixture
def cloud(pact_context):
    pact_context(team_name=TEAM, session_id="test-session")
    from shared.paths import get_claude_config_dir

    root = get_claude_config_dir()
    (root / "teams" / TEAM).mkdir(parents=True, exist_ok=True)
    return root


def _actor(root):
    return resolve_actor(cloud_subagent_frame(root, "pact-backend-coder"), TEAM)


def _spawned_coder():
    return [intent(TU1, "coder", "pact-backend-coder"),
            confirm(TU1, SUBAGENT_ID, "pact-backend-coder")]


def test_editing_own_meta_name_to_a_victim_does_not_frame_the_victim(cloud):
    write_roster(cloud, TEAM, _spawned_coder())
    write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("victim", "pact-backend-coder"))
    actor = _actor(cloud)
    assert actor.name is None and actor.source == SOURCE_UNATTRIBUTED
    assert actor.dispute == ("coder", "victim")


def test_deleting_own_meta_keeps_the_roster_name(cloud):
    write_roster(cloud, TEAM, _spawned_coder())
    actor = _actor(cloud)
    assert (actor.name, actor.source) == ("coder", SOURCE_ROSTER)


def test_editing_meta_type_or_tool_use_id_changes_nothing(cloud):
    write_roster(cloud, TEAM, _spawned_coder())
    meta = cloud_meta("coder", "pact-secretary")
    meta["toolUseId"] = TU2
    write_subagent_meta(cloud, SUBAGENT_ID, meta)
    actor = _actor(cloud)
    assert actor.agent_type == "pact-backend-coder"  # the frame's type wins
    assert (actor.name, actor.source) == ("coder", SOURCE_ROSTER_VERIFIED)


def test_forged_duplicate_intent_cannot_rename_a_spawn(cloud):
    write_roster(cloud, TEAM, [*_spawned_coder(),
                               intent(TU1, "victim", "pact-backend-coder")])
    write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("victim", "pact-backend-coder"))
    actor = _actor(cloud)
    assert actor.source == SOURCE_UNATTRIBUTED and actor.dispute == ("coder", "victim")


def test_forged_bind_with_the_wrong_type_is_dropped(cloud):
    write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary"),
                               bind(TU1, SUBAGENT_ID, "pact-backend-coder")])
    assert _actor(cloud).source == SOURCE_SUBAGENT_NO_META


def test_residual_two_file_forge_still_misattributes(cloud):
    """ACCEPTED RESIDUAL (user decision Q1, design §7.1): a forged intent for
    a fresh tool_use_id, a bind to it, and a matching meta.json name make the
    forger appear as `victim`. Same-uid tamper cannot be prevented in-process."""
    write_roster(cloud, TEAM, [intent(TU_FORGED, "victim", "pact-backend-coder"),
                               bind(TU_FORGED, SUBAGENT_ID, "pact-backend-coder")])
    write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("victim", "pact-backend-coder"))
    actor = _actor(cloud)
    assert (actor.name, actor.source) == ("victim", SOURCE_ROSTER_VERIFIED)


def test_residual_forged_intent_makes_a_fresh_owner_exempt_at_task_level(cloud):
    """ACCEPTED RESIDUAL (design §5.2, §7.1): a forged secretary-typed intent
    for a fresh name makes that owner exempt for task-level checks (a claim
    nudge, an ordering warn, the dispatch-variety deny). The same uid can
    already edit the task JSON directly."""
    write_roster(cloud, TEAM, [intent(TU_FORGED, "fresh", "PACT:pact-secretary")])
    assert intentional_wait.is_self_complete_exempt({"owner": "fresh", "metadata": {}}, TEAM)


def test_forged_intent_cannot_exempt_a_name_the_lead_spawned_as_non_exempt(cloud):
    write_roster(cloud, TEAM, [intent(TU1, "coder", "pact-backend-coder"),
                               intent(TU_FORGED, "coder", "pact-secretary")])
    assert not intentional_wait.is_self_complete_exempt({"owner": "coder", "metadata": {}}, TEAM)


def test_forged_intent_never_exempts_a_completion(cloud):
    """The completion-level call skips the roster leg: a forged intent plus a
    forged meta name cannot exempt a non-secretary's completion."""
    write_roster(cloud, TEAM, [intent(TU_FORGED, "fresh", "PACT:pact-secretary")])
    assert not intentional_wait.is_self_complete_exempt(
        {"owner": "fresh", "metadata": {}}, TEAM,
        actor_agent_type="pact-backend-coder", actor_name="fresh")


def test_truncated_roster_degrades_to_meta_only(cloud):
    path = write_roster(cloud, TEAM, _spawned_coder())
    path.write_text(path.read_text()[:40])
    write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("coder", "pact-backend-coder"))
    assert _actor(cloud).source == "subagent_meta"
    assert cloud_roster.load_roster(TEAM).bindings == {}
