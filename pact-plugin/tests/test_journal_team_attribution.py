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
from pathlib import Path
from unittest.mock import patch

import pytest

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
    def _emit(self, team_name, subject="collect the arms"):
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
                team_name, "12", "coder-a", subject,
                {"handoff": VALID_HANDOFF},
            )
        return calls

    def test_non_str_subject_is_sentinel_not_a_crash(self):
        """STRICT SENTINEL: the old local `subject.strip()` RAISED on a
        non-str, and this function's fail-open handler turned that into a
        silent no-emit. The shared helper substitutes the sentinel instead."""
        calls = self._emit("session-abc", subject=42)
        assert len(calls) == 1
        assert calls[0]["task_subject"] == "(no subject)"

    @pytest.mark.parametrize("subject", ["", "   "])
    def test_whitespace_subject_emits_the_sentinel(self, subject):
        """M-6 (b2 site): a whitespace subject must reach the event as the
        sentinel, or the emit hashes the raw whitespace while the harvest
        hashes the sentinel — identity mismatch, union skipped."""
        calls = self._emit("session-abc", subject=subject)
        assert len(calls) == 1
        assert calls[0]["task_subject"] == "(no subject)"

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
    def _emit(self, team_name, subject="collect the arms"):
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
                team_name, "12", subject, "coder-a",
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

    @pytest.mark.parametrize("subject", ["", "   "])
    def test_whitespace_subject_emits_the_sentinel(self, subject):
        """M-6 (snapshot site): the writer CALLS the shared helper — its own
        branch is gone — so this arm reddens when the HELPER loses `.strip()`,
        which was measured across all three sites. Without the strip the
        snapshot hashes raw whitespace while the harvest hashes the sentinel:
        identity mismatch, union skipped."""
        calls = self._emit("session-abc", subject=subject)
        assert len(calls) == 1
        assert calls[0]["subject"] == "(no subject)"

    def test_non_str_subject_emits_the_sentinel(self):
        """Pre-existing gap closed: b1 and b2 each had a non-str arm and the
        snapshot site did not. Wired to the strict helper, a non-str must
        become the sentinel rather than reaching the journal schema."""
        calls = self._emit("session-abc", subject=42)
        assert len(calls) == 1
        assert calls[0]["subject"] == "(no subject)"


class TestEmitTeamUncertaintyGuard:
    """The emit-side two-signal guard: b1 suppresses ONLY when the payload
    team NAMES AN EXISTING STORE (the live-claim gate) and DIFFERS from the
    resolved store, **and** the resolved store's record subject differs from
    the payload subject. One signal alone must still emit — an inert legacy
    stdin value, an absent payload store and a subject re-scope are all
    legitimate states, and widening the guard to any one of them loses real
    events."""

    OLD_TASK = {
        "status": "completed",
        "owner": "coder-a",
        "subject": "OLD subject",
        "metadata": {"handoff": VALID_HANDOFF},
    }

    def _run(self, tmp_path, monkeypatch, capsys, payload_team, payload_subject,
             context_team, create_payload_store=False,
             record_subject="OLD subject"):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        if create_payload_store:
            # The live-claim gate requires the payload's store to EXIST. The
            # harness materialises only the context team's store, so the
            # both-stores fixture creates this one explicitly.
            (tmp_path / ".claude" / "tasks" / payload_team).mkdir(
                parents=True, exist_ok=True
            )
        calls: list[dict] = []
        payload = {
            **EMITTER_STDIN,
            "team_name": payload_team,
            "task_subject": payload_subject,
        }
        task = dict(self.OLD_TASK)
        if record_subject is None:
            task.pop("subject", None)
        else:
            task["subject"] = record_subject
        code = _run_main(
            stdin_payload=payload,
            task_data=task,
            append_calls=calls,
            context_team_name=context_team,
        )
        return code, calls, capsys.readouterr().err

    @pytest.mark.parametrize("subject", ["", "   "])
    def test_whitespace_subject_emits_the_sentinel(
        self, tmp_path, monkeypatch, capsys, subject
    ):
        """M-6 (b1 site): the shared helper is STRICT, so a whitespace payload
        subject reaches the event as the sentinel. Dropping `.strip()` would
        hash the raw whitespace while the harvest hashes the sentinel —
        identity mismatch, union skipped."""
        code, calls, _ = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-abc",
            payload_subject=subject,
            context_team="session-abc",
        )
        assert code == 0
        assert len(calls) == 1
        assert calls[0]["task_subject"] == "(no subject)"

    def test_non_str_subject_is_sentinel_not_event_loss(
        self, tmp_path, monkeypatch, capsys
    ):
        """STRICT SENTINEL: a non-str payload subject used to pass the local
        falsy/whitespace test, then FAIL the journal schema after the marker
        was claimed — the event was LOST. The shared helper turns it into the
        sentinel before the claim, so the event emits."""
        code, calls, _ = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-abc",
            payload_subject=42,
            context_team="session-abc",
        )
        assert code == 0
        assert len(calls) == 1
        assert calls[0]["task_subject"] == "(no subject)"

    def test_non_tty_short_name_with_absent_store_emits(
        self, tmp_path, monkeypatch, capsys
    ):
        """THE RED SEAM CASE, as a unit arm. On the non-TTY substrate stdin
        carries the inert SHORT_NAME while the resolver lands on the
        FULL_UUID store; the short name's store does not exist, so signal 1
        is not an independent claim. MUST EMIT. Removing the live-claim gate
        suppresses here (the old behaviour)."""
        uuid_team = "9f8e7d6c-5b4a-3f2e-1d0c-9b8a7f6e5d4c"
        code, calls, err = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-1a2b3c4d",
            payload_subject="LIVE subject",
            context_team=uuid_team,
        )
        assert code == 0
        assert len(calls) == 1
        assert calls[0]["team"] == uuid_team
        assert "suppressing mixed handoff" not in err

    def test_both_stores_exist_suppresses_and_reports(
        self, tmp_path, monkeypatch, capsys
    ):
        """THE SUPPRESSION ARM. Both stores exist, the resolver returns the
        OLD store, the payload names the LIVE store, and the record subject
        differs. Removing the guard (or the live-claim gate) emits an event
        carrying the OLD store's handoff, so both assertions fail."""
        code, calls, err = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live",
            payload_subject="LIVE subject",
            context_team="session-old",
            create_payload_store=True,
        )
        assert code == 0
        assert calls == []
        assert (
            "resolved store 'session-old' is not the payload team "
            "'session-live'" in err
        )
        assert "suppressing mixed handoff for task 12" in err

    def test_payload_team_cannot_forge_a_report_line(
        self, tmp_path, monkeypatch, capsys
    ):
        """A payload team carrying a newline cannot name an EXISTING store —
        the path validator rejects it — so signal 1 never fires, no report is
        produced at all, and the value reaches no report by any route.
        report_safe itself is covered by TestReportSafe."""
        code, calls, err = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live\nFORGED: approved",
            payload_subject="LIVE subject",
            context_team="session-old",
            create_payload_store=True,
        )
        # The live-claim gate resolves the payload name through the path
        # validator, which REJECTS a line boundary, so this name cannot satisfy
        # signal 1: no report is produced at all and the event emits. That is
        # stronger than a sanitised report \u2014 the injection never reaches one.
        # report_safe itself is exercised by TestReportSafe and the merge
        # field-name arm, where the value DOES reach a report.
        assert code == 0
        assert len(calls) == 1
        assert "FORGED" not in err

    @pytest.mark.parametrize("sep", ["\x85", "\u2028"])
    def test_payload_team_cannot_forge_a_unicode_line(
        self, tmp_path, monkeypatch, capsys, sep
    ):
        """Same property for the Unicode boundaries: the path validator
        rejects them, so the payload can never name a live store and never
        reaches a report. report_safe covers the values that do."""
        code, calls, err = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live" + sep + "FORGED: approved",
            payload_subject="LIVE subject",
            context_team="session-old",
            create_payload_store=True,
        )
        assert code == 0
        assert len(calls) == 1
        assert "FORGED" not in err

    def test_both_stores_exist_with_agreeing_subjects_emits(
        self, tmp_path, monkeypatch, capsys
    ):
        """CONTROL: widening the guard to ANY subject mismatch must redden
        this. Both stores exist and the teams disagree, but the subjects
        agree, so the read is this task's and the emitter must emit."""
        code, calls, _ = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live",
            payload_subject="OLD subject",
            context_team="session-old",
            create_payload_store=True,
        )
        assert code == 0
        assert len(calls) == 1
        assert calls[0]["team"] == "session-old"

    def test_absent_store_with_agreeing_subjects_emits(
        self, tmp_path, monkeypatch, capsys
    ):
        """CONTROL: the live-claim gate alone keeps this emitting even when
        the payload store is absent — signal 1 cannot fire."""
        code, calls, _ = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live",
            payload_subject="OLD subject",
            context_team="session-old",
        )
        assert code == 0
        assert len(calls) == 1
        assert calls[0]["team"] == "session-old"

    def test_payload_team_differing_only_by_whitespace_emits(
        self, tmp_path, monkeypatch, capsys
    ):
        """F3a: task_dir_for strips surrounding whitespace before joining, so
        this payload names the SAME store as the resolver. Raw-string
        inequality suppressed here; store identity must not."""
        code, calls, _ = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live ",
            payload_subject="LIVE subject",
            context_team="session-live",
        )
        assert code == 0
        assert len(calls) == 1

    @pytest.mark.parametrize("payload_subject", [None, "", "   "])
    def test_missing_payload_subject_does_not_manufacture_signal_two(
        self, tmp_path, monkeypatch, capsys, payload_subject
    ):
        """F3b (payload side): a payload with no subject normalises to the
        sentinel on BOTH sides and must not count as a disagreement — it would
        suppress the very event the sentinel exists to preserve."""
        code, calls, _ = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live",
            payload_subject=payload_subject,
            context_team="session-old",
            create_payload_store=True,
        )
        assert code == 0
        assert len(calls) == 1

    def test_absent_record_subject_does_not_manufacture_signal_two(
        self, tmp_path, monkeypatch, capsys
    ):
        """F3b (record side, the symmetric case): an absent RECORD subject
        normalises to the sentinel too, so it cannot manufacture a
        disagreement with a genuine payload subject."""
        code, calls, _ = self._run(
            tmp_path, monkeypatch, capsys,
            payload_team="session-live",
            payload_subject="LIVE subject",
            context_team="session-old",
            create_payload_store=True,
            record_subject=None,
        )
        assert code == 0
        assert len(calls) == 1

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


HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"


class TestEmitPathsUseTheSharedSentinel:
    """ONE pin per emit site: the substitution must be IMPORTED AND CALLED,
    with no local definition. Without these a future un-wiring has no
    detector — which is how four copies of the rule shipped."""

    def test_task_completed_emit_path_calls_the_shared_helper(self):
        """PIN THE CALL EXPRESSION, not the name: the guard also calls the
        helper, so a bare-name assertion stayed green when the SUBSTITUTION
        was un-wired back to the inline form. The absent literal is what the
        un-wiring reintroduces."""
        src = (HOOKS_DIR / "agent_handoff_emitter.py").read_text(encoding="utf-8")
        assert "task_subject = emit_side_subject(raw_task_subject)" in src
        assert "def emit_side_subject" not in src
        assert '"(no subject)"' not in src

    def test_lead_side_emit_path_calls_the_shared_helper(self):
        src = (HOOKS_DIR / "task_lifecycle_gate.py").read_text(encoding="utf-8")
        assert "subject = emit_side_subject(subject)" in src
        assert "def emit_side_subject" not in src
        assert '"(no subject)"' not in src

    def test_snapshot_writer_calls_the_shared_helper(self):
        src = (
            HOOKS_DIR / "shared" / "task_metadata_snapshot.py"
        ).read_text(encoding="utf-8")
        assert "subject = emit_side_subject(subject)" in src
        assert "def emit_side_subject" not in src
        assert '"(no subject)"' not in src
        # The local branch is GONE, not merely unused.
        assert "if not isinstance(subject, str) or not subject.strip():" not in src
