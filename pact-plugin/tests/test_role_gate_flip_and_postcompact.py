"""Role-gate flip regression + postcompact suppression + #812 drift fixture.

Three comprehensive groups complementing the coder's gate-specific smoke
tests:

  1. POSTCOMPACT suppression (#881) — postcompact_archive's global-singleton
     compact-summary O_TRUNC write × {lead writes / teammate suppressed /
     plain suppressed}, end-to-end via stdin.

  2. CLASS-B GATE FLIP regression — each of the 5 migrated teammate-bypass
     gates: a non-lead frame (teammate both spellings + plain) takes the
     bypass branch (no-op), while a lead frame ENGAGES the gate. Parameterized
     across the gate seams so a single gate regressing to the old heuristic
     (or dropping the is_lead routing) is caught with a tight, gate-specific
     assertion. (bootstrap_gate's full DENY matrix is the coder's existing
     coverage; this file adds the consolidated cross-gate flip contract +
     the plain-frame row.)

  3. #812 DRIFT fixture — pins the CURRENT identity-signal facts so a future
     regression (agent_id re-appearing as a role signal, or an agent_type
     role-mapping change) surfaces loudly: agent_id is absent from the
     synthesized frames; agent_type maps to role per the SSOT.
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    lead_frame_qualified,
    lead_frame_unqualified,
    plain_frame,
    postcompact_frame,
    teammate_frame,
)


_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"


# ===========================================================================
# GROUP 3a — postcompact_archive #881 suppression
# ===========================================================================

class TestPostcompactSuppression:
    """The compact-summary write is a GLOBAL SINGLETON (#881) — a teammate or
    plain frame's PostCompact must NOT clobber the lead's summary. Gated behind
    is_lead. Drives postcompact_archive.main() end-to-end via stdin.
    """

    def _run_postcompact(self, frame, monkeypatch, tmp_path):
        from postcompact_archive import main

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        stdin_data = json.dumps(frame)

        with patch("postcompact_archive.write_compact_summary") as mock_write, \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        return mock_write

    @pytest.mark.parametrize("frame_builder", [
        lead_frame_qualified, lead_frame_unqualified,
    ], ids=["qualified", "unqualified"])
    def test_lead_writes_compact_summary(self, frame_builder, monkeypatch, tmp_path):
        """A lead frame with a compact_summary writes the summary."""
        frame = frame_builder(hook_event_name="PostCompact",
                              compact_summary="lead summary text")
        mock_write = self._run_postcompact(frame, monkeypatch, tmp_path)
        mock_write.assert_called_once()
        assert mock_write.call_args.args[0] == "lead summary text"

    @pytest.mark.parametrize("frame, role", [
        (postcompact_frame(agent_type="pact-backend-coder"), "teammate"),
        (postcompact_frame(agent_type=None), "plain"),
    ])
    def test_non_lead_suppresses_compact_summary(self, frame, role, monkeypatch, tmp_path):
        """A teammate/plain frame must NOT write the compact-summary (it would
        clobber the lead's global singleton)."""
        mock_write = self._run_postcompact(frame, monkeypatch, tmp_path)
        mock_write.assert_not_called()

    def test_lead_with_empty_summary_does_not_write(self, monkeypatch, tmp_path):
        """Even a lead frame with an empty compact_summary skips the write
        (the `compact_summary and is_lead(...)` short-circuit). Confirms the
        gate is `summary AND lead`, not `lead alone`."""
        frame = lead_frame_qualified(hook_event_name="PostCompact",
                                     compact_summary="")
        mock_write = self._run_postcompact(frame, monkeypatch, tmp_path)
        mock_write.assert_not_called()


# ===========================================================================
# GROUP 3b — Class-B gate flip regression (the 5 migrated teammate-bypass gates)
# ===========================================================================

class TestBootstrapGateFlip:
    """bootstrap_gate (DENY): lead(both spellings)-no-marker → ENFORCE;
    teammate/plain → PASSTHROUGH. Consolidated flip matrix (the coder's file
    has the full DENY-reason matrix; this adds the explicit plain-frame row +
    the both-spellings contrast in one place).
    """

    def _setup_session_no_marker(self, monkeypatch, tmp_path):
        """Minimal: a session_dir that exists with NO bootstrap marker, so the
        gate reaches the is_lead branch. Patches the two pre-is_lead guards."""
        import bootstrap_gate
        monkeypatch.setattr(
            bootstrap_gate.pact_context, "init", lambda _d: None
        )
        monkeypatch.setattr(
            bootstrap_gate.pact_context, "get_session_dir",
            lambda: str(tmp_path / "sess")
        )
        monkeypatch.setattr(bootstrap_gate, "is_marker_set", lambda _p: False)

    def _input(self, frame, tool_name="Edit"):
        return {
            "hook_event_name": "PreToolUse",
            "session_id": _SESSION_ID,
            "tool_name": tool_name,
            "tool_input": {},
            **frame,
        }

    @pytest.mark.parametrize("frame_builder", [
        lead_frame_qualified, lead_frame_unqualified,
    ], ids=["qualified", "unqualified"])
    def test_lead_no_marker_enforces(self, frame_builder, monkeypatch, tmp_path):
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        self._setup_session_no_marker(monkeypatch, tmp_path)
        result = _check_tool_allowed(self._input(frame_builder()))
        assert result == _DENY_REASON, (
            "lead frame, no marker, blocked tool → must ENFORCE (deny)"
        )

    @pytest.mark.parametrize("frame, role", [
        (teammate_frame(), "teammate"),
        (teammate_frame(agent_type="PACT:pact-backend-coder"), "teammate-qualified"),
        (plain_frame(), "plain"),
    ])
    def test_non_lead_passes_through(self, frame, role, monkeypatch, tmp_path):
        from bootstrap_gate import _check_tool_allowed
        self._setup_session_no_marker(monkeypatch, tmp_path)
        result = _check_tool_allowed(self._input(frame))
        assert result is None, (
            f"{role} frame must PASS THROUGH the bootstrap DENY gate (no-op)"
        )


class TestClassBGateBypassFlip:
    """The 4 OTHER migrated Class-B gates: a non-lead frame takes the bypass
    branch (no-op), a lead frame engages. Each gate is reached by patching its
    pre-is_lead guards so only the is_lead verdict decides.

    Asserting the FLIP (bypass for non-lead, engagement for lead) on each gate
    is the per-migrated-hook regression: a gate that regresses to the old
    `if resolve_agent_name(...)` heuristic would invert under tmux (lead
    bypasses, teammate engages) and fail here.
    """

    # --- bootstrap_prompt_gate._check_bootstrap_needed ---

    def _setup_prompt_gate(self, monkeypatch, tmp_path):
        import bootstrap_prompt_gate as g
        monkeypatch.setattr(g.pact_context, "init", lambda _d: None)
        monkeypatch.setattr(g.pact_context, "get_session_dir",
                            lambda: str(tmp_path / "sess"))
        monkeypatch.setattr(g, "is_marker_set", lambda _p: False)
        return g

    @pytest.mark.parametrize("frame, expect_bypass", [
        (lead_frame_qualified(), False),
        (lead_frame_unqualified(), False),
        (teammate_frame(), True),
        (plain_frame(), True),
    ], ids=["lead-q", "lead-u", "teammate", "plain"])
    def test_prompt_gate_flip(self, frame, expect_bypass, monkeypatch, tmp_path):
        g = self._setup_prompt_gate(monkeypatch, tmp_path)
        result = g._check_bootstrap_needed({"session_id": _SESSION_ID, **frame})
        if expect_bypass:
            assert result is None, "non-lead must bypass (no injection)"
        else:
            assert result is not None, "lead must engage (inject bootstrap instruction)"

    # --- bootstrap_marker_writer._try_write_marker ---

    @pytest.mark.parametrize("frame, expect_write", [
        (lead_frame_qualified(), True),
        (lead_frame_unqualified(), True),
        (teammate_frame(), False),
        (plain_frame(), False),
    ], ids=["lead-q", "lead-u", "teammate", "plain"])
    def test_marker_writer_flip(self, frame, expect_write, monkeypatch, tmp_path):
        """_try_write_marker(input_data) reads session_dir internally; is_lead
        gates BEFORE the team-has-secretary / plugin-version pre-conditions.
        Stub the whole post-is_lead chain so a lead frame reaches _write_marker
        and a non-lead frame bypasses before any of it. The write function is
        _write_marker (underscore)."""
        import bootstrap_marker_writer as g
        sess = tmp_path / "sess"
        sess.mkdir()
        monkeypatch.setattr(g.pact_context, "init", lambda _d: None)
        monkeypatch.setattr(g.pact_context, "get_session_dir", lambda: str(sess))
        monkeypatch.setattr(g, "is_marker_set", lambda _p: False)
        # Post-is_lead pre-conditions — stub so a lead frame reaches _write_marker.
        monkeypatch.setattr(g, "_team_has_secretary", lambda _t: True)
        monkeypatch.setattr(g.pact_context, "get_team_name", lambda: "t1")
        monkeypatch.setattr(g.pact_context, "get_plugin_root", lambda: str(tmp_path))
        monkeypatch.setattr(g, "_read_plugin_version", lambda _r: "9.9.9")
        with patch.object(g, "_write_marker") as mock_write:
            g._try_write_marker({"session_id": _SESSION_ID, **frame})
        if expect_write:
            assert mock_write.called, "lead must proceed to write the marker"
        else:
            assert not mock_write.called, "non-lead must bypass (no marker write)"

    # --- pin_caps_gate._check_tool_allowed ---

    @pytest.mark.parametrize("frame, expect_bypass", [
        (lead_frame_qualified(), False),
        (teammate_frame(), True),
        (plain_frame(), True),
    ], ids=["lead", "teammate", "plain"])
    def test_pin_caps_gate_bypass(self, frame, expect_bypass, monkeypatch, tmp_path):
        """A non-lead frame returns None at the is_lead bypass BEFORE reading
        tool_input / calling match_project_claude_md. Make the post-is_lead
        path observable via a sentinel on match_project_claude_md: non-lead must
        return None without hitting it; lead must hit it."""
        import pin_caps_gate as g

        monkeypatch.setattr(g.pact_context, "init", lambda _d: None)
        reached = {"hit": False}
        def _sentinel(_path):
            reached["hit"] = True
            return None  # no claude_md match → gate ultimately returns None
        monkeypatch.setattr(g, "match_project_claude_md", _sentinel)

        # tool_name must be a _GATED_TOOLS member ({Edit, Write}) to pass the
        # gate's first guard and reach the is_lead branch.
        inp = {"session_id": _SESSION_ID, "tool_name": "Edit",
               "tool_input": {"file_path": "/x/CLAUDE.md"}, **frame}
        result = g._check_tool_allowed(inp)
        assert result is None  # empty match → no deny regardless of role
        assert reached["hit"] is (not expect_bypass), (
            "non-lead must bypass before match_project_claude_md; "
            "lead must reach it"
        )

    # --- pin_staleness_gate._check_tool_allowed ---

    @pytest.mark.parametrize("frame, expect_bypass", [
        (lead_frame_qualified(), False),
        (teammate_frame(), True),
        (plain_frame(), True),
    ], ids=["lead", "teammate", "plain"])
    def test_pin_staleness_gate_bypass(self, frame, expect_bypass, monkeypatch, tmp_path):
        """For a non-lead frame the gate returns None at the is_lead bypass —
        BEFORE it would read session_dir. We assert the bypass by making the
        post-is_lead path (get_session_dir) raise: a non-lead frame must return
        None WITHOUT hitting it, a lead frame must hit it (and we catch the
        sentinel)."""
        import pin_staleness_gate as g

        _SENTINEL = RuntimeError("reached post-is_lead path")
        monkeypatch.setattr(g.pact_context, "init", lambda _d: None)
        def _boom():
            raise _SENTINEL
        monkeypatch.setattr(g.pact_context, "get_session_dir", _boom)

        # tool_name must be a _GATED_TOOLS member ({Edit, Write}) to pass the
        # gate's first guard and reach the is_lead branch.
        inp = {"session_id": _SESSION_ID, "tool_name": "Edit",
               "tool_input": {}, **frame}
        if expect_bypass:
            # Non-lead → returns None at is_lead, never calls get_session_dir.
            assert g._check_tool_allowed(inp) is None
        else:
            # Lead → proceeds past is_lead and hits the sentinel.
            with pytest.raises(RuntimeError, match="reached post-is_lead path"):
                g._check_tool_allowed(inp)


# ===========================================================================
# GROUP 3c — #812 drift-detection fixture
# ===========================================================================

class TestAgentIdDriftFixture:
    """Pin the CURRENT identity-signal facts. A future regression that
    re-introduces agent_id as a role signal, or remaps agent_type→role, breaks
    these — surfacing the #812 drift class loudly at CI rather than silently
    under tmux.
    """

    def test_synthesized_frames_carry_no_agent_id(self):
        """The synthesized role frames model the tmux reality: agent_id is
        ABSENT on every hook event. If a future capture/fixture re-adds it,
        this fails — prompting a deliberate review of whether the role
        discriminator still (correctly) ignores it."""
        for builder in (lead_frame_qualified, lead_frame_unqualified,
                        teammate_frame, plain_frame):
            frame = builder()
            assert "agent_id" not in frame, (
                f"{builder.__name__} unexpectedly carries agent_id — the tmux "
                f"capture matrix says agent_id is absent. If this changed, "
                f"re-verify the role discriminator still ignores agent_id."
            )

    def test_agent_type_maps_to_role_per_ssot(self):
        """Pin the agent_type→role mapping against the SSOT. A change to
        LEAD_AGENT_TYPES or the classifier that silently remaps a role surfaces
        here."""
        from shared.pact_context import classify_session_role, is_lead

        cases = [
            ("PACT:pact-orchestrator", "lead", True),
            ("pact-orchestrator", "lead", True),
            ("pact-backend-coder", "teammate", False),
            ("pact-secretary", "teammate", False),
            (None, "unknown", False),
        ]
        for agent_type, expected_role, expected_is_lead in cases:
            frame = {} if agent_type is None else {"agent_type": agent_type}
            assert classify_session_role(frame) == expected_role, (
                f"agent_type={agent_type!r} role drift"
            )
            assert is_lead(frame) is expected_is_lead

    def test_agent_id_presence_never_promotes_role(self):
        """The #812 guard, pinned at the discriminator level: adding an
        agent_id to a NON-lead frame must never flip it to lead — agent_type is
        the sole role signal."""
        from shared.pact_context import is_lead, classify_session_role

        # A teammate frame with an agent_id re-added (simulating a future CC
        # build) stays a teammate.
        frame = {"agent_type": "pact-backend-coder", "agent_id": "backend@team"}
        assert is_lead(frame) is False
        assert classify_session_role(frame) == "teammate"

        # A plain frame with an agent_id stays unknown (agent_id alone is not
        # a role).
        frame2 = {"agent_id": "someone@team"}
        assert is_lead(frame2) is False
        assert classify_session_role(frame2) == "unknown"
