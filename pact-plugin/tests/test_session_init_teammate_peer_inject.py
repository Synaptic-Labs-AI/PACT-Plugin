"""CODE-phase verification for the Family E relocation (#806 Item 3):
peer-context injection on a separate-process teammate's OWN SessionStart
(the session_init teammate-branch) + the get_peer_context
``include_role_marker`` contract.

Coverage (per the ratified architect spec):
  * both-modes matrix — classify=="teammate" injects the marker-free peer
    body AND suppresses the orchestrator block; "lead" and "unknown"/plain
    frames keep the existing orchestrator-directive ladder UNCHANGED; the
    in-process SubagentStart builder still emits the role-marker prelude.
  * structural invariant — the relocated branch gates on the
    classify_session_role SSOT and calls the builder with
    include_role_marker=False (the negative-AST "never re-key on
    agent_id/Subagent/environ" invariant is owned by
    test_lead_discriminator_invariant.py, which already covers session_init).
  * fail-safe — unknown/empty agent_type does NOT inject (falls to the
    orchestrator else-branch); a teammate frame with no peer body (None)
    raises nothing and injects nothing (no orchestrator block either).
  * idempotency — the peer body is inserted at most once per lifecycle.

Mirrors test_session_init_role_suppression.py's synthetic-stdin + Path.home
-> tmp_path isolation so the rows port cleanly.

NOTE: the TEST-phase tmux-flip empirical acceptance (a real separate-process
teammate receiving the peer context end-to-end) is a SEPARATE later merge
gate owned by the test-engineer; these are the CODE-phase unit/structural
rows the architect specified.
"""

import ast
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    lead_frame_qualified,
    lead_frame_unqualified,
    plain_frame,
    teammate_frame,
)

_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"
_PROJECT_DIR = "/Users/example/Sites/test-project"
_ORCH_MARKER = "YOUR PACT ROLE: orchestrator"
_PEER_SENTINEL = "PEER_CONTEXT_SENTINEL_BODY"


def _stdin_for(frame: dict, source: str = "resume") -> str:
    """SessionStart stdin carrying ``frame``'s role discriminator + a valid
    session_id and source (so the assembly path is fully reached)."""
    return json.dumps({"session_id": _SESSION_ID, "source": source, **frame})


def _run_main_capture(stdin_data, monkeypatch, tmp_path, *, peer_return=_PEER_SENTINEL):
    """Run session_init.main() with the heavy collaborators patched out and
    get_peer_context stubbed to ``peer_return``; return
    (additionalContext_str, get_peer_context_mock).

    Stubbing get_peer_context isolates the teammate/else FORK DECISION (what
    commit 2 added) from the builder internals (covered separately below +
    by the ported test_peer_inject corpus).
    """
    from session_init import main

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

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
         patch("session_init.check_paused_state", return_value=None), \
         patch("session_init.get_peer_context", return_value=peer_return) as mock_gpc, \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    raw = mock_stdout.getvalue().strip()
    additional = ""
    if raw:
        try:
            additional = json.loads(raw).get("hookSpecificOutput", {}).get(
                "additionalContext", ""
            )
        except json.JSONDecodeError:
            additional = raw
    return additional, mock_gpc


# ===========================================================================
# Both-modes matrix
# ===========================================================================

class TestTeammateBranchInjects:
    """classify=="teammate" → marker-free peer body injected; orchestrator
    block SUPPRESSED (the mis-roling fix)."""

    def test_teammate_frame_injects_peer_body(self, monkeypatch, tmp_path):
        additional, mock_gpc = _run_main_capture(
            _stdin_for(teammate_frame("pact-backend-coder")), monkeypatch, tmp_path
        )
        assert _PEER_SENTINEL in additional
        assert mock_gpc.called

    def test_teammate_frame_suppresses_orchestrator_block(self, monkeypatch, tmp_path):
        additional, _ = _run_main_capture(
            _stdin_for(teammate_frame("pact-backend-coder")), monkeypatch, tmp_path
        )
        assert _ORCH_MARKER not in additional, (
            "teammate frame must NOT receive the orchestrator role block "
            "(this is the sweep-confirmed mis-roling the fork fixes)"
        )

    def test_builder_called_marker_free(self, monkeypatch, tmp_path):
        _, mock_gpc = _run_main_capture(
            _stdin_for(teammate_frame("pact-frontend-coder")), monkeypatch, tmp_path
        )
        # The teammate surface must request the MARKER-FREE body: the spawn
        # prompt owns the role; re-claiming it is the mis-roling bug.
        assert mock_gpc.call_args.kwargs.get("include_role_marker") is False

    @pytest.mark.parametrize(
        "agent_type",
        ["pact-backend-coder", "pact-frontend-coder", "pact-devops-engineer",
         "pact-architect", "pact-secretary", "pact-test-engineer"],
    )
    def test_various_specialist_types_all_inject(self, agent_type, monkeypatch, tmp_path):
        additional, _ = _run_main_capture(
            _stdin_for(teammate_frame(agent_type)), monkeypatch, tmp_path
        )
        assert _PEER_SENTINEL in additional
        assert _ORCH_MARKER not in additional


class TestLeadAndUnknownKeepOrchestratorBlock:
    """"lead" and "unknown"/plain frames keep the existing orchestrator ladder
    UNCHANGED — no peer body, no behavior change (minimal scope)."""

    @pytest.mark.parametrize(
        "frame_builder", [lead_frame_qualified, lead_frame_unqualified]
    )
    def test_lead_keeps_orchestrator_block_no_peer_body(
        self, frame_builder, monkeypatch, tmp_path
    ):
        additional, mock_gpc = _run_main_capture(
            _stdin_for(frame_builder()), monkeypatch, tmp_path
        )
        assert _ORCH_MARKER in additional
        assert _PEER_SENTINEL not in additional
        assert not mock_gpc.called, "lead frame must not call the peer-context builder"

    def test_plain_unknown_frame_keeps_orchestrator_block_no_peer_body(
        self, monkeypatch, tmp_path
    ):
        additional, mock_gpc = _run_main_capture(
            _stdin_for(plain_frame()), monkeypatch, tmp_path
        )
        assert _ORCH_MARKER in additional, (
            "unknown/plain frame behavior is UNCHANGED (minimal scope) — it "
            "still receives the orchestrator block as before this fix"
        )
        assert _PEER_SENTINEL not in additional
        assert not mock_gpc.called


class TestInProcessSubagentSurfaceUnchanged:
    """The in-process SubagentStart builder still emits the role-marker
    prelude (include_role_marker default True) — byte-identical to before."""

    def _write_team(self, tmp_path):
        team_dir = tmp_path / "teams" / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({
            "members": [
                {"name": "architect", "agentType": "pact-architect"},
                {"name": "backend-coder", "agentType": "pact-backend-coder"},
            ]
        }))
        return str(tmp_path / "teams")

    def test_subagent_default_includes_role_marker(self, tmp_path):
        from peer_inject import get_peer_context
        result = get_peer_context(
            agent_type="pact-architect",
            team_name="pact-test",
            agent_name="architect",
            teams_dir=self._write_team(tmp_path),
        )
        assert result is not None
        assert "YOUR PACT ROLE: teammate (architect)" in result
        assert "pact-communication-charter.md" in result
        assert "backend-coder" in result  # peer list present

    def test_marker_free_drops_only_the_marker_line(self, tmp_path):
        from peer_inject import get_peer_context
        teams_dir = self._write_team(tmp_path)
        kwargs = dict(agent_type="pact-architect", team_name="pact-test",
                      agent_name="architect", teams_dir=teams_dir)
        with_marker = get_peer_context(**kwargs, include_role_marker=True)
        without_marker = get_peer_context(**kwargs, include_role_marker=False)
        # Marker-free output drops the role-marker line but keeps EVERYTHING
        # else (charter + peer list + banner + reminders).
        assert "YOUR PACT ROLE: teammate" in with_marker
        assert "YOUR PACT ROLE: teammate" not in without_marker
        assert "pact-communication-charter.md" in without_marker
        assert "backend-coder" in without_marker
        # The with-marker output is exactly the marker line prepended.
        assert with_marker.endswith(without_marker)
        assert with_marker[: -len(without_marker)] == \
            "YOUR PACT ROLE: teammate (architect).\n\n"


# ===========================================================================
# Fail-safe
# ===========================================================================

class TestFailSafe:
    def test_teammate_with_no_team_config_injects_nothing_no_raise(
        self, monkeypatch, tmp_path
    ):
        # Builder returns None (no team config) → teammate branch inserts
        # nothing; crucially NO orchestrator block either, and no raise.
        additional, _ = _run_main_capture(
            _stdin_for(teammate_frame("pact-backend-coder")),
            monkeypatch, tmp_path, peer_return=None,
        )
        assert _PEER_SENTINEL not in additional
        assert _ORCH_MARKER not in additional

    def test_empty_agent_type_does_not_inject(self, monkeypatch, tmp_path):
        # Empty agent_type → classify "unknown" → else-branch (orchestrator),
        # NOT the teammate branch (fail-safe: only a genuine teammate injects).
        additional, mock_gpc = _run_main_capture(
            _stdin_for({"agent_type": ""}), monkeypatch, tmp_path
        )
        assert not mock_gpc.called
        assert _PEER_SENTINEL not in additional

    def test_builder_none_on_missing_config_does_not_raise(self, tmp_path):
        from peer_inject import get_peer_context
        # No config at the resolved path → None, no raise (fail-open).
        result = get_peer_context(
            agent_type="pact-backend-coder",
            team_name="pact-nonexistent",
            agent_name="x",
            teams_dir=str(tmp_path / "teams"),
            include_role_marker=False,
        )
        assert result is None


# ===========================================================================
# Idempotency
# ===========================================================================

class TestIdempotency:
    def test_peer_body_injected_exactly_once(self, monkeypatch, tmp_path):
        additional, mock_gpc = _run_main_capture(
            _stdin_for(teammate_frame("pact-backend-coder")), monkeypatch, tmp_path
        )
        assert additional.count(_PEER_SENTINEL) == 1
        assert mock_gpc.call_count == 1


# ===========================================================================
# Structural invariant (relocation-specific; negative-AST owned by
# test_lead_discriminator_invariant.py)
# ===========================================================================

class TestStructuralInvariant:
    def _tree(self):
        import session_init
        return ast.parse(Path(session_init.__file__).read_text(encoding="utf-8"))

    def _get_peer_context_calls(self, tree):
        return [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "get_peer_context"
        ]

    def test_single_builder_call_is_marker_free(self):
        calls = self._get_peer_context_calls(self._tree())
        assert len(calls) == 1, "expected exactly one get_peer_context call in session_init"
        kw = {k.arg: k.value for k in calls[0].keywords}
        assert "include_role_marker" in kw, "must pass include_role_marker explicitly"
        assert isinstance(kw["include_role_marker"], ast.Constant)
        assert kw["include_role_marker"].value is False

    def test_builder_call_is_gated_by_classify_session_role_teammate(self):
        tree = self._tree()
        call = self._get_peer_context_calls(tree)[0]
        # Find the enclosing `if` whose body contains the call, and assert its
        # test is `classify_session_role(...) == "teammate"`.
        gating_ifs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                if any(child is call for child in ast.walk(node.test)):
                    continue
                if any(child is call for stmt in node.body for child in ast.walk(stmt)):
                    gating_ifs.append(node)
        # innermost gating-if is the teammate gate
        assert gating_ifs, "get_peer_context call is not inside any if-branch"
        # at least one gating if must test classify_session_role == "teammate"
        def tests_classify_teammate(test):
            return (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Call)
                and isinstance(test.left.func, ast.Name)
                and test.left.func.id == "classify_session_role"
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "teammate"
            )
        assert any(tests_classify_teammate(n.test) for n in gating_ifs), (
            "the get_peer_context injection must be gated on "
            'classify_session_role(input_data) == "teammate" (the SSOT)'
        )
