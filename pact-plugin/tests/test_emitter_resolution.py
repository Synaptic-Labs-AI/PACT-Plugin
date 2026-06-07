"""
Teammate-name resolution tests for agent_handoff_emitter.py —
plus fallback-field stderr discipline.

Both classes share the "field-resolution under partial input" thread:
- TestTeammateNamePrecedence: ``task_data.owner or input_data.teammate_name``
  ordering, including empty-string and whitespace falsy-truthy quirks.
- TestFallbackFieldStderr: missing task_id / task_subject — stderr fires
  exactly once, exit-0 invariant holds, no systemMessage on stdout.
"""
from fixtures.emitter import VALID_HANDOFF, _run_main


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
        """#917 R2 (validate-before-claim): a whitespace-only owner is treated
        as ABSENT, so the stdin teammate_name fallback preserves the handoff.

        A whitespace-only owner ('   ') fails the journal's non-empty-str
        `agent` schema (session_journal._validate_event_schema). Before R2 it
        was truthy in Python's `or`, so it both masked the valid stdin name AND
        would claim the O_EXCL marker then fail append_event — the
        claim-without-write poison the v4.4.10 writability gate only narrowed.
        R2 nulls a whitespace owner so resolution falls through to the valid
        stdin teammate_name and emits with THAT name (preservation-optimal),
        rather than poisoning or losing the handoff.
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
                "owner": "   ",  # whitespace-only → treated as absent (R2)
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        # R2: whitespace owner is treated as falsy → falls back to the valid
        # stdin teammate_name; the handoff is preserved under "proper-agent".
        assert len(calls) == 1
        assert calls[0]["agent"] == "proper-agent", (
            "whitespace-only owner is treated as absent (R2); resolution "
            "falls back to the stdin teammate_name rather than emitting a "
            "schema-invalid whitespace agent (which would poison the marker)."
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

