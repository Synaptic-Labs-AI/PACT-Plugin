"""
Robustness tests for agent_handoff_emitter.py — exit-code-zero
invariants under malformed/missing/exception-raising input shapes.

AC #8 demands the emitter never propagate a non-zero exit:
- TestMalformedStdin: invalid JSON, empty stdin, missing required fields.
- TestNullMetadata: ``"metadata": null`` shape — ``or {}`` coercion +
  Option E suppression.
- TestUnexpectedExceptionSuppression: outer try/except wrapping main()
  catches RuntimeError from append_event and collapses to exit 0.
"""
import io
import json
from unittest.mock import patch

import pytest

from fixtures.emitter import VALID_HANDOFF, _run_main


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
    """Pin two invariants for the `metadata: null` task.json shape:

    1. `or {}` coercion: `task_data.get("metadata") or {}` collapses
       JSON-null metadata to an empty dict, so subsequent `.get("type")`
       and `.get("handoff")` calls do not raise AttributeError. Without
       this coercion the emitter crashes mid-main(), violating the
       exit-0 invariant.

    2. Option E suppression: with metadata coerced to `{}`,
       `task_metadata.get("handoff")` is falsy and the handoff-presence
       gate suppresses emission. No journal entry is written for a
       null-metadata task — the empty-handoff fire is exactly the
       failure mode Option E was added to prevent.
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

