"""
Tests for hooks/pin_staleness_gate.py — PreToolUse marker-gate for
CLAUDE.md Pinned Context edits under stale-pins-pending state.

Risk tier: CRITICAL (auth-adjacent — gate blocks user tool calls). All
I/O failure paths MUST fail-open (SACROSANCT: gate bugs never block).

Matrix: marker absence/present × CLAUDE.md path match/miss × teammate/lead
        × Edit/Write → 16 cells minimum, plus fail-open assertions.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


@pytest.fixture
def gate_env(tmp_path, monkeypatch, pact_context):
    """Assemble a minimal PreToolUse gate environment.

    Returns a callable that writes a CLAUDE.md, optionally writes a
    pin-staleness-pending marker, sets pact_context, and yields the paths
    needed to build tool_input payloads.
    """
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        make_claude_md_with_pins([make_pin_entry(title="Pin", body_chars=4)]),
        encoding="utf-8",
    )

    session_dir = tmp_path / "session-dir"
    session_dir.mkdir()

    # Point pact_context at a writable session dir.
    pact_context(
        team_name="test-team",
        session_id="session-abc",
        project_dir=str(tmp_path),
    )

    # Patch get_session_dir to return our tmp path.
    import shared.pact_context as ctx_module
    monkeypatch.setattr(
        ctx_module, "get_session_dir", lambda: str(session_dir)
    )

    # Patch get_project_claude_md_path so _is_project_claude_md resolves
    # our tmp CLAUDE.md.
    import staleness
    monkeypatch.setattr(
        staleness, "get_project_claude_md_path", lambda: claude_md
    )

    def _setup(*, marker_present=True):
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME
        marker_path = session_dir / PIN_STALENESS_MARKER_NAME
        if marker_present and not marker_path.exists():
            marker_path.touch()
        elif not marker_present and marker_path.exists():
            marker_path.unlink()
        return {
            "claude_md": claude_md,
            "session_dir": session_dir,
            "marker_path": marker_path,
        }

    return _setup


def _call_gate(input_data):
    """Invoke _check_tool_allowed directly with a synthesized input_data."""
    from pin_staleness_gate import _check_tool_allowed
    return _check_tool_allowed(input_data)


class TestPinStalenessGate_ToolMatch:
    """Only Edit and Write are gated — other tools always pass."""

    @pytest.mark.parametrize("tool_name", ["Read", "Bash", "Glob", "Grep",
                                           "Task", "NotebookEdit", ""])
    def test_non_gated_tools_pass(self, tool_name, gate_env):
        gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": tool_name,
            "tool_input": {"file_path": "whatever", "content": "whatever"},
        })
        assert result is None


class TestPinStalenessGate_MarkerAbsent:
    """Marker absent → always allow regardless of path/content."""

    def test_edit_on_claude_md_without_marker_allowed(self, gate_env):
        env = gate_env(marker_present=False)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Pinned Context",
                "new_string": "## Pinned Context\nmore",
            },
        })
        assert result is None

    def test_write_on_claude_md_without_marker_allowed(self, gate_env):
        env = gate_env(marker_present=False)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": "## Pinned Context\nbody",
            },
        })
        assert result is None


class TestPinStalenessGate_MarkerPresent:
    """Marker present × path match × pinned-touching content → DENY."""

    def test_edit_on_claude_md_pinned_edit_denied(self, gate_env):
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Pinned Context",
                "new_string": "## Pinned Context\nnew content",
            },
        })
        assert result is not None
        assert "Pinned Context" in result
        assert "stale pins" in result

    def test_write_on_claude_md_always_denied(self, gate_env):
        """Write replaces the full file → necessarily affects Pinned Context."""
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": "anything",
            },
        })
        assert result is not None

    def test_edit_on_claude_md_touching_pin_comment_denied(self, gate_env):
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "some text",
                "new_string": "<!-- pinned: 2026-04-20 -->\n### X\nbody",
            },
        })
        assert result is not None

    def test_edit_on_claude_md_touching_memory_boundary_denied(self, gate_env):
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "<!-- PACT_MEMORY_START -->",
                "new_string": "<!-- PACT_MEMORY_START -->\nextra",
            },
        })
        assert result is not None


class TestPinStalenessGate_PathMiss:
    """Marker present but file_path does NOT match project CLAUDE.md → allow."""

    def test_edit_on_unrelated_file_allowed(self, gate_env, tmp_path):
        gate_env(marker_present=True)
        other = tmp_path / "README.md"
        other.write_text("readme", encoding="utf-8")
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(other),
                "old_string": "## Pinned Context",
                "new_string": "## Pinned Context\nnope",
            },
        })
        assert result is None

    def test_edit_with_empty_file_path_allowed(self, gate_env):
        gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "",
                "old_string": "a",
                "new_string": "b",
            },
        })
        assert result is None

    def test_edit_with_missing_file_path_allowed(self, gate_env):
        gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {"old_string": "a", "new_string": "b"},
        })
        assert result is None


class TestPinStalenessGate_NonTouchingEdit:
    """Marker present, path match, but edit does NOT touch pinned section → allow."""

    def test_edit_elsewhere_in_claude_md_allowed(self, gate_env):
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Working Memory",
                "new_string": "## Working Memory\nnew",
            },
        })
        assert result is None


class TestPinStalenessGate_TeammateBypass:
    """Teammates bypass the gate (worktree scope — no CLAUDE.md in worktrees)."""

    def test_teammate_edit_on_claude_md_allowed(self, gate_env, monkeypatch):
        env = gate_env(marker_present=True)
        import shared.pact_context as ctx_module
        monkeypatch.setattr(
            ctx_module, "resolve_agent_name",
            lambda _input_data: "backend-coder",
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Pinned Context",
                "new_string": "## Pinned Context\nteammate edit",
            },
        })
        assert result is None


class TestPinStalenessGate_FailOpen:
    """SACROSANCT: any exception in gate logic → allow (fail-open)."""

    def test_session_dir_none_allows(self, gate_env, monkeypatch):
        gate_env(marker_present=True)
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: None)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {"file_path": "foo", "content": "bar"},
        })
        assert result is None

    def test_tool_input_not_dict_allowed(self, gate_env):
        gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": "malformed-string-not-dict",
        })
        assert result is None

    def test_claude_md_resolution_none_allows(self, gate_env, monkeypatch):
        env = gate_env(marker_present=True)
        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: None
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Pinned Context",
                "new_string": "## Pinned Context\n",
            },
        })
        assert result is None

    def test_main_malformed_stdin_suppresses_output(self, monkeypatch, capsys):
        """Malformed stdin → exit 0 with {"suppressOutput": true}."""
        from io import StringIO
        import pin_staleness_gate
        monkeypatch.setattr(sys, "stdin", StringIO("not-json"))
        with pytest.raises(SystemExit) as exc_info:
            pin_staleness_gate.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        assert json.loads(out) == {"suppressOutput": True}

    def test_main_internal_exception_suppresses_output(
        self, gate_env, monkeypatch, capsys
    ):
        """Exception inside _check_tool_allowed → exit 0 fail-open."""
        from io import StringIO
        import pin_staleness_gate
        gate_env(marker_present=True)
        monkeypatch.setattr(
            pin_staleness_gate, "_check_tool_allowed",
            lambda _x: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "x", "old_string": "a", "new_string": "b"},
        })))
        with pytest.raises(SystemExit) as exc_info:
            pin_staleness_gate.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        assert json.loads(out) == {"suppressOutput": True}


class TestPinStalenessGate_MainDenyPath:
    """Main emits permissionDecision=deny + exit 2 on positive detection."""

    def test_main_denies_write_on_claude_md_with_marker(
        self, gate_env, monkeypatch, capsys
    ):
        from io import StringIO
        import pin_staleness_gate
        env = gate_env(marker_present=True)
        monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": "replacement",
            },
        })))
        with pytest.raises(SystemExit) as exc_info:
            pin_staleness_gate.main()
        assert exc_info.value.code == 2
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        hso = payload["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "stale pins" in hso["permissionDecisionReason"]
