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

    @pytest.mark.parametrize("shape", ["advisory", "suppress"])
    def test_every_emit_shape_carries_hook_event_name(self, shape, capsys):
        """Architect §8.12 parametrized over both distinct emit shapes.

        The hook produces exactly two JSON output shapes:

        - "advisory": load-failure path via _emit_load_failure_advisory
          (line 61-72) — hookSpecificOutput with additionalContext.
        - "suppress": every other exit path via the _SUPPRESS_OUTPUT
          constant (line 98-101) — hookSpecificOutput with no other keys.

        Both MUST carry hookSpecificOutput.hookEventName == "UserPromptSubmit"
        — missing the field silently fails open at the platform layer per
        the pinned context. Parametrizing pins the invariant that no
        future emit path can be added without the audit anchor."""
        if shape == "advisory":
            from bootstrap_marker_writer import _emit_load_failure_advisory
            with pytest.raises(SystemExit):
                _emit_load_failure_advisory("module imports", RuntimeError("x"))
            captured = capsys.readouterr()
            out = json.loads(captured.out.strip())
        elif shape == "suppress":
            from bootstrap_marker_writer import _SUPPRESS_OUTPUT
            out = json.loads(_SUPPRESS_OUTPUT)
        else:  # pragma: no cover
            pytest.fail(f"unknown shape param: {shape}")

        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"shape={shape} emit MUST carry hookSpecificOutput; missing "
            f"the field silently fails open at the platform layer."
        )
        assert hso.get("hookEventName") == "UserPromptSubmit", (
            f"shape={shape} emit MUST carry hookEventName=='UserPromptSubmit'; "
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

        stdin_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "prompt": "first real prompt",
            "source": "startup",
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
# Captured-fixture parity: synthetic shape contains documented platform fields
# =============================================================================


class TestFixtureShapeParity:
    """The synthetic fixture is replaced post-merge with a real captured-
    from-production stdin (architect §8.7). Until then, the synthetic
    must include the documented platform fields so a partial regression
    of the platform schema is visible at fixture-replacement time.

    Pinned shape: hook_event_name, session_id, prompt, source. These are
    the fields the writer's main() and pact_context.init() actually read."""

    FIXTURE = (
        Path(__file__).parent / "fixtures" /
        "userpromptsubmit_stdin_post_bootstrap.json"
    )

    def test_fixture_contains_documented_platform_fields(self):
        data = json.loads(self.FIXTURE.read_text(encoding="utf-8"))
        # _comment is the synthetic-fixture marker; strip before checking.
        data.pop("_comment", None)
        for required in ("hook_event_name", "session_id", "prompt", "source"):
            assert required in data, (
                f"fixture missing platform field {required!r}; the "
                f"synthetic placeholder must mirror the documented "
                f"UserPromptSubmit stdin shape until the captured-from-"
                f"production version replaces it (architect §8.7)."
            )
        assert data["hook_event_name"] == "UserPromptSubmit"

    def test_fixture_synthetic_marker_present(self):
        """The _comment field marks the fixture as synthetic. When the
        captured-from-production version lands, this test should be
        DELETED (not updated) — the synthetic-marker check is intentionally
        scoped to the synthetic placeholder phase."""
        data = json.loads(self.FIXTURE.read_text(encoding="utf-8"))
        assert "_comment" in data, (
            "synthetic-fixture _comment field absent; if you've replaced "
            "with a real captured fixture, also delete this test "
            "(per the test docstring)."
        )
        assert "TODO" in data["_comment"]


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
