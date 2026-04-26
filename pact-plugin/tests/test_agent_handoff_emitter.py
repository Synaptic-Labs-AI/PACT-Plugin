"""
Smoke tests for agent_handoff_emitter.py — #538 TaskCompleted journal writer.

Covers the happy path, disk-status gate (#528 regression guard),
signal-task bypass, non-agent bypass, and the sidecar O_EXCL idempotency
guard. Comprehensive coverage (malformed stdin, fallback-field
substitution, marker-OSError fail-open) lands in the TEST phase; this
file is the CODE-phase smoke test per #538 plan C1.
"""
import errno
import io
import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# --- Pinned fixtures ---

VALID_HANDOFF = {
    "produced": ["src/auth.ts"],
    "decisions": ["Used JWT"],
    "uncertainty": [],
    "integration": ["UserService"],
    "open_questions": [],
}


# Verbatim 9-field stdin shape captured by 3 real-platform probes during #551
# PREPARE phase (docs/preparation/551-emitter-regression-diagnostic.md
# § "Real-platform stdin shape"). Pinned as a fixture so future emitter
# changes are tested against what the platform actually delivers, not
# against a synthetic shape that test authors guessed.
PLATFORM_STDIN_SHAPE = {
    "session_id": "1fb6500d-25ba-48c6-af00-5f92024644d0",
    "transcript_path": (
        "/Users/mj/.claude/projects/"
        "-Users-mj-Sites-collab-PACT-prompt/"
        "1fb6500d-25ba-48c6-af00-5f92024644d0.jsonl"
    ),
    "cwd": "/Users/mj/Sites/collab/PACT-prompt",
    "hook_event_name": "TaskCompleted",
    "task_id": "12",
    "task_subject": "PROBE: capture real TaskCompleted stdin shape",
    "task_description": "diagnostic probe payload",
    "teammate_name": "preparer",
    "team_name": "pact-1fb6500d",
}


def _write_task_json(tmp_path, team, task_id, payload):
    """Helper for TestRealDiskRead — write a task.json under the
    team-scoped path that read_task_json checks first.

    Returns the Path to the written file. Caller is responsible for
    setting `monkeypatch.setenv("HOME", str(tmp_path))` so HOME-relative
    resolution lands under tmp_path.
    """
    tasks_dir = tmp_path / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_json = tasks_dir / f"{task_id}.json"
    task_json.write_text(json.dumps(payload), encoding="utf-8")
    return task_json


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


class TestMarkerFailOpen:
    """Architect §2.4 fail-OPEN contract: if the marker subsystem itself
    errors (permission denied, ENOSPC, directory creation failure), the
    emitter MUST still write the journal event rather than suppress.
    Data-integrity (preserving the HANDOFF) beats duplication-prevention
    when the marker layer breaks. Worst case: fall back to pre-#538
    duplication on THIS task only.

    These tests target `_already_emitted`'s OSError branches directly,
    which are otherwise hard to exercise without filesystem manipulation.
    """

    def test_marker_dir_mkdir_permission_denied_still_emits(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []

        import agent_handoff_emitter
        original_mkdir = Path.mkdir

        def _mkdir_denied(self_path, *args, **kwargs):
            # Only deny the marker dir; let other mkdir calls proceed.
            if ".agent_handoff_emitted" in str(self_path):
                raise PermissionError(13, "Permission denied")
            return original_mkdir(self_path, *args, **kwargs)

        with patch.object(Path, "mkdir", _mkdir_denied):
            _run_main(
                stdin_payload={
                    "task_id": "perm-denied",
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
        assert len(calls) == 1, (
            "marker dir PermissionError must fail-OPEN and still emit — "
            "architect §2.4 carve-out preserves data-integrity over dedup."
        )

    def test_marker_open_enospc_still_emits(self, tmp_path, monkeypatch):
        """ENOSPC during O_EXCL marker creation must not suppress the
        journal write. `os.open` raises OSError(errno=ENOSPC); the
        emitter's _already_emitted returns False on any non-EEXIST
        OSError, allowing the fire-OPEN path."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []

        original_os_open = os.open

        def _os_open_enospc(path, flags, mode=0o777, *, dir_fd=None):
            if ".agent_handoff_emitted" in str(path):
                raise OSError(errno.ENOSPC, "No space left on device", str(path))
            return original_os_open(path, flags, mode)

        with patch("agent_handoff_emitter.os.open", side_effect=_os_open_enospc):
            _run_main(
                stdin_payload={
                    "task_id": "enospc-probe",
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
        assert len(calls) == 1, (
            "ENOSPC on marker open must fail-OPEN and still emit the "
            "agent_handoff event — data-integrity carve-out per §2.4."
        )

    def test_journal_write_failure_loses_event_but_marker_persists(
        self, tmp_path, monkeypatch
    ):
        """Document the marker-before-emit ordering asymmetry
        (backend-reviewer LOW #2, task #12).

        `_already_emitted` creates the sidecar marker BEFORE `append_event`
        is called. If `append_event` silently fails (session_journal.py
        fail-open contract — returns None/False rather than raising), the
        marker persists but the event is lost from the journal. This is
        the intentional trade-off: avoiding 37× duplicate emission (the
        #528 amplification class) is strictly more important than
        recovering a rare single-event loss on journal-write failure.

        This test pins the CURRENT behavior so a future reviewer reading
        `_already_emitted` → `append_event` → `_mark_emitted` ordering
        does not mistake it for a bug. If the ordering is ever inverted
        (journal-write first, marker second) — e.g., to try to prevent
        the loss — this test would fail and force the change to be
        justified against the amplification-prevention property.

        Mock choice: `append_event` returning None simulates
        session_journal's silent fail-open. An exception path from
        append_event would be caught by the outer try/except (task #16
        fix) and is covered by TestUnexpectedExceptionSuppression —
        we specifically exercise the NON-exception failure here.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_handoff_emitter import main

        append_call_count = {"n": 0}

        def _append_silent_fail(event):
            append_call_count["n"] += 1
            return None  # session_journal's silent fail-open

        task_data = {
            "status": "completed",
            "owner": "probe-agent",
            "metadata": {"handoff": VALID_HANDOFF},
        }
        payload = {
            "task_id": "journal-fail-probe",
            "task_subject": "journal write fails silently",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }

        # First invocation: marker gets created (by _already_emitted),
        # append_event returns None (silent failure), event is lost.
        with patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
             patch("agent_handoff_emitter.append_event", side_effect=_append_silent_fail), \
             patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with pytest.raises(SystemExit) as exc1:
                main()
        assert exc1.value.code == 0, (
            "AC #8: silent journal-write failure must not break exit-0 invariant"
        )
        assert append_call_count["n"] == 1, (
            "append_event must be called exactly once on first invocation — "
            "the journal-write path IS attempted, not skipped"
        )
        marker = (
            tmp_path / ".claude" / "teams" / "pact-test"
            / ".agent_handoff_emitted" / "journal-fail-probe"
        )
        assert marker.exists(), (
            "marker persists despite journal-write failure — this is the "
            "intentional asymmetry. `_already_emitted` creates the marker "
            "BEFORE `append_event` is called; a silent fail in append_event "
            "does NOT unwind the marker. Trade-off: prevents 37× duplicate "
            "emission at the cost of rare single-event loss."
        )

        # Second invocation with same (team, task_id): marker-based dedup
        # engages, append_event is NOT called again, exit 0 suppressOutput.
        # This property is what the trade-off buys us — dedup remains
        # intact despite the lost event.
        with patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
             patch("agent_handoff_emitter.append_event", side_effect=_append_silent_fail), \
             patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with pytest.raises(SystemExit) as exc2:
                main()
        assert exc2.value.code == 0
        assert append_call_count["n"] == 1, (
            "second invocation with same (team, task_id) must NOT retry "
            "append_event — marker-based dedup engaged. If this assertion "
            "fails, the dedup property is broken and amplification returns."
        )


class TestConcurrentFireRace:
    """O_EXCL marker must deterministically deduplicate concurrent
    `_already_emitted` calls for the same (team, task_id). One caller
    wins marker creation (returns False → emit); the other observes
    FileExistsError (returns True → suppress).

    We target the atomic test-and-set primitive directly rather than
    invoking `main()` in threads — `sys.stdin` and unittest.mock patches
    are process-global state that thread-based invocation of main()
    cannot safely share. The atomicity invariant lives in
    `_already_emitted`, which is what #538 C1 added to defend against
    stopHooks.ts duplication.
    """

    def test_concurrent_already_emitted_exactly_one_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_handoff_emitter import _already_emitted

        team = "pact-race"
        task_id = "race-probe"
        results: list[bool] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def _fire():
            barrier.wait()
            r = _already_emitted(team, task_id)
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=_fire) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one thread wins the marker creation (returns False —
        # proceed with emit). All others lose (return True — suppress).
        false_count = sum(1 for r in results if r is False)
        true_count = sum(1 for r in results if r is True)
        assert false_count == 1, (
            f"O_EXCL marker failed to deduplicate {len(results)} "
            f"concurrent fires: {false_count} winners (expected 1). "
            f"Race window widened — re-verify atomicity of "
            f"os.open(O_WRONLY|O_CREAT|O_EXCL)."
        )
        assert true_count == len(results) - 1, (
            f"expected {len(results) - 1} losers returning True; got {true_count}"
        )
        # The marker file exists on disk after the race.
        marker = tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted" / task_id
        assert marker.exists(), "race winner must have created the marker file"


class TestTeammateNamePrecedence:
    """Architect §2.3 ordering: `task_data.get("owner") or
    input_data.get("teammate_name")`. Owner takes precedence; stdin
    teammate_name is fallback. Empty strings and missing fields should
    degrade gracefully.
    """

    def test_empty_owner_string_falls_back_to_stdin_teammate_name(
        self, tmp_path, monkeypatch
    ):
        """owner='' (falsy) should defer to input_data.teammate_name."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        _run_main(
            stdin_payload={
                "task_id": "empty-owner",
                "task_subject": "empty owner, stdin teammate present",
                "teammate_name": "stdin-fallback-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                "owner": "",  # empty string — falsy, same as missing
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert len(calls) == 1
        assert calls[0]["agent"] == "stdin-fallback-agent", (
            "empty-string owner must fall back to stdin teammate_name "
            "per architect §2.3 `or`-chain semantics."
        )

    def test_missing_owner_and_empty_stdin_teammate_name_no_event(
        self, tmp_path, monkeypatch
    ):
        """Both signals empty/missing → non-agent completion → suppress."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        _run_main(
            stdin_payload={
                "task_id": "no-agent",
                "task_subject": "non-agent feature task",
                "teammate_name": "",  # empty stdin signal
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                # no "owner" key at all
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert calls == [], (
            "both owner and stdin teammate_name empty → non-agent "
            "completion → MUST suppress (no phantom agent_handoff event)."
        )

    def test_owner_whitespace_only_is_treated_as_falsy(
        self, tmp_path, monkeypatch
    ):
        """Whitespace-only owner — Python `or` treats non-empty strings
        as truthy, so '   ' would pass. This test pins the CURRENT
        behavior: whitespace owner IS used as agent name. If we want
        stricter validation (strip+empty check), that's a follow-up.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        _run_main(
            stdin_payload={
                "task_id": "ws-owner",
                "task_subject": "whitespace owner",
                "teammate_name": "proper-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                "owner": "   ",  # whitespace-only but truthy in Python
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        # Current behavior: whitespace-only is truthy; it wins over stdin
        # teammate_name. This pins the CURRENT contract — if a future
        # hardening wants strict validation, update this test.
        assert len(calls) == 1
        assert calls[0]["agent"] == "   ", (
            "whitespace-only owner IS currently truthy; this test pins "
            "that behavior. If stricter validation lands, update here."
        )


class TestFallbackFieldStderr:
    """Backend LOW uncertainty #3: the fallback-field stderr write for
    missing task_id/task_subject is a carve-out in architect §2.7. It
    must:
      - fire at most once per invocation (not a loop),
      - NOT set exit-2 (non-blocking),
      - NOT emit a systemMessage (no protocol-level signal).
    """

    def test_missing_task_id_emits_stderr_but_not_systemmessage(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        exit_code = _run_main(
            stdin_payload={
                # task_id missing entirely
                "task_subject": "stderr fallback probe",
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
        captured = capsys.readouterr()
        assert exit_code == 0, (
            "fallback-field path must NOT propagate a blocking exit; "
            "architect §2.7 forbids exit-2 from this carve-out."
        )
        assert "MISSING" in captured.err, (
            "fallback-field stderr warning expected to surface which "
            "field was missing"
        )
        # Protocol-level signal check: only _SUPPRESS_OUTPUT JSON on stdout.
        assert "systemMessage" not in captured.out, (
            "fallback-field path emitted a systemMessage — violates "
            "architect §2.7 zero-emission-sink invariant."
        )
        # Event IS still written — preserving HANDOFF beats dropping it.
        assert len(calls) == 1

    def test_missing_task_subject_emits_stderr_and_persists_event(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        _run_main(
            stdin_payload={
                "task_id": "ts-probe",
                # task_subject missing
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
        captured = capsys.readouterr()
        assert "task_subject=MISSING" in captured.err
        assert len(calls) == 1
        assert calls[0]["task_subject"] == "(no subject)", (
            "missing task_subject must fall back to sentinel, not None"
        )


class TestMalformedStdin:
    """#10 remediation (per task #16): closes the header promise-drift at
    lines 6-8 — "Comprehensive coverage (malformed stdin, ...) lands in
    the TEST phase." Marker-OSError and fallback-field landed in initial
    TEST; JSONDecodeError path at agent_handoff_emitter.py:134-138 did
    not. These tests pin that path directly.

    AC #8 invariant under test: no matter what stdin carries, the
    emitter exits 0 with stdout=_SUPPRESS_OUTPUT and writes no event.
    """

    def test_invalid_json_exits_clean(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_handoff_emitter import main

        calls: list[dict] = []
        with patch(
            "agent_handoff_emitter.append_event",
            side_effect=lambda e: (calls.append(e), True)[1],
        ), patch("sys.stdin", io.StringIO("not{valid json}")):
            with pytest.raises(SystemExit) as exc:
                main()
        captured = capsys.readouterr()
        assert exc.value.code == 0, (
            "JSONDecodeError path must exit 0; exit-2 would break AC #8"
        )
        assert "suppressOutput" in captured.out, (
            "malformed stdin must emit _SUPPRESS_OUTPUT to hide the error "
            "from Claude Code's hook-error display"
        )
        assert calls == [], (
            "no journal event must be written when stdin cannot be parsed"
        )

    def test_empty_stdin_exits_clean(self, tmp_path, monkeypatch, capsys):
        """Empty stdin (dispatcher sent zero-byte payload) is a special
        case of JSONDecodeError — json.load on empty stream raises
        JSONDecodeError("Expecting value", ...). Same invariant."""
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_handoff_emitter import main

        calls: list[dict] = []
        with patch(
            "agent_handoff_emitter.append_event",
            side_effect=lambda e: (calls.append(e), True)[1],
        ), patch("sys.stdin", io.StringIO("")):
            with pytest.raises(SystemExit) as exc:
                main()
        captured = capsys.readouterr()
        assert exc.value.code == 0
        assert "suppressOutput" in captured.out
        assert calls == []

    def test_missing_required_fields_uses_fallback_and_emits_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        """Stdin lacks BOTH task_id AND task_subject simultaneously —
        fallback path must fire, stderr warning must name both fields as
        MISSING, event must still persist with sentinel values (data-
        integrity carve-out per architect §2.7)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        _run_main(
            stdin_payload={
                # neither task_id nor task_subject present
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
        captured = capsys.readouterr()
        assert "task_id=MISSING" in captured.err, (
            "stderr warning must name task_id as missing"
        )
        assert "task_subject=MISSING" in captured.err, (
            "stderr warning must name task_subject as missing"
        )
        assert len(calls) == 1, (
            "fallback path must still persist the journal event — "
            "architect §2.7: data-integrity beats dropping the HANDOFF"
        )
        assert calls[0]["task_id"] == "unknown"
        assert calls[0]["task_subject"] == "(no subject)"
        # No systemMessage on stdout — stderr is the only sink.
        assert "systemMessage" not in captured.out


class TestNullMetadata:
    """#4 pair (per task #16): security-reviewer's fix at
    agent_handoff_emitter.py guards `task_data.get("metadata")` against
    JSON `null` via `or {}` coercion. Without the fix, a crafted
    task.json with `"metadata": null` (valid JSON, valid semantically as
    "no metadata") would raise AttributeError on `.get("type")` or
    `.get("handoff")`, crashing the emitter mid-main() — violating AC #8.

    This test pins the post-fix invariant. Against pre-fix emitter, this
    test WILL fail (AttributeError propagates through main's try/except
    wrapper, depending on #16 fix order). That RED-initial state IS the
    counter-test-by-revert load-bearingness proof per the #538 dogfood
    discipline.
    """

    def test_null_metadata_field_does_not_crash_exit_zero_invariant_holds(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        # Crafted task_data with metadata explicitly null (not missing).
        # This shape can land on disk if a teammate/platform writes
        # task.json with `"metadata": null` — valid JSON but crashes
        # `.get("type")` chain without the `or {}` coercion fix.
        exit_code = _run_main(
            stdin_payload={
                "task_id": "null-meta-probe",
                "task_subject": "null metadata probe",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                "owner": "probe-agent",
                "metadata": None,  # the adversarial shape
            },
            append_calls=calls,
        )
        captured = capsys.readouterr()
        assert exit_code == 0, (
            "metadata:null must not break AC #8 exit-0 invariant. "
            "Security-reviewer's #4 fix (`or {}` coercion) must be "
            "present in agent_handoff_emitter for this test to pass."
        )
        assert "suppressOutput" in captured.out
        # Under Option E (handoff-presence gate), metadata=None coerces
        # to {} via `or {}`, then `task_metadata.get("handoff")` is
        # falsy → suppress emission. No event is written. The crash-
        # invariant (exit 0, suppressOutput) is what this test pins;
        # the empty-handoff path is now correctly suppressed instead
        # of producing a content-less journal entry.
        assert calls == [], (
            "Option E: metadata=None collapses to {} via `or {}`, then "
            "the handoff-presence gate suppresses emission. A journal "
            "entry with empty handoff is exactly the B1 failure mode "
            "Option E was added to prevent."
        )


class TestUnexpectedExceptionSuppression:
    """#16 pair (per task #16): security-reviewer adds an outer
    try/except around main()'s body. Without it, any unhandled exception
    (runtime errors in append_event, task_utils, pact_context, etc.)
    escapes the hook as a non-zero exit with traceback on stderr — and
    more critically, may propagate a blocking exit code depending on
    Claude Code's hook-dispatcher contract. AC #8 demands exit-0 suppression.

    This test simulates an unexpected exception deep in the emit path by
    patching `append_event` to raise RuntimeError, then asserts the
    emitter STILL exits 0 and emits _SUPPRESS_OUTPUT. Pre-fix this RED;
    post-fix GREEN.
    """

    def test_unexpected_exception_suppressed_exit_zero_invariant_holds(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_handoff_emitter import main

        def _append_boom(event):
            raise RuntimeError(
                "simulated deep-path failure (e.g., journal write fault)"
            )

        task_data = {
            "status": "completed",
            "owner": "probe-agent",
            "metadata": {"handoff": VALID_HANDOFF},
        }
        payload = {
            "task_id": "boom-probe",
            "task_subject": "unexpected exception probe",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }

        with patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
             patch("agent_handoff_emitter.append_event", side_effect=_append_boom), \
             patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with pytest.raises(SystemExit) as exc:
                main()
        captured = capsys.readouterr()
        assert exc.value.code == 0, (
            "AC #8: any unhandled exception in main() must be caught by "
            "the outer try/except (security-reviewer #16 fix) and collapse "
            "to exit 0. A non-zero exit here means the fix is missing or "
            "the exception escaped the guard."
        )
        assert "suppressOutput" in captured.out, (
            "exit-0 without _SUPPRESS_OUTPUT still surfaces the hook-error "
            "display to the user; AC #8 requires the full suppression "
            "contract on every code path."
        )
        # stderr may contain the traceback or a short error line — either
        # is acceptable per architect §2.7 (stderr is non-blocking). What's
        # NOT acceptable is a systemMessage on stdout.
        assert "systemMessage" not in captured.out, (
            "outer try/except handler must NOT emit a systemMessage — "
            "a protocol-level signal on an error path would re-introduce "
            "the livelock-capability category #538 removed."
        )


class TestPathSanitization:
    """Direct coverage for `_sanitize_path_component` helper + integration
    coverage for degenerate post-sanitize values.

    Gap addressed (4-reviewer corroboration: architect-blind Y1 +
    backend-blind Y3 + test-blind TB-Y1 + test-blind TB-Y2): the helper
    shipped in dd6e434 with zero direct unit coverage. All cycle-1 testing
    went through integration. The helper uses `re.sub(r"[/\\\\]|\\.\\.", "", v)`
    which strips `/`, `\\`, and `..` substrings — but leaves single-dot
    segments untouched. This creates degenerate post-sanitize values
    (`''`, `'.'`, `'..'`) which, pre-guard, collapsed the marker path onto
    an existing directory (`marker_dir / '.'` → marker_dir itself),
    permanently suppressing future emits for the degenerate key.

    Security-reviewer's task #24 adds the guard:
        if team_name in ("", ".", "..") or task_id in ("", ".", ".."):
            return False  # emit without marker

    This class covers:
      - SanitizeHelper: direct unit tests pin the regex's stripping behavior
        AND the documented single-dot preservation quirk.
      - DegenerateTaskIdDoesNotCreateMarker: integration tests verify the
        guard — degenerate post-sanitize values emit the journal event but
        do NOT create a marker file (paired with task #24 guard).
      - IntegrationPathTraversalAttempts: integration tests confirm that
        path-traversal inputs produce markers inside the team dir, never
        escaping to parent/sibling paths.
    """

    class TestSanitizeHelper:
        """Direct unit tests against _sanitize_path_component. Independent
        of task #24 guard — these exercise the regex behavior alone."""

        @pytest.mark.parametrize(
            "legitimate",
            ["42", "12345", "feature-task-5", "task_5", "abc-def"],
        )
        def test_sanitize_preserves_legitimate_task_ids(self, legitimate):
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component(legitimate) == legitimate, (
                f"legitimate task_id {legitimate!r} was altered by sanitizer; "
                f"the regex must only strip `/`, `\\\\`, and `..` substrings."
            )

        def test_sanitize_strips_forward_slash(self):
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component("foo/bar") == "foobar"

        def test_sanitize_strips_backslash(self):
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component("foo\\bar") == "foobar"

        @pytest.mark.parametrize(
            "input_value,expected",
            [
                ("../foo", "foo"),
                ("..", ""),
                ("a..b", "ab"),
                ("..\\..", ""),
                ("...", "."),  # first two dots stripped; third survives
                ("....", ""),  # two consecutive `..` pairs strip to empty
            ],
        )
        def test_sanitize_strips_dotdot_sequences(self, input_value, expected):
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component(input_value) == expected, (
                f"sanitize({input_value!r}) expected {expected!r}; "
                f"regex may have drifted."
            )

        @pytest.mark.parametrize(
            "traversal_attempt",
            [
                "/etc/passwd",
                "./etc/passwd",
                "../../../../etc/shadow",
                "\\..\\..\\foo",
                "/../../../../root/.ssh/id_rsa",
            ],
        )
        def test_sanitize_strips_path_traversal_combinations(self, traversal_attempt):
            """Compound attack inputs — the output must contain no `/`,
            no `\\`, and no `..` substring. Exact value is less
            important than the absence of traversal primitives."""
            from agent_handoff_emitter import _sanitize_path_component
            out = _sanitize_path_component(traversal_attempt)
            assert "/" not in out, f"forward slash survived in {out!r}"
            assert "\\" not in out, f"backslash survived in {out!r}"
            assert ".." not in out, f"parent-dir sequence survived in {out!r}"

        def test_sanitize_preserves_single_dot(self):
            """Documented quirk: single `.` is NOT stripped (regex only
            matches `..`). Caller guards against this degenerate shape
            separately (#24 guard). This test pins the current contract
            so a future regex tightening is a deliberate decision."""
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component(".") == "."

        def test_sanitize_preserves_whitespace(self):
            """Whitespace is not a path-traversal primitive — regex doesn't
            strip it. Whitespace-only values create filesystem-valid
            (if unusual) filenames, so the guard does NOT need to treat
            them as degenerate."""
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component(" ") == " "
            assert _sanitize_path_component("  ") == "  "

        def test_sanitize_empty_string_unchanged(self):
            """Empty input returns empty — pinned for guard-paired tests
            that rely on the empty sentinel reaching _already_emitted."""
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component("") == ""

    class TestDegenerateInputsDoNotCreateMarker:
        """Integration coverage — depends on security-reviewer's task #24
        guard. Degenerate post-sanitize values (`''`, `'.'`, `'..'`) in
        EITHER axis (task_id OR team_name) must NOT create a marker file,
        but MUST still emit the journal event (fail-open data-integrity
        per architect §2.4).

        The bug class is SYMMETRIC across both axes:
        - task_id degenerate: `marker_dir / '.'` → marker_dir itself;
          EEXIST collapses to "marker already exists" → permanent
          suppression of the degenerate key.
        - team_name='..' (WORSE): `home/.claude/teams/../.agent_handoff_emitted`
          normalizes to `home/.claude/.agent_handoff_emitted` — marker
          created OUTSIDE any team's scope, polluting user home root.
        - team_name='.': `home/.claude/teams/./.agent_handoff_emitted`
          normalizes to `home/.claude/teams/.agent_handoff_emitted` —
          cross-team pollution (marker directly under teams/, visible to
          every team's enumeration).

        Pre-#24 guard: `if not team_name or not task_id` caught empty
        string only. Post-#24: extended to `task_id/team_name in
        ("", ".", "..")` — catches the full degenerate set per axis.
        """

        @pytest.mark.parametrize(
            "raw_task_id",
            ["..", "..\\..", "...."],
            # All sanitize to `''`. Note `''` itself can't be sent directly
            # — the main() fallback substitutes "unknown" before sanitize,
            # so we test the PRE-sanitize inputs that produce empty output.
            # Empty post-sanitize was ALREADY guarded pre-#24 via the
            # original `if not team_name or not task_id` branch; these
            # tests pin that behavior and serve as regression guards.
        )
        def test_empty_post_sanitize_task_id_emits_without_marker(
            self, raw_task_id, tmp_path, monkeypatch
        ):
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "degenerate-empty probe",
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
            assert len(calls) == 1, (
                f"degenerate task_id {raw_task_id!r} (sanitizes to empty) "
                f"must still emit the journal event per fail-open data-"
                f"integrity invariant. Pre-#24 guard, EEXIST on "
                f"`marker_dir / ''` permanently suppressed the emit."
            )
            # Marker directory may exist (created by _already_emitted before
            # the guard returned False), but it must contain NO file named
            # with the degenerate sanitized value.
            marker_dir = (
                tmp_path / ".claude" / "teams" / "pact-test"
                / ".agent_handoff_emitted"
            )
            if marker_dir.exists():
                # The guard must prevent any degenerate marker file from
                # being created inside. Empty-string filename isn't a valid
                # path component; check no stray file landed here.
                files_in_dir = list(marker_dir.iterdir())
                assert files_in_dir == [], (
                    f"guard failed — degenerate task_id {raw_task_id!r} "
                    f"produced stray files in marker dir: {files_in_dir}"
                )

        @pytest.mark.parametrize(
            "raw_task_id",
            [".", "...", "/./"],  # all sanitize to '.'
            # These are the NEWLY-guarded cases in #24. Pre-#24 the guard
            # was `if not task_id` which missed post-sanitize `.` — it's
            # truthy. These tests are the paired-regression proof that
            # #24's extended check (`task_id in ("", ".", "..")`) closes
            # the collapse-onto-marker_dir bug.
        )
        def test_dot_only_post_sanitize_emits_without_marker(
            self, raw_task_id, tmp_path, monkeypatch
        ):
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "degenerate-dot probe",
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
            assert len(calls) == 1, (
                f"degenerate task_id {raw_task_id!r} (sanitizes to '.') "
                f"must still emit — the #24 guard protects against the "
                f"`marker_dir / '.'` collapse that otherwise permanently "
                f"suppresses future emits via spurious EEXIST."
            )
            marker_dir = (
                tmp_path / ".claude" / "teams" / "pact-test"
                / ".agent_handoff_emitted"
            )
            # Crucial invariant: marker_dir itself must not have been
            # interpreted as THE marker. If it was, a subsequent fire
            # with the same degenerate key would see EEXIST and suppress.
            # Verify by firing a SECOND time with the same degenerate key
            # and asserting a second event is written.
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "degenerate-dot probe second fire",
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
            # Post-#24, degenerate keys are UN-DEDUPABLE (no marker
            # created → every fire emits). This is the intentional
            # accepted trade-off: rare duplication for degenerate keys
            # beats silent permanent event loss for ALL future fires.
            assert len(calls) == 2, (
                f"degenerate key {raw_task_id!r} second fire was suppressed "
                f"— the #24 guard is missing or the pre-guard EEXIST-on-dir "
                f"bug has resurfaced."
            )

        @pytest.mark.parametrize(
            "raw_task_id",
            ["/./.", ".//."],  # both sanitize to '..'
            # Per security-reviewer-538's empirical 17-input probe: these
            # forms produce `..` after regex stripping (two single-dot
            # segments separated by `/` collapse to `..` once the `/` is
            # stripped). Exercises the `task_id == ".."` branch of the
            # #24 guard — distinct from the `"."` branch covered above.
            # Pre-#24 without the branch, `marker_dir / ".."` resolves to
            # `marker_dir.parent`, causing EEXIST → permanent suppression
            # with a marker landing OUTSIDE the intended path.
        )
        def test_dotdot_post_sanitize_emits_via_guard(
            self, raw_task_id, tmp_path, monkeypatch
        ):
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "dotdot-collapse probe",
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
            assert len(calls) == 1, (
                f"task_id {raw_task_id!r} sanitizes to '..' which pre-#24 "
                f"resolved to `marker_dir.parent` → permanent suppression. "
                f"#24 guard `task_id in ('', '.', '..')` must catch this."
            )
            # Pin that no stray marker files landed above team scope at
            # `~/.claude/teams/` level (the pre-#24 escape shape when
            # the `..` collapses marker_dir onto its parent).
            teams_dir = tmp_path / ".claude" / "teams"
            teams_children = (
                {p.name for p in teams_dir.iterdir()}
                if teams_dir.exists() else set()
            )
            assert teams_children <= {"pact-test"}, (
                f"unexpected children in {teams_dir}: "
                f"{teams_children - {'pact-test'}}. The `..` collapse may "
                f"have created marker files above the team scope."
            )

        @pytest.mark.parametrize(
            "raw_team_name,expected_sanitized",
            [
                ("..", ""),       # pre-#24 guarded (empty branch)
                ("..\\..", ""),   # pre-#24 guarded
                (".", "."),       # NEWLY guarded by #24 — cross-team pollution without guard
                ("...", "."),     # NEWLY guarded by #24
                (".....", "."),   # NEWLY guarded by #24 — odd-count dots collapse to '.'
                ("/./", "."),     # NEWLY guarded by #24 — same root cause as task_id case
                ("/./.", ".."),   # NEWLY guarded by #24 — dotdot-collapse branch
                (".//.", ".."),   # NEWLY guarded by #24 — same class as /./.
            ],
        )
        def test_degenerate_team_name_values_guarded(
            self, raw_team_name, expected_sanitized, tmp_path, monkeypatch
        ):
            """team_name axis symmetry.

            Pre-#24 with team_name='..': marker_dir resolves to
            `home/.claude/teams/../.agent_handoff_emitted`, which Path-
            normalizes to `home/.claude/.agent_handoff_emitted` — a
            marker file created directly under the user's home .claude
            dir (OUTSIDE any team's scope). This is the home-root
            pollution case.

            Pre-#24 with team_name='.': marker_dir resolves to
            `home/.claude/teams/./.agent_handoff_emitted`, normalizing
            to `home/.claude/teams/.agent_handoff_emitted` — a marker
            file directly under teams/, visible to every team.

            Post-#24 guard catches all degenerate team_name values in
            `("", ".", "..")` and returns False before marker creation.
            """
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": "42",
                    "task_subject": "degenerate team probe",
                    "teammate_name": "probe-agent",
                    "team_name": raw_team_name,
                },
                task_data={
                    "status": "completed",
                    "owner": "probe-agent",
                    "metadata": {"handoff": VALID_HANDOFF},
                },
                append_calls=calls,
            )
            assert len(calls) == 1, (
                f"degenerate team_name {raw_team_name!r} (sanitizes to "
                f"{expected_sanitized!r}) must emit the journal event via "
                f"the #24 guard. team_name and task_id are symmetrically "
                f"protected."
            )
            # Critical home-root-pollution assertions — the bug's
            # WORST-case form is marker creation OUTSIDE any team's
            # scope. Guard must prevent all three escape paths:
            home_root_marker = (
                tmp_path / ".claude" / ".agent_handoff_emitted"
            )
            assert not home_root_marker.exists(), (
                f"home-root pollution detected: degenerate team_name "
                f"{raw_team_name!r} created marker at {home_root_marker} "
                f"(OUTSIDE any team's scope). The #24 guard failed."
            )
            teams_root_marker = (
                tmp_path / ".claude" / "teams" / ".agent_handoff_emitted"
            )
            assert not teams_root_marker.exists(), (
                f"cross-team pollution detected: degenerate team_name "
                f"{raw_team_name!r} created marker at {teams_root_marker} "
                f"(directly under teams/, visible to every team)."
            )
            # And no marker file bearing the task_id basename was
            # created at either escape path.
            assert not (home_root_marker / "42").exists()
            assert not (teams_root_marker / "42").exists()

        @pytest.mark.parametrize(
            "raw_task_id,raw_team_name",
            [
                ("", "."),
                (".", ""),
                ("..", "."),
                (".", ".."),
                ("...", "..."),
                ("/./", "/./"),
            ],
        )
        def test_combined_degenerate_both_axes_guarded(
            self, raw_task_id, raw_team_name, tmp_path, monkeypatch
        ):
            """Combined-axis matrix: both task_id AND team_name degenerate
            simultaneously. Emit invariant must still hold (fail-open
            wins over the compound-pollution failure mode).

            Pre-#24: either axis alone could trigger the collapse bug;
            both together produce either home-root pollution (if
            team_name='..') or permanent suppression (if task_id
            collapses). Post-#24 guard returns False on EITHER axis
            being degenerate, so the compound case short-circuits via
            the first matched branch.
            """
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "compound degenerate probe",
                    "teammate_name": "probe-agent",
                    "team_name": raw_team_name,
                },
                task_data={
                    "status": "completed",
                    "owner": "probe-agent",
                    "metadata": {"handoff": VALID_HANDOFF},
                },
                append_calls=calls,
            )
            assert len(calls) == 1, (
                f"compound degenerate (task_id={raw_task_id!r}, "
                f"team_name={raw_team_name!r}) must emit via #24 guard."
            )
            # Neither home-root nor teams-root pollution.
            home_root_marker = tmp_path / ".claude" / ".agent_handoff_emitted"
            teams_root_marker = (
                tmp_path / ".claude" / "teams" / ".agent_handoff_emitted"
            )
            assert not home_root_marker.exists()
            assert not teams_root_marker.exists()

    class TestIntegrationPathTraversalAttempts:
        """Path-traversal inputs must NOT escape the team's marker dir.
        Independent of #24 guard — these test the sanitizer's stripping
        behavior integrated through main(). Input with traversal primitives
        sanitizes to a legitimate basename that lives inside the team dir.
        """

        @pytest.mark.parametrize(
            "attack_task_id,expected_sanitized",
            [
                ("../../../etc/shadow", "etcshadow"),
                ("/etc/passwd", "etcpasswd"),
                ("\\..\\..\\foo", "foo"),
                ("../../secrets", "secrets"),
            ],
        )
        def test_path_traversal_task_ids_contained_in_team_dir(
            self, attack_task_id, expected_sanitized, tmp_path, monkeypatch
        ):
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": attack_task_id,
                    "task_subject": "path-traversal probe",
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
            assert len(calls) == 1
            # Marker file (if created) lives at the sanitized basename
            # INSIDE the team's .agent_handoff_emitted dir — never at
            # an escape path.
            expected_marker = (
                tmp_path / ".claude" / "teams" / "pact-test"
                / ".agent_handoff_emitted" / expected_sanitized
            )
            assert expected_marker.exists(), (
                f"path-traversal attempt {attack_task_id!r} sanitized to "
                f"{expected_sanitized!r}; marker must exist at expected "
                f"location but was not found at {expected_marker}."
            )
            # Escape-path check: nothing was created outside the team dir.
            # Any path containing "etc/shadow", "etc/passwd", or absolute
            # leakage is a failure.
            escape_targets = [
                tmp_path.parent / "etc" / "shadow",
                tmp_path / "etc" / "passwd",
                Path("/etc/shadow"),
                Path("/etc/passwd"),
            ]
            for escape in escape_targets:
                # Skip absolute system paths if they happen to pre-exist
                # (e.g., /etc/passwd on macOS is real — we can't assert
                # it doesn't exist; we assert we didn't CREATE it by
                # asserting our sanitized marker exists inside tmp_path
                # above, which is sufficient).
                if escape.is_absolute() and escape.exists():
                    continue
                assert not escape.exists(), (
                    f"path-traversal attempt {attack_task_id!r} created "
                    f"a file at escape path {escape}."
                )


class TestRaceShapeRegression:
    """#551 root-cause regression guard. Platform fires TaskCompleted with
    `hook_event_name="TaskCompleted"` BEFORE persisting status="completed"
    to disk. Pre-Option-B, the (then-primary) disk-status gate read
    status="in_progress", aborted, and the journal write was never reached
    (3/3 PREPARE-phase probes confirmed; 0/51 cumulative production loss).

    Under Option B, hook_event_name="TaskCompleted" is the PRIMARY
    transition signal — disk-status is fallback only when stdin lacks
    hook_event_name. The journal write succeeds despite the on-disk
    status mismatch.

    Parametrized across two race shapes:
      (a) v3.19.2 race — disk shows in_progress because platform write
          hasn't persisted yet; this is the empirically-confirmed shape
          producing 0/51.
      (b) phantom-fire-revert — disk shows in_progress because the
          TaskUpdate was metadata-only (memory `21b4576b` documents 200+
          such fires pre-#538). Under Option B this also emits one event,
          then the `_already_emitted` O_EXCL marker suppresses any
          subsequent fires for the same (team, task_id).

    BOTH cases must produce exactly one append_event call. The marker
    persists either way, so subsequent fires for the same task are
    suppressed regardless of which race shape produced the first fire.
    """

    @pytest.mark.parametrize(
        "race_kind,disk_status,disk_metadata",
        [
            # (a) v3.19.2 race — platform fires TaskCompleted BEFORE
            #     persisting status=completed to disk, but the teammate
            #     has already stored metadata.handoff (the same TaskUpdate
            #     that flips status carries handoff in its metadata write).
            #     Disk shows status=in_progress; handoff is on disk.
            #     Option E gate passes (handoff present); hook_event_name
            #     primary signal triggers emission despite stale status.
            (
                "v3_19_2_race_pre_persist",
                "in_progress",
                {"handoff": VALID_HANDOFF},
            ),
            # (b) phantom-fire-revert — completion already happened;
            #     the disk reflects it (status=completed, handoff stored);
            #     and a follow-up TaskCompleted fires (e.g., from
            #     stopHooks.ts re-dispatch). Both signals positive,
            #     handoff present, marker dedup absorbs duplicate fires
            #     (covered by TestIdempotency).
            (
                "phantom_fire_revert_metadata_only",
                "in_progress",
                {"handoff": VALID_HANDOFF},
            ),
        ],
    )
    def test_hook_event_name_primary_signal_emits_despite_disk_in_progress(
        self, race_kind, disk_status, disk_metadata, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        exit_code = _run_main(
            stdin_payload={
                "hook_event_name": "TaskCompleted",
                "task_id": "race-probe",
                "task_subject": f"race-shape probe: {race_kind}",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": disk_status,
                "owner": "probe-agent",
                "metadata": disk_metadata,
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert len(calls) == 1, (
            f"#551 regression: race-shape {race_kind!r} (disk status="
            f"{disk_status!r}) should emit when hook_event_name="
            f"'TaskCompleted' is present. Pre-Option-B, the disk-status "
            f"gate aborted before reaching append_event — that is the "
            f"0/51 cumulative production loss this test pins."
        )
        assert calls[0]["agent"] == "probe-agent"
        assert calls[0]["task_id"] == "race-probe"

    def test_handoff_presence_gate_two_fire_sequence_real_revert(
        self, tmp_path, monkeypatch
    ):
        """Phantom-fire-revert realistic two-fire sequence under Option E
        handoff-presence gate. This pins the B1 fix from PR #563 review
        (review-architect): under platform revert, the FIRST fire arrives
        BEFORE the teammate has stored metadata.handoff (the fire is for
        a metadata-only TaskUpdate like briefing_delivered=true). The
        emitter MUST suppress that fire WITHOUT consuming the marker —
        otherwise the LATER genuine completion (with full handoff) gets
        suppressed by an empty-content marker, producing 51 empty journal
        entries instead of 51 substantive ones.

        Sequence:
          Fire 1: metadata={"briefing_delivered": True}, no handoff
                  → handoff-presence gate suppresses, NO marker, NO event.
          Fire 2: metadata={"handoff": VALID_HANDOFF, "briefing_delivered": True}
                  → handoff present, marker claimed, ONE event with full
                    handoff content lands in journal.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        payload = {
            "hook_event_name": "TaskCompleted",
            "task_id": "two-fire-revert",
            "task_subject": "two-fire revert sequence probe",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }

        # Fire 1: early metadata-only TaskUpdate fires TaskCompleted under
        # platform revert. Disk shows status=in_progress AND no handoff
        # key in metadata. Option E gate must suppress emission AND skip
        # marker creation.
        _run_main(
            payload,
            task_data={
                "status": "in_progress",
                "owner": "probe-agent",
                "metadata": {"briefing_delivered": True},  # NO handoff
            },
            append_calls=calls,
        )
        assert calls == [], (
            "Fire 1: handoff-presence gate failed to suppress an early "
            "metadata-only fire (no handoff key on disk). The B1 trace "
            "(review-architect, PR #563) would resurface — empty-content "
            "marker would suppress the later genuine completion."
        )
        marker = (
            tmp_path / ".claude" / "teams" / "pact-test"
            / ".agent_handoff_emitted" / "two-fire-revert"
        )
        assert not marker.exists(), (
            "Fire 1: marker MUST NOT be created when handoff is absent. "
            "If marker exists here, the genuine completion's later fire "
            "will hit EEXIST and silently drop the substantive HANDOFF — "
            "the exact B1 failure mode."
        )

        # Fire 2: genuine completion. Teammate has now stored
        # metadata.handoff; status flipped to completed. Option E gate
        # passes (handoff present), marker is claimed, journal write
        # produces the substantive entry.
        _run_main(
            payload,
            task_data={
                "status": "completed",
                "owner": "probe-agent",
                "metadata": {
                    "handoff": VALID_HANDOFF,
                    "briefing_delivered": True,
                },
            },
            append_calls=calls,
        )
        assert len(calls) == 1, (
            "Fire 2: genuine completion failed to emit. Either the "
            "handoff-presence gate is over-suppressing (rejected a "
            "valid completion) or the gate ordering is wrong relative "
            "to the marker check."
        )
        assert calls[0]["handoff"] == VALID_HANDOFF, (
            "Fire 2: journal entry has empty/incorrect handoff. The "
            "gate suppressed Fire 1 correctly but the marker subsystem "
            "or append_event flow lost the handoff content."
        )
        assert marker.exists(), (
            "Fire 2: marker MUST be created on the genuine completion. "
            "Subsequent fires for the same (team, task_id) need it for "
            "dedup."
        )

    def test_handoff_presence_gate_suppresses_all_metadata_only_fires(
        self, tmp_path, monkeypatch
    ):
        """Worst-case: 5 sequential metadata-only fires (all without
        handoff stored). All must suppress; marker MUST NOT be created.
        This pins the property that no number of phantom fires can
        consume the marker prematurely.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        payload = {
            "hook_event_name": "TaskCompleted",
            "task_id": "no-handoff-storm",
            "task_subject": "metadata-only storm probe",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }
        task_data = {
            "status": "in_progress",
            "owner": "probe-agent",
            "metadata": {"briefing_delivered": True},  # never has handoff
        }
        for _ in range(5):
            _run_main(payload, task_data, calls)
        assert calls == [], (
            "metadata-only storm produced phantom journal events. The "
            "Option E handoff-presence gate is the load-bearing defense "
            "against B1; if any of the 5 fires emitted, the marker "
            "would be consumed with empty content."
        )
        marker = (
            tmp_path / ".claude" / "teams" / "pact-test"
            / ".agent_handoff_emitted" / "no-handoff-storm"
        )
        assert not marker.exists(), (
            "marker created during a metadata-only storm — B1 root "
            "cause. The genuine completion's later fire would be "
            "silently dropped."
        )

    def test_disk_status_fallback_when_hook_event_name_absent(
        self, tmp_path, monkeypatch
    ):
        """Forward-compat: stdin without hook_event_name should fall back
        to the disk-status gate. This preserves correctness if a future
        platform shape omits the field, AND it pins the fallback path so
        a future refactor cannot silently delete it.

        With status=in_progress on disk AND no hook_event_name in stdin,
        the fallback gate aborts and no event is written.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        _run_main(
            stdin_payload={
                # hook_event_name intentionally absent
                "task_id": "no-event-name",
                "task_subject": "stdin lacks hook_event_name",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": "in_progress",
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert calls == [], (
            "fallback disk-status gate failed to fire when hook_event_name "
            "absent. The Option B fallback path is load-bearing for forward "
            "compatibility — do not delete it without a replacement."
        )

    def test_disk_status_fallback_emits_when_disk_completed(
        self, tmp_path, monkeypatch
    ):
        """Symmetric pair: stdin without hook_event_name AND disk shows
        status=completed → fallback gate passes → event emits. This is
        the path the suite's mocked-read tests exercise (none pass
        hook_event_name), so this test confirms their happy-path
        semantics still hold.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        _run_main(
            stdin_payload={
                # hook_event_name intentionally absent
                "task_id": "fallback-happy",
                "task_subject": "fallback path happy case",
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
        assert len(calls) == 1


class TestRealDiskRead:
    """The suite's mocked-read tests patch read_task_json and never
    exercise the actual on-disk read path that ships in production. This
    class fires main() against a real ~/.claude/tasks/{team}/{id}.json
    file written under tmp_path — verifies path-join and JSON parse on
    the read path that mocked tests bypass. (Sanitization is unit-tested
    separately in TestPathSanitization; these tests use safe inputs.)

    Without this coverage, a regression in read_task_json's path
    construction (e.g., team-scoped vs base directory ordering) would
    not be caught by the unit suite — exactly the test-vs-production
    gap that masked #551.
    """

    def test_real_disk_read_completed_task_emits_event(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        # Write a real task.json at the team-scoped path that
        # read_task_json checks first (the team-scoped branch in
        # task_utils.read_task_json's `for task_dir in task_dirs:` loop).
        _write_task_json(
            tmp_path, "pact-test", "real-disk-1",
            {
                "id": "real-disk-1",
                "subject": "real disk read probe",
                "status": "completed",
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
        )

        # Patch the tasks_base_dir to point at our tmp tree. read_task_json
        # accepts the override; we go through main() so the full pipeline
        # (init, sanitize, status gate, marker, append) is exercised
        # except for the bare read_task_json call site, which we redirect
        # to use our tmp path.
        from agent_handoff_emitter import main
        from shared import task_utils

        original_read = task_utils.read_task_json

        # Belt-and-suspenders: explicit tasks_base_dir override + HOME
        # monkeypatch route to the same path; intentional defense-in-depth
        # against future fixture-isolation changes.
        def _read_with_tmp_base(task_id, team_name, tasks_base_dir=None):
            return original_read(
                task_id, team_name,
                tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
            )

        calls: list[dict] = []

        def _append_spy(event):
            calls.append(event)
            return True

        with patch(
            "agent_handoff_emitter.read_task_json",
            side_effect=_read_with_tmp_base,
        ), patch(
            "agent_handoff_emitter.append_event",
            side_effect=_append_spy,
        ), patch("sys.stdin", io.StringIO(json.dumps({
            "session_id": "test-session-1",
            "hook_event_name": "TaskCompleted",
            "task_id": "real-disk-1",
            "task_subject": "real disk read probe",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }))):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 0
        assert len(calls) == 1, (
            "real-disk-read path failed to emit event despite valid "
            "task.json on disk. Sanitization, path-join, or JSON parse "
            "regression — investigate read_task_json in shared/task_utils.py."
        )
        assert calls[0]["task_id"] == "real-disk-1"
        assert calls[0]["agent"] == "probe-agent"
        assert calls[0]["handoff"] == VALID_HANDOFF

    def test_real_disk_read_in_progress_with_hook_event_name_still_emits(
        self, tmp_path, monkeypatch
    ):
        """The #551 race shape, fully end-to-end with a real on-disk
        task.json showing status=in_progress. Under Option B,
        hook_event_name primary signal trumps disk-status, journal
        write succeeds. This is the most direct production-fidelity
        regression guard."""
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_task_json(
            tmp_path, "pact-test", "real-disk-race",
            {
                "id": "real-disk-race",
                "subject": "race shape on real disk",
                "status": "in_progress",  # THE #551 race
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
        )

        from agent_handoff_emitter import main
        from shared import task_utils

        original_read = task_utils.read_task_json

        # Belt-and-suspenders: explicit tasks_base_dir override + HOME
        # monkeypatch route to the same path; intentional defense-in-depth
        # against future fixture-isolation changes.
        def _read_with_tmp_base(task_id, team_name, tasks_base_dir=None):
            return original_read(
                task_id, team_name,
                tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
            )

        calls: list[dict] = []

        with patch(
            "agent_handoff_emitter.read_task_json",
            side_effect=_read_with_tmp_base,
        ), patch(
            "agent_handoff_emitter.append_event",
            side_effect=lambda e: (calls.append(e), True)[1],
        ), patch("sys.stdin", io.StringIO(json.dumps({
            "session_id": "test-session-1",
            "hook_event_name": "TaskCompleted",
            "task_id": "real-disk-race",
            "task_subject": "race shape on real disk",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }))):
            with pytest.raises(SystemExit):
                main()

        assert len(calls) == 1, (
            "#551 race against REAL disk + hook_event_name primary signal "
            "must emit. If this fails, Option B is not actually wired up "
            "to the production read path."
        )


class TestStdinShapePin:
    """Pin the verbatim 9-field platform stdin shape captured during
    PREPARE-phase probes (3/3 fires identical structure). Future emitter
    changes are now tested against what the platform actually delivers,
    not against a synthetic shape that may drift.

    Diagnostic capture: docs/preparation/551-emitter-regression-diagnostic.md
    § "Real-platform stdin shape". Fields:
      session_id, transcript_path, cwd, hook_event_name, task_id,
      task_subject, task_description, teammate_name, team_name.
    """

    def test_platform_stdin_shape_emits_event_under_option_b(
        self, tmp_path, monkeypatch
    ):
        """The platform stdin always carries hook_event_name; under
        Option B the primary signal fires and the event lands. This is
        the realistic-shape equivalent of TestRaceShapeRegression's
        synthetic minimal payload — same Option B path, real fields."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        # Use the verbatim shape but ensure task_data shows in_progress
        # (the empirical race) so we prove the primary signal works on
        # the real shape, not just on the synthetic one.
        _run_main(
            stdin_payload=PLATFORM_STDIN_SHAPE,
            task_data={
                "status": "in_progress",
                "owner": "preparer",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert len(calls) == 1
        assert calls[0]["agent"] == "preparer"
        assert calls[0]["task_id"] == "12"
        assert calls[0]["task_subject"] == (
            "PROBE: capture real TaskCompleted stdin shape"
        )

    def test_platform_stdin_shape_extra_fields_do_not_break_main(
        self, tmp_path, monkeypatch
    ):
        """The platform delivers `transcript_path`, `cwd`, and
        `task_description` — fields the emitter does not consume. Pin
        that their presence does NOT crash main() (e.g. via a stricter
        future schema check). If a regression makes the emitter strict
        about unknown stdin fields, this test catches it before
        production."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        # All 9 fields present, including the ones the emitter ignores.
        _run_main(
            stdin_payload=PLATFORM_STDIN_SHAPE,
            task_data={
                "status": "completed",
                "owner": "preparer",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        # Event emits cleanly — no exception, no extra fields leaked
        # into the journal entry beyond what the emitter explicitly
        # forwards (agent, task_id, task_subject, handoff).
        assert len(calls) == 1
        event = calls[0]
        assert set(event.keys()) >= {
            "type", "agent", "task_id", "task_subject", "handoff",
        }
        # transcript_path / cwd / task_description / session_id /
        # team_name / teammate_name / hook_event_name from stdin must
        # NOT leak into the journal event payload.
        for leaked_field in (
            "transcript_path", "cwd", "task_description",
            "session_id", "team_name", "teammate_name", "hook_event_name",
        ):
            assert leaked_field not in event, (
                f"stdin field {leaked_field!r} leaked into journal "
                f"event — emitter is forwarding too much data."
            )
