"""
Location: pact-plugin/tests/test_cloud_roster_meta_claim.py
Summary: Regression tests for test-roster's live residual (task #90): a nested
         agent took an orphan intent's name by binding to it by type and
         editing its own meta.json. Covers the `foreign` spawn record
         (dispatch_gate), the latest-spawn-event type join (roster_writer),
         the rule that a roster name is claimable only through a roster
         binding (actor_identity step 4), and the lead-overlap swap.
Used by: the pact-plugin test suite.

The event order replays the REAL frames of the live run
(scratchpad roster-live/out3/frames.jsonl, CC 2.1.310): lead Pre(ghost,
Explore) [then denied by another hook], Pre(alpha) Start(alpha), Pre(beta)
Start(beta), then alpha's nested Pre(Agent, Explore) carrying alpha's
agent_id, immediately followed by the nested Start(Explore).
"""

from __future__ import annotations

import json

import pytest

import roster_writer
import task_lifecycle_gate as tlg
from fixtures.actor_frames import (
    cloud_meta,
    cloud_subagent_frame,
    write_subagent_meta,
)
from fixtures.roster_frames import (
    agent_id,
    confirm,
    intent,
    read_roster,
    subagent_start_frame,
    tool_use_id,
    write_roster,
)
from shared import cloud_roster
from shared.actor_identity import (
    SOURCE_ROSTER_VERIFIED,
    SOURCE_SUBAGENT_META,
    SOURCE_UNATTRIBUTED,
    resolve_actor,
)

TEAM = "test-team"
TU_GHOST, TU_ALPHA, TU_BETA, TU_NESTED = (tool_use_id(n) for n in (1, 2, 3, 4))
TU_GHOST2 = tool_use_id(5)
ALPHA, BETA, NESTED, GHOST = (agent_id(n) for n in (0xA1, 0xB2, 0xC3, 0xD4))


def foreign(tu, spawner, agent_type):
    return {"v": 1, "k": "foreign", "tu": tu, "aid": spawner, "type": agent_type,
            "ts": cloud_roster.now_timestamp()}


@pytest.fixture
def cloud(pact_context):
    pact_context(team_name=TEAM, session_id="test-session")
    from shared.paths import get_claude_config_dir

    root = get_claude_config_dir()
    (root / "teams" / TEAM).mkdir(parents=True, exist_ok=True)
    return root


def _start(root, aid, agent_type):
    return roster_writer.handle_subagent_start(subagent_start_frame(root, aid, agent_type))


def _binds(root):
    return {r["aid"]: r["tu"] for r in read_roster(root, TEAM) if r["k"] == "bind"}


def _actor(root, aid, agent_type="Explore"):
    return resolve_actor(cloud_subagent_frame(root, agent_type, agent_id=aid), TEAM)


def _live_sequence(root):
    """The live run up to the nested start, written as the hooks write it."""
    write_roster(root, TEAM, [intent(TU_GHOST, "ghost", "Explore")])  # then denied
    cloud_roster.append_record(TEAM, {"k": "intent", "tu": TU_ALPHA, "name": "alpha",
                                      "type": "general-purpose"})
    assert _start(root, ALPHA, "general-purpose")
    cloud_roster.append_record(TEAM, {"k": "intent", "tu": TU_BETA, "name": "beta",
                                      "type": "general-purpose"})
    assert _start(root, BETA, "general-purpose")
    cloud_roster.append_record(TEAM, {"k": "foreign", "tu": TU_NESTED, "aid": ALPHA,
                                      "type": "Explore"})


# ─── the live residual ───────────────────────────────────────────────────────


def test_nested_start_after_a_foreign_spawn_does_not_bind(cloud):
    _live_sequence(cloud)
    assert not _start(cloud, NESTED, "Explore")
    assert NESTED not in _binds(cloud)
    assert _binds(cloud) == {ALPHA: TU_ALPHA, BETA: TU_BETA}


def test_impostor_meta_claiming_the_orphan_name_is_unattributed(cloud):
    _live_sequence(cloud)
    _start(cloud, NESTED, "Explore")
    write_subagent_meta(cloud, NESTED, cloud_meta("ghost", "Explore"))
    actor = _actor(cloud, NESTED)
    assert actor.name is None and actor.source == SOURCE_UNATTRIBUTED


def test_after_ghost_respawn_real_ghost_verified_and_impostor_not(cloud):
    _live_sequence(cloud)
    _start(cloud, NESTED, "Explore")
    write_subagent_meta(cloud, NESTED, cloud_meta("ghost", "Explore"))
    cloud_roster.append_record(TEAM, {"k": "intent", "tu": TU_GHOST2, "name": "ghost",
                                      "type": "Explore"})
    assert _start(cloud, GHOST, "Explore")
    assert _binds(cloud)[GHOST] == TU_GHOST2
    write_subagent_meta(cloud, GHOST, cloud_meta("ghost", "Explore"))
    real = _actor(cloud, GHOST)
    assert (real.name, real.source) == ("ghost", SOURCE_ROSTER_VERIFIED)
    impostor = _actor(cloud, NESTED)
    assert impostor.name is None and impostor.source == SOURCE_UNATTRIBUTED


def _complete(root, aid, owner, monkeypatch):
    monkeypatch.setattr(tlg, "_writeback_dispute", lambda _t: True)
    frame = cloud_subagent_frame(root, "Explore", agent_id=aid)
    frame["tool_response"] = {"task": {"id": "9", "subject": "explore x", "owner": owner,
                                       "metadata": {}}}
    decision: dict = {}
    rules = [r for r, _ in tlg.evaluate_lifecycle(frame, decision=decision)
             if r in ("self_completion", "non_lead_completion")]
    return rules, decision["actor"]


def test_impostor_completing_a_ghost_task_is_flagged_not_self_completion(cloud, monkeypatch):
    _live_sequence(cloud)
    _start(cloud, NESTED, "Explore")
    write_subagent_meta(cloud, NESTED, cloud_meta("ghost", "Explore"))
    rules, actor = _complete(cloud, NESTED, "ghost", monkeypatch)
    assert rules == ["non_lead_completion"]
    assert actor["source"] == SOURCE_UNATTRIBUTED and actor["owner_match"] is False


def test_real_ghost_completing_its_own_task_is_self_completion(cloud, monkeypatch):
    _live_sequence(cloud)
    cloud_roster.append_record(TEAM, {"k": "intent", "tu": TU_GHOST2, "name": "ghost",
                                      "type": "Explore"})
    _start(cloud, GHOST, "Explore")
    write_subagent_meta(cloud, GHOST, cloud_meta("ghost", "Explore"))
    rules, actor = _complete(cloud, GHOST, "ghost", monkeypatch)
    assert rules == ["self_completion"]
    assert actor["source"] == SOURCE_ROSTER_VERIFIED


# ─── the type join: latest same-type spawn event ─────────────────────────────


def test_older_orphan_no_longer_blocks_a_later_same_type_spawn(cloud):
    write_roster(cloud, TEAM, [intent(TU_GHOST, "ghost", "general-purpose"),
                               intent(TU_ALPHA, "alpha", "general-purpose")])
    assert _start(cloud, ALPHA, "general-purpose")
    assert _binds(cloud) == {ALPHA: TU_ALPHA}


def test_interleaved_lead_and_foreign_spawns_bind_nothing(cloud):
    """Pre(A), Pre(F), Start(A), Start(F): neither start can tell, so none binds."""
    write_roster(cloud, TEAM, [intent(TU_ALPHA, "alpha", "Explore"),
                               foreign(TU_NESTED, BETA, "Explore")])
    assert not _start(cloud, ALPHA, "Explore")
    assert not _start(cloud, NESTED, "Explore")
    assert _binds(cloud) == {}


def test_foreign_spawn_of_another_type_does_not_block(cloud):
    write_roster(cloud, TEAM, [intent(TU_ALPHA, "alpha", "general-purpose"),
                               foreign(TU_NESTED, BETA, "Explore")])
    assert _start(cloud, ALPHA, "general-purpose")


def test_latest_intent_already_attached_binds_nothing(cloud):
    write_roster(cloud, TEAM, [intent(TU_ALPHA, "alpha", "Explore"),
                               confirm(TU_ALPHA, ALPHA)])
    assert not _start(cloud, NESTED, "Explore")


def test_lead_overlap_swap_is_temporary_and_corrected_by_the_confirms(cloud):
    """Pre(B), Pre(C), Start(B), Start(C) for two same-type LEAD spawns: B
    binds to C's intent (the latest). Neither agent is ever named after the
    other: B's meta name disagrees (unattributed + dispute), C is unbound
    with a roster name (unattributed). The lead's confirms at handback put
    both right."""
    write_roster(cloud, TEAM, [intent(TU_ALPHA, "alpha", "Explore"),
                               intent(TU_BETA, "beta", "Explore")])
    assert _start(cloud, ALPHA, "Explore")
    assert _binds(cloud) == {ALPHA: TU_BETA}  # the swap
    assert not _start(cloud, BETA, "Explore")
    write_subagent_meta(cloud, ALPHA, cloud_meta("alpha", "Explore"))
    write_subagent_meta(cloud, BETA, cloud_meta("beta", "Explore"))
    a, b = _actor(cloud, ALPHA), _actor(cloud, BETA)
    assert (a.name, a.source, a.dispute) == (None, SOURCE_UNATTRIBUTED, ("beta", "alpha"))
    assert (b.name, b.source) == (None, SOURCE_UNATTRIBUTED)

    cloud_roster.append_record(TEAM, {"k": "confirm", "tu": TU_BETA, "aid": BETA,
                                      "type": "Explore"})
    cloud_roster.append_record(TEAM, {"k": "confirm", "tu": TU_ALPHA, "aid": ALPHA,
                                      "type": "Explore"})
    a, b = _actor(cloud, ALPHA), _actor(cloud, BETA)
    assert (a.name, a.source) == ("alpha", SOURCE_ROSTER_VERIFIED)
    assert (b.name, b.source) == ("beta", SOURCE_ROSTER_VERIFIED)


# ─── a roster name is claimable only through a binding ──────────────────────


def test_unbound_meta_name_held_by_another_agent_is_unattributed(cloud):
    write_roster(cloud, TEAM, [intent(TU_ALPHA, "alpha", "Explore"),
                               confirm(TU_ALPHA, ALPHA)])
    write_subagent_meta(cloud, NESTED, cloud_meta("alpha", "Explore"))
    assert _actor(cloud, NESTED).source == SOURCE_UNATTRIBUTED


def test_unbound_meta_name_not_in_the_roster_stays_meta_only(cloud):
    write_roster(cloud, TEAM, [intent(TU_ALPHA, "alpha", "Explore")])
    write_subagent_meta(cloud, NESTED, cloud_meta("scratch-explorer", "Explore"))
    actor = _actor(cloud, NESTED)
    assert (actor.name, actor.source) == ("scratch-explorer", SOURCE_SUBAGENT_META)


def test_no_roster_file_keeps_meta_only(cloud):
    write_subagent_meta(cloud, NESTED, cloud_meta("ghost", "Explore"))
    assert _actor(cloud, NESTED).source == SOURCE_SUBAGENT_META


# ─── the foreign record: validation and fold ────────────────────────────────


@pytest.mark.parametrize("record", [
    {"k": "foreign", "tu": "bad", "aid": ALPHA, "type": "Explore"},
    {"k": "foreign", "tu": TU_NESTED, "aid": "not-an-id", "type": "Explore"},
    {"k": "foreign", "tu": TU_NESTED, "type": "Explore"},
    {"k": "foreign", "tu": TU_NESTED, "aid": ALPHA, "type": "bad type"},
    {"k": "foreign", "tu": TU_NESTED, "aid": ALPHA},
    {"k": "foreign", "tu": TU_NESTED, "aid": ALPHA, "type": "Explore", "pad": "x" * 1100},
])
def test_invalid_foreign_records_are_refused(cloud, record):
    assert not cloud_roster.append_record(TEAM, record)


def test_foreign_record_never_attaches_to_an_intent(cloud):
    """A forged foreign record reusing an intent's tool_use_id binds nothing."""
    write_roster(cloud, TEAM, [intent(TU_ALPHA, "alpha", "Explore"),
                               foreign(TU_ALPHA, NESTED, "Explore")])
    roster = cloud_roster.load_roster(TEAM)
    assert roster.binding_for(NESTED) is None
    assert [i.name for i in roster.unattached_intents()] == ["alpha"]


# ─── dispatch_gate writes the foreign record ────────────────────────────────


class TestDispatchGateForeign:
    @pytest.fixture
    def gate_env(self, tmp_path, monkeypatch):
        from test_dispatch_gate import _seed_plugin, _setup_session

        plugin_root = tmp_path / "plugin"
        _seed_plugin(plugin_root)
        _setup_session(monkeypatch, tmp_path, plugin_root, team_name="pact-test")
        (tmp_path / ".claude" / "teams" / "pact-test").mkdir(parents=True, exist_ok=True)
        return tmp_path / ".claude"

    @staticmethod
    def _frame(agent_type, aid=None):
        """Shape of the live nested Pre(Agent): a teammate frame with agent_id."""
        frame = {
            "hook_event_name": "PreToolUse", "tool_name": "Agent",
            "session_id": "test-session", "tool_use_id": TU_NESTED,
            "agent_type": agent_type, "cwd": "/tmp", "permission_mode": "default",
            "tool_input": {"description": "nested", "prompt": "look around",
                           "subagent_type": "Explore"},
        }
        if aid is not None:
            frame["agent_id"] = aid
        return frame

    def _run(self, frame, capsys):
        from test_dispatch_gate import _run_main
        return _run_main(frame, capsys)

    def test_nested_spawn_writes_a_foreign_record(self, gate_env, capsys):
        code, out = self._run(self._frame("general-purpose", ALPHA), capsys)
        assert code == 0 and out == {"suppressOutput": True}
        (record,) = read_roster(gate_env, "pact-test")
        assert record["k"] == "foreign" and record["aid"] == ALPHA
        assert record["type"] == "Explore" and record["tu"] == TU_NESTED

    def test_teammate_frame_without_agent_id_writes_nothing(self, gate_env, capsys):
        self._run(self._frame("general-purpose"), capsys)
        assert read_roster(gate_env, "pact-test") == []

    def test_local_team_writes_no_foreign_record(self, gate_env, capsys):
        (gate_env / "teams" / "pact-test" / "config.json").write_text(
            json.dumps({"members": []}))
        self._run(self._frame("general-purpose", ALPHA), capsys)
        assert not (gate_env / "teams" / "pact-test" / "pact-roster.jsonl").exists()
