"""#956 Component A — handoff_ordering_gate.py PreToolUse WARN gate.

The NUDGE half of the #956 fix: when the lead's TaskUpdate(status="completed")
lands on a HANDOFF-expecting task whose metadata.handoff is not yet on disk, the
gate surfaces an ACTIONABLE advisory (additionalContext) so the lead does
handoff-then-complete. It NEVER denies — the backstop guarantees the emit; this
gate only nudges.

These tests cover the both-modes matrix rows M1-M6 (lead vs teammate frame) plus
the gate's fail-OPEN contract on every error path, and a main()-level integration
proving the exit-0 + additionalContext (NOT permissionDecision) output shape.

Drives the gate via _evaluate(input_data) (the logic entry) with a real on-disk
task.json so read_task_json resolves; is_lead keys on agent_type (the only
tmux-safe discriminator). The pact_context fixture pre-sets the context path so
the gate's internal init() is a no-op against it.
"""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import handoff_ordering_gate as gate  # noqa: E402

TEAM = "test-team"
LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-devops-engineer"
HANDOFF = {"decisions": ["x"], "produced": ["f.py"]}


def _seed_task(tmp_path, team, task_id, **fields):
    tasks_dir = tmp_path / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _complete_update(task_id, *, agent_type=LEAD, metadata=None):
    """A TaskUpdate(status=completed). `metadata` (if given) is the INCOMING
    update metadata (e.g. a bundled handoff)."""
    tool_input = {"taskId": task_id, "status": "completed"}
    if metadata is not None:
        tool_input["metadata"] = metadata
    payload = {"tool_name": "TaskUpdate", "tool_input": tool_input}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


def _ctx(pact_context, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id="s1", project_dir=str(tmp_path))


# =============================================================================
# M1 — HANDOFF-expecting task completed, handoff absent → advisory (lead) / none (teammate)
# =============================================================================
class TestM1WarnOnOrderingMistake:
    def test_lead_frame_warns(self, tmp_path, monkeypatch, pact_context):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={},  # completed, NO handoff
        )
        advisory = gate._evaluate(_complete_update("42"))
        assert advisory is not None
        assert "no metadata.handoff yet" in advisory
        assert "42" in advisory and "devops" in advisory

    def test_teammate_frame_no_warn(self, tmp_path, monkeypatch, pact_context):
        """M1 dual-mode: identical fixture under a TEAMMATE frame (is_lead
        False) → no advisory. The advisory is for the lead who completes."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={},
        )
        assert gate._evaluate(_complete_update("42", agent_type=TEAMMATE)) is None


# =============================================================================
# M2 — handoff already on disk → no warn
# =============================================================================
class TestM2HandoffAlreadyPresent:
    def test_no_warn_when_handoff_on_disk(self, tmp_path, monkeypatch, pact_context):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={"handoff": HANDOFF},
        )
        assert gate._evaluate(_complete_update("42")) is None


# =============================================================================
# M3 — teachback Task-A (exempt by subject) → no warn
# =============================================================================
class TestM3TeachbackExempt:
    def test_no_warn_on_teachback_subject(self, tmp_path, monkeypatch, pact_context):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "A",
            subject="devops: TEACHBACK for the thing", owner="devops",
            status="completed", metadata={},
        )
        assert gate._evaluate(_complete_update("A")) is None


# =============================================================================
# M4 — secretary task (exempt Surface 1, signal-type proxy) → no warn
# M5 — signal-task (exempt Surface 2) → no warn
# =============================================================================
class TestM4M5Exempt:
    @pytest.mark.parametrize("signal_type", ["blocker", "algedonic"])
    def test_no_warn_on_signal_task(self, tmp_path, monkeypatch, pact_context, signal_type):
        """M5: a signal task (completion_type=signal + type in {blocker,
        algedonic}) is self-complete-exempt → no handoff expected → no warn."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "S",
            subject="devops: raise blocker", owner="devops",
            status="completed",
            metadata={"completion_type": "signal", "type": signal_type},
        )
        assert gate._evaluate(_complete_update("S")) is None

    def test_warn_positive_control_non_signal(self, tmp_path, monkeypatch, pact_context):
        """Positive control for M5: the SAME fixture WITHOUT the signal
        metadata DOES warn — proving the suppression above is the exempt
        predicate firing, not a missing precondition."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "S",
            subject="devops: raise blocker", owner="devops",
            status="completed", metadata={},
        )
        assert gate._evaluate(_complete_update("S")) is not None


# =============================================================================
# M6 — bundled handoff+complete in one TaskUpdate → no warn
# =============================================================================
class TestM6BundledHandoffComplete:
    def test_no_warn_when_incoming_handoff_bundled(self, tmp_path, monkeypatch, pact_context):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={},
        )
        # The completing TaskUpdate ALSO carries the handoff → no race.
        adv = gate._evaluate(_complete_update("42", metadata={"handoff": HANDOFF}))
        assert adv is None


# =============================================================================
# Scoping / fail-open contract
# =============================================================================
class TestScopingAndFailOpen:
    def test_non_completion_update_no_warn(self, tmp_path, monkeypatch, pact_context):
        """Only completion transitions are gated."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="pending", metadata={},
        )
        payload = {
            "tool_name": "TaskUpdate",
            "agent_type": LEAD,
            "tool_input": {"taskId": "42", "metadata": {"foo": "bar"}},  # no status=completed
        }
        assert gate._evaluate(payload) is None

    def test_no_owner_no_warn(self, tmp_path, monkeypatch, pact_context):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="",
            status="completed", metadata={},
        )
        assert gate._evaluate(_complete_update("42")) is None

    def test_missing_task_on_disk_no_warn(self, tmp_path, monkeypatch, pact_context):
        """No task file → read_task_json returns {} → bypass (fail-open)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        assert gate._evaluate(_complete_update("does-not-exist")) is None

    def test_non_taskupdate_tool_no_warn(self, tmp_path, monkeypatch, pact_context):
        _ctx(pact_context, monkeypatch, tmp_path)
        payload = {"tool_name": "TaskCreate", "agent_type": LEAD, "tool_input": {}}
        assert gate._evaluate(payload) is None

    def test_empty_agent_type_no_warn(self, tmp_path, monkeypatch, pact_context):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={},
        )
        assert gate._evaluate(_complete_update("42", agent_type="")) is None


# =============================================================================
# main() integration — exit-0 + additionalContext (NEVER permissionDecision)
# =============================================================================
class TestMainContract:
    def _run_main(self, monkeypatch, capsys, stdin_obj):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin_obj)))
        with pytest.raises(SystemExit) as exc:
            gate.main()
        out = capsys.readouterr().out
        return exc.value.code, out

    def test_advisory_path_exits_zero_with_additional_context(
        self, tmp_path, monkeypatch, pact_context, capsys
    ):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={},
        )
        code, out = self._run_main(monkeypatch, capsys, _complete_update("42"))
        assert code == 0, "WARN gate must ALWAYS exit 0 — never deny"
        parsed = json.loads(out)
        hso = parsed["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert "additionalContext" in hso
        assert "permissionDecision" not in hso, "a WARN gate must NEVER emit a deny"

    def test_passthrough_path_suppresses_and_exits_zero(
        self, tmp_path, monkeypatch, pact_context, capsys
    ):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={"handoff": HANDOFF},  # already present → no warn
        )
        code, out = self._run_main(monkeypatch, capsys, _complete_update("42"))
        assert code == 0
        assert json.loads(out) == {"suppressOutput": True}

    def test_malformed_stdin_fails_open(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
        with pytest.raises(SystemExit) as exc:
            gate.main()
        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out) == {"suppressOutput": True}


# =============================================================================
# main() through the REAL pact_context on-disk resolution (advisory path)
# =============================================================================
class TestMainRealContextResolution:
    """The other main() tests (TestMainContract) use the `pact_context`
    fixture, which monkeypatches `pact_context._context_path` to a pre-written
    file. Because `init()` early-returns when `_context_path is not None`, those
    tests SKIP the gate's real session-context resolution chain — the path
    `init(input_data)` resolves from `input_data.session_id` + CLAUDE_PROJECT_DIR,
    then `get_pact_context()` reads the on-disk pact-session-context.json to
    recover `team_name`, then `read_task_json(task_id, team_name)`.

    This test deliberately does NOT use the `pact_context` fixture. It writes a
    REAL on-disk context via `pact_context.write_context(...)`, leaves
    `_context_path`/`_cache` UNSET (None) so `init()` performs the genuine
    resolution, and drives `main()` end-to-end for the POSITIVE-advisory path.
    It proves team_name resolution from real disk reaches the warn branch — not
    just the pre-injected-path shortcut. (Non-vacuity: if the advisory path or
    the real context resolution is broken, team_name resolves empty, the gate
    bypasses, and the additionalContext assertion fails.)
    """

    def test_advisory_path_through_real_on_disk_context(
        self, tmp_path, monkeypatch, capsys
    ):
        import os
        import shared.pact_context as pc

        sid = "real-ctx-session-001"
        project_dir = str(tmp_path / "PACT-Plugin")  # basename slug == "PACT-Plugin"

        # Filesystem isolation: every Path.home() (write_context, init's path
        # builder, read_task_json) resolves under tmp_path.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # CLAUDE_PROJECT_DIR is the OTHER half of init()'s path resolution.
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", project_dir)

        # Write the REAL on-disk session-context file at the session-scoped path.
        # Start from clean module state so write_context resolves freshly.
        monkeypatch.setattr(pc, "_context_path", None)
        monkeypatch.setattr(pc, "_cache", None)
        pc.write_context(TEAM, sid, project_dir)

        # CRITICAL: write_context populates `_cache` (and `_context_path`). Reset
        # BOTH to None so the gate's init()/get_pact_context() must perform the
        # genuine on-disk resolution rather than hitting the warm cache — that
        # resolution chain is exactly what this test exists to exercise.
        monkeypatch.setattr(pc, "_context_path", None)
        monkeypatch.setattr(pc, "_cache", None)

        # Seed the on-disk task: completed, HANDOFF-expecting (owner, no handoff).
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={},
        )

        # Frame carries agent_type (is_lead reads it directly) AND session_id
        # (init() reads it to resolve the context path). No pre-set _context_path.
        frame = _complete_update("42", agent_type=LEAD)
        frame["session_id"] = sid

        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(frame)))
        with pytest.raises(SystemExit) as exc:
            gate.main()
        out = capsys.readouterr().out

        assert exc.value.code == 0, "WARN gate must ALWAYS exit 0 — never deny"
        parsed = json.loads(out)
        hso = parsed["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert "additionalContext" in hso, (
            "the advisory must fire through the REAL on-disk context resolution "
            "(team_name recovered from the written pact-session-context.json); a "
            "missing advisory means the resolution chain or the warn branch broke"
        )
        assert "42" in hso["additionalContext"] and "devops" in hso["additionalContext"]
        assert "permissionDecision" not in hso, "a WARN gate must NEVER emit a deny"
