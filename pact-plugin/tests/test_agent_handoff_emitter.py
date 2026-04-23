"""
Smoke tests for agent_handoff_emitter.py — #538 TaskCompleted journal writer.

Covers the happy path, disk-status gate (#528 regression guard),
signal-task bypass, non-agent bypass, and the sidecar O_EXCL idempotency
guard. Comprehensive coverage (malformed stdin, fallback-field
substitution, marker-OSError fail-open) lands in the TEST phase; this
file is the CODE-phase smoke test per #538 plan C1.
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


VALID_HANDOFF = {
    "produced": ["src/auth.ts"],
    "decisions": ["Used JWT"],
    "uncertainty": [],
    "integration": ["UserService"],
    "open_questions": [],
}


def _run_main(stdin_payload, task_data, append_calls):
    """Invoke agent_handoff_emitter.main() with patched IO/deps."""
    from agent_handoff_emitter import main

    def _append_spy(event):
        append_calls.append(event)
        return True

    with patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
         patch("agent_handoff_emitter.append_event", side_effect=_append_spy), \
         patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
        with pytest.raises(SystemExit) as exc_info:
            main()
    return exc_info.value.code


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
                "task_subject": "handed off from lead",
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


class TestStatusGate:
    """#528 regression guard: TaskCompleted fires on ANY TaskUpdate, not just
    status transitions to completed. The on-disk status read MUST gate
    emission or metadata-only TaskUpdates will journal phantom events."""

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


class TestIdempotency:
    def test_second_fire_for_same_team_task_is_suppressed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        task_data = {
            "status": "completed",
            "owner": "backend-coder-538",
            "metadata": {"handoff": VALID_HANDOFF},
        }
        payload = {
            "task_id": "5",
            "task_subject": "same task completing again",
            "teammate_name": "backend-coder-538",
            "team_name": "pact-test",
        }
        _run_main(payload, task_data, calls)
        _run_main(payload, task_data, calls)
        assert len(calls) == 1, "O_EXCL marker must deduplicate re-fires"

    def test_different_task_ids_each_emit_once(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        task_data = {
            "status": "completed",
            "owner": "backend-coder-538",
            "metadata": {"handoff": VALID_HANDOFF},
        }
        _run_main(
            {"task_id": "5", "task_subject": "t5", "teammate_name": "x", "team_name": "pact-test"},
            task_data, calls,
        )
        _run_main(
            {"task_id": "6", "task_subject": "t6", "teammate_name": "x", "team_name": "pact-test"},
            task_data, calls,
        )
        assert len(calls) == 2

    def test_marker_file_created_at_expected_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        _run_main(
            stdin_payload={
                "task_id": "marker-probe",
                "task_subject": "probe",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        marker = tmp_path / ".claude" / "teams" / "pact-test" / ".agent_handoff_emitted" / "marker-probe"
        assert marker.exists(), "fire-once marker must be created at team-scoped path"
