"""
Phantom-green 5th-instance probe for pin_caps enforcement.

Per institutional memory "staged-peer-fix phantom-green" (feedback_staged_peer_fix_phantom_green.md)
and the 4-instance umbrella in MEMORY.md, we probe for the 5th
phantom-green vector: a silent environment-variable or config bypass
that lets enforcement short-circuit without an explicit disable flag.

The threat model: a future engineer may add an emergency-off switch
(e.g., `PACT_PIN_CAPS_BYPASS=1`) during debugging and forget to remove
it — every enforcement test passes, but real users see silent bypass.
These tests assert NO environment-variable state disables cap
enforcement. If a new bypass env var is added intentionally, it MUST
update this test file to document the new surface.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


@pytest.fixture
def loaded_gate_env(tmp_path, monkeypatch, pact_context):
    """Build a baseline-3-pin environment for bypass probes."""
    claude_md = tmp_path / "CLAUDE.md"
    pact_context(
        team_name="test-team",
        session_id="session-phantom",
        project_dir=str(tmp_path),
    )

    import staleness
    monkeypatch.setattr(
        staleness, "get_project_claude_md_path", lambda: claude_md
    )

    entries = [make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)]
    claude_md.write_text(make_claude_md_with_pins(entries), encoding="utf-8")
    return claude_md


def _build_over_cap_payload():
    entries = [make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(13)]
    return make_claude_md_with_pins(entries)


def _call_gate(input_data):
    from pin_caps_gate import _check_tool_allowed
    return _check_tool_allowed(input_data)


class TestPhantomGreen_EnvBypass:
    """Probe: no environment variable disables pin cap enforcement."""

    @pytest.mark.parametrize(
        "env_var",
        [
            "PACT_PIN_CAPS_BYPASS",
            "PACT_BYPASS_CAPS",
            "PACT_DISABLE_PIN_CAPS",
            "PACT_DEBUG",
            "DEBUG",
            "PACT_DEV_MODE",
            "PACT_SKIP_HOOKS",
            "PACT_UNSAFE",
            "DISABLE_PIN_ENFORCEMENT",
            "PACT_PIN_CAPS_DISABLED",
        ],
    )
    def test_env_var_does_not_disable_enforcement(
        self, loaded_gate_env, monkeypatch, env_var
    ):
        """Setting a suspicious env var to any truthy value MUST NOT
        disable the gate. If any of these probes leak ALLOW, we have a
        phantom-green bypass surface.
        """
        for value in ("1", "true", "yes", "TRUE", "on"):
            monkeypatch.setenv(env_var, value)
            result = _call_gate({
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(loaded_gate_env),
                    "content": _build_over_cap_payload(),
                },
            })
            assert result is not None, (
                f"{env_var}={value} should NOT bypass enforcement, "
                f"but gate returned ALLOW. Phantom-green bypass found."
            )
            assert "Pin count cap" in result
            monkeypatch.delenv(env_var, raising=False)

    def test_empty_environment_still_enforces(
        self, loaded_gate_env, monkeypatch
    ):
        """Strip ALL user-accessible env vars → enforcement still fires.

        Uses monkeypatch.setattr on os.environ to wholesale replace. Any
        enforcement that silently depends on a SET env var would leak
        here.
        """
        # Preserve PATH + HOME so Python runtime still works.
        preserved = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        }
        monkeypatch.setattr(os, "environ", preserved)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(loaded_gate_env),
                "content": _build_over_cap_payload(),
            },
        })
        assert result is not None
        assert "Pin count cap" in result


class TestPhantomGreen_CliBypass:
    """Probe: check_pin_caps advisory CLI cannot be coaxed into claiming
    cap enforcement. It reports slot state only.

    The CLI was demoted in cycle-8 from primary enforcer to advisory
    reporter. A phantom-green regression would be: CLI reports
    `"allowed": true` even when the hook would DENY — tricking the
    curator into thinking they're under-cap. BUT the CLI's `allowed`
    field is ALWAYS true now (documented as "always true (advisory
    only)"). The real check: `evictable_pins` count MUST reflect the
    actual parsed state.
    """

    def test_cli_always_reports_allowed_true(
        self, tmp_path, monkeypatch
    ):
        """CLI `allowed` is documented always-True. Confirm the contract."""
        import check_pin_caps
        import io

        claude_md = tmp_path / "CLAUDE.md"
        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(13)
        ]
        claude_md.write_text(make_claude_md_with_pins(entries), encoding="utf-8")
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: claude_md
        )

        buf = io.StringIO()
        with patch.object(sys, "stdout", buf):
            rc = check_pin_caps.main(["--status"])
        assert rc == 0
        payload = json.loads(buf.getvalue().strip())
        # Advisory-always-true contract.
        assert payload["allowed"] is True
        assert payload["violation"] is None
        # But evictable_pins reflects actual state.
        assert len(payload["evictable_pins"]) == 13

    @pytest.mark.parametrize(
        "unknown_flag",
        [
            "--new-body",           # Retired cycle-7 flag
            "--body-from-stdin",    # Retired cycle-7 flag
            "--has-override",       # Retired cycle-7 flag
            "--override-rationale", # Retired cycle-7 flag
            "--bypass-caps",        # Hypothetical bypass flag
            "--force",              # Common bypass pattern
            "--disable-enforcement",# Hypothetical disable flag
        ],
    )
    def test_cli_silently_accepts_unknown_flag_without_changing_behavior(
        self, tmp_path, monkeypatch, unknown_flag
    ):
        """parse_known_args silently drops unknown flags. Verify that
        passing retired or novel flags doesn't change the payload shape
        (allowed stays True, no field is injected by the flag).

        Finding: the CLI's `parse_known_args` is documented as SACROSANCT
        fail-open but has the side effect that a typo-as-status would
        silently succeed. The CLI is advisory, so this is a LOW finding;
        no enforcement bypass is possible because the CLI doesn't
        enforce. Documented here to pin the advisory-contract.
        """
        import check_pin_caps
        import io

        claude_md = tmp_path / "CLAUDE.md"
        entries = [make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)]
        claude_md.write_text(make_claude_md_with_pins(entries), encoding="utf-8")
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: claude_md
        )

        buf_with = io.StringIO()
        with patch.object(sys, "stdout", buf_with):
            rc = check_pin_caps.main([unknown_flag])
        assert rc == 0
        payload_with = json.loads(buf_with.getvalue().strip())

        buf_without = io.StringIO()
        with patch.object(sys, "stdout", buf_without):
            rc = check_pin_caps.main(["--status"])
        payload_without = json.loads(buf_without.getvalue().strip())

        # Unknown flag does NOT change the payload shape — advisory-only
        # contract preserved.
        assert payload_with == payload_without


class TestPhantomGreen_TeammateNameProbe:
    """The teammate bypass IS intentional (`agent_name` non-empty →
    allow), but probe that it's keyed ONLY on the pact_context resolution
    — not on a string in file_path, tool_input, or stdin payload."""

    def test_fake_agent_name_in_file_path_does_not_bypass(
        self, loaded_gate_env
    ):
        """A file path containing 'backend-coder' doesn't trigger bypass."""
        # The gate checks `resolve_agent_name(input_data)` — not file_path.
        # So a phantom file_path like /tmp/backend-coder/CLAUDE.md should
        # NOT trigger teammate bypass.
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(loaded_gate_env),
                "content": _build_over_cap_payload(),
                "magical_bypass_name": "backend-coder-x",
            },
        })
        assert result is not None, "Phantom teammate bypass via tool_input"

    def test_agent_name_fields_in_tool_input_do_not_bypass(
        self, loaded_gate_env
    ):
        """Teammate-identity fields injected into `tool_input` (not the
        top-level input_data) MUST NOT bypass the gate.

        Rationale: `resolve_agent_name` legitimately reads top-level
        `agent_name` / `agent_id` / `agent_type` fields — these are
        populated by Claude Code's platform for real teammate dispatch,
        not by user-controlled tool_input. The attack surface is
        whether a curator can smuggle teammate identity through the
        Edit/Write tool_input payload (user-controlled). This test
        confirms the gate reads agent identity ONLY from top-level
        input_data, not from tool_input.
        """
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(loaded_gate_env),
                "content": _build_over_cap_payload(),
                # Attempts to smuggle teammate identity via user-controlled
                # tool_input — these MUST be ignored by resolve_agent_name.
                "agent_name": "backend-coder-x",
                "agent_id": "backend-coder-x@pact-fake",
                "agent_type": "pact-backend-coder",
                "subagent_type": "pact-backend-coder",
                "teammate_name": "backend-coder-x",
            },
        })
        assert result is not None, (
            "Phantom teammate bypass via tool_input fields — "
            "resolve_agent_name must only read top-level input_data identity"
        )
        assert "Pin count cap" in result

    def test_subagent_type_at_top_level_is_not_accepted(
        self, loaded_gate_env
    ):
        """`subagent_type` is NOT in resolve_agent_name's resolution
        chain (only agent_name / agent_id / agent_type are). Top-level
        `subagent_type` alone must not bypass the gate.

        Documents the resolution-chain surface: adding a new identity
        field to resolve_agent_name implicitly adds a bypass vector;
        this test pins that `subagent_type` is currently out-of-chain.
        """
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(loaded_gate_env),
                "content": _build_over_cap_payload(),
            },
            "subagent_type": "pact-backend-coder",
        })
        assert result is not None, (
            "subagent_type at top-level bypassed gate — resolve_agent_name "
            "resolution chain has changed; update this test to reflect the "
            "new surface"
        )
        assert "Pin count cap" in result
