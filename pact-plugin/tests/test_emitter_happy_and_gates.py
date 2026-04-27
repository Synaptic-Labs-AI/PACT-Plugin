"""
Emit-or-suppress gate tests for agent_handoff_emitter.py.

Covers the happy path, the disk-status fallback gate (#528 regression
guard), the production-shape Option-E handoff-presence gate, and the
non-agent / signal-task bypasses. These are the four classes of
"does this fire emit at all?" — the positive baseline plus three
negative gates.
"""
import pytest

from conftest import VALID_HANDOFF, _run_main


class TestHappyPath:
    def test_writes_agent_handoff_event_on_valid_completion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        exit_code = _run_main(
            stdin_payload={
                "task_id": "5",
                "task_subject": "backend-coder task #5",
                "teammate_name": "backend-coder-538",
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                "owner": "backend-coder-538",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert len(calls) == 1
        event = calls[0]
        assert event["type"] == "agent_handoff"
        assert event["agent"] == "backend-coder-538"
        assert event["task_id"] == "5"
        assert event["handoff"] == VALID_HANDOFF

    def test_owner_takes_precedence_over_stdin_teammate_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        _run_main(
            stdin_payload={
                "task_id": "6",
                "task_subject": "handed off from team-lead",
                "teammate_name": "platform-placeholder",
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                "owner": "secretary",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert calls[0]["agent"] == "secretary"


class TestStatusFallbackGate:
    """Fallback-path regression guard. Covers the disk-status gate that
    fires ONLY when stdin lacks `hook_event_name` (forward-compat path).
    The production-shape path (with hook_event_name=TaskCompleted) is
    covered by TestProductionShapeMetadataOnly.

    Origin: #528 regression guard — TaskCompleted fires on ANY TaskUpdate,
    not just status transitions to completed. The on-disk status read
    MUST gate emission or metadata-only TaskUpdates will journal phantom
    events. Renamed to TestStatusFallbackGate post-Option-B (PR #563)
    because the disk-status check is now the FALLBACK, not the primary
    transition signal.
    """

    def test_metadata_only_taskupdate_in_progress_no_event_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        exit_code = _run_main(
            stdin_payload={
                "task_id": "5",
                "task_subject": "metadata-only update — briefing delivered",
                "teammate_name": "backend-coder-538",
                "team_name": "pact-test",
            },
            task_data={
                "status": "in_progress",
                "owner": "backend-coder-538",
                "metadata": {"briefing_delivered": True},
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert calls == [], (
            "TaskCompleted fired on an in_progress metadata-only TaskUpdate; "
            "emitter must NOT journal an event. This is the #528 regression shape."
        )

    def test_pending_status_no_event_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        _run_main(
            stdin_payload={
                "task_id": "5",
                "task_subject": "pending",
                "teammate_name": "backend-coder-538",
                "team_name": "pact-test",
            },
            task_data={
                "status": "pending",
                "owner": "backend-coder-538",
                "metadata": {},
            },
            append_calls=calls,
        )
        assert calls == []

    def test_missing_status_no_event_written(self, tmp_path, monkeypatch):
        """Absence of `status` key is treated as "not completed" — fail-closed
        rather than emit a phantom event. Corrupt task JSON or stale file
        landing on disk should not fall through to the journal write."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        _run_main(
            stdin_payload={
                "task_id": "5",
                "task_subject": "task file lacks status field",
                "teammate_name": "backend-coder-538",
                "team_name": "pact-test",
            },
            task_data={
                "owner": "backend-coder-538",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert calls == []

class TestProductionShapeMetadataOnly:
    """Production-shape coverage post-Option-B. Stdin carries
    `hook_event_name="TaskCompleted"` (the platform's actual signal),
    NOT the bare-payload shape that TestStatusFallbackGate exercises.

    Two property bundles:
    1. **Option E gate** — when `metadata.handoff` is missing/empty,
       suppress emission AND skip marker creation regardless of
       on-disk status. Covers the B1 failure mode: early metadata-only
       fires under platform-revert MUST NOT consume the marker.
    2. **S7 best-effort delta** — under Option B, status values that
       previously suppressed (deleted, pending) now emit when
       hook_event_name is TaskCompleted AND handoff is present.
       Architect-accepted as best-effort preservation; pinned here so
       the behavior delta is a deliberate test contract.
    """

    @pytest.mark.parametrize(
        "disk_status",
        ["in_progress", "completed", "pending", "deleted"],
    )
    def test_no_handoff_suppresses_under_production_stdin(
        self, disk_status, tmp_path, monkeypatch
    ):
        """Under production-shape stdin (hook_event_name=TaskCompleted)
        with NO handoff in metadata, Option E gate suppresses regardless
        of on-disk status. Pins the B1 fix property across all 4
        observable status values."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        _run_main(
            stdin_payload={
                "session_id": "test-session-1",
                "hook_event_name": "TaskCompleted",
                "task_id": f"no-handoff-{disk_status}",
                "task_subject": f"production-shape probe: status={disk_status}",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": disk_status,
                "owner": "probe-agent",
                "metadata": {"briefing_delivered": True},  # NO handoff
            },
            append_calls=calls,
        )
        assert calls == [], (
            f"production-shape stdin + status={disk_status!r} + no handoff "
            f"should suppress. Option E handoff-presence gate is the B1 "
            f"defense; if any status value emits without handoff, the "
            f"genuine completion's marker is at risk."
        )
        marker = (
            tmp_path / ".claude" / "teams" / "pact-test"
            / ".agent_handoff_emitted" / f"no-handoff-{disk_status}"
        )
        assert not marker.exists(), (
            f"marker created with status={disk_status!r} despite no "
            f"handoff — B1 root cause; the genuine completion would be "
            f"silently dropped."
        )

    def test_status_deleted_with_handoff_emits(
        self, tmp_path, monkeypatch
    ):
        """Behavior delta from Option B adoption: status=deleted +
        hook_event_name=TaskCompleted + handoff present now emits
        ONE event. Pre-Option-B the disk-status gate would have
        suppressed (status != completed). Architect-accepted as
        best-effort preservation; pinned so a future status-strict
        regression is caught.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        _run_main(
            stdin_payload={
                "session_id": "test-session-1",
                "hook_event_name": "TaskCompleted",
                "task_id": "deleted-with-handoff",
                "task_subject": "deleted-status emit pin",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": "deleted",
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert len(calls) == 1, (
            "S7 behavior delta: status=deleted + hook_event_name + "
            "handoff present must emit under Option B. If this fails, "
            "a status-strict regression was introduced — Option B "
            "intentionally accepts non-completed statuses as valid "
            "transition signals when hook_event_name asserts."
        )
        assert calls[0]["handoff"] == VALID_HANDOFF

    def test_status_pending_with_handoff_emits(
        self, tmp_path, monkeypatch
    ):
        """Symmetric pin to the deleted-status case. Pre-Option-B,
        status=pending was suppressed; post-Option-B + handoff present
        + hook_event_name=TaskCompleted emits ONE event."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        _run_main(
            stdin_payload={
                "session_id": "test-session-1",
                "hook_event_name": "TaskCompleted",
                "task_id": "pending-with-handoff",
                "task_subject": "pending-status emit pin",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": "pending",
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert len(calls) == 1, (
            "S7 behavior delta: status=pending + hook_event_name + "
            "handoff present must emit under Option B."
        )
        assert calls[0]["handoff"] == VALID_HANDOFF

class TestBypasses:
    def test_non_agent_task_no_event_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        exit_code = _run_main(
            stdin_payload={
                "task_id": "99",
                "task_subject": "Feature: ship it",
                "team_name": "pact-test",
            },
            task_data={"status": "completed", "metadata": {}},
            append_calls=calls,
        )
        assert exit_code == 0
        assert calls == []

    def test_blocker_signal_task_no_event_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        exit_code = _run_main(
            stdin_payload={
                "task_id": "blk-1",
                "task_subject": "BLOCKER: schema migration reverts",
                "teammate_name": "database-engineer",
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                "owner": "database-engineer",
                "metadata": {"type": "blocker"},
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert calls == [], "blocker signal tasks must not emit agent_handoff events"

    def test_algedonic_signal_task_no_event_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        exit_code = _run_main(
            stdin_payload={
                "task_id": "algo-1",
                "task_subject": "HALT: SECURITY",
                "teammate_name": "security-engineer",
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                "owner": "security-engineer",
                "metadata": {"type": "algedonic"},
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert calls == []

