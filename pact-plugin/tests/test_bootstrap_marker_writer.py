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

Captured fixture (P1 — architect §8.7, real captured frame):
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


def _make_input(session_id=_SESSION_ID, source="startup",
                agent_type="pact-orchestrator"):
    """Build a minimal UserPromptSubmit hook input dict.

    #878: the writer now keys lead-detection on the harness-set agent_type via
    is_lead. The default is a LEAD frame (the unmarked case these tests
    historically assumed). Teammate/non-lead tests pass agent_type=<teammate>
    or agent_type=None to exercise the bypass branch.
    """
    data = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "prompt": "Hello world",
        "source": source,
    }
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


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


def _make_posttooluse_agent_input(
    session_id=_SESSION_ID,
    agent_type="pact-orchestrator",
    agent_id="",
    tool_name="Agent",
):
    """Build a PostToolUse(Agent) hook input dict (#975 Option A).

    Mirrors the role-discriminator fields of the captured lead PostToolUse
    frame (tests/fixtures/role_frames.py::lead_posttooluse_taskupdate_completed):
    top-level ``agent_type`` (lead spelling), ``hook_event_name == "PostToolUse"``,
    ``session_id``, a top-level ``agent_id`` (EMPTY STRING on the lead side per
    §10.6 / record 0b8d4fd0), and a ``tool_response`` key (present on every
    PostToolUse frame).

    The only captured PostToolUse frame is a TaskUpdate frame, so this synthetic
    frame closes the Agent-frame-vs-TaskUpdate-frame residual (§10.6 / Coverage
    item 6). It is structurally faithful because the marker WRITE decision is
    tool_name-AGNOSTIC: main() -> _try_write_marker reads only agent_type (via
    is_lead), session_id, and members[] on disk — never tool_name or agent_id.
    The synthetic-vs-real fidelity ceiling (a real Agent-spawn-return frame may
    differ in fields this hook ignores) is a documented unit-level bound; true
    end-to-end effectiveness is deferred to the §9 post-merge fresh-session probe.

    Pass ``agent_type=None`` for a non-lead/plain frame, or a teammate spelling
    (e.g. "pact-backend-coder") to exercise the is_lead bypass. Pass
    ``agent_id=None`` to omit the field entirely (vs the empty-string default).
    """
    data = {
        "hook_event_name": "PostToolUse",
        "session_id": session_id,
        "tool_name": tool_name,
        # tool_input/tool_response are present on every PostToolUse frame; the
        # writer reads neither, but their presence keeps the frame realistic.
        "tool_input": {"description": "spawn", "subagent_type": "pact-secretary"},
        "tool_response": {"content": [{"type": "text", "text": "spawned"}]},
    }
    if agent_type is not None:
        data["agent_type"] = agent_type
    if agent_id is not None:
        data["agent_id"] = agent_id
    return data


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
        """Architect §8.2 requires session_dir mode 0o700 exactly. Setting
        os.umask(0) at test entry ensures the mkdir's mode argument is the
        sole determinant of the final mode bits — the prior `mode & 0o700`
        bitmask-AND assertion silently accepted default-mode mkdir under
        permissive umasks, leaving the explicit mode= kwarg as a false-pin.
        Pin the exact mode so a revert from `mkdir(..., mode=0o700)` to
        `mkdir(...)` (default 0o777) fails the assertion."""
        from bootstrap_marker_writer import _write_marker

        session_dir = tmp_path / "new-sd"
        # not created by test
        old_umask = os.umask(0)
        try:
            _write_marker(session_dir, "sid", "/plug", "1.0")
        finally:
            os.umask(old_umask)
        mode = stat.S_IMODE(os.lstat(session_dir).st_mode)
        assert mode == 0o700, (
            f"session_dir mode must be exactly 0o700 (architect §8.2); "
            f"got {oct(mode)}. Under permissive umask + default mkdir() "
            f"this would silently produce 0o777 — the bitmask-AND form "
            f"of this assertion would pass anyway. The explicit mode= "
            f"kwarg in _write_marker must be the determinant."
        )

    def test_temp_file_unlinked_on_replace_failure(self, tmp_path, monkeypatch):
        from bootstrap_marker_writer import _write_marker

        session_dir = tmp_path / "sd"
        session_dir.mkdir()

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
        """Architect §6 fast-path contract: when is_marker_set returns True,
        _write_marker MUST NOT be called. Spying on _write_marker pins the
        early-return semantic directly. The prior bytes-equality form was
        a false-pin: the digest is deterministic over identical inputs, so
        a revert of the `if is_marker_set(...): return` early-out would
        re-stamp byte-identical content and the bytes oracle would still
        pass. Counter-test cardinality under that revert: this strengthened
        form fails {1} (assert_not_called)."""
        import bootstrap_marker_writer as bmw

        members = [{"id": "a-1", "name": "secretary"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path,
            with_team_config=True, members=members, with_marker=True,
        )
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        before = marker.read_bytes()

        write_calls = []
        original_write = bmw._write_marker

        def spy_write(*args, **kwargs):
            write_calls.append((args, kwargs))
            return original_write(*args, **kwargs)

        monkeypatch.setattr(bmw, "_write_marker", spy_write)

        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        assert write_calls == [], (
            f"_write_marker must NOT be called when marker is already valid "
            f"(architect §6 fast path). Got {len(write_calls)} call(s). "
            f"Reverting the `if is_marker_set(...): return` early-out would "
            f"re-stamp byte-identical content (deterministic digest) — the "
            f"prior bytes-equality assertion was a false-pin."
        )
        # Marker still byte-unchanged (defensive secondary check).
        assert marker.read_bytes() == before

    def test_teammate_session_skips_writer(self, monkeypatch, tmp_path, capsys):
        """Teammate session (non-lead agent_type) → no marker write.
        Teammates don't drive bootstrap.

        #878: lead-detection migrated to is_lead, which reads agent_type. A
        specialist agent_type is not a lead spelling, so the writer bypasses.
        """
        members = [
            {"id": "agent-uuid-xyz", "name": "secretary"},
            {"id": "agent-uuid-tm", "name": "backend-coder"},
        ]
        session_dir, _ = _setup_session(monkeypatch, tmp_path,
                                        with_team_config=True, members=members)
        # Teammate input shape: a non-lead agent_type.
        input_data = _make_input(agent_type="pact-backend-coder")
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
# Captured fixture round-trip — real captured-from-production UserPromptSubmit
# stdin exercises the platform-shape parser end-to-end through main().
# =============================================================================


class TestCapturedFixtureRoundTrip:
    """The fixture is a REAL captured UserPromptSubmit frame (qualified lead
    spelling, Claude Code 2.1.167), carrying ``_meta.capture_method``
    provenance. The hook should run cleanly on it: the captured session_id has
    no team config in the test environment, so main() takes the silent
    verify-and-refuse / no-session-dir path and exits 0."""

    FIXTURE = (
        Path(__file__).parent / "fixtures" /
        "userpromptsubmit_stdin_post_bootstrap.json"
    )

    def test_fixture_exists(self):
        assert self.FIXTURE.exists(), (
            "captured UserPromptSubmit fixture missing"
        )

    def test_main_runs_on_fixture(self, capsys):
        from bootstrap_marker_writer import main

        fixture_data = json.loads(self.FIXTURE.read_text(encoding="utf-8"))
        # Strip the _meta provenance key — the platform doesn't deliver it.
        fixture_data.pop("_meta", None)

        with patch("sys.stdin", io.StringIO(json.dumps(fixture_data))):
            with pytest.raises(SystemExit) as exc_info:
                main()
        captured = capsys.readouterr()
        assert exc_info.value.code == 0
        # Captured session_id has no real team config in the test env; expect
        # silent suppressOutput (verify-and-refuse / no-session-dir path).
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED


# =============================================================================
# Audit-anchor compliance
# =============================================================================


class TestAuditAnchorCompliance:
    """Architect §5.4. Every JSON output path — the module-load advisory
    AND the suppressOutput envelope — carries
    hookSpecificOutput.hookEventName echoing the ACTUAL firing event. Missing
    or stale hookEventName silently fails open at the platform layer (per
    pinned context). Post-#975 the event name is DYNAMIC (resolved from the
    frame's hook_event_name via _resolve_event_name), so these pins assert the
    dual-event behavior — a PostToolUse fire emits "PostToolUse", a
    UserPromptSubmit fire emits "UserPromptSubmit" — plus the VALID safe
    default ("UserPromptSubmit") when the frame is genuinely unavailable."""

    def test_module_load_advisory_carries_hook_event_name(
        self, capsys, monkeypatch,
    ):
        """The import-time advisory best-effort pre-parses stdin for the firing
        event (#975 §5.3(A)); with no JSON on stdin it falls back to the VALID
        safe default "UserPromptSubmit". stdin is isolated to an empty stream so
        the default path is exercised deterministically (capsys does not control
        stdin)."""
        from bootstrap_marker_writer import _emit_load_failure_advisory

        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        with pytest.raises(SystemExit) as exc_info:
            _emit_load_failure_advisory("module imports", RuntimeError("boom"))
        captured = capsys.readouterr()
        assert exc_info.value.code == 0
        out = json.loads(captured.out.strip())
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "UserPromptSubmit"
        assert "additionalContext" in hso
        assert "bootstrap_marker_writer" in hso["additionalContext"]

    @pytest.mark.parametrize(
        "stdin_event,expected",
        [
            ("PostToolUse", "PostToolUse"),
            ("UserPromptSubmit", "UserPromptSubmit"),
        ],
    )
    def test_module_load_advisory_echoes_frame_event(
        self, capsys, monkeypatch, stdin_event, expected,
    ):
        """When the advisory CAN pre-parse stdin, it echoes the frame's actual
        firing event (#975 §5.3(A)) — covering both PostToolUse and
        UserPromptSubmit so a PostToolUse module-load failure is not mislabeled
        "UserPromptSubmit"."""
        from bootstrap_marker_writer import _emit_load_failure_advisory

        frame = json.dumps({"hook_event_name": stdin_event})
        monkeypatch.setattr(sys, "stdin", io.StringIO(frame))
        with pytest.raises(SystemExit) as exc_info:
            _emit_load_failure_advisory("module imports", RuntimeError("boom"))
        captured = capsys.readouterr()
        assert exc_info.value.code == 0
        out = json.loads(captured.out.strip())
        assert out["hookSpecificOutput"]["hookEventName"] == expected

    @pytest.mark.parametrize(
        "event", ["UserPromptSubmit", "PostToolUse"],
    )
    def test_suppress_output_carries_hook_event_name(self, event):
        """The suppressOutput envelope echoes the resolved firing event.
        Post-#975 the _SUPPRESS_OUTPUT module constant is the _suppress_output
        function so every main() emit site builds the envelope with the actual
        event — covering both events here pins that PostToolUse is echoed, not
        a hard-coded "UserPromptSubmit"."""
        from bootstrap_marker_writer import _suppress_output

        out = json.loads(_suppress_output(event))
        assert out["suppressOutput"] is True
        assert out["hookSpecificOutput"]["hookEventName"] == event

    @pytest.mark.parametrize("event", ["UserPromptSubmit", "PostToolUse"])
    @pytest.mark.parametrize("shape", ["advisory", "suppress"])
    def test_every_emit_shape_carries_hook_event_name(
        self, shape, event, capsys, monkeypatch,
    ):
        """Architect §5.4 parametrized over (shape × event).

        The hook produces exactly two JSON output shapes:

        - "advisory": load-failure path via _emit_load_failure_advisory —
          hookSpecificOutput with additionalContext; resolves the event from a
          best-effort stdin pre-parse (#975 §5.3(A)).
        - "suppress": every other exit path via _suppress_output(event) —
          hookSpecificOutput with no other keys.

        Both MUST carry hookSpecificOutput.hookEventName echoing the GIVEN
        firing event — missing/stale silently fails open at the platform layer
        per the pinned context. Parametrizing over both shapes AND both events
        pins that no emit path can be added without echoing the actual event
        (the #975 dual-event AUDIT-ANCHOR), not just a hard-coded value."""
        if shape == "advisory":
            from bootstrap_marker_writer import _emit_load_failure_advisory
            frame = json.dumps({"hook_event_name": event})
            monkeypatch.setattr(sys, "stdin", io.StringIO(frame))
            with pytest.raises(SystemExit):
                _emit_load_failure_advisory("module imports", RuntimeError("x"))
            captured = capsys.readouterr()
            out = json.loads(captured.out.strip())
        elif shape == "suppress":
            from bootstrap_marker_writer import _suppress_output
            out = json.loads(_suppress_output(event))
        else:  # pragma: no cover
            pytest.fail(f"unknown shape param: {shape}")

        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"shape={shape} emit MUST carry hookSpecificOutput; missing "
            f"the field silently fails open at the platform layer."
        )
        assert hso.get("hookEventName") == event, (
            f"shape={shape} emit MUST echo hookEventName=={event!r}; "
            f"got {hso!r}"
        )


# =============================================================================
# Adversarial team_config.json shapes
# =============================================================================


class TestAdversarialTeamConfig:
    """Adversarial inputs at the team-config-read surface. _team_has_secretary
    must return False (silent — the sibling bootstrap_prompt_gate owns the
    user-visible advisory) on every malformed shape, and the writer must
    NOT raise. Architect §13 risk #2 (members[] shape change upstream) is
    mitigated by the lookup catching shape drift; these tests pin the
    concrete shapes that count as "drift" today."""

    def _write_team_config(self, tmp_path, body):
        team_dir = tmp_path / ".claude" / "teams" / _TEAM_NAME
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "config.json").write_text(body, encoding="utf-8")

    def test_malformed_json_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_team_config(tmp_path, "{ this is not json")
        from bootstrap_marker_writer import _team_has_secretary
        assert _team_has_secretary(_TEAM_NAME) is False

    def test_non_object_top_level_no_marker_written(
        self, monkeypatch, tmp_path, capsys,
    ):
        """Top-level JSON array (not object) in team_config.json. The
        architect-§6 invariant is the verify-and-refuse contract: under
        any adversarial team_config shape, no marker may land on disk.
        The prior form of this test asserted a behavioral disjunction
        (`_team_has_secretary` returns False OR raises AttributeError),
        which pinned an implementation detail rather than the contract
        — under any future hardening it would silently switch branches.

        This reshape calls `main()` end-to-end and asserts the contract
        directly: clean exit 0 + suppressOutput envelope + no marker
        file on disk. The outer main() try/except absorbs whatever the
        helper raises (AttributeError today; nothing if hardened with
        isinstance(data, dict)) — both implementations satisfy the
        single contract."""
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=False,
        )
        # Overwrite team_config.json with a top-level JSON array.
        team_dir = tmp_path / ".claude" / "teams" / _TEAM_NAME
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "config.json").write_text(
            '["not", "an", "object"]', encoding="utf-8",
        )

        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists(), (
            "verify-and-refuse contract: under top-level non-object "
            "team_config.json, NO marker may land on disk regardless of "
            "whether _team_has_secretary returns False or raises "
            "AttributeError. The architect-§6 contract is end-to-end."
        )

    def test_members_key_absent_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_team_config(tmp_path, json.dumps({"name": _TEAM_NAME}))
        from bootstrap_marker_writer import _team_has_secretary
        assert _team_has_secretary(_TEAM_NAME) is False

    def test_members_not_a_list_returns_false(self, monkeypatch, tmp_path):
        """members is a dict instead of a list — the L126-127 isinstance
        guard rejects, returning False."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_team_config(
            tmp_path, json.dumps({"members": {"name": "secretary"}})
        )
        from bootstrap_marker_writer import _team_has_secretary
        assert _team_has_secretary(_TEAM_NAME) is False

    def test_member_entry_not_a_dict_skipped(self, monkeypatch, tmp_path):
        """members[] contains non-dict entries (str, int, None) interleaved
        with dicts; non-dicts are skipped via L129 isinstance(member, dict)
        guard, then the dict member with name=='secretary' is matched."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_team_config(tmp_path, json.dumps({
            "members": ["string-entry", 42, None, {"name": "secretary"}]
        }))
        from bootstrap_marker_writer import _team_has_secretary
        assert _team_has_secretary(_TEAM_NAME) is True

    def test_member_without_name_key_skipped(self, monkeypatch, tmp_path):
        """The lookup is name-keyed, NOT agentType-keyed. A member with
        only agentType=='secretary' but no 'name' field is NOT matched.
        Documents the brittleness flagged in the coder handoff
        open_questions: if the secretary's name field is renamed but
        agentType is preserved, the writer refuses silently."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_team_config(tmp_path, json.dumps({
            "members": [{"id": "a-1"}, {"id": "a-2", "agentType": "secretary"}]
        }))
        from bootstrap_marker_writer import _team_has_secretary
        assert _team_has_secretary(_TEAM_NAME) is False

    def test_member_name_non_string_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_team_config(tmp_path, json.dumps({
            "members": [{"name": 12345}, {"name": ["secretary"]}]
        }))
        from bootstrap_marker_writer import _team_has_secretary
        # Non-string name fails the equality check against the literal
        # "secretary"; helper returns False without raising.
        assert _team_has_secretary(_TEAM_NAME) is False

    def test_team_config_unreadable_returns_false(self, monkeypatch, tmp_path):
        """Permission-denied on read → caught by the OSError clause."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team_dir = tmp_path / ".claude" / "teams" / _TEAM_NAME
        team_dir.mkdir(parents=True, exist_ok=True)
        cfg = team_dir / "config.json"
        cfg.write_text(json.dumps({"members": [{"name": "secretary"}]}),
                       encoding="utf-8")
        os.chmod(cfg, 0o000)
        try:
            from bootstrap_marker_writer import _team_has_secretary
            assert _team_has_secretary(_TEAM_NAME) is False
        finally:
            # Restore so tmp_path teardown succeeds.
            os.chmod(cfg, 0o600)


# =============================================================================
# Adversarial plugin.json shapes (writer's _read_plugin_version)
# =============================================================================


class TestAdversarialPluginJson:
    """The writer reads plugin.json's `version` field to compute the
    fingerprint. Adversarial shapes must yield empty-string ('') so
    _try_write_marker short-circuits at L231-232 and no marker is written."""

    def test_plugin_root_empty_returns_empty_version(self):
        from bootstrap_marker_writer import _read_plugin_version
        assert _read_plugin_version("") == ""

    def test_plugin_json_missing_returns_empty_version(self, tmp_path):
        from bootstrap_marker_writer import _read_plugin_version
        plugin_root = tmp_path / "plugin"
        # No .claude-plugin/ dir → plugin.json doesn't exist.
        assert _read_plugin_version(str(plugin_root)) == ""

    def test_plugin_json_malformed_returns_empty_version(self, tmp_path):
        from bootstrap_marker_writer import _read_plugin_version
        plugin_root = tmp_path / "plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            "{ malformed", encoding="utf-8"
        )
        assert _read_plugin_version(str(plugin_root)) == ""

    def test_plugin_json_without_version_key_returns_empty(self, tmp_path):
        from bootstrap_marker_writer import _read_plugin_version
        plugin_root = tmp_path / "plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "pact"}), encoding="utf-8"
        )
        assert _read_plugin_version(str(plugin_root)) == ""

    def test_plugin_json_version_empty_string_short_circuits_write(
        self, monkeypatch, tmp_path, capsys,
    ):
        """Empty version string → _try_write_marker's L231-232 short-circuit
        fires before _write_marker. No marker on disk."""
        members = [{"name": "secretary"}]
        session_dir, plugin_root = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        # Overwrite plugin.json with an empty version string.
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": ""}), encoding="utf-8"
        )
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()


# =============================================================================
# Pre-condition edge cases
# =============================================================================


class TestPreConditionEdgeCases:
    """Edge cases the coder's smoke suite touches but doesn't fully pin."""

    def test_team_name_empty_string_returns_false(self):
        """L116-117: empty team_name short-circuits before file read."""
        from bootstrap_marker_writer import _team_has_secretary
        assert _team_has_secretary("") is False

    def test_team_name_missing_directory_returns_false(self, monkeypatch, tmp_path):
        """team_name set but ~/.claude/teams/{name}/config.json doesn't
        exist → OSError on read → False."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from bootstrap_marker_writer import _team_has_secretary
        assert _team_has_secretary("nonexistent-team") is False

    def test_session_dir_unresolvable_skips_write(
        self, monkeypatch, tmp_path, capsys,
    ):
        """pact_context.get_session_dir() returns falsy → L208-210 returns
        without proceeding. No marker, no exception."""
        import shared.pact_context as ctx_module
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Force pact_context to return empty session_dir.
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: "")
        monkeypatch.setattr(ctx_module, "init", lambda _input: None)
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED


# =============================================================================
# Symlink regression: pin os.replace stdlib semantic on the marker target
# =============================================================================


class TestSymlinkRegression:
    """Pin current behavior at the symlink/marker boundary. Architect §13
    does NOT list symlink hardening as a writer-side concern; the verifier
    (bootstrap_gate.is_marker_set) has S2/S4 defenses that reject markers
    living under symlinked ancestors or symlinked leaves. These tests
    document the producer-side stdlib semantics so a future change to
    either surface is visible in the test diff.

    NOT a hardening test — a regression-pin test. Out-of-scope for this
    PR per team-lead's clarification on the teachback's symlink judgment
    call (most_likely_wrong)."""

    def test_os_replace_clobbers_symlink_target_with_regular_file(self, tmp_path):
        """When the marker target is a pre-existing symlink to elsewhere,
        os.replace(tmp, target) atomically substitutes the symlink with a
        regular file — the original symlink-pointed-to file is unchanged.
        This is the cross-platform stdlib semantic per `os.replace` docs.

        Consequence for the writer: a same-user attacker who plants a
        symlink at <session_dir>/bootstrap-complete pointing to /etc/hosts
        does NOT get /etc/hosts overwritten; the writer's os.replace just
        clobbers the symlink. The verifier's S2 defense
        (bootstrap_gate.is_marker_set lstat + S_ISREG) is moot for the
        leaf after this PR's writer runs — the file is regular by
        construction. S4 (ancestor symlink) is still the verifier's
        defense and is unaffected by this PR."""
        from bootstrap_marker_writer import _write_marker

        session_dir = tmp_path / "sd"
        session_dir.mkdir()

        # Plant a pre-existing symlink at the marker path.
        external_target = tmp_path / "external.txt"
        external_target.write_text("DO NOT OVERWRITE", encoding="utf-8")
        marker_path = session_dir / BOOTSTRAP_MARKER_NAME
        marker_path.symlink_to(external_target)
        assert marker_path.is_symlink()

        _write_marker(session_dir, "sid", "/plug", "1.0")

        # Symlink replaced with regular file; external file untouched.
        assert not marker_path.is_symlink()
        assert marker_path.is_file()
        assert external_target.read_text(encoding="utf-8") == "DO NOT OVERWRITE"
        body = json.loads(marker_path.read_text(encoding="utf-8"))
        assert body["sid"] == "sid"

    def test_verifier_rejects_marker_under_ancestor_symlink(
        self, monkeypatch, tmp_path,
    ):
        """S4 defense: if any ancestor of session_dir is a symlink,
        is_marker_set returns False even if the marker is bit-perfect.
        Pins this behavior survives the constants relocation in this PR
        (the verifier still imports MARKER_SCHEMA_VERSION/expected_marker_signature
        from shared.marker_schema after the relocation)."""
        from bootstrap_gate import is_marker_set
        from bootstrap_marker_writer import _write_marker

        real_root = tmp_path / "real"
        real_root.mkdir()
        session_dir = real_root / _SESSION_ID
        session_dir.mkdir()

        plugin_root = tmp_path / "plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": _PLUGIN_VERSION}), encoding="utf-8"
        )

        _write_marker(session_dir, _SESSION_ID, str(plugin_root),
                      _PLUGIN_VERSION)

        import shared.pact_context as ctx_module
        ctx_module._cache = {
            "plugin_root": str(plugin_root),
            "session_id": _SESSION_ID,
            "session_dir": str(session_dir),
            "team_name": _TEAM_NAME,
            "project_dir": _PROJECT_DIR,
        }

        # Direct access — verifier accepts.
        assert is_marker_set(session_dir) is True
        # Through ancestor symlink — verifier rejects per S4.
        sym_root = tmp_path / "sym"
        sym_root.symlink_to(real_root)
        symlinked_session = sym_root / _SESSION_ID
        assert is_marker_set(symlinked_session) is False


# =============================================================================
# Oversized marker payload
# =============================================================================


class TestOversizedMarker:
    """The MARKER_MAX_BYTES=256 cap is enforced inside _write_marker before
    write. A future schema growth that outpaces the cap raises ValueError
    pre-write so no malformed marker lands on disk."""

    def test_oversized_payload_raises_value_error(self, tmp_path, monkeypatch):
        from bootstrap_marker_writer import _write_marker
        import bootstrap_marker_writer as bmw
        import shared.marker_schema as ms

        # Patch BOTH the SSOT module and the writer's bound reference
        # (the writer imported the symbol at module-load time).
        monkeypatch.setattr(ms, "MARKER_MAX_BYTES", 32)
        monkeypatch.setattr(bmw, "MARKER_MAX_BYTES", 32)

        session_dir = tmp_path / "sd"
        session_dir.mkdir()
        with pytest.raises(ValueError, match="exceeds MARKER_MAX_BYTES"):
            _write_marker(session_dir, "long-" * 20, "/plug", "1.0")

        # No partial marker on disk.
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()
        # No leftover temp files either.
        leftovers = [
            p for p in session_dir.iterdir()
            if p.name.startswith(".bootstrap-complete-")
        ]
        assert leftovers == []

    def test_main_swallows_oversized_into_suppress(
        self, monkeypatch, tmp_path, capsys,
    ):
        """When _write_marker raises, main()'s outer try/except converts
        to suppressOutput (runtime fail-OPEN per architect §6)."""
        members = [{"name": "secretary"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        import bootstrap_marker_writer as bmw
        import shared.marker_schema as ms
        monkeypatch.setattr(ms, "MARKER_MAX_BYTES", 8)
        monkeypatch.setattr(bmw, "MARKER_MAX_BYTES", 8)

        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()


# =============================================================================
# Tampered-marker recovery: writer overwrites invalid markers next prompt
# =============================================================================


class TestTamperedMarkerRecovery:
    """A marker tampered post-write fails verification; on the next
    UserPromptSubmit, the writer re-runs is_marker_set, sees False, and
    re-stamps a fresh valid marker. This is the steady-state self-heal
    promise of the writer-FIRST hook design (architect §10 + persona §2
    Re-invoke clause)."""

    @pytest.mark.parametrize("mutation", [
        "wrong_v",
        "wrong_sid",
        "missing_sig",
        "extra_key",
        "non_object",
        "empty_object",
    ])
    def test_invalid_marker_overwritten_on_next_prompt(
        self, mutation, monkeypatch, tmp_path, capsys,
    ):
        members = [{"name": "secretary"}]
        session_dir, plugin_root = _setup_session(
            monkeypatch, tmp_path,
            with_team_config=True, members=members,
        )
        import shared.pact_context as ctx_module

        marker = session_dir / BOOTSTRAP_MARKER_NAME

        sid = session_dir.name
        valid_sig = hashlib.sha256(
            f"{sid}|{str(plugin_root).rstrip('/')}|{_PLUGIN_VERSION}|1".encode()
        ).hexdigest()

        if mutation == "wrong_v":
            marker.write_text(
                json.dumps({"v": 999, "sid": sid, "sig": valid_sig}),
                encoding="utf-8",
            )
        elif mutation == "wrong_sid":
            marker.write_text(
                json.dumps({"v": 1, "sid": "wrong-sid", "sig": valid_sig}),
                encoding="utf-8",
            )
        elif mutation == "missing_sig":
            marker.write_text(json.dumps({"v": 1, "sid": sid}),
                              encoding="utf-8")
        elif mutation == "extra_key":
            marker.write_text(
                json.dumps({"v": 1, "sid": sid, "sig": valid_sig,
                            "extra": "hi"}),
                encoding="utf-8",
            )
        elif mutation == "non_object":
            marker.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        elif mutation == "empty_object":
            marker.write_text(json.dumps({}), encoding="utf-8")

        # Confirm verifier rejects the planted invalid marker.
        from bootstrap_gate import is_marker_set
        ctx_module._cache = None
        ctx_module.init({"session_id": _SESSION_ID})
        assert is_marker_set(session_dir) is False, (
            f"baseline: verifier should reject mutation={mutation}"
        )

        # Run the writer; it should overwrite with a valid marker.
        ctx_module._cache = None
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED

        # Marker is now valid.
        ctx_module._cache = None
        ctx_module.init({"session_id": _SESSION_ID})
        assert is_marker_set(session_dir) is True


# =============================================================================
# Subprocess integration: 1 happy-path round-trip
# =============================================================================


class TestSubprocessIntegration:
    """One subprocess test for the happy path. In-process tests via import
    + monkeypatch can mask sys.path / module-load issues that only surface
    when the platform spawns the hook as a fresh process. Per team-lead
    teachback acceptance: 1 test is the right scope; the bulk stays
    in-process."""

    def test_subprocess_happy_path_writes_marker(self, tmp_path):
        import subprocess

        home = tmp_path
        slug = "testproj"
        session_id = "subproc-session-id"
        team_name = "pact-subproc01"
        plugin_version = "9.9.9-subproc"

        session_dir = home / ".claude" / "pact-sessions" / slug / session_id
        session_dir.mkdir(parents=True)

        plugin_root = home / "plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": plugin_version}), encoding="utf-8"
        )

        team_dir = home / ".claude" / "teams" / team_name
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"members": [{"id": "a-1", "name": "secretary"}]}),
            encoding="utf-8",
        )

        ctx = session_dir / "pact-session-context.json"
        ctx.write_text(json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": f"/tmp/{slug}",
            "plugin_root": str(plugin_root),
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")

        hook_path = (
            Path(__file__).parent.parent / "hooks" /
            "bootstrap_marker_writer.py"
        )
        assert hook_path.exists(), f"writer hook missing at {hook_path}"

        # #878: lead frame (agent_type) so the is_lead-gated writer runs.
        stdin_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "prompt": "first real prompt",
            "source": "startup",
            "agent_type": "pact-orchestrator",
        })

        env = os.environ.copy()
        env["HOME"] = str(home)
        # pact_context.init reads CLAUDE_PROJECT_DIR to compute the slug
        # that the session_dir lives under (~/.claude/pact-sessions/{slug}/
        # {session_id}). Without it, get_session_dir() returns '' and the
        # writer silently no-ops at L208-210.
        env["CLAUDE_PROJECT_DIR"] = f"/tmp/{slug}"

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_payload,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
            timeout=10,
        )
        assert result.returncode == 0, (
            f"subprocess exit non-zero. stderr={result.stderr!r} "
            f"stdout={result.stdout!r}"
        )
        # suppressOutput envelope (post-audit-fix shape includes
        # hookSpecificOutput.hookEventName).
        out = json.loads(result.stdout.strip())
        assert out["suppressOutput"] is True
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

        marker = session_dir / BOOTSTRAP_MARKER_NAME
        assert marker.exists(), "marker should be written via subprocess"
        body = json.loads(marker.read_text(encoding="utf-8"))
        assert body["v"] == 1
        assert body["sid"] == session_id
        expected_sig = hashlib.sha256(
            f"{session_id}|{plugin_root}|{plugin_version}|1".encode()
        ).hexdigest()
        assert body["sig"] == expected_sig


# =============================================================================
# Constants relocation regression sweep
# =============================================================================


class TestConstantsRelocationRegression:
    """Pin that shared/marker_schema.py is the SOLE source of truth for
    MARKER_SCHEMA_VERSION / MARKER_MAX_BYTES / expected_marker_signature.

    Expected sources today:
    - shared/marker_schema.py — the SSOT.

    The earlier parallel constant in shared/dispatch_helpers.py:79-81 was
    removed in the #673 cleanup; the test now enforces the single-source
    invariant strictly. A new file defining MARKER_SCHEMA_VERSION = N or
    MARKER_MAX_BYTES = N inside hooks/ should make this test fail until
    the new definition is justified (and added to the expected set) or
    removed."""

    HOOKS_ROOT = Path(__file__).parent.parent / "hooks"

    def test_marker_schema_version_defined_only_in_marker_schema(self):
        import re
        pat = re.compile(r"^MARKER_SCHEMA_VERSION\s*=\s*\d+", re.MULTILINE)
        offenders = []
        for py in self.HOOKS_ROOT.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if pat.search(text):
                offenders.append(str(py.relative_to(self.HOOKS_ROOT)))
        offenders_str = sorted(offenders)
        assert offenders_str == ["shared/marker_schema.py"], (
            f"Unexpected MARKER_SCHEMA_VERSION definitions in hooks/: "
            f"{offenders_str}. shared/marker_schema.py is the SSOT; any "
            f"other definition (including a return of the old "
            f"shared/dispatch_helpers.py parallel constant removed in "
            f"the #673 cleanup) is a regression."
        )

    def test_marker_max_bytes_defined_only_in_marker_schema(self):
        import re
        pat = re.compile(r"^MARKER_MAX_BYTES\s*=\s*\d+", re.MULTILINE)
        offenders = []
        for py in self.HOOKS_ROOT.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if pat.search(text):
                offenders.append(str(py.relative_to(self.HOOKS_ROOT)))
        offenders_str = sorted(offenders)
        assert offenders_str == ["shared/marker_schema.py"], (
            f"Unexpected MARKER_MAX_BYTES definitions in hooks/: "
            f"{offenders_str}. Only shared/marker_schema.py is permitted."
        )

    def test_expected_marker_signature_defined_only_in_marker_schema(self):
        import re
        pat = re.compile(r"^def\s+expected_marker_signature\s*\(", re.MULTILINE)
        offenders = []
        for py in self.HOOKS_ROOT.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if pat.search(text):
                offenders.append(str(py.relative_to(self.HOOKS_ROOT)))
        offenders_str = sorted(offenders)
        assert offenders_str == ["shared/marker_schema.py"], (
            f"Unexpected expected_marker_signature definitions: "
            f"{offenders_str}. Only shared/marker_schema.py is permitted."
        )


# =============================================================================
# Captured-fixture parity: the real fixture carries the platform fields a
# UserPromptSubmit frame actually delivers (and omits the ones it does not).
# =============================================================================


class TestFixtureShapeParity:
    """The fixture is a real captured-from-production UserPromptSubmit frame.
    Pin the platform fields it must carry so a partial regression of the
    platform schema is visible.

    Pinned shape: hook_event_name, session_id, prompt, permission_mode — the
    fields a real UserPromptSubmit frame delivers (session_id is the one
    pact_context.init() reads to resolve the session path). NOTE: the prior
    synthetic placeholder asserted a ``source`` field here, but ``source`` is a
    SessionStart-only field — a real UserPromptSubmit frame does NOT carry it.
    That synthetic-vs-real discrepancy is exactly what this fixture promotion
    corrects, so the negative is pinned below."""

    FIXTURE = (
        Path(__file__).parent / "fixtures" /
        "userpromptsubmit_stdin_post_bootstrap.json"
    )

    def test_fixture_contains_documented_platform_fields(self):
        data = json.loads(self.FIXTURE.read_text(encoding="utf-8"))
        # _meta is our provenance annotation; the platform doesn't deliver it.
        data.pop("_meta", None)
        for required in ("hook_event_name", "session_id", "prompt",
                         "permission_mode"):
            assert required in data, (
                f"fixture missing platform field {required!r}; the captured "
                f"UserPromptSubmit fixture must mirror the real platform "
                f"stdin shape."
            )
        assert data["hook_event_name"] == "UserPromptSubmit"
        # `source` is SessionStart-only; a real UserPromptSubmit frame omits it.
        assert "source" not in data


# =============================================================================
# Marker-absent path interaction: writer self-heals or refuses based on team-config state
# =============================================================================


class TestClearPathInteraction:
    """Persona §2 Re-invoke clause. The writer's self-healing promise
    covers two distinct marker-absent states:

    1. Marker absent, team config preserved → next-prompt writer rewrites
       (steady-state self-heal). This IS the path /clear takes:
       `session_init._clear_bootstrap_marker` removes ONLY the marker;
       team config persists. No orchestrator action required.
    2. Marker AND team config both absent (independent removal of team
       config; NOT a /clear semantic) → writer refuses (verify-and-refuse
       silent path) until TeamCreate runs again. The persona §2 Re-invoke
       clause directs the orchestrator to re-execute the bootstrap ritual
       in that case."""

    def test_marker_only_zap_self_heals_on_next_prompt(
        self, monkeypatch, tmp_path, capsys,
    ):
        """Steady-state path (also the actual /clear path): team config
        intact, marker deleted. Writer rewrites a valid marker on the next
        prompt without orchestrator
        intervention."""
        members = [{"name": "secretary"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path,
            with_team_config=True, members=members, with_marker=True,
        )
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        assert marker.exists()
        marker.unlink()

        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        # Self-heal: marker rewritten without manual orchestrator action.
        assert marker.exists()

    def test_team_config_absent_refuses_silently(
        self, monkeypatch, tmp_path, capsys,
    ):
        """Team config absent (independent removal — NOT a /clear semantic):
        marker AND team config both gone. Writer refuses (verify-and-refuse
        silent path); persona §2 Re-invoke directive owns the
        orchestrator-driven TeamCreate re-execution."""
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=False,
        )
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out == _SUPPRESS_EXPECTED
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()


# =============================================================================
# Atomicity: temp-file location is in same dir as target (cross-FS-safe replace)
# =============================================================================


class TestAtomicityTempFileLocation:
    """Architect §5: tempfile.mkstemp(dir=session_dir) ensures os.replace
    is atomic. A cross-FS replace degrades to copy+unlink, breaking the
    atomicity contract. Pin the same-dir invariant by spying on mkstemp."""

    def test_tempfile_created_in_session_dir(self, tmp_path, monkeypatch):
        from bootstrap_marker_writer import _write_marker
        import bootstrap_marker_writer as bmw

        session_dir = tmp_path / "sd"
        session_dir.mkdir()

        captured = {}
        original_mkstemp = bmw.tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            captured["dir"] = kwargs.get("dir")
            captured["prefix"] = kwargs.get("prefix")
            captured["suffix"] = kwargs.get("suffix")
            return original_mkstemp(*args, **kwargs)

        monkeypatch.setattr(bmw.tempfile, "mkstemp", spy_mkstemp)

        _write_marker(session_dir, "sid", "/plug", "1.0")

        assert captured["dir"] == str(session_dir)
        assert captured["prefix"] == ".bootstrap-complete-"
        assert captured["suffix"] == ".tmp"


class TestSubprocessSelfHeal:
    """Subprocess integration for the UserPromptSubmit self-heal: the
    TestSubprocessIntegration scaffold MINUS the context file. A lead-frame
    run must HEAL the missing pact-session-context.json (assert exists +
    content) while STILL refusing the marker when the team-config
    pre-conditions are unmet — heal never forges bootstrap completion.

    Self-masker rule: marker_writer exits 0 on every path, so health is
    asserted via stdout envelope + on-disk effects, never via returncode
    alone.
    """

    def test_lead_run_heals_missing_context_but_refuses_marker(self, tmp_path):
        import subprocess

        home = tmp_path
        slug = "healproj"
        # Hex prefix → deterministic generated team name "pact-deadbeef".
        session_id = "deadbeef-4242-4242-4242-deadbeef4242"

        plugin_root = home / "plugin"
        plugin_root.mkdir(parents=True)

        # Session dir intentionally NOT created; context file ABSENT;
        # team config for "pact-deadbeef" intentionally NOT created.
        session_dir = home / ".claude" / "pact-sessions" / slug / session_id
        ctx = session_dir / "pact-session-context.json"

        hook_path = (
            Path(__file__).parent.parent / "hooks" /
            "bootstrap_marker_writer.py"
        )
        assert hook_path.exists(), f"writer hook missing at {hook_path}"

        stdin_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "prompt": "first prompt after session_init crashed",
            "source": "startup",
            "agent_type": "pact-orchestrator",
        })

        env = os.environ.copy()
        env["HOME"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)  # force the HOME/.claude fallback
        env["CLAUDE_PROJECT_DIR"] = f"/tmp/{slug}"
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_payload,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
            timeout=10,
        )

        # Content assertion FIRST (self-masker rule), rc second.
        out = json.loads(result.stdout.strip())
        assert out["suppressOutput"] is True
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )

        # Healed: context file re-created with session_init-parity content.
        assert ctx.exists(), "self-heal should re-create the context file"
        content = json.loads(ctx.read_text(encoding="utf-8"))
        assert content["team_name"] == "pact-deadbeef"
        assert content["session_id"] == session_id
        assert content["project_dir"] == f"/tmp/{slug}"
        assert content["plugin_root"] == str(plugin_root)
        assert content["started_at"]

        # Heal != forged bootstrap: the team-config pre-condition is unmet
        # (no ~/.claude/teams/pact-deadbeef/config.json with a secretary),
        # so the marker MUST NOT be written.
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        assert not marker.exists(), (
            "heal must not forge bootstrap completion — marker pre-conditions "
            "are unmet"
        )

    def test_teammate_run_does_not_heal(self, tmp_path):
        """Same scaffold, teammate frame → no heal (is_lead gate, #877).

        Single-variable discipline: the env (including CLAUDE_PLUGIN_ROOT
        and the plugin_root dir on disk) mirrors the lead positive control
        above EXACTLY, so agent_type is the ONLY input that differs — the
        no-heal outcome is attributable to the is_lead gate alone, not to
        an incidentally missing env var."""
        import subprocess

        home = tmp_path
        slug = "healproj"
        session_id = "deadbeef-4242-4242-4242-deadbeef4242"

        plugin_root = home / "plugin"
        plugin_root.mkdir(parents=True)

        session_dir = home / ".claude" / "pact-sessions" / slug / session_id
        ctx = session_dir / "pact-session-context.json"

        hook_path = (
            Path(__file__).parent.parent / "hooks" /
            "bootstrap_marker_writer.py"
        )

        stdin_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "prompt": "teammate frame",
            "source": "startup",
            "agent_type": "pact-backend-coder",
        })

        env = os.environ.copy()
        env["HOME"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env["CLAUDE_PROJECT_DIR"] = f"/tmp/{slug}"
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_payload,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
            timeout=10,
        )

        out = json.loads(result.stdout.strip())
        assert out["suppressOutput"] is True
        assert result.returncode == 0
        assert not ctx.exists(), "teammate frame must never heal (#877)"


class TestConcurrentTwoHealerRace:
    """TRUE parallel-process model of the two-healer race: on the SAME
    UserPromptSubmit, the platform runs bootstrap_marker_writer and
    bootstrap_prompt_gate in parallel; with the context file ABSENT, BOTH
    are eligible to heal. persist_context is atomic (mkstemp + rename) and
    both healers compute identical content except started_at, so NO
    interleaving may produce a torn/malformed file, a crashed hook, or a
    forged marker — and the assertions below are winner-agnostic.

    Scope honesty: a single run cannot force a specific interleaving (the
    OS schedules the two processes); what this pins is that the REAL
    parallel composition holds the invariants on every observed schedule.
    Content parity across healers is pinned deterministically by the
    sequential-equivalence unit test
    (test_pact_context.py::TestHealContextIfMissing::
    test_sequential_heals_equivalent_content).

    Self-masker rule: both hooks exit 0 on every path — health asserted on
    stdout envelopes + on-disk effects, never returncode alone.
    """

    def test_parallel_healers_yield_one_wellformed_context_no_marker(
            self, tmp_path):
        import subprocess

        home = tmp_path
        slug = "raceproj"
        # Hex prefix → deterministic generated team name "pact-deadbeef".
        session_id = "deadbeef-5555-6666-7777-deadbeef8888"

        plugin_root = home / "plugin"
        plugin_root.mkdir(parents=True)

        # Context ABSENT; no team config (marker pre-conditions unmet, so
        # the writer must refuse the marker on every schedule).
        session_dir = home / ".claude" / "pact-sessions" / slug / session_id
        ctx = session_dir / "pact-session-context.json"

        hooks_dir = Path(__file__).parent.parent / "hooks"
        writer_path = hooks_dir / "bootstrap_marker_writer.py"
        gate_path = hooks_dir / "bootstrap_prompt_gate.py"
        assert writer_path.exists() and gate_path.exists()

        stdin_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "prompt": "first prompt after session_init crashed",
            "agent_type": "pact-orchestrator",
        }).encode("utf-8")

        env = os.environ.copy()
        env["HOME"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env["CLAUDE_PROJECT_DIR"] = f"/tmp/{slug}"
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)

        # Start BOTH processes, feed both stdins, THEN reap — the payload
        # is far below the pipe buffer, so the writes don't block and the
        # two hooks genuinely execute concurrently (a communicate() on the
        # first process before spawning work on the second would serialize
        # them and silently test the sequential case instead).
        procs = [
            subprocess.Popen(
                [sys.executable, str(path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=str(home),
            )
            for path in (writer_path, gate_path)
        ]
        for proc in procs:
            proc.stdin.write(stdin_payload)
            proc.stdin.close()
        outs = []
        for proc in procs:
            out = proc.stdout.read().decode("utf-8")
            err = proc.stderr.read().decode("utf-8")
            rc = proc.wait(timeout=15)
            outs.append((rc, out, err))

        writer_rc, writer_out, writer_err = outs[0]
        gate_rc, gate_out, gate_err = outs[1]

        # Both hooks emitted their healthy envelopes (no crash on any
        # schedule); content first, rc alongside.
        writer_json = json.loads(writer_out.strip())
        assert writer_json["suppressOutput"] is True
        assert (writer_json["hookSpecificOutput"]["hookEventName"]
                == "UserPromptSubmit")
        assert writer_rc == 0, f"writer stderr={writer_err!r}"

        gate_json = json.loads(gate_out.strip())
        gate_hso = gate_json["hookSpecificOutput"]
        assert gate_hso["hookEventName"] == "UserPromptSubmit"
        assert "PACT:bootstrap" in gate_hso["additionalContext"], (
            "healed lead session without marker must inject, not suppress"
        )
        assert gate_rc == 0, f"gate stderr={gate_err!r}"

        # Exactly one well-formed context file with the expected content,
        # whichever healer won the rename race.
        assert ctx.exists(), "at least one healer must land the file"
        content = json.loads(ctx.read_text(encoding="utf-8"))
        assert set(content.keys()) == {
            "team_name", "session_id", "project_dir", "plugin_root",
            "started_at",
        }
        assert content["team_name"] == "pact-deadbeef"
        assert content["session_id"] == session_id
        assert content["project_dir"] == f"/tmp/{slug}"
        assert content["plugin_root"] == str(plugin_root)
        assert content["started_at"]

        # No leftover mkstemp temp files (atomic-rename hygiene).
        stray = [p for p in session_dir.iterdir()
                 if p.name != "pact-session-context.json"]
        assert stray == [], f"unexpected files after race: {stray}"

        # Heal != forged bootstrap on EVERY schedule: marker pre-conditions
        # are unmet (no team config), so the marker must not exist.
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        assert not marker.exists(), (
            "two-healer race must never forge bootstrap completion"
        )


# =============================================================================
# #975 Option A — PostToolUse(Agent) dual-event coverage (design §10).
#
# The marker writer is now registered under BOTH UserPromptSubmit (steady-state
# turn-2+ self-heal) AND PostToolUse matched on Agent (stamps WITHIN the
# bootstrapping turn). These tests drive a synthetic PostToolUse(Agent) frame
# through main() to close the §10 coverage items the coder could only prove
# structurally. The write DECISION is tool_name-agnostic — main() ->
# _try_write_marker reads only agent_type (via is_lead), session_id, and
# members[] on disk — so a synthetic Agent frame is structurally faithful for
# this hook (the synthetic-vs-real fidelity ceiling is deferred to the §9
# post-merge fresh-session probe; documented per coverage item 6).
# =============================================================================


class TestPostToolUseFailSafeNotFailOpen:
    """§10.5 — the SACROSANCT invariant (release-blocker-grade).

    A PostToolUse(Agent) fire with the secretary NOT yet in members[] (the
    tmux-race / too-early-fire simulation) MUST refuse: NO marker written,
    exit 0, suppressOutput. The writer NEVER stamps on the bare Agent-spawn
    fact — it re-derives the precondition (secretary observed in members[] on
    disk via the NAME-keyed _team_has_secretary) and degrades to a silent
    no-op when unmet. This is "observe, never infer": the firing of the
    PostToolUse(Agent) hook is the OCCASION to check, never the EVIDENCE that
    bootstrap completed.

    A failing or missing version of this test is a release blocker (design
    §8 Risk-gate locus #1, §12 risk #2)."""

    def test_secretary_absent_from_members_refuses_no_marker(
        self, monkeypatch, tmp_path, capsys,
    ):
        """members[] populated but WITHOUT a secretary entry → refuse.

        This is the worst-case PostToolUse(Agent) fire: the platform ran the
        Agent tool, but the entry it wrote is NOT the secretary (or the
        secretary has not landed yet). The writer must NOT mistake the
        Agent-spawn fact for bootstrap completion."""
        members = [
            {"id": "a-1", "name": "preparer"},
            {"id": "a-2", "name": "backend-coder"},
        ]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        code, out = _run_main(_make_posttooluse_agent_input(), capsys)
        assert code == 0
        # Echoes the ACTUAL firing event (AUDIT-ANCHOR) — PostToolUse, not the
        # hard-coded UserPromptSubmit default.
        assert out == {
            "suppressOutput": True,
            "hookSpecificOutput": {"hookEventName": "PostToolUse"},
        }
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists(), (
            "SACROSANCT fail-safe: a PostToolUse(Agent) fire with no secretary "
            "in members[] must NOT stamp the marker. Stamping here would forge "
            "bootstrap completion off the bare spawn fact (fail-OPEN), the "
            "exact failure mode #975's verify-and-refuse design forbids."
        )

    def test_empty_members_refuses_no_marker(
        self, monkeypatch, tmp_path, capsys,
    ):
        """members[] empty → refuse. The PostToolUse(Agent) hook fired but the
        platform has not yet written ANY member; degrade to no-op (the marker
        lands on a later fire or the UserPromptSubmit fallback)."""
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=[],
        )
        code, out = _run_main(_make_posttooluse_agent_input(), capsys)
        assert code == 0
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()

    def test_team_config_absent_refuses_no_marker(
        self, monkeypatch, tmp_path, capsys,
    ):
        """Team config absent entirely → refuse silently, no exception."""
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=False,
        )
        code, out = _run_main(_make_posttooluse_agent_input(), capsys)
        assert code == 0
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()

    def test_non_lead_agent_frame_refuses_even_with_secretary(
        self, monkeypatch, tmp_path, capsys,
    ):
        """A PostToolUse(Agent) fire carrying a TEAMMATE agent_type → refuse,
        even though the secretary IS in members[]. The is_lead gate keys on
        agent_type, so only the lead's Agent-spawn fire stamps the marker.

        (In practice teammates have no Agent-spawn-fire path, but pinning the
        is_lead gate on the PostToolUse frame guards against a future frame
        that carries a teammate agent_type reaching this hook.)"""
        members = [{"id": "a-1", "name": "secretary"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        code, out = _run_main(
            _make_posttooluse_agent_input(agent_type="pact-backend-coder"),
            capsys,
        )
        assert code == 0
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists(), (
            "is_lead gate: a teammate-agent_type PostToolUse(Agent) frame must "
            "not stamp the marker even with the secretary present."
        )


class TestPostToolUseEndToEndByteIdentity:
    """§10.3 / §10.8 — END-TO-END write + cross-event byte-identity.

    Coverage item 1: drive main() with a synthetic PostToolUse(Agent) lead
    frame where the secretary IS in members[] on disk → assert the marker is
    actually WRITTEN, and that it is BYTE-IDENTICAL to the marker the same
    session would produce on a UserPromptSubmit fire.

    Byte-identity is proven by writing BOTH markers in the SAME session
    (identical session_id / plugin_root / plugin_version → identical digest
    inputs) and comparing the on-disk bytes — NOT by recomputing an expected
    value. This closes the coder's structural-only proof: it directly
    demonstrates the firing event does not enter the digest (design §8)."""

    def test_posttooluse_agent_writes_marker(
        self, monkeypatch, tmp_path, capsys,
    ):
        """Secretary present + lead PostToolUse(Agent) frame → marker written,
        valid schema, correct sid."""
        members = [
            {"id": "a-1", "name": "secretary"},
            {"id": "a-2", "name": "preparer"},
        ]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        code, out = _run_main(_make_posttooluse_agent_input(), capsys)
        assert code == 0
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        assert marker.exists(), (
            "PostToolUse(Agent) fire with secretary in members[] must stamp "
            "the marker WITHIN the bootstrapping turn (#975 Option A)."
        )
        body = json.loads(marker.read_text(encoding="utf-8"))
        assert body["v"] == MARKER_SCHEMA_VERSION
        assert body["sid"] == _SESSION_ID
        assert set(body.keys()) == {"v", "sid", "sig"}

    def test_marker_byte_identical_across_events(
        self, monkeypatch, tmp_path, capsys,
    ):
        """The marker bytes produced via the PostToolUse(Agent) path equal the
        bytes produced via the UserPromptSubmit path, for the SAME session.

        Method: write via PostToolUse(Agent), capture bytes, delete the marker
        and reset the context cache, then write via UserPromptSubmit and
        capture bytes. Same session_dir + plugin_root + session_id → the only
        thing that differs between the two runs is the firing event. Equal
        bytes prove the event never enters the digest (design §8 byte-identity
        contract)."""
        import shared.pact_context as ctx_module

        members = [{"id": "a-1", "name": "secretary"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        marker = session_dir / BOOTSTRAP_MARKER_NAME

        # --- Write 1: PostToolUse(Agent) fire ---
        code, out = _run_main(_make_posttooluse_agent_input(), capsys)
        assert code == 0
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert marker.exists(), "PostToolUse path must write the marker"
        ptu_bytes = marker.read_bytes()

        # Reset to a clean marker-absent state for the second write. The
        # context file/path are unchanged so digest inputs stay identical;
        # only the firing event differs on the second run.
        marker.unlink()
        ctx_module._cache = None

        # --- Write 2: UserPromptSubmit fire ---
        code, out = _run_main(_make_input(), capsys)
        assert code == 0
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert marker.exists(), "UserPromptSubmit path must write the marker"
        ups_bytes = marker.read_bytes()

        assert ptu_bytes == ups_bytes, (
            "marker bytes must be IDENTICAL regardless of firing event "
            "(design §8): the firing event affects only the echoed "
            "hookEventName envelope, never the marker digest. "
            f"PostToolUse={ptu_bytes!r} UserPromptSubmit={ups_bytes!r}"
        )


class TestPostToolUseIdempotency:
    """§10.4 — IDEMPOTENCY.

    A second PostToolUse(Agent) fire with the marker already valid hits the
    is_marker_set fast-path and no-ops: _write_marker is NOT called, the marker
    bytes are unchanged, exit 0. After the bootstrapping turn every subsequent
    Agent spawn hits this fast-path (design §3b — the general Agent matcher is
    cheap because of this no-op)."""

    def test_second_posttooluse_agent_fire_is_noop(
        self, monkeypatch, tmp_path, capsys,
    ):
        import bootstrap_marker_writer as bmw

        members = [{"id": "a-1", "name": "secretary"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        marker = session_dir / BOOTSTRAP_MARKER_NAME

        # First fire writes the marker.
        code, out = _run_main(_make_posttooluse_agent_input(), capsys)
        assert code == 0
        assert marker.exists()
        before = marker.read_bytes()

        # Reset the context cache so the second run re-reads from disk (same as
        # a fresh hook process would), then spy on _write_marker.
        import shared.pact_context as ctx_module
        ctx_module._cache = None

        write_calls = []
        original_write = bmw._write_marker

        def spy_write(*args, **kwargs):
            write_calls.append((args, kwargs))
            return original_write(*args, **kwargs)

        monkeypatch.setattr(bmw, "_write_marker", spy_write)

        # Second PostToolUse(Agent) fire (e.g. spawning the preparer) → no-op.
        code, out = _run_main(_make_posttooluse_agent_input(), capsys)
        assert code == 0
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert write_calls == [], (
            "second PostToolUse(Agent) fire must hit the is_marker_set "
            f"fast-path and NOT call _write_marker; got {len(write_calls)} "
            "call(s). Every post-bootstrap Agent spawn no-ops here (§3b)."
        )
        assert marker.read_bytes() == before, "marker bytes must be unchanged"


class TestPostToolUseGotchas:
    """§10.6 — agent_id and Agent-internal-name gotchas (record 0b8d4fd0).

    On a lead-side PostToolUse(Agent) frame, the top-level agent_id is the
    EMPTY STRING (the captured TaskUpdate frame shows agent_id null/absent).
    The role decision is keyed on agent_type via is_lead, NOT agent_id, so an
    empty/absent agent_id must NOT block a legitimate stamp, and a non-secretary
    Agent-internal-name spawn must NOT cause a spurious stamp or crash."""

    @pytest.mark.parametrize("agent_id", ["", None, "agent-xyz-123"])
    def test_agent_id_variants_do_not_block_legitimate_stamp(
        self, monkeypatch, tmp_path, capsys, agent_id,
    ):
        """agent_id empty-string (lead-side default), absent, or populated —
        none affect the write decision: with a lead agent_type + secretary in
        members[], the marker is stamped regardless. Pins that role is keyed on
        agent_type (is_lead), not agent_id."""
        members = [{"id": "a-1", "name": "secretary"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        frame = _make_posttooluse_agent_input(agent_id=agent_id)
        code, out = _run_main(frame, capsys)
        assert code == 0
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert (session_dir / BOOTSTRAP_MARKER_NAME).exists(), (
            f"agent_id={agent_id!r} must not block the stamp — the role "
            "decision keys on agent_type via is_lead, never agent_id (§10.6)."
        )

    def test_empty_agent_id_does_not_force_a_stamp_without_secretary(
        self, monkeypatch, tmp_path, capsys,
    ):
        """The empty-string agent_id is NOT itself treated as a signal: with
        the secretary ABSENT, an empty agent_id still refuses. (Guards against
        a future reader mistaking agent_id=='' for a lead/bootstrap signal.)"""
        members = [{"id": "a-1", "name": "preparer"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        code, out = _run_main(
            _make_posttooluse_agent_input(agent_id=""), capsys,
        )
        assert code == 0
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()

    def test_non_secretary_agent_spawn_does_not_stamp(
        self, monkeypatch, tmp_path, capsys,
    ):
        """A PostToolUse(Agent) fire for a NON-secretary spawn while the
        secretary is not yet present → no stamp. The tool_input names a
        non-secretary subagent; the writer ignores tool_input entirely and
        re-derives from members[], which lacks a secretary → refuse.

        This pins that the writer does not infer 'a secretary spawn happened'
        from the Agent call's arguments — it only observes members[] on disk."""
        members = [{"id": "a-1", "name": "preparer"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        frame = _make_posttooluse_agent_input()
        # tool_input names a NON-secretary subagent — the writer must ignore it.
        frame["tool_input"] = {
            "description": "spawn coder", "subagent_type": "pact-backend-coder",
        }
        code, out = _run_main(frame, capsys)
        assert code == 0
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists(), (
            "the writer must not infer bootstrap completion from the Agent "
            "call's tool_input (subagent_type); it observes members[] only."
        )

    def test_malformed_agent_type_does_not_crash_or_stamp(
        self, monkeypatch, tmp_path, capsys,
    ):
        """A non-string agent_type (list) on the PostToolUse frame → is_lead is
        TOTAL (isinstance guard) so it returns False without raising; the
        writer refuses cleanly, exit 0, no marker."""
        members = [{"id": "a-1", "name": "secretary"}]
        session_dir, _ = _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        frame = _make_posttooluse_agent_input()
        frame["agent_type"] = ["PACT:pact-orchestrator"]  # unhashable / non-str
        code, out = _run_main(frame, capsys)
        assert code == 0
        assert out == {
            "suppressOutput": True,
            "hookSpecificOutput": {"hookEventName": "PostToolUse"},
        }
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()


class TestPostToolUsePinIntegrity:
    """§10.5 (coverage item 5) — INDEPENDENT pin-integrity check.

    The coder updated three §7.1 pins to assert the dual-event behavior. This
    confirms — independently of merely-passing — that those pins genuinely
    assert the NEW correct behavior (echo the GIVEN event, including
    "PostToolUse") and would DETECT a regression to the old hard-coded
    "UserPromptSubmit". Rather than trust the pins pass, we exercise the
    underlying contract directly: the suppress envelope and the emit-shape
    invariant MUST reflect a "PostToolUse" event, not a hard-coded value."""

    def test_suppress_output_echoes_posttooluse_not_hardcoded(self):
        """_suppress_output("PostToolUse") MUST carry hookEventName ==
        "PostToolUse" — if a regression reintroduced a hard-coded
        "UserPromptSubmit" constant, this assertion fails. (Independent of the
        coder's parametrized pin, which this corroborates.)"""
        from bootstrap_marker_writer import _suppress_output

        out = json.loads(_suppress_output("PostToolUse"))
        assert out["suppressOutput"] is True
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse", (
            "the suppress envelope must ECHO the given event; a hard-coded "
            "value here is the silent-fail-open AUDIT-ANCHOR trap under a "
            "PostToolUse fire (design §5.4)."
        )

    def test_resolve_event_name_returns_posttooluse_from_frame(self):
        """_resolve_event_name reads the frame's hook_event_name verbatim — a
        PostToolUse frame resolves to "PostToolUse", proving the dynamic
        resolution the dual-event design depends on actually reads the frame."""
        from bootstrap_marker_writer import _resolve_event_name

        assert _resolve_event_name(
            {"hook_event_name": "PostToolUse"}
        ) == "PostToolUse"
        # The safe default only applies when the frame is genuinely unavailable.
        assert _resolve_event_name(None) == "UserPromptSubmit"
        assert _resolve_event_name({}) == "UserPromptSubmit"
        assert _resolve_event_name({"hook_event_name": ""}) == "UserPromptSubmit"
        assert _resolve_event_name(
            {"hook_event_name": 123}
        ) == "UserPromptSubmit"

    def test_main_end_to_end_emits_posttooluse_event(
        self, monkeypatch, tmp_path, capsys,
    ):
        """End-to-end through main(): a PostToolUse frame's suppress envelope
        carries hookEventName == "PostToolUse". This is the integration-level
        corroboration that the dynamic resolution wires through main()'s emit
        sites, not just the unit helpers — a regression to a static constant
        would surface here as well as in the coder's parametrized pin."""
        members = [{"id": "a-1", "name": "secretary"}]
        _setup_session(
            monkeypatch, tmp_path, with_team_config=True, members=members,
        )
        code, out = _run_main(_make_posttooluse_agent_input(), capsys)
        assert code == 0
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


class TestPostToolUseSubprocessIntegration:
    """Subprocess integration for the PostToolUse(Agent) happy path — the
    real-process analogue of TestSubprocessIntegration but for the #975 event.

    In-process import+monkeypatch tests can mask sys.path / module-load issues
    that only surface when the platform spawns the hook as a fresh process; one
    subprocess test for the new event closes that gap (mirrors the existing
    UserPromptSubmit subprocess test's scope rationale)."""

    def test_subprocess_posttooluse_agent_writes_marker(self, tmp_path):
        import subprocess

        home = tmp_path
        slug = "ptuproj"
        session_id = "ptu-subproc-session-id"
        team_name = "pact-ptusubp01"
        plugin_version = "9.9.9-ptu"

        session_dir = home / ".claude" / "pact-sessions" / slug / session_id
        session_dir.mkdir(parents=True)

        plugin_root = home / "plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": plugin_version}), encoding="utf-8"
        )

        team_dir = home / ".claude" / "teams" / team_name
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"members": [{"id": "a-1", "name": "secretary"}]}),
            encoding="utf-8",
        )

        ctx = session_dir / "pact-session-context.json"
        ctx.write_text(json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": f"/tmp/{slug}",
            "plugin_root": str(plugin_root),
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")

        hook_path = (
            Path(__file__).parent.parent / "hooks" /
            "bootstrap_marker_writer.py"
        )
        assert hook_path.exists(), f"writer hook missing at {hook_path}"

        # A real PostToolUse(Agent) lead frame: agent_type lead spelling,
        # hook_event_name PostToolUse, empty agent_id (lead-side), tool_response.
        stdin_payload = json.dumps({
            "hook_event_name": "PostToolUse",
            "session_id": session_id,
            "tool_name": "Agent",
            "agent_id": "",
            "agent_type": "pact-orchestrator",
            "tool_input": {"subagent_type": "pact-secretary"},
            "tool_response": {"content": [{"type": "text", "text": "ok"}]},
        })

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["CLAUDE_PROJECT_DIR"] = f"/tmp/{slug}"

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_payload,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
            timeout=10,
        )
        assert result.returncode == 0, (
            f"subprocess exit non-zero. stderr={result.stderr!r} "
            f"stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip())
        assert out["suppressOutput"] is True
        # AUDIT-ANCHOR end-to-end through a fresh process: the echoed event is
        # PostToolUse, not the hard-coded default.
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

        marker = session_dir / BOOTSTRAP_MARKER_NAME
        assert marker.exists(), (
            "marker should be written via the PostToolUse(Agent) subprocess fire"
        )
        body = json.loads(marker.read_text(encoding="utf-8"))
        assert body["v"] == 1
        assert body["sid"] == session_id
        expected_sig = hashlib.sha256(
            f"{session_id}|{plugin_root}|{plugin_version}|1".encode()
        ).hexdigest()
        assert body["sig"] == expected_sig


# =============================================================================
# #975 — hooks.json registration: PostToolUse(Agent) added; UserPromptSubmit
# registration + writer-before-prompt-gate ordering UNCHANGED (design §10.7).
# =============================================================================


class TestHooksJsonDualRegistration:
    """§10.7 — assert the registration delta is exactly as designed: the writer
    is registered under PostToolUse with matcher 'Agent', AND its
    UserPromptSubmit registration is retained. (The ordering no-touch pin lives
    in test_hooks_json.py per §7.2; this corroborates the new block from the
    writer test's perspective.)"""

    HOOKS_JSON = Path(__file__).parent.parent / "hooks" / "hooks.json"

    def _load(self):
        return json.loads(self.HOOKS_JSON.read_text(encoding="utf-8"))

    def test_writer_registered_under_posttooluse_agent(self):
        config = self._load()
        ptu_blocks = config["hooks"].get("PostToolUse", [])
        agent_writer_blocks = [
            block for block in ptu_blocks
            if block.get("matcher") == "Agent"
            and any(
                "bootstrap_marker_writer.py" in h.get("command", "")
                for h in block.get("hooks", [])
            )
        ]
        assert len(agent_writer_blocks) == 1, (
            "exactly one PostToolUse block with matcher 'Agent' must register "
            f"bootstrap_marker_writer.py; found {len(agent_writer_blocks)}."
        )
        # Registered synchronously (no async) — the marker must be written
        # before subsequent dispatches are gate-checked (design §7.2).
        for h in agent_writer_blocks[0]["hooks"]:
            assert h.get("type") == "command"
            assert "async" not in h, (
                "the PostToolUse(Agent) writer must be synchronous"
            )

    def test_writer_still_registered_under_userpromptsubmit(self):
        config = self._load()
        ups_blocks = config["hooks"].get("UserPromptSubmit", [])
        ups_writer = [
            block for block in ups_blocks
            if any(
                "bootstrap_marker_writer.py" in h.get("command", "")
                for h in block.get("hooks", [])
            )
        ]
        assert len(ups_writer) >= 1, (
            "the UserPromptSubmit registration of the writer must be RETAINED "
            "as the steady-state self-heal surface (#975 keeps both events)."
        )
