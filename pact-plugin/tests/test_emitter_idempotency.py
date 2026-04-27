"""
Idempotency tests for agent_handoff_emitter.py — marker-side dedup family.

Covers the O_EXCL marker mechanism that prevents duplicate emission:
- TestIdempotency: second-fire suppression for the same (team, task_id).
- TestMarkerFailOpen: data-integrity carve-out — marker-subsystem
  errors (PermissionError, ENOSPC, journal-write silent fail) MUST NOT
  drop the journal event. Architect §2.4.
- TestMarkerDirSymlinkGuard: containment carve-out — pre-planted
  symlink at marker_dir path must short-circuit before any os.open.
- TestConcurrentFireRace: atomic test-and-set under 8-thread contention.
"""
import errno
import io
import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.emitter import VALID_HANDOFF, _run_main


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

class TestMarkerDirSymlinkGuard:
    """Pin the symlink-containment pre-check at the marker_dir creation
    site. If `~/.claude/teams/{team}/.agent_handoff_emitted` already
    exists as a symlink, `_already_emitted` MUST return False (fail-open
    emit) without following the symlink — refusing to create the marker
    file at an attacker-controlled location.

    Pairs with the existing fail-open tests in `TestMarkerFailOpen`:
    a corrupted/hostile marker layer never causes silent suppression
    (data-integrity over duplication-prevention) AND never causes a
    write through a redirected path (containment over emit-at-any-cost).
    """

    def test_marker_dir_symlink_returns_false_no_traversal(
        self, tmp_path, monkeypatch
    ):
        """A pre-planted symlink at the marker_dir path must be detected
        and short-circuited. The function returns False (fail-open emit)
        and creates no file at the symlink target."""
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_handoff_emitter import _already_emitted

        team = "pact-test"
        task_id = "t1"

        team_dir = tmp_path / ".claude" / "teams" / team
        team_dir.mkdir(parents=True)
        attacker_target = tmp_path / "attacker_target"
        attacker_target.mkdir()

        marker_dir_path = team_dir / ".agent_handoff_emitted"
        marker_dir_path.symlink_to(attacker_target, target_is_directory=True)

        result = _already_emitted(team, task_id)

        assert result is False, (
            "symlink at marker_dir must fail-open emit (return False); "
            "removing the is_symlink() guard would let the marker create "
            "via the symlink and return False/True based on EEXIST race."
        )
        assert not (attacker_target / task_id).exists(), (
            "no file may be created at the symlink target — the guard "
            "must short-circuit BEFORE any os.open call follows the link."
        )

    def test_marker_dir_ordinary_directory_not_misclassified(
        self, tmp_path, monkeypatch
    ):
        """A pre-existing ordinary (non-symlink) directory at the
        marker_dir path must NOT be flagged by the guard. The first
        call creates the marker file inside it (returns False); the
        second call observes the marker via O_EXCL EEXIST (returns
        True). Confirms the guard discriminates symlink vs ordinary
        dir rather than treating any pre-existing dir as hostile."""
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_handoff_emitter import _already_emitted

        team = "pact-test"
        task_id = "t1"

        marker_dir_path = (
            tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted"
        )
        marker_dir_path.mkdir(parents=True)

        first = _already_emitted(team, task_id)
        second = _already_emitted(team, task_id)

        assert first is False, (
            "first call against an ordinary marker_dir must create the "
            "marker file and return False (winner / emit path)."
        )
        assert second is True, (
            "second call must observe EEXIST on the existing marker and "
            "return True (suppress duplicate emission)."
        )
        assert (marker_dir_path / task_id).exists(), (
            "winner must have created a real marker file inside the "
            "ordinary marker_dir."
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

