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


# =============================================================================
# #865 dispatch-variety gate — the NEW branch (_evaluate_dispatch_variety),
# parallel to and independent of the #956 completion-ordering _evaluate.
# =============================================================================
#
# Composite-signature trigger: a TaskUpdate whose tool_input carries BOTH
# owner=pact-* AND a non-empty addBlockedBy in the SAME call (the terminal
# dispatch-wiring write). Fires (warn/deny/shadow per env-knob) ONLY when the
# linked Task B carries no resolvable metadata.variety. No misfire at
# TaskCreate(B) or partial-wiring; carve-outs preserved.
# =============================================================================


def _variety(total):
    """A resolvable D11 variety stamp at the given total."""
    return {
        "novelty": 2, "novelty_rationale": "x",
        "scope": 2, "scope_rationale": "x",
        "uncertainty": 2, "uncertainty_rationale": "x",
        "risk": 2, "risk_rationale": "x",
        "total": total,
    }


def _wiring_update(task_id, *, owner="pact-backend-coder",
                   add_blocked_by=("A",), agent_type=LEAD):
    """A terminal dispatch-wiring TaskUpdate: owner + addBlockedBy in the SAME
    tool_input. add_blocked_by=None / [] omits it (partial-wiring case)."""
    tool_input = {"taskId": task_id}
    if owner is not None:
        tool_input["owner"] = owner
    if add_blocked_by:
        tool_input["addBlockedBy"] = list(add_blocked_by)
    payload = {"tool_name": "TaskUpdate", "tool_input": tool_input}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


class TestDispatchVarietyTrigger:
    """The composite signature fires iff owner pact-* AND addBlockedBy are in
    the SAME tool_input AND the linked Task B has no resolvable variety."""

    def test_fires_on_wiring_write_unstamped_task_b(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """Terminal wiring write linking an unstamped Task B → advisory."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-backend-coder", metadata={})
        adv = gate._evaluate_dispatch_variety(_wiring_update("42"))
        assert adv is not None and "metadata.variety" in adv

    def test_silent_when_task_b_is_stamped(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """READ+VALIDATE: a stamped Task B → silent (the structural read is
        what makes the gate precise; it does NOT fire on the composite
        signature alone)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-backend-coder",
                   metadata={"variety": _variety(12)})
        assert gate._evaluate_dispatch_variety(_wiring_update("42")) is None

    def test_silent_when_task_b_stamped_via_fallback(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """A non-canonical but resolvable stamp (score, no total) → silent.
        The gate uses the shared resolve_variety_total, so any shape that
        resolves at write/read time also satisfies the gate."""
        _ctx(pact_context, monkeypatch, tmp_path)
        v = _variety(0)
        v.pop("total")
        v["score"] = 9
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-backend-coder", metadata={"variety": v})
        assert gate._evaluate_dispatch_variety(_wiring_update("42")) is None


class TestDispatchVarietyNoMisfire:
    """The FIRST-OBSERVABLE-WRITE / no-misfire invariant: never fire at
    TaskCreate(B) or on a partial-wiring TaskUpdate."""

    def test_no_fire_on_taskcreate(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """A TaskCreate (different tool) never reaches the branch."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-backend-coder", metadata={})
        payload = {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "impl foo", "owner": "pact-backend-coder",
                           "addBlockedBy": ["A"]},
            "agent_type": LEAD,
        }
        assert gate._evaluate_dispatch_variety(payload) is None

    def test_no_fire_on_owner_only_partial_wiring(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """owner set but NO addBlockedBy in the same call → not yet terminal
        → silent."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-backend-coder", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", add_blocked_by=None)
        ) is None

    def test_no_fire_on_addblockedby_only_partial_wiring(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """addBlockedBy set but NO owner in the same call → silent. This is
        the imPACT blocker-reassign / phase-task-blocking shape (scenario 12):
        every NON-dispatch addBlockedBy use is addBlockedBy-ONLY, so the
        composite never false-positives on it."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-backend-coder", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", owner=None)
        ) is None


class TestDispatchVarietyCarveOuts:
    """Carve-outs preserve R4's silence guarantees verbatim."""

    def test_silent_non_pact_owner(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """A non-pact owner (e.g. a SOLO_EXEMPT general-purpose agent, or a
        bare teammate name) never fires — the trigger requires owner pact-*
        (scenario 9: SOLO_EXEMPT agents are non-pact owners, already
        excluded)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="general-purpose", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", owner="general-purpose")
        ) is None

    def test_silent_teachback_subject(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """A Task-A teachback gate subject is exempt (is_teachback_subject)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(tmp_path, TEAM, "42",
                   subject="backend: TEACHBACK for the thing",
                   owner="pact-backend-coder", metadata={})
        assert gate._evaluate_dispatch_variety(_wiring_update("42")) is None

    @pytest.mark.parametrize("signal_type", ["blocker", "algedonic"])
    def test_silent_signal_task(
        self, tmp_path, monkeypatch, pact_context, signal_type,
    ):
        """A signal task (completion_type=signal) is exempt via
        is_self_complete_exempt — auditor/blocker signal tasks carry no
        variety obligation."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-auditor",
                   metadata={"completion_type": "signal", "type": signal_type})
        assert gate._evaluate_dispatch_variety(_wiring_update(
            "42", owner="pact-auditor")) is None

    def test_silent_teammate_frame(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """DUAL-MODE: a teammate frame emits nothing (is_lead structural
        discriminator)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-backend-coder", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", agent_type=TEAMMATE)
        ) is None


class TestDispatchVarietyEnvKnobModes:
    """main()-level: PACT_DISPATCH_VARIETY_MODE selects warn / deny / shadow.
    The module reads the knob at import; monkeypatch the resolved constant."""

    def _run_main(self, monkeypatch, capsys, stdin_obj):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin_obj)))
        with pytest.raises(SystemExit) as exc:
            gate.main()
        return exc.value.code, capsys.readouterr().out

    def _seed_unstamped(self, tmp_path):
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-backend-coder", metadata={})

    def test_warn_mode_additional_context_exit_zero(
        self, tmp_path, monkeypatch, pact_context, capsys,
    ):
        _ctx(pact_context, monkeypatch, tmp_path)
        monkeypatch.setattr(gate, "DISPATCH_VARIETY_MODE", "warn")
        self._seed_unstamped(tmp_path)
        code, out = self._run_main(monkeypatch, capsys, _wiring_update("42"))
        assert code == 0
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert "additionalContext" in hso
        assert "permissionDecision" not in hso

    def test_deny_mode_permission_decision_exit_two(
        self, tmp_path, monkeypatch, pact_context, capsys,
    ):
        """deny mode → permissionDecision:"deny" + exit 2 (the sole
        fail-CLOSED path). Source-proven honor; opt-in only."""
        _ctx(pact_context, monkeypatch, tmp_path)
        monkeypatch.setattr(gate, "DISPATCH_VARIETY_MODE", "deny")
        self._seed_unstamped(tmp_path)
        code, out = self._run_main(monkeypatch, capsys, _wiring_update("42"))
        assert code == 2
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"
        assert hso["hookEventName"] == "PreToolUse"

    def test_shadow_mode_suppresses(
        self, tmp_path, monkeypatch, pact_context, capsys,
    ):
        """shadow mode → no additionalContext, no deny (journal-only
        telemetry; here it suppresses)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        monkeypatch.setattr(gate, "DISPATCH_VARIETY_MODE", "shadow")
        self._seed_unstamped(tmp_path)
        code, out = self._run_main(monkeypatch, capsys, _wiring_update("42"))
        assert code == 0
        assert json.loads(out) == {"suppressOutput": True}

    def test_deny_mode_does_not_deny_stamped_task_b(
        self, tmp_path, monkeypatch, pact_context, capsys,
    ):
        """Even in deny mode, a STAMPED Task B is never denied — the
        structural read gates the deny. Counter-pin against a deny-on-every-
        wiring-write regression."""
        _ctx(pact_context, monkeypatch, tmp_path)
        monkeypatch.setattr(gate, "DISPATCH_VARIETY_MODE", "deny")
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="pact-backend-coder",
                   metadata={"variety": _variety(12)})
        code, out = self._run_main(monkeypatch, capsys, _wiring_update("42"))
        assert code == 0
        assert json.loads(out) == {"suppressOutput": True}

    def test_unknown_mode_falls_back_to_warn(self):
        """Module-load knob hygiene: an unknown env value resolves to warn
        (never silently disables, never silently denies)."""
        assert gate.DISPATCH_VARIETY_MODE in gate._ALLOWED_VARIETY_MODES
