"""
P11 — the source-team attribution on journal events.

Every writer that names a task store on a journal event must record WHICH
store it read: `agent_handoff` at both emit paths (b1
agent_handoff_emitter.main and b2 the lead-side gate) and
`task_metadata_snapshot`. The key is `team` — never `team_name`, which is a
caller-supplied stdin field — and it is OMITTED when the resolver fails
closed, because a registered optional `str` rejects a present empty value
and the rejected append would lose the event, not just its attribution.

The omit-when-empty arms are the load-bearing ones: an unconditional write
passes every "resolved name present" assertion and then silently drops
events in production when the resolver returns "".
"""
from unittest.mock import patch

from fixtures.emitter import VALID_HANDOFF, _run_main

from shared.session_journal import _validate_event_schema

EMITTER_STDIN = {
    "session_id": "",
    "transcript_path": "",
    "cwd": "/test/project",
    "hook_event_name": "TaskCompleted",
    "task_id": "12",
    "task_subject": "collect the arms",
    "task_description": "",
    "teammate_name": "coder-a",
    "team_name": "session-abc",
}

TASK_DATA = {
    "status": "completed",
    "owner": "coder-a",
    "metadata": {"handoff": VALID_HANDOFF},
}


def _run_emitter(tmp_path, monkeypatch, context_team_name="session-abc"):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    calls: list[dict] = []
    code = _run_main(
        stdin_payload=EMITTER_STDIN,
        task_data=TASK_DATA,
        append_calls=calls,
        context_team_name=context_team_name,
    )
    assert code == 0
    return calls


class TestEmitterTeamField:
    def test_resolved_store_name_is_recorded(self, tmp_path, monkeypatch):
        calls = _run_emitter(tmp_path, monkeypatch)
        assert len(calls) == 1
        assert calls[0]["team"] == "session-abc"

    def test_stdin_team_name_is_still_not_forwarded(self, tmp_path, monkeypatch):
        """The value comes from the RESOLVER, not stdin. TestStdinShapePin
        asserts the key `team_name` is absent; this pins that the attribute
        did not arrive by forwarding it."""
        calls = _run_emitter(tmp_path, monkeypatch)
        assert "team_name" not in calls[0]

    def test_key_is_absent_when_the_resolver_fails_closed(
        self, tmp_path, monkeypatch
    ):
        """DISCRIMINATING: an unconditional write emits team="" here, which
        the journal's optional-str contract rejects in production and the
        event is LOST. The event must still emit, without the key."""
        calls = _run_emitter(tmp_path, monkeypatch, context_team_name="")
        assert len(calls) == 1
        assert "team" not in calls[0]


class TestLeadSideGateTeamField:
    def _emit(self, team_name):
        calls: list[dict] = []
        with patch(
            "task_lifecycle_gate.get_journal_path", return_value="/tmp/j.jsonl"
        ), patch(
            "task_lifecycle_gate.handoff_already_emitted", return_value=False
        ), patch(
            "task_lifecycle_gate.append_event",
            side_effect=lambda event: (calls.append(event) or True),
        ):
            from task_lifecycle_gate import _emit_lead_side_agent_handoff

            _emit_lead_side_agent_handoff(
                team_name, "12", "coder-a", "collect the arms",
                {"handoff": VALID_HANDOFF},
            )
        return calls

    def test_resolved_store_name_is_recorded(self):
        calls = self._emit("session-abc")
        assert len(calls) == 1
        assert calls[0]["team"] == "session-abc"

    def test_key_is_absent_when_the_resolver_fails_closed(self):
        calls = self._emit("")
        assert len(calls) == 1
        assert "team" not in calls[0]

    def test_key_shape_matches_the_task_completed_writer(self):
        """Both writers must produce the same attribution key, or a harvest
        partition would miss one of them."""
        event = self._emit("session-abc")[0]
        assert {"agent", "task_id", "task_subject", "handoff", "team"} <= set(event)


class TestSnapshotTeamField:
    def _emit(self, team_name):
        from shared.task_metadata_snapshot import emit_task_metadata_snapshot

        calls: list[dict] = []
        with patch(
            "shared.task_metadata_snapshot.get_journal_path",
            return_value="/tmp/j.jsonl",
        ), patch(
            "shared.task_metadata_snapshot.snapshot_already_emitted",
            return_value=False,
        ), patch(
            "shared.task_metadata_snapshot.append_event",
            side_effect=lambda event: (calls.append(event) or True),
        ):
            emit_task_metadata_snapshot(
                team_name, "12", "collect the arms", "coder-a",
                {"audit_summary": "a finding"},
            )
        return calls

    def test_resolved_store_name_is_recorded(self):
        calls = self._emit("session-abc")
        assert len(calls) == 1
        assert calls[0]["team"] == "session-abc"

    def test_key_is_absent_when_the_resolver_fails_closed(self):
        calls = self._emit("")
        assert len(calls) == 1
        assert "team" not in calls[0]


class TestEmitTeamUncertaintyGuard:
    """The emit-side two-signal guard: b1 suppresses ONLY when the payload
    team and the resolved store DIFFER **and** the resolved store's record
    subject differs from the payload subject. One signal alone must still
    emit — a stale stdin value and a subject re-scope are both legitimate
    states, and widening the guard to either one loses real events."""

    OLD_TASK = {
        "status": "completed",
        "owner": "coder-a",
        "subject": "OLD subject",
        "metadata": {"handoff": VALID_HANDOFF},
    }

    def _run(self, tmp_path, monkeypatch, capsys, payload_team, payload_subject,
             context_team):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        calls: list[dict] = []
        payload = {
            **EMITTER_STDIN,
            "team_name": payload_team,
            "task_subject": payload_subject,
        }
        code = _run_main(
            stdin_payload=payload,
            task_data=dict(self.OLD_TASK),
            append_calls=calls,
            context_team_name=context_team,
        )
        return code, calls, capsys.readouterr().err

    def test_both_signals_suppress_and_report(self, tmp_path, monkeypatch, capsys):
        """THE ARM. The live store does not exist, so the resolver returns the
        old store; the payload names the live team with the live subject and
        the old record carries a different subject plus a handoff. Removing
        the guard emits exactly one event carrying the OLD store's handoff, so
        both the zero-events assertion and the report assertion fail."""
        code, calls, err = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live",
            payload_subject="LIVE subject",
            context_team="session-old",
        )
        assert code == 0
        assert calls == []
        assert (
            "resolved store 'session-old' is not the payload team "
            "'session-live'" in err
        )
        assert "suppressing mixed handoff for task 12" in err

    def test_team_disagreement_with_agreeing_subjects_emits(
        self, tmp_path, monkeypatch, capsys
    ):
        """CONTROL: a stale stdin team value with a healthy resolver must NOT
        suppress. The subjects agreeing is the proof that the read is this
        task's."""
        code, calls, _ = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live",
            payload_subject="OLD subject",
            context_team="session-old",
        )
        assert code == 0
        assert len(calls) == 1
        assert calls[0]["team"] == "session-old"

    def test_agreeing_teams_with_differing_subjects_emits(
        self, tmp_path, monkeypatch, capsys
    ):
        """CONTROL: a subject re-scope race. The teams agree, so the content is
        this team's; the emitter must still emit."""
        code, calls, _ = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-old",
            payload_subject="LIVE subject",
            context_team="session-old",
        )
        assert code == 0
        assert len(calls) == 1


class TestTeamFieldSchemaContract:
    HANDOFF_BASE = {
        "v": 1,
        "type": "agent_handoff",
        "agent": "coder-a",
        "task_id": "12",
        "task_subject": "collect the arms",
        "handoff": {"produced": "x"},
    }
    SNAPSHOT_BASE = {
        "v": 1,
        "type": "task_metadata_snapshot",
        "task_id": "12",
        "metadata": {"audit_summary": "a finding"},
    }

    def test_non_empty_team_is_accepted_for_both_families(self):
        for base in (self.HANDOFF_BASE, self.SNAPSHOT_BASE):
            assert _validate_event_schema({**base, "team": "session-abc"}) == (
                True,
                "ok",
            )

    def test_absent_team_is_accepted_for_both_families(self):
        for base in (self.HANDOFF_BASE, self.SNAPSHOT_BASE):
            assert _validate_event_schema(dict(base)) == (True, "ok")

    def test_empty_team_is_rejected_for_both_families(self):
        """This is WHY both writers omit the key. A present empty value is a
        schema rejection, and the rejected append would lose the event."""
        for base in (self.HANDOFF_BASE, self.SNAPSHOT_BASE):
            ok, reason = _validate_event_schema({**base, "team": ""})
            assert ok is False
            assert "team" in reason

    def test_whitespace_team_is_rejected_too(self):
        for base in (self.HANDOFF_BASE, self.SNAPSHOT_BASE):
            ok, reason = _validate_event_schema({**base, "team": "   "})
            assert ok is False
            assert "team" in reason

    def test_wrong_type_team_is_rejected_for_both_families(self):
        for base in (self.HANDOFF_BASE, self.SNAPSHOT_BASE):
            ok, reason = _validate_event_schema({**base, "team": 42})
            assert ok is False
            assert "team" in reason
