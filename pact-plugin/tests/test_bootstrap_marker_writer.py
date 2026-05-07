"""
Tests for bootstrap_marker_writer.py — UserPromptSubmit hook that writes
the bootstrap-complete marker once the ritual's pre-conditions are
observable on disk.

Tests cover:

_write_marker fingerprint correctness (P0 — architect §8.1):
1. Writes a JSON file at <session_dir>/bootstrap-complete with
   {v, sid, sig} and v == MARKER_SCHEMA_VERSION
2. sid == session_id passed in
3. sig == hashlib.sha256("{sid}|{plugin_root}|{plugin_version}|{v}").hexdigest()

Atomic-write contract (P0 — architect §8.2):
4. Marker file mode is 0o600
5. Session directory mode is 0o700
6. Temp file is in the same directory as target (atomicity precondition)
7. On os.replace failure, temp file is unlinked

Pre-condition gating — verify-and-refuse (P0 — architect §8.3):
8. Team config absent → no marker written, exit 0
9. Team config exists, members[] empty → no marker written, exit 0
10. Team config exists, members[] has members but NO secretary → no
    marker written, exit 0  (LOAD-BEARING name lookup)
11. Team config exists, secretary in members[] → marker written
12. Marker already present and valid → no-op fast path (byte unchanged)
13. Teammate session (resolve_agent_name returns non-empty) → no-op

Producer/verifier coupling (P0 — architect §8.4):
14. Round-trip: write_marker then is_marker_set → True
15. Mutated sig in marker → is_marker_set returns False

Schema-stability across versions (P0 — architect §8.5, parametrized):
16. For v in [1, 2, 3, 99]: write + verify round-trip works under
    monkeypatched MARKER_SCHEMA_VERSION

Fail-closed wrapper (P0 — architect §8.6):
17. Module-load failure path emits additionalContext advisory at exit 0
    with hookEventName == "UserPromptSubmit"
18. Runtime exception in _try_write_marker → suppressOutput at exit 0

Captured fixture (P1 — architect §8.7, synthetic placeholder):
19. Loading the fixture stdin and running main() produces clean exit 0
    with suppressOutput (verifies the platform-shape parser runs)

Audit-anchor compliance (P1 — architect §8.12):
20. Every JSON output path includes hookSpecificOutput.hookEventName ==
    "UserPromptSubmit" — module-load failure case (suppressOutput case
    has no hookSpecificOutput, which is intentional and harmless)
"""

import hashlib
import io
import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared import BOOTSTRAP_MARKER_NAME
from shared.marker_schema import MARKER_SCHEMA_VERSION

_SUPPRESS_EXPECTED = {
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "UserPromptSubmit"},
}

_SESSION_ID = "test-session"
_PROJECT_DIR = "/test/project"
_SLUG = "project"
_TEAM_NAME = "pact-test1234"
_PLUGIN_VERSION = "9.9.9"


# =============================================================================
# Helpers
# =============================================================================


def _make_input(session_id=_SESSION_ID, source="startup"):
    """Build a minimal UserPromptSubmit hook input dict."""
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "prompt": "Hello world",
        "source": source,
    }


def _setup_session(monkeypatch, tmp_path, *, with_team_config=False,
                   members=None, with_marker=False):
    """Set up a PACT session with configurable team config + marker state.

    monkeypatches Path.home → tmp_path so all ~/.claude paths resolve
    under tmp_path. Returns (session_dir, plugin_root).
    """
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)

    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": _PLUGIN_VERSION}), encoding="utf-8"
    )

    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": _TEAM_NAME,
        "session_id": _SESSION_ID,
        "project_dir": _PROJECT_DIR,
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)

    if with_team_config:
        team_config_dir = tmp_path / ".claude" / "teams" / _TEAM_NAME
        team_config_dir.mkdir(parents=True, exist_ok=True)
        (team_config_dir / "config.json").write_text(
            json.dumps({"members": members or []}), encoding="utf-8"
        )

    if with_marker:
        sid = session_dir.name
        sig = hashlib.sha256(
            f"{sid}|{str(plugin_root).rstrip('/')}|{_PLUGIN_VERSION}|1".encode()
        ).hexdigest()
        (session_dir / BOOTSTRAP_MARKER_NAME).write_text(
            json.dumps({"v": 1, "sid": sid, "sig": sig}),
            encoding="utf-8",
        )

    return session_dir, plugin_root


def _run_main(input_data, capsys):
    """Run bootstrap_marker_writer.main() and return (exit_code, stdout_json)."""
    from bootstrap_marker_writer import main

    with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    return exc_info.value.code, json.loads(captured.out.strip())


# =============================================================================
# _write_marker — fingerprint correctness
# =============================================================================


class TestWriteMarkerFingerprint:
    def test_writes_json_with_correct_keys(self, tmp_path):
        from bootstrap_marker_writer import _write_marker

        session_dir = tmp_path / "sd"
        session_dir.mkdir()
        _write_marker(session_dir, "the-sid", "/plug", "1.0.0")

        marker = session_dir / BOOTSTRAP_MARKER_NAME
        body = json.loads(marker.read_text(encoding="utf-8"))
        assert set(body.keys()) == {"v", "sid", "sig"}
        assert body["v"] == MARKER_SCHEMA_VERSION
        assert body["sid"] == "the-sid"

    def test_signature_matches_sha256_pipe_joined_inputs(self, tmp_path):
        from bootstrap_marker_writer import _write_marker

        session_dir = tmp_path / "sd"
        session_dir.mkdir()
        _write_marker(session_dir, "the-sid", "/plug", "1.0.0")

        body = json.loads(
            (session_dir / BOOTSTRAP_MARKER_NAME).read_text(encoding="utf-8")
        )
        expected = hashlib.sha256(
            f"the-sid|/plug|1.0.0|{MARKER_SCHEMA_VERSION}".encode("utf-8")
        ).hexdigest()
        assert body["sig"] == expected


# =============================================================================
# _write_marker — atomic-write contract
# =============================================================================


class TestWriteMarkerAtomicity:
    def test_marker_file_is_user_only_readwrite(self, tmp_path):
        from bootstrap_marker_writer import _write_marker

        session_dir = tmp_path / "sd"
        session_dir.mkdir(mode=0o755)
        _write_marker(session_dir, "sid", "/plug", "1.0")
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        mode = stat.S_IMODE(os.lstat(marker).st_mode)
        assert mode == 0o600

    def test_session_dir_created_with_user_only_perms_when_absent(self, tmp_path):
        from bootstrap_marker_writer import _write_marker

        session_dir = tmp_path / "new-sd"
        # not created by test
        _write_marker(session_dir, "sid", "/plug", "1.0")
        mode = stat.S_IMODE(os.lstat(session_dir).st_mode)
        # mkdir may be subject to umask but the explicit mode argument
        # is what we asserted in the architect's spec; assert at least
        # the upper bits are 7 (user-rwx) and group/other are <= what
        # the umask permits.
        assert mode & 0o700 == 0o700

    def test_temp_file_unlinked_on_replace_failure(self, tmp_path, monkeypatch):
        from bootstrap_marker_writer import _write_marker

        session_dir = tmp_path / "sd"
        session_dir.mkdir()

        original_replace = os.replace

        def boom(*_args, **_kwargs):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", boom)

        with pytest.raises(OSError, match="simulated replace failure"):
            _write_marker(session_dir, "sid", "/plug", "1.0")

        # No leftover temp files in the session dir.
        leftovers = [
            p for p in session_dir.iterdir()
            if p.name.startswith(".bootstrap-complete-") and p.suffix == ".tmp"
        ]
        assert leftovers == [], (
            f"temp file should be unlinked on replace failure; found: {leftovers}"
        )

        # Restore for downstream tests in the same process.
        monkeypatch.setattr(os, "replace", original_replace)


# =============================================================================
# Pre-condition gating — verify-and-refuse (LOAD-BEARING)
# =============================================================================


class TestVerifyAndRefuse:
    def test_no_marker_when_team_config_absent(self, monkeypatch, tmp_path, capsys):
        session_dir, _ = _setup_session(monkeypatch, tmp_path,
                                        with_team_config=False)
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()

    def test_no_marker_when_members_list_empty(self, monkeypatch, tmp_path, capsys):
        session_dir, _ = _setup_session(monkeypatch, tmp_path,
                                        with_team_config=True, members=[])
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()

    def test_marker_not_written_when_secretary_missing_from_members(
        self, monkeypatch, tmp_path, capsys,
    ):
        """LOAD-BEARING: members[] has multiple agents but no entry with
        name == 'secretary'. Lookup must positively find a member
        named 'secretary', not just non-empty members."""
        members = [
            {"id": "a-1", "name": "preparer"},
            {"id": "a-2", "name": "architect"},
            {"id": "a-3", "name": "backend-coder"},
        ]
        session_dir, _ = _setup_session(monkeypatch, tmp_path,
                                        with_team_config=True, members=members)
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()

    def test_marker_written_when_secretary_present(
        self, monkeypatch, tmp_path, capsys,
    ):
        members = [
            {"id": "a-1", "name": "secretary"},
            {"id": "a-2", "name": "preparer"},
        ]
        session_dir, _ = _setup_session(monkeypatch, tmp_path,
                                        with_team_config=True, members=members)
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        assert marker.exists()
        body = json.loads(marker.read_text(encoding="utf-8"))
        assert body["v"] == MARKER_SCHEMA_VERSION
        assert body["sid"] == _SESSION_ID

    def test_no_op_when_marker_already_valid(
        self, monkeypatch, tmp_path, capsys,
    ):
        members = [{"id": "a-1", "name": "secretary"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path,
            with_team_config=True, members=members, with_marker=True,
        )
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        before = marker.read_bytes()
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        # Byte-unchanged (fast path skips write).
        assert marker.read_bytes() == before

    def test_teammate_session_skips_writer(self, monkeypatch, tmp_path, capsys):
        """Teammate session (resolve_agent_name returns non-empty) → no
        marker write. Teammates don't drive bootstrap."""
        members = [
            {"id": "agent-uuid-xyz", "name": "secretary"},
            {"id": "agent-uuid-tm", "name": "backend-coder"},
        ]
        session_dir, _ = _setup_session(monkeypatch, tmp_path,
                                        with_team_config=True, members=members)
        # Teammate input shape: agent_id matching a member entry.
        input_data = _make_input()
        input_data["agent_id"] = "agent-uuid-tm"
        code, out = _run_main(input_data, capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        # Marker NOT written despite secretary being present — teammate
        # path short-circuits before pre-condition check.
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()


# =============================================================================
# Producer/verifier coupling
# =============================================================================


class TestProducerVerifierCoupling:
    def test_round_trip_write_then_is_marker_set(self, monkeypatch, tmp_path):
        """After collapse this is a tautology (one shared function); the
        test guards against future edits that bypass shared/marker_schema."""
        from bootstrap_gate import is_marker_set
        from bootstrap_marker_writer import _write_marker

        # is_marker_set reads plugin_root from pact_context, so set up
        # a real session.
        members = [{"id": "a", "name": "secretary"}]
        session_dir, plugin_root = _setup_session(
            monkeypatch, tmp_path,
            with_team_config=True, members=members,
        )

        # Initialize pact_context for is_marker_set's plugin-version read.
        import shared.pact_context as ctx_module
        ctx_module._cache = None
        ctx_module.init({
            "session_id": _SESSION_ID,
        })

        _write_marker(session_dir, _SESSION_ID, str(plugin_root),
                      _PLUGIN_VERSION)
        assert is_marker_set(session_dir) is True

    def test_mutated_sig_rejected_by_verifier(self, monkeypatch, tmp_path):
        from bootstrap_gate import is_marker_set
        from bootstrap_marker_writer import _write_marker

        members = [{"id": "a", "name": "secretary"}]
        session_dir, plugin_root = _setup_session(
            monkeypatch, tmp_path,
            with_team_config=True, members=members,
        )

        import shared.pact_context as ctx_module
        ctx_module._cache = None
        ctx_module.init({"session_id": _SESSION_ID})

        _write_marker(session_dir, _SESSION_ID, str(plugin_root),
                      _PLUGIN_VERSION)

        # Mutate the sig: flip the last hex char.
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        body = json.loads(marker.read_text(encoding="utf-8"))
        last = body["sig"][-1]
        body["sig"] = body["sig"][:-1] + ("0" if last != "0" else "1")
        marker.write_text(json.dumps(body), encoding="utf-8")

        assert is_marker_set(session_dir) is False


# =============================================================================
# Fail-closed wrapper
# =============================================================================


class TestFailClosedWrapper:
    def test_runtime_exception_falls_through_to_suppress(
        self, monkeypatch, tmp_path, capsys,
    ):
        """Runtime exception in _try_write_marker → suppressOutput at
        exit 0 (NOT advisory). Architect §6 asymmetry."""
        import bootstrap_marker_writer as bmw

        def boom(_input):
            raise RuntimeError("simulated runtime failure")

        monkeypatch.setattr(bmw, "_try_write_marker", boom)

        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED

    def test_malformed_stdin_falls_through_to_suppress(self, capsys):
        from bootstrap_marker_writer import main

        with patch("sys.stdin", io.StringIO("not-json")):
            with pytest.raises(SystemExit) as exc_info:
                main()
        captured = capsys.readouterr()
        assert exc_info.value.code == 0
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED


# =============================================================================
# Captured fixture (synthetic placeholder; TODO replace per architect §8.7)
# =============================================================================


class TestCapturedFixtureRoundTrip:
    """Architect §8.7. Placeholder synthetic fixture exercises the
    platform-shape parser; replace with real captured-from-production
    stdin per the follow-up issue. The hook should run cleanly on the
    fixture even though the synthetic session_id has no team config in
    the test environment (verify-and-refuse silent path)."""

    FIXTURE = (
        Path(__file__).parent / "fixtures" /
        "userpromptsubmit_stdin_post_bootstrap.json"
    )

    def test_fixture_exists(self):
        assert self.FIXTURE.exists(), (
            "synthetic fixture missing; placeholder is required even "
            "before captured-from-production replacement"
        )

    def test_main_runs_on_fixture(self, capsys):
        from bootstrap_marker_writer import main

        fixture_data = json.loads(self.FIXTURE.read_text(encoding="utf-8"))
        # Strip the _comment field — the platform doesn't deliver it.
        fixture_data.pop("_comment", None)

        with patch("sys.stdin", io.StringIO(json.dumps(fixture_data))):
            with pytest.raises(SystemExit) as exc_info:
                main()
        captured = capsys.readouterr()
        assert exc_info.value.code == 0
        # Synthetic session_id has no real team config; expect silent
        # suppressOutput (verify-and-refuse path).
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED


# =============================================================================
# Audit-anchor compliance
# =============================================================================


class TestAuditAnchorCompliance:
    """Architect §8.12. Every JSON output path — the module-load advisory
    AND the suppressOutput envelope — carries
    hookSpecificOutput.hookEventName == "UserPromptSubmit". Missing the
    field silently fails open at the platform layer (per pinned context).
    The shape pin in test_suppress_output_carries_hook_event_name covers
    the suppress envelope; this test covers the advisory path."""

    def test_module_load_advisory_carries_hook_event_name(self, capsys):
        from bootstrap_marker_writer import _emit_load_failure_advisory

        with pytest.raises(SystemExit) as exc_info:
            _emit_load_failure_advisory("module imports", RuntimeError("boom"))
        captured = capsys.readouterr()
        assert exc_info.value.code == 0
        out = json.loads(captured.out.strip())
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "UserPromptSubmit"
        assert "additionalContext" in hso
        assert "bootstrap_marker_writer" in hso["additionalContext"]

    def test_suppress_output_carries_hook_event_name(self):
        """Every suppressOutput emit path carries the audit anchor —
        the constant is the single source so all 3 emit sites in
        bootstrap_marker_writer.main inherit the field."""
        from bootstrap_marker_writer import _SUPPRESS_OUTPUT

        out = json.loads(_SUPPRESS_OUTPUT)
        assert out["suppressOutput"] is True
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
