"""
Location: pact-plugin/tests/test_cloud_roster_consumers.py
Summary: Tests for every cloud-roster consumer and writer outside the roster
         module itself: dispatch_gate's `intent` write, actor_identity's
         step-4 roster table (R1), the intentional_wait owner-type fallback
         (R2) and its teachback parity, handoff_ordering_gate's suppression,
         the lifecycle gate's disputed-identity advisory end to end, and
         task_claim_gate's cloud tier (WP6). Local behaviour (a team
         config.json present) is pinned unchanged in each.
Used by: the pact-plugin test suite. Design: docs/architecture/cloud-roster.md §9.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import handoff_ordering_gate
import task_claim_gate
import task_lifecycle_gate as tlg
from fixtures.actor_frames import (
    SUBAGENT_ID,
    cloud_meta,
    cloud_subagent_frame,
    write_subagent_meta,
    write_team_config,
)
from fixtures.roster_frames import (
    confirm,
    intent,
    read_roster,
    tool_use_id,
    write_roster,
)
from shared import actor_identity, cloud_roster, intentional_wait
from shared.actor_identity import (
    ROLE_TEAMMATE,
    SOURCE_ROSTER,
    SOURCE_ROSTER_VERIFIED,
    SOURCE_SUBAGENT_META,
    SOURCE_UNATTRIBUTED,
    ActorIdentity,
    resolve_actor,
)

TEAM = "test-team"
TU1, TU2 = tool_use_id(1), tool_use_id(2)
_HANDOFF = {"produced": "x", "decisions": "x", "reasoning_chain": "x",
            "uncertainty": "x", "integration": "x", "open_questions": "x"}


def _config_root() -> Path:
    from shared.paths import get_claude_config_dir
    return get_claude_config_dir()


@pytest.fixture
def cloud(pact_context):
    """A cloud-shaped team (team dir, no config.json) with a live context."""
    pact_context(team_name=TEAM, session_id="test-session")
    root = _config_root()
    (root / "teams" / TEAM).mkdir(parents=True, exist_ok=True)
    assert cloud_roster.applies(TEAM)
    return root


def _bound(root, name, agent_type, aid=SUBAGENT_ID, tu=TU1, extra=()):
    write_roster(root, TEAM, [intent(tu, name, agent_type), confirm(tu, aid, agent_type),
                              *extra])


# =============================================================================
# dispatch_gate: the intent write (WP3)
# =============================================================================


class TestDispatchGateIntent:
    @pytest.fixture
    def gate_env(self, tmp_path, monkeypatch):
        from test_dispatch_gate import _seed_plugin, _setup_session

        plugin_root = tmp_path / "plugin"
        _seed_plugin(plugin_root)
        _setup_session(monkeypatch, tmp_path, plugin_root, team_name="pact-test")
        tasks = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks.mkdir(parents=True, exist_ok=True)
        (tasks / "1.json").write_text(json.dumps(
            {"id": "1", "owner": "tester", "status": "pending"}))
        team_dir = tmp_path / ".claude" / "teams" / "pact-test"
        team_dir.mkdir(parents=True, exist_ok=True)
        return tmp_path / ".claude"

    @staticmethod
    def _frame(**overrides):
        from test_dispatch_gate import _LEAD_AGENT_TYPE, _make_input

        frame = _make_input(agent_type=overrides.pop("agent_type", _LEAD_AGENT_TYPE),
                            **overrides)
        frame["tool_use_id"] = TU1
        return frame

    def _run(self, frame, capsys):
        from test_dispatch_gate import _run_main
        return _run_main(frame, capsys)

    def test_allowed_lead_spawn_writes_one_intent(self, gate_env, capsys):
        code, out = self._run(self._frame(), capsys)
        assert code == 0 and out == {"suppressOutput": True}
        (record,) = read_roster(gate_env, "pact-test")
        assert record["k"] == "intent" and record["tu"] == TU1
        assert record["name"] == "tester" and record["type"] == "pact-architect"

    def test_solo_exempt_spawn_records_its_type_spelling(self, gate_env, capsys):
        self._run(self._frame(subagent_type="general-purpose", name="probe"), capsys)
        (record,) = read_roster(gate_env, "pact-test")
        assert record["type"] == "general-purpose" and record["name"] == "probe"

    def test_denied_spawn_writes_no_intent(self, gate_env, capsys):
        code, _ = self._run(self._frame(name="team-lead"), capsys)
        assert code == 2
        assert read_roster(gate_env, "pact-test") == []

    def test_teammate_frame_writes_no_intent(self, gate_env, capsys):
        self._run(self._frame(subagent_type="general-purpose",
                              agent_type="pact-backend-coder"), capsys)
        assert read_roster(gate_env, "pact-test") == []

    def test_unnamed_spawn_writes_no_intent(self, gate_env, capsys):
        frame = self._frame(subagent_type="general-purpose")
        del frame["tool_input"]["name"]
        self._run(frame, capsys)
        assert read_roster(gate_env, "pact-test") == []

    def test_local_team_writes_no_intent(self, gate_env, capsys):
        (gate_env / "teams" / "pact-test" / "config.json").write_text(
            json.dumps({"members": []}))
        self._run(self._frame(), capsys)
        assert not (gate_env / "teams" / "pact-test" / "pact-roster.jsonl").exists()

    def test_roster_failure_leaves_output_and_journal_identical(
        self, gate_env, capsys, monkeypatch
    ):
        from test_dispatch_gate import _capture_journal

        journal = _capture_journal(monkeypatch)
        good = self._run(self._frame(), capsys)
        good_journal = [dict(e, ts=None) for e in journal]
        journal.clear()

        def _boom(*_a, **_k):
            raise RuntimeError("roster down")

        monkeypatch.setattr(cloud_roster, "append_record", _boom)
        bad = self._run(self._frame(), capsys)
        assert bad == good
        assert [dict(e, ts=None) for e in journal] == good_journal


# =============================================================================
# actor_identity step 4: the roster table (R1, WP4)
# =============================================================================


class TestActorIdentityRoster:
    def test_roster_and_meta_agree_is_verified(self, cloud):
        _bound(cloud, "coder", "pact-backend-coder")
        write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("coder", "pact-backend-coder"))
        actor = resolve_actor(cloud_subagent_frame(cloud, "pact-backend-coder"), TEAM)
        assert actor == ActorIdentity(ROLE_TEAMMATE, "coder", "pact-backend-coder",
                                      SOURCE_ROSTER_VERIFIED)

    def test_meta_missing_uses_roster_name(self, cloud):
        _bound(cloud, "coder", "pact-backend-coder")
        actor = resolve_actor(cloud_subagent_frame(cloud, "pact-backend-coder"), TEAM)
        assert actor == ActorIdentity(ROLE_TEAMMATE, "coder", "pact-backend-coder",
                                      SOURCE_ROSTER)

    def test_meta_with_invalid_name_uses_roster_name(self, cloud):
        _bound(cloud, "coder", "pact-backend-coder")
        write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("x" * 200, "pact-backend-coder"))
        actor = resolve_actor(cloud_subagent_frame(cloud, "pact-backend-coder"), TEAM)
        assert (actor.name, actor.source) == ("coder", SOURCE_ROSTER)

    def test_disagreement_is_unattributed_with_both_claims(self, cloud):
        _bound(cloud, "coder", "pact-backend-coder")
        write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("victim", "pact-backend-coder"))
        actor = resolve_actor(cloud_subagent_frame(cloud, "pact-backend-coder"), TEAM)
        assert actor == ActorIdentity(ROLE_TEAMMATE, None, "pact-backend-coder",
                                      SOURCE_UNATTRIBUTED, dispute=("coder", "victim"))

    def test_no_binding_falls_back_to_meta(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "coder", "pact-backend-coder")])
        write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("coder", "pact-backend-coder"))
        actor = resolve_actor(cloud_subagent_frame(cloud, "pact-backend-coder"), TEAM)
        assert actor.source == SOURCE_SUBAGENT_META

    def test_binding_whose_type_disagrees_with_frame_is_ignored(self, cloud):
        _bound(cloud, "secretary", "pact-secretary")
        write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("coder", "pact-backend-coder"))
        actor = resolve_actor(cloud_subagent_frame(cloud, "pact-backend-coder"), TEAM)
        assert (actor.name, actor.source) == ("coder", SOURCE_SUBAGENT_META)

    def test_roster_never_supplies_the_type(self, cloud):
        _bound(cloud, "coder", "pact-backend-coder")
        actor = resolve_actor(cloud_subagent_frame(cloud, "PACT:pact-backend-coder"), TEAM)
        assert actor.agent_type == "pact-backend-coder"

    def test_local_config_never_opens_the_roster(self, cloud, monkeypatch):
        _bound(cloud, "coder", "pact-backend-coder")
        write_team_config(cloud, TEAM, [])
        calls = []
        monkeypatch.setattr(cloud_roster, "load_roster",
                            lambda *a, **k: calls.append(a) or None)
        write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("coder", "pact-backend-coder"))
        actor = resolve_actor(cloud_subagent_frame(cloud, "pact-backend-coder"), TEAM)
        assert actor.source == SOURCE_SUBAGENT_META and calls == []

    def test_journal_view_carries_dispute_only_when_set(self):
        plain = ActorIdentity(ROLE_TEAMMATE, "c", "t", SOURCE_ROSTER)
        assert set(plain.journal_view()) == {"role", "source", "name", "agent_type"}
        disputed = ActorIdentity(ROLE_TEAMMATE, None, "t", SOURCE_UNATTRIBUTED,
                                 dispute=("a", "b"))
        assert disputed.journal_view()["dispute"] == ["a", "b"]

    def test_typed_without_frame_refuses_a_shadowed_bare_type(self, tmp_path, monkeypatch):
        project = tmp_path / "proj"
        (project / ".claude" / "agents").mkdir(parents=True)
        (project / ".claude" / "agents" / "pact-secretary.md").write_text(
            "---\nname: pact-secretary\n---\n")
        assert actor_identity.typed_without_frame("pact-secretary", str(project)) is None
        assert actor_identity.typed_without_frame(
            "PACT:pact-secretary", str(project)) == "pact-secretary"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        assert actor_identity.typed_without_frame("pact-secretary") is None


# =============================================================================
# intentional_wait: the owner-type fallback (R2, WP5)
# =============================================================================


def _task(owner, **metadata):
    return {"id": "7", "owner": owner, "metadata": metadata}


class TestOwnerTypeFallback:
    def test_secretary_only_name_is_exempt(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary"),
                                   intent(TU2, "secretary", "PACT:pact-secretary")])
        assert intentional_wait.is_self_complete_exempt(_task("secretary"), TEAM)

    def test_intent_alone_suffices_without_binding(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary")])
        assert intentional_wait.is_self_complete_exempt(_task("secretary"), TEAM)

    def test_mixed_types_for_one_name_are_not_exempt(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary"),
                                   intent(TU2, "secretary", "pact-backend-coder")])
        assert not intentional_wait.is_self_complete_exempt(_task("secretary"), TEAM)

    def test_unknown_owner_is_not_exempt(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary")])
        assert not intentional_wait.is_self_complete_exempt(_task("coder"), TEAM)

    def test_shadowed_bare_type_is_not_exempt(self, cloud, tmp_path, monkeypatch):
        project = tmp_path / "proj"
        (project / ".claude" / "agents").mkdir(parents=True)
        (project / ".claude" / "agents" / "pact-secretary.md").write_text(
            "---\nname: pact-secretary\n---\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary")])
        assert not intentional_wait.is_self_complete_exempt(_task("secretary"), TEAM)
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "PACT:pact-secretary")])
        assert intentional_wait.is_self_complete_exempt(_task("secretary"), TEAM)

    def test_config_present_member_missing_stays_not_exempt(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary")])
        write_team_config(cloud, TEAM, [{"name": "coder", "agentType": "pact-backend-coder"}])
        assert not intentional_wait.is_self_complete_exempt(_task("secretary"), TEAM)
        assert not intentional_wait.is_teachback_exempt("secretary", TEAM)

    def test_completion_level_call_skips_the_roster_leg(self, cloud):
        """A completion-level call (actor_name passed, even None) never takes
        the owner's roster type: the actor's name may be its own meta claim."""
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary")])
        task = _task("secretary")
        assert not intentional_wait.is_self_complete_exempt(
            task, TEAM, actor_agent_type="pact-backend-coder", actor_name="secretary")
        assert not intentional_wait.is_self_complete_exempt(
            task, TEAM, actor_agent_type="pact-backend-coder", actor_name=None)
        # Surface 1b (the actor's own harness type) still exempts a real secretary.
        assert intentional_wait.is_self_complete_exempt(
            task, TEAM, actor_agent_type="pact-secretary", actor_name="secretary")

    def test_teachback_parity(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary"),
                                   intent(TU2, "coder", "pact-backend-coder")])
        assert intentional_wait.is_teachback_exempt("secretary", TEAM)
        assert not intentional_wait.is_teachback_exempt("coder", TEAM)

    def test_teachback_parity_unanimity(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "x", "pact-secretary"),
                                   intent(TU2, "x", "pact-auditor")])
        assert not intentional_wait.is_teachback_exempt("x", TEAM)

    def test_no_roster_is_not_exempt(self, cloud):
        assert not intentional_wait.is_self_complete_exempt(_task("secretary"), TEAM)


# =============================================================================
# handoff_ordering_gate: owner exemption via the roster (no call-site change)
# =============================================================================


class TestHandoffOrderingCloud:
    LEAD = "PACT:pact-orchestrator"

    def _seed(self, root, owner):
        tasks = root / "tasks" / TEAM
        tasks.mkdir(parents=True, exist_ok=True)
        (tasks / "42.json").write_text(json.dumps({
            "id": "42", "subject": "secretary: save memory", "owner": owner,
            "status": "completed", "metadata": {}}))

    def _update(self):
        return {"tool_name": "TaskUpdate", "agent_type": self.LEAD,
                "tool_input": {"taskId": "42", "status": "completed"}}

    def test_cloud_secretary_owner_is_suppressed(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary")])
        self._seed(cloud, "secretary")
        assert handoff_ordering_gate._evaluate(self._update()) is None

    def test_cloud_coder_owner_still_warns(self, cloud):
        write_roster(cloud, TEAM, [intent(TU1, "coder", "pact-backend-coder")])
        self._seed(cloud, "coder")
        advisory = handoff_ordering_gate._evaluate(self._update())
        assert advisory is not None and "no metadata.handoff yet" in advisory


# =============================================================================
# task_lifecycle_gate: R1 end to end (WP4 + WP7)
# =============================================================================


@pytest.fixture
def writebacks(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tlg, "_writeback_dispute", lambda task_id: calls.append(task_id) or True)
    return calls


def _completion(root, agent_type, owner, task_id="7"):
    frame = cloud_subagent_frame(root, agent_type, task_id)
    frame["tool_response"] = {"task": {"id": task_id, "subject": "implement foo",
                                       "owner": owner, "metadata": {"handoff": _HANDOFF}}}
    return frame


def _completion_advisories(advisories):
    return [(r, m) for r, m in advisories if r in ("self_completion", "non_lead_completion")]


class TestLifecycleR1:
    def test_meta_name_set_to_victim_is_disputed_not_framed(self, cloud, writebacks):
        _bound(cloud, "coder", "pact-backend-coder")
        write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("victim", "pact-backend-coder"))
        decision: dict = {}
        advisories = tlg.evaluate_lifecycle(
            _completion(cloud, "pact-backend-coder", "victim"), decision=decision)
        ((rule, message),) = _completion_advisories(advisories)
        assert rule == "non_lead_completion"
        assert message.startswith(
            "PACT task_lifecycle_gate: a teammate (identity disputed: roster says "
            "'coder', meta.json says 'victim') completed Task 7, owned by 'victim'.")
        assert decision["actor"]["source"] == SOURCE_UNATTRIBUTED
        assert decision["actor"]["dispute"] == ["coder", "victim"]
        assert decision["actor"]["name"] is None
        assert decision["actor"]["owner_match"] is False
        assert writebacks == ["7"]

    def test_meta_deleted_attributes_by_roster_name(self, cloud, writebacks):
        _bound(cloud, "coder", "pact-backend-coder")
        decision: dict = {}
        advisories = tlg.evaluate_lifecycle(
            _completion(cloud, "pact-backend-coder", "coder"), decision=decision)
        ((rule, message),) = _completion_advisories(advisories)
        assert rule == "self_completion" and "teammate 'coder' self-completed" in message
        assert decision["actor"]["source"] == SOURCE_ROSTER
        assert "dispute" not in decision["actor"]

    def test_non_secretary_with_meta_name_secretary_is_flagged(self, cloud, writebacks):
        """Lead-approved pin: a meta edit never buys an exemption. A coder
        that names itself 'secretary' in meta.json and completes the real
        secretary's task is flagged, even though the roster says owner
        'secretary' is secretary-typed."""
        write_roster(cloud, TEAM, [intent(TU1, "secretary", "pact-secretary")])
        write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("secretary", "pact-backend-coder"))
        advisories = tlg.evaluate_lifecycle(
            _completion(cloud, "pact-backend-coder", "secretary"))
        assert [r for r, _ in _completion_advisories(advisories)] == ["self_completion"]
        assert writebacks == ["7"]

    def test_real_cloud_secretary_stays_exempt(self, cloud, writebacks):
        _bound(cloud, "secretary", "pact-secretary")
        write_subagent_meta(cloud, SUBAGENT_ID, cloud_meta("secretary", "pact-secretary"))
        advisories = tlg.evaluate_lifecycle(_completion(cloud, "pact-secretary", "secretary"))
        assert _completion_advisories(advisories) == []
        assert writebacks == []


# =============================================================================
# task_claim_gate: the cloud tier (WP6)
# =============================================================================


class TestTaskClaimGateCloud:
    @pytest.fixture
    def gate(self, cloud, monkeypatch):
        monkeypatch.setattr(task_claim_gate, "registry_resolve", lambda _sid: None)
        return cloud

    def _seed(self, root, *tasks):
        store = root / "tasks" / TEAM
        store.mkdir(parents=True, exist_ok=True)
        for t in tasks:
            (store / f"{t['id']}.json").write_text(json.dumps(t))

    def _frame(self, root, agent_type="pact-backend-coder", agent_id=SUBAGENT_ID):
        frame = cloud_subagent_frame(root, agent_type, agent_id=agent_id)
        frame.update(hook_event_name="PreToolUse", tool_name="Bash",
                     tool_input={"command": "ls"})
        return frame

    def _pending(self, task_id, owner, **extra):
        return {"id": task_id, "owner": owner, "status": "pending",
                "subject": "implement foo", "blockedBy": [], **extra}

    def test_roster_named_actor_gets_task_specific_nudge(self, gate):
        _bound(gate, "coder", "pact-backend-coder")
        self._seed(gate, self._pending("5", "coder"))
        advisory = task_claim_gate._evaluate(self._frame(gate))
        assert advisory == task_claim_gate._claim_nudge_single("5")
        task = json.loads((gate / "tasks" / TEAM / "5.json").read_text())
        assert task["status"] == "pending"  # never auto-claimed

    def test_roster_named_actor_with_two_tasks_gets_list(self, gate):
        _bound(gate, "coder", "pact-backend-coder")
        self._seed(gate, self._pending("5", "coder"), self._pending("6", "coder"))
        advisory = task_claim_gate._evaluate(self._frame(gate))
        assert advisory == task_claim_gate._claim_nudge_multi(["5", "6"]) or \
            advisory == task_claim_gate._claim_nudge_multi(["6", "5"])

    def test_roster_named_actor_with_nothing_claimable_is_silent(self, gate):
        _bound(gate, "coder", "pact-backend-coder")
        self._seed(gate, self._pending("5", "someone-else"))
        assert task_claim_gate._evaluate(self._frame(gate)) is None

    def test_meta_only_actor_gets_generic_nudge(self, gate):
        write_subagent_meta(gate, SUBAGENT_ID, cloud_meta("coder", "pact-backend-coder"))
        self._seed(gate, self._pending("5", "coder"))
        assert task_claim_gate._evaluate(self._frame(gate)) == \
            task_claim_gate._GENERIC_CLAIM_NUDGE

    def test_disputed_actor_gets_generic_nudge(self, gate):
        _bound(gate, "coder", "pact-backend-coder")
        write_subagent_meta(gate, SUBAGENT_ID, cloud_meta("victim", "pact-backend-coder"))
        self._seed(gate, self._pending("5", "victim"))
        assert task_claim_gate._evaluate(self._frame(gate)) == \
            task_claim_gate._GENERIC_CLAIM_NUDGE

    def test_generic_nudge_needs_a_candidate(self, gate):
        self._seed(gate, {"id": "5", "owner": "coder", "status": "in_progress"})
        assert task_claim_gate._evaluate(self._frame(gate)) is None

    def test_exempt_owner_is_not_a_candidate(self, gate):
        _bound(gate, "secretary", "pact-secretary")
        self._seed(gate, self._pending("5", "secretary"))
        assert task_claim_gate._evaluate(self._frame(gate, "pact-secretary")) is None

    def test_non_subagent_frame_stays_noop(self, gate):
        self._seed(gate, self._pending("5", "coder"))
        frame = self._frame(gate)
        del frame["agent_id"]
        assert task_claim_gate._evaluate(frame) is None

    def test_unreadable_config_stays_noop(self, gate):
        """A config that exists but has no leadSessionId is not the cloud shape."""
        write_team_config(gate, TEAM, [])
        _bound(gate, "coder", "pact-backend-coder")
        self._seed(gate, self._pending("5", "coder"))
        assert task_claim_gate._evaluate(self._frame(gate)) is None
