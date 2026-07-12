"""
Location: pact-plugin/tests/test_snapshot_seam_emitter.py
Summary: Seam tests for the task_metadata_snapshot emission point inside
         agent_handoff_emitter.py — the teammate-frame twin positioned
         after the transition gate and before the signal-task bypass.
         Covers: snapshot emission for no-handoff sibling-bearing tasks
         (the class unreachable after the emitter's hardened exits),
         signal-task INCLUSION with the unconditional pinned leg
         (agent_handoff suppression for signal tasks unchanged),
         stdin-primary discipline (no disk status requirement of the
         snapshot's own), writability DEFER-not-poison, gate inheritance
         (owner + transition gates sit above the seam), and content-key
         dedup across re-fires.
Used by: pytest (CODE-phase verification; matrix depth is TEST phase work).
"""

import pytest

import shared.task_metadata_snapshot as tms
from fixtures.emitter import VALID_HANDOFF, _run_main

SIBLINGS = {
    "teachback_submit": {"understanding": "u"},
    "variety": {"total": 9},
}


@pytest.fixture
def snapshot_events(monkeypatch):
    """Spy on the SUBSTRATE's bindings: capture snapshot appends and make
    the substrate's writability precondition pass (the emitter fixture only
    patches the emitter module's own get_journal_path symbol)."""
    events: list[dict] = []

    def _spy(event):
        events.append(event)
        return True

    monkeypatch.setattr(tms, "append_event", _spy)
    monkeypatch.setattr(
        tms, "get_journal_path", lambda: "/tmp/x/session-journal.jsonl"
    )
    return events


def _stdin(task_id="5", subject="backend-coder: build thing",
           teammate="backend-coder", **extra):
    payload = {
        "task_id": task_id,
        "task_subject": subject,
        "teammate_name": teammate,
        "team_name": "pact-test",
    }
    payload.update(extra)
    return payload


class TestSeamCEmits:
    def test_completion_with_siblings_snapshots_and_handoff_emits(
        self, tmp_path, monkeypatch, snapshot_events
    ):
        """Happy path: both event families emit — the snapshot mirrors the
        siblings, the agent_handoff carries the handoff, and the two use
        independent markers."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        exit_code = _run_main(
            stdin_payload=_stdin(),
            task_data={
                "status": "completed",
                "owner": "backend-coder",
                "metadata": {**SIBLINGS, "handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert [e["type"] for e in calls] == ["agent_handoff"]
        assert len(snapshot_events) == 1
        event = snapshot_events[0]
        assert event["type"] == "task_metadata_snapshot"
        assert event["task_id"] == "5"
        assert event["owner"] == "backend-coder"
        assert event["metadata"] == SIBLINGS
        assert "handoff" not in event["metadata"]

    def test_no_handoff_siblings_still_snapshot(
        self, tmp_path, monkeypatch, snapshot_events
    ):
        """The seam-position payoff: a self-completed task with sibling
        metadata but NO handoff (e.g. a rejected-teachback gate task) is
        unreachable after the handoff-presence exit — the snapshot still
        emits from the pre-gate position while agent_handoff correctly
        stays silent."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        exit_code = _run_main(
            stdin_payload=_stdin(task_id="6"),
            task_data={
                "status": "completed",
                "owner": "backend-coder",
                "metadata": {
                    "teachback_submit": {"understanding": "u"},
                    "teachback_rejection": {"reason": "missing field"},
                },
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert calls == [], "no handoff → no agent_handoff event"
        assert len(snapshot_events) == 1
        assert set(snapshot_events[0]["metadata"]) == {
            "teachback_submit",
            "teachback_rejection",
        }

    def test_signal_task_snapshots_handoff_suppression_pinned(
        self, tmp_path, monkeypatch, snapshot_events
    ):
        """Signal-task INCLUSION with the unconditional leg: a blocker task
        snapshots (task_type mirrored) while the emitter's signal-task
        agent_handoff suppression stays exactly as pinned."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        exit_code = _run_main(
            stdin_payload=_stdin(task_id="7", subject="HALT: security"),
            task_data={
                "status": "completed",
                "owner": "security-engineer",
                "metadata": {
                    "type": "blocker",
                    "halt_context": {"reason": "injection vector"},
                    "handoff": VALID_HANDOFF,
                },
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert calls == [], "signal task must not emit agent_handoff (pinned)"
        assert len(snapshot_events) == 1
        assert snapshot_events[0]["task_type"] == "blocker"
        assert snapshot_events[0]["metadata"]["type"] == "blocker"

    def test_stdin_primary_no_own_disk_status_requirement(
        self, tmp_path, monkeypatch, snapshot_events
    ):
        """hook_event_name == TaskCompleted passes the transition gate even
        when the platform's disk write is mid-flight (disk status still
        in_progress) — the snapshot inherits the gate by position and must
        not add a disk status check of its own."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        exit_code = _run_main(
            stdin_payload=_stdin(
                task_id="8", hook_event_name="TaskCompleted"
            ),
            task_data={
                "status": "in_progress",  # mid-flight persist
                "owner": "backend-coder",
                "metadata": dict(SIBLINGS),
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert len(snapshot_events) == 1

    def test_refire_same_content_dedups(
        self, tmp_path, monkeypatch, snapshot_events
    ):
        """The platform Stop-sweep re-dispatches TaskCompleted; identical
        content dedups to one event via the content-keyed marker."""
        monkeypatch.setenv("HOME", str(tmp_path))
        for _ in range(3):
            _run_main(
                stdin_payload=_stdin(task_id="9"),
                task_data={
                    "status": "completed",
                    "owner": "backend-coder",
                    "metadata": dict(SIBLINGS),
                },
                append_calls=[],
            )
        assert len(snapshot_events) == 1


class TestSeamCGateInheritance:
    def test_transition_gate_exit_precedes_seam(
        self, tmp_path, monkeypatch, snapshot_events
    ):
        """Neither hook_event_name nor disk status completed → the
        transition gate exits BEFORE the seam: no snapshot."""
        monkeypatch.setenv("HOME", str(tmp_path))
        exit_code = _run_main(
            stdin_payload=_stdin(task_id="10"),
            task_data={
                "status": "in_progress",
                "owner": "backend-coder",
                "metadata": dict(SIBLINGS),
            },
            append_calls=[],
        )
        assert exit_code == 0
        assert snapshot_events == []

    def test_owner_gate_exit_precedes_seam(
        self, tmp_path, monkeypatch, snapshot_events
    ):
        """No owner and no teammate_name → the owner gate exits BEFORE the
        seam (ownerless coverage belongs to the lead-frame seams)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        exit_code = _run_main(
            stdin_payload=_stdin(task_id="11", teammate=""),
            task_data={
                "status": "completed",
                "owner": "",
                "metadata": dict(SIBLINGS),
            },
            append_calls=[],
        )
        assert exit_code == 0
        assert snapshot_events == []


class TestSeamCWritabilityDefer:
    def test_unwritable_frame_defers_not_poisons(
        self, tmp_path, monkeypatch
    ):
        """DEFER-not-poison: an unresolvable teammate frame (substrate
        writability precondition fails) neither emits NOR claims the
        marker; a later writable fire for the SAME content emits — proving
        no poisoned marker was left behind."""
        monkeypatch.setenv("HOME", str(tmp_path))
        events: list[dict] = []

        def _spy(event):
            events.append(event)
            return True

        monkeypatch.setattr(tms, "append_event", _spy)
        task_data = {
            "status": "completed",
            "owner": "backend-coder",
            "metadata": dict(SIBLINGS),
        }
        # Unwritable frame: substrate's own journal-path resolution empty.
        monkeypatch.setattr(tms, "get_journal_path", lambda: "")
        _run_main(
            stdin_payload=_stdin(task_id="12"),
            task_data=task_data,
            append_calls=[],
        )
        assert events == [], "unwritable frame must not emit"
        # Writable re-fire (e.g. the lead-frame seam or a later b1 fire).
        monkeypatch.setattr(
            tms, "get_journal_path", lambda: "/tmp/x/journal.jsonl"
        )
        _run_main(
            stdin_payload=_stdin(task_id="12"),
            task_data=task_data,
            append_calls=[],
        )
        assert len(events) == 1, (
            "the deferred fire must re-emit — an unwritable frame that "
            "claimed the marker would have poisoned this"
        )

    def test_failed_write_unclaims_for_retry(
        self, tmp_path, monkeypatch
    ):
        """Compensating unclaim: a claim whose append fails is rolled back
        so a later healthy fire re-emits."""
        monkeypatch.setenv("HOME", str(tmp_path))
        events: list[dict] = []
        monkeypatch.setattr(
            tms, "get_journal_path", lambda: "/tmp/x/journal.jsonl"
        )
        task_data = {
            "status": "completed",
            "owner": "backend-coder",
            "metadata": dict(SIBLINGS),
        }
        monkeypatch.setattr(tms, "append_event", lambda event: False)
        _run_main(
            stdin_payload=_stdin(task_id="13"),
            task_data=task_data,
            append_calls=[],
        )

        def _spy(event):
            events.append(event)
            return True

        monkeypatch.setattr(tms, "append_event", _spy)
        _run_main(
            stdin_payload=_stdin(task_id="13"),
            task_data=task_data,
            append_calls=[],
        )
        assert len(events) == 1, "rolled-back claim must allow the re-emit"
