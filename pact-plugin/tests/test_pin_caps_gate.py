"""
Smoke tests for hooks/pin_caps_gate.py — PreToolUse hook enforcing
pin count / size / embedded-pin / override caps on Edit|Write of the
project CLAUDE.md.

Risk tier: CRITICAL (hook can deny every Edit to CLAUDE.md). Full
matrix (count ladder, size ladder, teammate bypass cells, override
ladder, adversarial Edit fragments, counter-test-by-revert per
predicate) lives in Phase E (test-engineer-6 scope) per the cycle-8
CODE/TEST phase split.

Minimum coverage shipped in the code-phase commit:
  - happy-path ALLOW (under-cap Edit)
  - happy-path DENY (count cap — pre-clean, post-violation)
  - teammate bypass (agent_name non-empty → always allow)
  - fail-open on _check_tool_allowed exception (SACROSANCT)
  - Write-baseline fail-CLOSED when baseline read fails AND Write is
    over-cap
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
def caps_gate_env(tmp_path, monkeypatch, pact_context):
    """Build a minimal pin_caps_gate test environment.

    Yields a `setup(pin_count=...)` callable that writes a CLAUDE.md with
    the requested number of pins and returns the tmp paths for building
    tool_input payloads.
    """
    claude_md = tmp_path / "CLAUDE.md"
    pact_context(
        team_name="test-team",
        session_id="session-xyz",
        project_dir=str(tmp_path),
    )

    # Point the lifted match_project_claude_md at our tmp CLAUDE.md via
    # staleness.get_project_claude_md_path (the lazy import inside
    # shared/claude_md_manager.match_project_claude_md).
    import staleness
    monkeypatch.setattr(
        staleness, "get_project_claude_md_path", lambda: claude_md
    )

    def _setup(pin_count: int = 1):
        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(pin_count)
        ]
        claude_md.write_text(
            make_claude_md_with_pins(entries), encoding="utf-8"
        )
        return {"claude_md": claude_md}

    return _setup


def _call_gate(input_data):
    from pin_caps_gate import _check_tool_allowed
    return _check_tool_allowed(input_data)


class TestPinCapsGate_Smoke:
    """Minimal hook-primary cap enforcement smoke tests."""

    def test_edit_under_cap_allows(self, caps_gate_env):
        env = caps_gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "irrelevant",
                "new_string": "also irrelevant",
                "replace_all": False,
            },
        })
        assert result is None

    def test_write_at_cap_boundary_allows(self, caps_gate_env):
        """Post-state at cap (12/12) is NOT a violation under strict `>`."""
        env = caps_gate_env(pin_count=3)
        # Write a full CLAUDE.md with exactly 12 pins (at cap, not over).
        entries = [make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(12)]
        new_content = make_claude_md_with_pins(entries)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is None

    def test_write_over_count_cap_denies(self, caps_gate_env):
        """Post-state 13/12 from a clean baseline denies via net-worse."""
        env = caps_gate_env(pin_count=3)
        entries = [make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(13)]
        new_content = make_claude_md_with_pins(entries)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is not None
        assert "Pin count cap" in result

    def test_non_claude_md_path_allows(self, caps_gate_env):
        env = caps_gate_env(pin_count=3)
        # Different file → gate short-circuits at the path match.
        other = env["claude_md"].parent / "other.md"
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(other),
                "content": "# not claude_md\n",
            },
        })
        assert result is None

    def test_non_gated_tool_passes(self, caps_gate_env):
        env = caps_gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Read",
            "tool_input": {"file_path": str(env["claude_md"])},
        })
        assert result is None

    def test_teammate_bypass(self, caps_gate_env):
        """Teammate sessions (agent_name non-empty) bypass the gate."""
        env = caps_gate_env(pin_count=3)
        # Patch resolve_agent_name to return a non-empty name.
        import shared.pact_context as ctx_module
        with patch.object(
            ctx_module, "resolve_agent_name", return_value="backend-coder-x"
        ):
            entries = [
                make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(13)
            ]
            new_content = make_claude_md_with_pins(entries)
            result = _call_gate({
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(env["claude_md"]),
                    "content": new_content,
                },
            })
        assert result is None


class TestPinCapsGate_FailOpen:
    """SACROSANCT: gate bugs never block (with Write-baseline exception)."""

    def test_main_catches_unexpected_exception(self, caps_gate_env, monkeypatch):
        """If _check_tool_allowed raises, main() fail-opens."""
        import pin_caps_gate
        monkeypatch.setattr(
            pin_caps_gate,
            "_check_tool_allowed",
            lambda _: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        stdin_payload = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/nonexistent", "old_string": "",
                           "new_string": ""},
        })
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(stdin_payload))
        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 0

    def test_invalid_json_stdin_fails_open(self, monkeypatch):
        """Malformed stdin → fail-open with suppressOutput."""
        import pin_caps_gate
        monkeypatch.setattr(
            "sys.stdin", __import__("io").StringIO("not valid json")
        )
        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 0


class TestPinCapsGate_WriteBaselineFailClosed:
    """Asymmetric SACROSANCT exception: Write with unreadable baseline
    AND over-cap content → fail-CLOSED (Sec N7)."""

    def test_write_over_cap_with_missing_baseline_denies(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Baseline CLAUDE.md doesn't exist on disk; Write payload is
        13/12. Asymmetric rule denies rather than fail-opening."""
        claude_md = tmp_path / "CLAUDE.md"  # Deliberately NOT created.
        pact_context(team_name="t", session_id="s", project_dir=str(tmp_path))

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(13)
        ]
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(claude_md),
                "content": make_claude_md_with_pins(entries),
            },
        })
        assert result is not None
        assert "Refusing Write" in result

    def test_write_under_cap_with_missing_baseline_allows(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Same baseline-missing condition, but the Write content is
        under-cap → allow. Fail-CLOSED only fires on a concrete
        over-cap Write; a clean Write isn't punished for a missing file."""
        claude_md = tmp_path / "CLAUDE.md"  # Deliberately NOT created.
        pact_context(team_name="t", session_id="s", project_dir=str(tmp_path))

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)
        ]
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(claude_md),
                "content": make_claude_md_with_pins(entries),
            },
        })
        assert result is None

    def test_edit_with_missing_baseline_fails_open(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Edit (not Write) with baseline missing → fail-OPEN.
        Asymmetric rule applies only to Write."""
        claude_md = tmp_path / "CLAUDE.md"
        pact_context(team_name="t", session_id="s", project_dir=str(tmp_path))

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(claude_md),
                "old_string": "x",
                "new_string": "y",
                "replace_all": False,
            },
        })
        assert result is None
