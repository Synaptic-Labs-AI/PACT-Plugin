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
        # `.get("type")` chain pre-fix.
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
        # Event is still written — metadata absent is NOT a signal-task
        # bypass; it's a missing-metadata happy path with empty handoff.
        assert len(calls) == 1, (
            "metadata:null collapses to empty-metadata path; journal "
            "event still persists with empty handoff."
        )
        assert calls[0]["handoff"] == {}, (
            "post-fix: null metadata collapses to `{}` via `or {}`; "
            "handoff field is empty dict, not None"
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
