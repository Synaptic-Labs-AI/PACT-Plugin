"""Row 6 -- both-modes / role matrix for the SessionStart "PACT Runtime Config"
injection, driven through the REAL session_init.main() (non-mocked seam).

The injection is gated lead-only via ``frame_role != "teammate"`` where
``frame_role = classify_session_role(input_data)`` -- and classify_session_role
reads ONLY ``agent_type`` (lead := agent_type in LEAD_AGENT_TYPES; teammate :=
agent_type present and not-lead; unknown := agent_type absent). It NEVER reads
``session_id``. So the gate keys on the ROLE axis, not the session-topology
(``session_id`` vs ``leadSessionId``) axis. This file pins BOTH facts:

- MECHANISM (TestRoleGate): a lead / unknown frame RECEIVES the block; a teammate
  frame does NOT -- asserted through real main() emission (the
  os.environ -> llm_options -> format_pact_runtime_config -> additionalContext
  seam is left REAL, only injection-orthogonal heavy collaborators are stubbed).

- TOPOLOGY-INVARIANCE (TestTopologyInvariance): the SAME agent_type under
  session_id == a nominal leadSessionId (the in-process collapse shape) AND under
  session_id != it (the tmux per-teammate-SessionStart shape) yields the SAME
  block-presence outcome. This is the "both-modes" drift-guard: if a future edit
  made the injection branch on session_id-vs-leadSessionId, one of these cells
  would flip and red the test.

Fan-out reachability (why the two modes matter in production):
- tmux: each teammate fires its OWN SessionStart, so a teammate frame really does
  reach this gate and MUST be suppressed (the TMUX teammate cell is live).
- in-process: teammates share the lead's single SessionStart and never reach this
  gate via their own frame; only the lead frame does -> block present.

Provenance: role assertions use the CAPTURED real SessionStart frames from the
#812 discriminator audit where available (highest fidelity); the session_id
variation uses synthesized frames so the topology axis can be set explicitly.
"""
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    captured_lead_sessionstart_qualified,
    captured_lead_sessionstart_unqualified,
    captured_plain_sessionstart,
    captured_teammate_sessionstart,
    lead_frame_qualified,
    teammate_frame,
)

import session_init

_PROJECT_DIR = "/Users/example/Sites/test-project"
_HEADING = "## PACT Runtime Config (resolved at session start)"

# A nominal leadSessionId and a DISTINCT teammate session_id (tmux topology).
# session_init's injection gate never compares against either -- that non-read is
# exactly what TestTopologyInvariance pins.
_LEAD_SID = "11110000-0000-0000-0000-000000000000"
_TMUX_TEAMMATE_SID = "22223333-0000-0000-0000-000000000000"


def _emit(frame, monkeypatch, tmp_path):
    """Drive real session_init.main() with injection-orthogonal heavy
    collaborators stubbed; the config seam is NOT patched. Returns the emitted
    additionalContext string (or "" if none). Mirrors the stub set the CODE-phase
    injection test uses, so the REAL classify_session_role -> gate ->
    format_pact_runtime_config path executes."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
    # PACT_PR_GREEDY_FIX on so a PRESENT block is unambiguous (ON text); the gate
    # decision under test is presence/absence, independent of the value.
    monkeypatch.setenv("PACT_PR_GREEDY_FIX", "1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    stdin_data = json.dumps({"source": "startup", **frame})
    with patch("session_init.setup_plugin_symlinks", return_value=None), \
         patch("session_init.ensure_project_memory_md", return_value=None), \
         patch("session_init.check_pinned_staleness", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("session_init.restore_last_session", return_value=None), \
         patch("session_init.build_context_cache",
               return_value=(Path("/tmp/ctx.json"), {})), \
         patch("session_init.persist_context", return_value=None), \
         patch("session_init.append_event"), \
         patch("session_init.update_session_info", return_value=None), \
         patch("session_init.check_resume_state", return_value=None), \
         patch("session_init._registry_resolve", return_value=None), \
         patch("session_init.get_peer_context", return_value=None), \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    raw = mock_stdout.getvalue().strip()
    if not raw:
        return ""
    return json.loads(raw).get("hookSpecificOutput", {}).get("additionalContext", "")


class TestRoleGate:
    """Real-main() emission per role, using CAPTURED real SessionStart frames."""

    @pytest.mark.parametrize("frame_factory", [
        captured_lead_sessionstart_qualified,
        captured_lead_sessionstart_unqualified,
        captured_plain_sessionstart,   # unknown role (agent_type absent) -> still injected
    ], ids=["lead-qualified", "lead-unqualified", "plain-unknown"])
    def test_lead_or_unknown_frame_receives_block(self, frame_factory, monkeypatch, tmp_path):
        ctx = _emit(frame_factory(), monkeypatch, tmp_path)
        assert _HEADING in ctx
        assert "PR greedy-fix: ON (PACT_PR_GREEDY_FIX)" in ctx

    def test_teammate_frame_omits_block(self, monkeypatch, tmp_path):
        # Real captured teammate SessionStart (--agent pact-preparer): a recognized
        # specialist spelling -> classify "teammate" -> gated out.
        ctx = _emit(captured_teammate_sessionstart(), monkeypatch, tmp_path)
        assert "PACT Runtime Config" not in ctx


class TestTopologyInvariance:
    """The block-presence outcome keys on ROLE (agent_type), not on the
    session_id-vs-leadSessionId topology. The same agent_type under both session
    relationships yields the same outcome -- a drift-guard against a future edit
    that (wrongly) makes the injection read session_id."""

    @pytest.mark.parametrize("sid", [_LEAD_SID, _TMUX_TEAMMATE_SID],
                             ids=["session_id==leadSessionId", "session_id!=leadSessionId"])
    def test_teammate_gated_regardless_of_session_id(self, sid, monkeypatch, tmp_path):
        # tmux teammate (session_id != lead) is the live production path; the
        # session_id == lead cell is the in-process-collapse shape. BOTH must gate
        # out because the gate keys on agent_type, not session_id.
        ctx = _emit(teammate_frame("pact-backend-coder", session_id=sid),
                    monkeypatch, tmp_path)
        assert "PACT Runtime Config" not in ctx, (
            "teammate frame must be gated out under BOTH session topologies "
            "(the injection keys on frame_role, never session_id)"
        )

    @pytest.mark.parametrize("sid", [_LEAD_SID, _TMUX_TEAMMATE_SID],
                             ids=["session_id==leadSessionId", "session_id!=leadSessionId"])
    def test_lead_receives_regardless_of_session_id(self, sid, monkeypatch, tmp_path):
        ctx = _emit(lead_frame_qualified(session_id=sid), monkeypatch, tmp_path)
        assert _HEADING in ctx, (
            "lead frame must receive the block under BOTH session topologies "
            "(the injection keys on frame_role, never session_id)"
        )

    def test_role_is_the_only_discriminator(self, monkeypatch, tmp_path):
        # Hold session_id FIXED and flip only agent_type: presence flips. This
        # isolates role as the causal variable (companion to the invariance above).
        lead_ctx = _emit(lead_frame_qualified(session_id=_LEAD_SID), monkeypatch, tmp_path)
        team_ctx = _emit(teammate_frame("pact-backend-coder", session_id=_LEAD_SID),
                         monkeypatch, tmp_path)
        assert _HEADING in lead_ctx
        assert "PACT Runtime Config" not in team_ctx
