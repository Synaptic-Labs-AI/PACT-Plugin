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


# Default team member set the dispatch-variety predicate resolves against.
# REAL owners are BARE names; the team config maps each bare name to its
# pact-* agentType — the resolution the corrected gate predicate performs.
_DEFAULT_MEMBERS = [
    {"name": "backend-coder", "agentType": "pact-backend-coder"},
    {"name": "test-engineer", "agentType": "pact-test-engineer"},
    {"name": "secretary", "agentType": "pact-secretary"},
    {"name": "explorer", "agentType": "general-purpose"},  # SOLO_EXEMPT (non-pact)
]


def _seed_team_config(tmp_path, monkeypatch, team, members=None):
    """Make the corrected gate predicate resolvable in-test:
      (a) write ~/.claude/teams/{team}/config.json so pact_context.iter_team_members
          resolves bare owners → agentType, and
      (b) seed a plugin root with agents/pact-*.md for each member's pact-*
          agentType + point the context's plugin_root at it + clear the
          registry cache so is_registered_pact_specialist resolves (in
          production the live agents/ dir is found; in-test the glob needs a
          seeded plugin root, mirroring test_dispatch_gate._seed_plugin).
    Forces HOME/.claude resolution by clearing CLAUDE_CONFIG_DIR."""
    import shared.dispatch_helpers as dh
    import shared.pact_context as ctx_module

    members = _DEFAULT_MEMBERS if members is None else members
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    team_dir = tmp_path / ".claude" / "teams" / team
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"members": members}), encoding="utf-8",
    )

    # Seed the specialist registry: one agents/pact-*.md per pact-* agentType.
    plugin_root = tmp_path / "plugin"
    agents_dir = plugin_root / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for m in members:
        at = m.get("agentType", "")
        if isinstance(at, str) and at.startswith("pact-"):
            (agents_dir / f"{at}.md").write_text(
                f"---\nname: {at}\n---\n", encoding="utf-8",
            )
    monkeypatch.setattr(ctx_module, "get_plugin_root", lambda: str(plugin_root))
    dh._specialist_registry.cache_clear()


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


def _wiring_update(task_id, *, owner="backend-coder",
                   add_blocked_by=("A",), agent_type=LEAD):
    """A terminal dispatch-wiring TaskUpdate: owner + addBlockedBy in the SAME
    tool_input. owner is a BARE specialist name (the real shape) resolving via
    team config to a pact agentType. add_blocked_by=None / [] omits it
    (partial-wiring case)."""
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
        """Terminal wiring write linking an unstamped Task B → advisory. The
        BARE-NAME-FIRES case: owner 'backend-coder' resolves via team config to
        agentType pact-backend-coder. This is the case that was DEAD under the
        old owner.startswith('pact-') predicate."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        adv = gate._evaluate_dispatch_variety(_wiring_update("42"))
        assert adv is not None and "metadata.variety" in adv

    def test_fires_reds_if_predicate_reverted_to_prefix(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """NON-VACUITY: the bare-name-fires case proves the gate is ALIVE only
        if it REDs under the reverted (dead) predicate. Simulate the revert by
        monkeypatching the predicate back to owner.startswith('pact-'): with a
        BARE owner that check is False → gate silent → adv is None. So the test
        above genuinely depends on the corrected resolution, not on the
        composite signature alone."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        # Revert: the dead prefix predicate on the BARE owner.
        monkeypatch.setattr(
            gate, "is_pact_specialist_owner",
            lambda owner, team_name: isinstance(owner, str)
            and owner.startswith("pact-"),
        )
        assert gate._evaluate_dispatch_variety(_wiring_update("42")) is None

    def test_silent_when_task_b_is_stamped(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """READ+VALIDATE: a stamped Task B → silent (the structural read is
        what makes the gate precise; it does NOT fire on the composite
        signature alone)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder",
                   metadata={"variety": _variety(12)})
        assert gate._evaluate_dispatch_variety(_wiring_update("42")) is None

    def test_silent_when_task_b_stamped_via_fallback(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """A non-canonical but resolvable stamp (score, no total) → silent.
        The gate uses the shared resolve_variety_total, so any shape that
        resolves at write/read time also satisfies the gate."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        v = _variety(0)
        v.pop("total")
        v["score"] = 9
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={"variety": v})
        assert gate._evaluate_dispatch_variety(_wiring_update("42")) is None

    def test_silent_when_owner_not_in_team_config(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """FAIL-OPEN: a bare owner that is NOT a known team member resolves to
        False → gate silent (never strands). The corrected predicate's
        unresolvable-owner floor."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="ghost-coder", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", owner="ghost-coder")
        ) is None

    def test_silent_when_team_config_missing(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """FAIL-OPEN: no team config on disk → iter_team_members returns [] →
        is_pact_specialist_owner False → gate silent. Consumer-wide-safe: an
        unresolvable config never strands a dispatch."""
        _ctx(pact_context, monkeypatch, tmp_path)
        # Deliberately do NOT seed team config.
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
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
        """A SOLO_EXEMPT owner resolves to a NON-pact agentType
        (explorer→general-purpose) → is_pact_specialist_owner False → never
        fires (scenario 9: SOLO_EXEMPT agents have non-pact agentTypes, so the
        corrected predicate excludes them naturally — no explicit check)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="explorer", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", owner="explorer")
        ) is None

    def test_silent_teachback_subject(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """A Task-A teachback gate subject is exempt (is_teachback_subject).
        The bare owner RESOLVES (passes the trigger), so the subject carve-out
        is what suppresses it."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42",
                   subject="backend: TEACHBACK for the thing",
                   owner="backend-coder", metadata={})
        assert gate._evaluate_dispatch_variety(_wiring_update("42")) is None

    @pytest.mark.parametrize("signal_type", ["blocker", "algedonic"])
    def test_silent_signal_task(
        self, tmp_path, monkeypatch, pact_context, signal_type,
    ):
        """A signal task (completion_type=signal) is exempt via
        is_self_complete_exempt — auditor/blocker signal tasks carry no
        variety obligation. The bare 'auditor' owner RESOLVES to pact-auditor
        (passes the trigger), so the signal carve-out is what suppresses it."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM, members=[
            {"name": "auditor", "agentType": "pact-auditor"},
        ])
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="auditor",
                   metadata={"completion_type": "signal", "type": signal_type})
        assert gate._evaluate_dispatch_variety(_wiring_update(
            "42", owner="auditor")) is None

    def test_silent_secretary_owner(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """LOAD-BEARING carve-out: the secretary (pact-secretary) IS a
        registered specialist, so the bare 'secretary' owner PASSES the
        corrected trigger — meaning is_self_complete_exempt MUST suppress it.
        A secretary-owned wiring write with no variety → SILENT, NOT warn/deny.
        If the carve-out regressed, this would wrongly warn/deny a legit
        secretary dispatch."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="secretary", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", owner="secretary")
        ) is None

    def test_silent_teammate_frame(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """DUAL-MODE: a teammate frame emits nothing (is_lead structural
        discriminator). Short-circuits before owner resolution."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", agent_type=TEAMMATE)
        ) is None


class TestDispatchVarietyPerfGuard:
    """F1 — the extra per-dispatch task-read is BOUNDED: read_task_json must
    NOT be called when the cheap in-memory guards (is_lead / owner-present /
    addBlockedBy-present / taskId-present) fail. Pins the cost-order so a future
    refactor cannot move the disk read ahead of the guards (which would add a
    disk hit to EVERY TaskUpdate in EVERY consumer session, not just genuine
    dispatch-wiring writes)."""

    def _spy_read_task_json(self, monkeypatch):
        """Replace gate.read_task_json with a call-counting spy. Patches the
        name in the GATE module namespace (where it is looked up via the
        module-level `from shared.task_utils import read_task_json`), NOT
        shared.task_utils (where it is defined) — patch-where-looked-up."""
        calls = []
        real = gate.read_task_json

        def _spy(task_id, team_name, *a, **k):
            calls.append((task_id, team_name))
            return real(task_id, team_name, *a, **k)

        monkeypatch.setattr(gate, "read_task_json", _spy)
        return calls

    @pytest.mark.parametrize(
        "frame_desc, frame_factory",
        [
            # is_lead guard: a teammate frame short-circuits first.
            ("teammate_frame",
             lambda: _wiring_update("42", agent_type=TEAMMATE)),
            # owner-absent guard (partial wiring).
            ("owner_absent",
             lambda: _wiring_update("42", owner=None)),
            # addBlockedBy-absent guard (partial wiring).
            ("addblockedby_absent",
             lambda: _wiring_update("42", add_blocked_by=None)),
            # taskId-absent guard.
            ("taskid_absent",
             lambda: {"tool_name": "TaskUpdate", "agent_type": LEAD,
                      "tool_input": {"owner": "backend-coder",
                                     "addBlockedBy": ["A"]}}),
        ],
    )
    def test_no_disk_read_when_cheap_guards_fail(
        self, tmp_path, monkeypatch, pact_context, frame_desc, frame_factory,
    ):
        """Each guard-failing frame must bypass read_task_json entirely."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        calls = self._spy_read_task_json(monkeypatch)
        gate._evaluate_dispatch_variety(frame_factory())
        assert calls == [], (
            f"read_task_json must NOT be called on {frame_desc} "
            f"(cheap guards short-circuit first); got {calls}"
        )

    def test_disk_read_happens_on_a_real_dispatch_wiring_write(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """Counter-case (proves the spy is wired): a genuine wiring write that
        passes every cheap guard DOES read the task — so the test above asserts
        a real short-circuit, not a dead spy."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        calls = self._spy_read_task_json(monkeypatch)
        gate._evaluate_dispatch_variety(_wiring_update("42"))
        assert calls == [("42", TEAM)], (
            f"a real dispatch wiring write must read the linked task once; "
            f"got {calls}"
        )


class TestDispatchVarietyBothModesMatrix:
    """F2 — the is_lead dual-mode discriminator as a single explicit matrix:
    a lead frame FIRES (advisory present); a teammate frame SUPPRESSES (None).
    Consolidates the previously-paired lead-fire / teammate-silent coverage
    into one parametrized case so the both-directions contract reads as a unit.
    """

    @pytest.mark.parametrize(
        "mode, agent_type, expect_fires",
        [
            ("in_process_lead", LEAD, True),
            ("tmux_teammate", TEAMMATE, False),
        ],
    )
    def test_is_lead_matrix(
        self, tmp_path, monkeypatch, pact_context, mode, agent_type,
        expect_fires,
    ):
        """Same unstamped bare-owner wiring write under each frame role:
        lead → advisory; teammate → silent (is_lead structural branch)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        adv = gate._evaluate_dispatch_variety(
            _wiring_update("42", agent_type=agent_type)
        )
        if expect_fires:
            assert adv is not None and "metadata.variety" in adv, (
                f"{mode}: lead frame must fire the advisory; got {adv!r}"
            )
        else:
            assert adv is None, (
                f"{mode}: teammate frame must suppress (is_lead False); "
                f"got {adv!r}"
            )


class TestDispatchVarietyMalformedType:
    """F3 — a variety value of the WRONG TYPE (list / string, not a dict) at
    the gate: resolve_variety_total returns None for it, so the gate treats it
    as the missing-stamp gap and FIRES. Closes the one un-probed malformed
    shape (the dict-shaped malformed cases are covered by the R4 split tests)."""

    @pytest.mark.parametrize(
        "bad_variety",
        [
            pytest.param(["not", "a", "dict"], id="variety_is_list"),
            pytest.param("twelve", id="variety_is_string"),
            pytest.param(12, id="variety_is_bare_int"),
        ],
    )
    def test_fires_on_non_dict_variety(
        self, tmp_path, monkeypatch, pact_context, bad_variety,
    ):
        """A non-dict metadata.variety does not resolve to a total → the gate
        fires the missing-stamp advisory (never crashes, never silently
        passes)."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={"variety": bad_variety})
        adv = gate._evaluate_dispatch_variety(_wiring_update("42"))
        assert adv is not None and "metadata.variety" in adv, (
            f"a non-dict variety ({bad_variety!r}) must fire the missing-stamp "
            f"advisory; got {adv!r}"
        )


class TestDispatchVarietyEnvKnobModes:
    """main()-level: PACT_DISPATCH_VARIETY_MODE selects warn / deny / shadow.
    The module reads the knob at import; monkeypatch the resolved constant."""

    def _run_main(self, monkeypatch, capsys, stdin_obj):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin_obj)))
        with pytest.raises(SystemExit) as exc:
            gate.main()
        return exc.value.code, capsys.readouterr().out

    def _seed_unstamped(self, tmp_path, monkeypatch):
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})

    def test_warn_mode_additional_context_exit_zero(
        self, tmp_path, monkeypatch, pact_context, capsys,
    ):
        _ctx(pact_context, monkeypatch, tmp_path)
        monkeypatch.setattr(gate, "DISPATCH_VARIETY_MODE", "warn")
        self._seed_unstamped(tmp_path, monkeypatch)
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
        self._seed_unstamped(tmp_path, monkeypatch)
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
        self._seed_unstamped(tmp_path, monkeypatch)
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
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder",
                   metadata={"variety": _variety(12)})
        code, out = self._run_main(monkeypatch, capsys, _wiring_update("42"))
        assert code == 0
        assert json.loads(out) == {"suppressOutput": True}

    @pytest.mark.parametrize("env_value, expected", [
        ("deny", "deny"),
        ("DENY", "deny"),       # case-folded
        (" deny ", "deny"),     # whitespace-stripped
        ("Deny", "deny"),
        (" shadow\t", "shadow"),
        ("warn", "warn"),
        ("", "warn"),           # empty is SET-but-invalid → invalid_fallback
        ("bogus", "warn"),      # unknown → the DECLARED invalid_fallback
        ("denY ", "deny"),
    ])
    def test_env_knob_strip_lower_normalization(
        self, monkeypatch, env_value, expected,
    ):
        """The PACT_DISPATCH_VARIETY_MODE read normalizes with .strip().lower()
        BEFORE the membership check, then falls back to the option's DECLARED
        default for anything not in the allowed set. NON-TAUTOLOGICAL: reloads
        the module under the real env value so it exercises the actual
        os.environ read + normalize + fallback (not the already-resolved
        constant).

        WHAT THE FALLBACK ROWS ASSERT, and their polarity has now moved twice:
        first when this gate was armed (`deny` became the default), then again
        when the row declared `invalid_fallback = "warn"`. What they prove is
        the property that survived both moves — an unrecognised token resolves
        to the row's DECLARED landing point for THAT path and to nothing else.
        It is never derived from the token and never lands outside the allowed
        set.

        NOTE THE ASYMMETRY THESE ROWS NOW CARRY, because it is the whole point
        of the declaration: an UNSET variable resolves to `deny` (enforcing),
        while a MISSPELLED opt-down resolves here to `warn`. Absence is consent
        to the shipped posture; an unparseable value is a request the resolver
        could not read, and the only consumers who reach this branch are ones
        trying to opt down.

        The explicit `("warn", "warn")` row is the discriminator: if resolution
        ever stopped consulting the registry and hardcoded a mode, that row and
        the fallback rows could not both hold."""
        import importlib
        monkeypatch.setenv("PACT_DISPATCH_VARIETY_MODE", env_value)
        reloaded = importlib.reload(gate)
        try:
            assert reloaded.DISPATCH_VARIETY_MODE == expected
        finally:
            # Restore the module's default-env resolution for sibling tests.
            monkeypatch.delenv("PACT_DISPATCH_VARIETY_MODE", raising=False)
            importlib.reload(gate)

    def test_deny_mode_fails_open_when_evaluation_raises(
        self, tmp_path, monkeypatch, pact_context, capsys,
    ):
        """ADVERSARIAL fail-OPEN invariant: in DENY mode, an exception inside
        _evaluate_dispatch_variety must NOT brick the TaskUpdate — main()
        catches it, sets variety_gap=None, and falls through to suppress
        (exit 0), NEVER exit-2/deny. The deny is fail-CLOSED only on a
        CONFIRMED missing stamp, never on evaluation uncertainty. A consumer-
        wide deny gate that denied on its own crash would strand every
        legitimate dispatch wiring write whenever the evaluator hit a malformed
        frame — strictly worse than the gap it guards."""
        _ctx(pact_context, monkeypatch, tmp_path)
        monkeypatch.setattr(gate, "DISPATCH_VARIETY_MODE", "deny")
        self._seed_unstamped(tmp_path, monkeypatch)

        def _boom(_input_data):
            raise RuntimeError("simulated evaluator crash")

        monkeypatch.setattr(gate, "_evaluate_dispatch_variety", _boom)
        code, out = self._run_main(monkeypatch, capsys, _wiring_update("42"))
        assert code == 0, "deny mode must fail-OPEN (exit 0) on evaluator crash"
        parsed = json.loads(out)
        assert "permissionDecision" not in parsed.get(
            "hookSpecificOutput", {}
        ), "a crash must never produce a deny verdict"


# =============================================================================
# is_pact_specialist_owner — the corrected-predicate resolution helper.
# Direct unit coverage of the bare owner → agentType → registry resolution +
# the fail-CLOSED-to-False contract (which composes to gate fail-OPEN).
# =============================================================================
class TestIsPactSpecialistOwner:
    def test_true_for_bare_specialist_owner(self, tmp_path, monkeypatch):
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        from shared.dispatch_helpers import is_pact_specialist_owner
        assert is_pact_specialist_owner("backend-coder", TEAM) is True

    def test_true_for_secretary_owner(self, tmp_path, monkeypatch):
        """pact-secretary IS a registered specialist → True. (The gate's
        is_self_complete_exempt carve-out, NOT this helper, suppresses it.)"""
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        from shared.dispatch_helpers import is_pact_specialist_owner
        assert is_pact_specialist_owner("secretary", TEAM) is True

    def test_false_for_solo_exempt_owner(self, tmp_path, monkeypatch):
        """explorer→general-purpose is a NON-pact agentType (no
        agents/general-purpose.md) → False."""
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        from shared.dispatch_helpers import is_pact_specialist_owner
        assert is_pact_specialist_owner("explorer", TEAM) is False

    def test_false_for_unknown_owner(self, tmp_path, monkeypatch):
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        from shared.dispatch_helpers import is_pact_specialist_owner
        assert is_pact_specialist_owner("ghost", TEAM) is False

    def test_false_for_missing_team_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        from shared.dispatch_helpers import is_pact_specialist_owner
        assert is_pact_specialist_owner("backend-coder", TEAM) is False

    def test_false_for_empty_inputs(self):
        from shared.dispatch_helpers import is_pact_specialist_owner
        assert is_pact_specialist_owner("", TEAM) is False
        assert is_pact_specialist_owner("backend-coder", "") is False
        assert is_pact_specialist_owner(None, TEAM) is False

    def test_false_and_never_raises_on_iter_members_exception(self, monkeypatch):
        """The bare-except fail-closed wrap: if iter_team_members raises (e.g. the
        get_claude_config_dir→Path.home RuntimeError seam that ESCAPES
        iter_team_members' own typed except), the helper returns False and never
        propagates — so the gate fail-OPENS rather than crashing."""
        import shared.pact_context as ctx_module
        from shared.dispatch_helpers import is_pact_specialist_owner

        def _raise(team_name, teams_dir=None):
            raise RuntimeError("simulated config-dir resolution failure")

        monkeypatch.setattr(ctx_module, "iter_team_members", _raise)
        assert is_pact_specialist_owner("backend-coder", TEAM) is False


# =============================================================================
# The FRAME GATE — enforcement now matches the emit's canonical-frame predicate.
# =============================================================================
class TestEnforcementReachesTheCanonicalFrame:
    """The emit recorded a dispatch_site on ANY canonical journal frame while
    enforcement only reached the lead's, so an in-process teammate's wiring
    write was COUNTED and never ENFORCED.

    WHY THE PRE-EXISTING TEAMMATE-FRAME ARM STILL PASSES, and it is not a
    vacuity: ``is_canonical_journal_frame`` short-circuits to False when the
    frame carries no ``session_id`` at all, BEFORE it pays the config read.
    Frames built without one are therefore unaffected by this change and
    correctly still emit nothing. The widening is reachable ONLY through the
    topology leg, which needs BOTH a ``session_id`` on the frame AND a
    ``leadSessionId`` in the team config — so a test that omits either cannot
    observe it, and would report a live change as a no-op.

    THE TOPOLOGY LEG IS A BLIND SPOT FOR THE REST OF THIS FILE, stated at
    class level because the consequence is not local to these two arms. The
    pair below are the ONLY frames in this file that reach that leg. Every
    other frame here short-circuits before it: ``_wiring_update`` builds no
    ``session_id``, and the default ``_seed_team_config`` writes no
    ``leadSessionId``, so those frames resolve through the ``is_lead`` leg or
    exit on the absent ``session_id``. These two arms reach it only because
    they supply BOTH by hand — ``frame["session_id"] = ...`` and
    ``_seed_lead_session``.

    WHAT THAT COSTS, and it cuts BOTH ways: outside this class the file
    cannot detect a change to the frame predicate in EITHER direction. A
    widening would go unobserved, and so would a narrowing back to
    ``is_lead`` — the frames would answer identically under both predicates,
    so a green run here is not evidence about the frame gate.

    THE CURE IS THE FIXTURE DEFAULTS, not more arms: seed a ``session_id``
    in ``_wiring_update`` and a ``leadSessionId`` in ``_seed_team_config``,
    so that reaching the leg is the file's default rather than something two
    tests opt into. Until then, read a frame-predicate claim as covered only
    if it is asserted here.

    MUTATION THAT REDDENS: restore ``is_lead`` in ``_evaluate_dispatch_variety``.
    The first test flips to None. The tmux twin below does NOT — it is False
    under both predicates, which is what makes the pair a discriminator rather
    than a pair of restatements.
    """

    @staticmethod
    def _seed_lead_session(tmp_path, team, sid):
        cfg = tmp_path / ".claude" / "teams" / team / "config.json"
        data = json.loads(cfg.read_text())
        data["leadSessionId"] = sid
        cfg.write_text(json.dumps(data), encoding="utf-8")

    def test_IN_PROCESS_teammate_frame_is_now_ENFORCED(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """session_id == leadSessionId: one process, one session. The write
        lands in the canonical journal, so it must also be enforced.

        SCOPE, STATED BECAUSE THIS ARM CANNOT CERTIFY THE WHOLE FIX: it calls
        `_evaluate_dispatch_variety` directly, with `_ctx` having pre-seeded
        `_context_path`. `init()` early-returns when that is set, so this arm
        is TRUE EITHER SIDE of the frame-gate/`init` ORDERING — it distinguishes
        the PREDICATE (in-process vs tmux) and nothing else. The ordering is
        certified by `test_dispatch_variety_deny_honor_probe`, which drives
        `main()` as a real subprocess with an unseeded context. Do not read a
        green here as covering that.
        """
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        self._seed_lead_session(tmp_path, TEAM, "S-LEAD")
        _seed_task(tmp_path, TEAM, "42", subject="impl foo", owner="", metadata={})
        frame = _wiring_update("42", agent_type=TEAMMATE)
        frame["session_id"] = "S-LEAD"
        assert gate._evaluate_dispatch_variety(frame) is not None, (
            "an in-process teammate's un-stamped wiring write was recorded as "
            "a dispatch site and NOT enforced — the gap this fix closes"
        )

    def test_TMUX_teammate_frame_is_still_NOT_enforced(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """Distinct session_id: a separate process writing a separate journal.
        Unchanged by this fix, and the bound on how far it widens."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        self._seed_lead_session(tmp_path, TEAM, "S-LEAD")
        _seed_task(tmp_path, TEAM, "42", subject="impl foo", owner="", metadata={})
        frame = _wiring_update("42", agent_type=TEAMMATE)
        frame["session_id"] = "S-OTHER"
        assert gate._evaluate_dispatch_variety(frame) is None, (
            "a tmux teammate frame was enforced — this fix must widen to the "
            "in-process frame ONLY"
        )


# =============================================================================
# The OWNER as of this write — the exemption over-block.
# =============================================================================
class TestExemptionReadsTheOwnerAsOfThisWrite:
    """The gate ADMITS on ``tool_input["owner"]`` but the exemption predicate
    resolved ``task["owner"]`` from DISK. At the terminal wiring write the disk
    owner is still empty — THIS write is what sets it — and
    ``_is_exempt_agent_type`` returns False on an empty owner, so no exempt
    owner could ever reach its carve-out. Every exempt dispatch fell through to
    enforcement, on a deny default. An OVER-BLOCK, which is the cardinal
    failure for a control that can refuse a consumer's write.

    MUTATION THAT REDDENS: in ``_evaluate_dispatch_variety``, pass ``task``
    instead of ``task_as_of_this_write`` to ``is_self_complete_exempt``. The
    first test flips to an advisory. That is the base behaviour, measured.

    THE SECOND TEST IS THE LOAD-BEARING ARM. An overlay that exempted
    everything would make the first test pass while silently retiring the
    control, which is worse than the bug it fixes: it closes a Blocking finding
    AND satisfies the acceptance criterion while leaving nothing enforced. Only
    an arm that MUST still deny can tell the two apart.
    """

    def test_exempt_owner_in_the_WIRE_is_carved_out(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """Disk owner empty, secretary in the wire, no stamp -> ALLOW."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo", owner="")
        adv = gate._evaluate_dispatch_variety(
            _wiring_update("42", owner="secretary"),
        )
        assert adv is None, (
            "an exempt owner carried in the wiring write was refused — the "
            f"carve-out cannot see the owner that admitted the write: {adv!r}"
        )

    def test_ordinary_specialist_in_the_WIRE_still_DENIES(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """The must-deny control. Same shape, non-exempt agentType -> DENY.
        Guards against an overlay that exempts everything."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo", owner="")
        adv = gate._evaluate_dispatch_variety(
            _wiring_update("42", owner="backend-coder"),
        )
        assert adv is not None, (
            "an ordinary specialist's un-stamped dispatch was ALLOWED — the "
            "overlay retired the control instead of repairing the carve-out"
        )

    def test_overlay_does_not_mutate_the_task_record(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """The overlay builds a new dict; the caller's task is untouched."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo", owner="")
        gate._evaluate_dispatch_variety(_wiring_update("42", owner="secretary"))
        on_disk = json.loads(
            (tmp_path / ".claude" / "tasks" / TEAM / "42.json").read_text()
        )
        assert on_disk["owner"] == "", (
            f"the exemption overlay wrote through to the task record: {on_disk!r}"
        )

    # ---- RE-WIRE: the only arms where "incoming wins" is observable, because
    # "disk-first" and "incoming-first" agree whenever the disk owner is empty,
    # which is the whole ordinary wiring case. Both directions are pinned; the
    # second is a DELIBERATE deny-widening and must not be read as a regression.

    def test_rewire_TO_an_exempt_owner_is_carved_out(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """disk=backend-coder -> wire=secretary. The task WILL be secretary-
        owned, so it is exempt. Refusing it would be a new over-block created
        by the fix for an over-block."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo", owner="backend-coder")
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", owner="secretary"),
        ) is None

    def test_rewire_AWAY_from_an_exempt_owner_now_DENIES(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """disk=secretary -> wire=backend-coder. INTENDED DENY-WIDENING.

        Keying on the stale DISK owner would let a write that assigns a real
        specialist inherit the departing secretary's exemption — an unstamped
        dispatch landing on a live coder in one ordinary-looking write. The
        post-write model asks who the task WILL be owned by, so it denies."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo", owner="secretary")
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", owner="backend-coder"),
        ) is not None

    def test_overlay_does_NOT_widen_the_exempt_AGENT_TYPE_set(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """A signal-shaped task whose metadata.type is ABSENT satisfies NEITHER
        exemption surface: pact-auditor is not in SELF_COMPLETE_EXEMPT_AGENT_TYPES
        and surface 2 needs type in {blocker, algedonic}. No owner overlay can
        reach it, and it MUST NOT — granting it would mean enlarging the exempt
        agentType set, which is a security change with its own review."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(
            tmp_path, monkeypatch, TEAM,
            members=_DEFAULT_MEMBERS + [
                {"name": "auditor", "agentType": "pact-auditor"},
            ],
        )
        _seed_task(tmp_path, TEAM, "42", subject="impl foo", owner="",
                   metadata={"completion_type": "signal"})
        assert gate._evaluate_dispatch_variety(
            _wiring_update("42", owner="auditor"),
        ) is not None


# =============================================================================
# The disk/incoming OVERLAY — the cardinal over-block fix.
# =============================================================================
def _wiring_update_with_metadata(task_id, metadata, **kw):
    """A wiring write that ALSO carries metadata — the atomic wire+stamp."""
    payload = _wiring_update(task_id, **kw)
    payload["tool_input"]["metadata"] = metadata
    return payload


class TestDispatchVarietyReadsTheIncomingWrite:
    """THE CARDINAL CASE. `TaskUpdate(B, owner=..., addBlockedBy=[A],
    metadata={"variety": {...}})` wires and stamps in ONE call. At PreToolUse
    the stamp is in the write and not yet on disk, so a disk-only read REFUSES
    a faithful single command — which for a control that can deny is the
    failure that must never ship.

    MUTATION THAT REDDENS: in `_evaluate_dispatch_variety`, replace
    `variety_stamp_as_of_write(tool_input, task)` with the disk-only read
    (`task.get("metadata", {}).get("variety")`).

    WHAT THAT MUTATION ACTUALLY DOES, measured rather than asserted, because
    the members no longer move together and a single summary sentence about
    them would be false:
      - the ATOMIC arms (a stamp carried in the write against a bare disk)
        flip ALLOW -> advisory. This is the cardinal over-block, and it is
        the direction the class is named for.
      - the arms with a stamp ON DISK — partial, delete, malformed, same-key
        — flip the OTHER way, DENY -> allow: the disk-only read sees the very
        stamp the write is about to destroy and blesses it. Same defect,
        opposite symptom.
      - some members do not move at all, and their staying green is a PASS
        rather than a failed mutation. Named rather than counted, because a
        tally here rots the moment a member is added:
        `test_STILL_DENIES_when_neither_side_carries_a_stamp` and
        `test_a_variety_DELETE_with_NOTHING_on_disk_is_not_a_replace` have
        no disk stamp for the two reads to differ over, and
        `test_atomic_stamp_via_the_variety_score_sibling` resolves through
        `metadata["variety_score"]`, which this mutation does not touch.

    So do not read this class as a block that reddens on cue. A mutation
    sweep over it must check the DIRECTION of each flip, and must expect two
    non-movers; counting reds alone would score a partial disarm as success.

    INSTRUMENT HAZARD FOR ANY SUCH SWEEP: `TestDispatchVarietyEnvKnobModes`
    above calls `importlib.reload(gate)`, which rebinds the real helper and
    silently disarms an in-process patch for every test defined after it —
    including this whole class. Re-apply the mutation per test, or the null
    is an artefact of the reload rather than a property of these arms.
    """

    def test_atomic_wire_and_stamp_is_SILENT(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """PARTIALNESS SHAPE (c) — THE MUST-HOLD TWIN. A complete stamp
        carried in the wiring write itself must still be ALLOWED.

        It is the non-vacuity control for the two partial arms below: a gate
        rebuilt to refuse anything with an incoming `variety` would satisfy
        both of those and still be wrong, and this is the only arm that can
        tell the difference. Deliberately NOT discriminating against the
        union — union and replace agree here, because the disk side is empty
        — so if this one ever moves, the write-model change over-reached
        rather than a model being distinguished."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        adv = gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata("42", {"variety": _variety(12)}),
        )
        assert adv is None, (
            "a wiring write CARRYING a complete variety stamp was refused — "
            f"this is the cardinal over-block: {adv!r}"
        )

    def test_atomic_stamp_survives_a_task_with_no_metadata_key(
        self, tmp_path, monkeypatch, pact_context,
    ):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder")
        assert gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata("42", {"variety": _variety(12)}),
        ) is None

    def test_atomic_stamp_via_the_variety_score_sibling(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """The non-canonical spelling resolve_variety_total documents as
        candidate 3. Reaching it needs `variety_stamp_as_of_write` to hand the
        resolver a DICT rather than None: candidates 1, 2 and 4 all read from
        `variety` and fall through on `{}`, which is what lets candidate 3 —
        the only one that reads `metadata` instead — be consulted at all.

        THAT IS WHY THE HELPER RETURNS `{}` AND NEVER None, and it is the
        mechanism that made this path reachable. The resolver's own non-dict
        coercion cannot do it here, because this call site never hands it a
        non-dict; the helper's return type is doing the work."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata("42", {"variety_score": 12}),
        ) is None

    def test_STILL_DENIES_when_neither_side_carries_a_stamp(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """The other direction, and the reason the overlay is not a hole: an
        incoming metadata that carries no variety leaves the gate firing."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        assert gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata("42", {"handoff": {"produced": ["f"]}}),
        ) is not None

    def test_a_SUBSET_partial_restamp_that_lands_UNSTAMPED_denies(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """PARTIALNESS SHAPE (b) — the incoming keys are a STRICT SUBSET of
        the disk stamp's. Post-write the task carries `{"novelty": 4}` and
        resolves to nothing, because `TaskUpdate` replaces `metadata.variety`
        wholesale, so the refusal is truthful.

        This case was once ALLOWED, on the reasoning that refusing "a
        correctly-stamped dispatch" would be a cardinal over-block. That
        reasoning read the UNION of disk and wire — a state the task never
        holds — so allowing it let an un-stamped dispatch through with the
        gate's permission.

        DISCRIMINATING: green here, RED under a mutation restoring the union,
        which resolves 12 from the surviving disk keys and returns None.

        Its diagnosis is the PARTIAL-REPLACE sentence, not the values one:
        `{"novelty": 4}` is a perfectly good value and sending the consumer
        to re-read it wastes their time. The same-key sibling below is what
        keeps that distinction honest."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={"variety": _variety(12)})
        adv = gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata("42", {"variety": {"novelty": 4}}),
        )
        assert adv is not None, (
            "a write that leaves the task un-stamped was allowed"
        )
        assert "REPLACES the stamp already on disk" in adv, (
            "a partial write was diagnosed as bad VALUES; the values are "
            f"fine and the write is what dropped the stamp: {adv!r}"
        )

    def test_a_DISJOINT_partial_restamp_that_lands_UNSTAMPED_denies(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """PARTIALNESS SHAPE (a) — THE CASE A CONTAINMENT READING EXCLUDES,
        and the reason the precondition is partialness rather than subset.

        The incoming stamp carries keys the disk stamp LACKS (`novelty`,
        `scope` against a disk holding only `total`). It is not a subset of
        anything, yet it still REPLACES the disk block: post-write the task
        holds `{"novelty": 4, "scope": 4}`, which resolves to nothing because
        two dimensions are neither a total nor all four.

        Had the arms been built to containment, this fixture would not have
        satisfied the precondition and the shape would have gone unpinned —
        a green report over a population that excludes the defect the write
        model was corrected for.

        DISCRIMINATING: green here, RED under a mutation restoring the union,
        which sees the disk `total` the write actually destroys, resolves 12
        and returns None."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={"variety": {"total": 12}})
        adv = gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata(
                "42", {"variety": {"novelty": 4, "scope": 4}},
            ),
        )
        assert adv is not None, (
            "a disjoint partial write that lands the task un-stamped was "
            "allowed — the containment reading of the precondition"
        )
        assert "REPLACES the stamp already on disk" in adv, (
            f"the partial-replace diagnosis did not reach a disjoint write, "
            f"which is the shape it was written for: {adv!r}"
        )

    def test_a_variety_DELETE_over_a_stamped_task_denies(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """PARTIALNESS AT FULL STRENGTH. `metadata={"variety": None}` is the
        platform's remove-the-key form: it NAMES `variety` and carries
        nothing forward, so it drops EVERY key the disk held and the task
        lands un-stamped.

        It takes the replace sentence rather than the values one for the
        reason the partial arms do, only more so — there are no values here
        to re-read, so sending the caller to look at them is advice they
        cannot act on.

        DISCRIMINATING: green here, RED under a mutation restoring the union,
        where a null incoming contributes nothing and the disk stamp
        survives to resolve 12."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={"variety": _variety(12)})
        adv = gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata("42", {"variety": None}),
        )
        assert adv is not None, "a write that deletes the stamp was allowed"
        assert "REPLACES the stamp already on disk" in adv, (
            f"a delete was diagnosed as bad VALUES, and it carries none: "
            f"{adv!r}"
        )

    def test_a_variety_DELETE_with_NOTHING_on_disk_is_not_a_replace(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """THE DISK-SIDE GUARD, and the non-vacuity control for the arm
        above. With no stamp on disk the same write drops nothing — it is a
        null write against an already-un-stamped task, not a replace — so the
        replace sentence would be a false explanation.

        Without this arm the guard could be deleted and the suite would stay
        green while the gate told callers their write destroyed a stamp that
        was never there.

        THIS ARM ONCE ASSERTED ONLY THE NEGATIVE, and that is why the wrong
        sentence shipped. It checked that the answer was not the REPLACE
        diagnosis and stopped there — which was satisfied by the ADD-THE-BLOCK
        sentence (correct) and equally by the RE-READ-THE-VALUES sentence
        (wrong, and what the gate actually returned). On a three-way selector
        a negative assertion excludes ONE branch and licenses the rest.

        It now asserts the branch it EXPECTS. Keep it that way: assert the
        positive, and if the alternatives matter, exclude them explicitly the
        way the selection table does."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={})
        adv = gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata("42", {"variety": None}),
        )
        assert adv is not None
        assert FIRST_ADD_THE_BLOCK in adv, (
            f"a null write against a task that never carried a stamp must be "
            f"told to stamp the block — nothing was supplied to re-read and "
            f"nothing was on disk to replace: {adv!r}"
        )

    def test_a_MALFORMED_variety_value_keeps_the_values_sentence(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """THE OTHER EDGE OF THE DELETE WIDENING. A `variety` arriving as a
        string also lands the task un-stamped and also drops the disk keys —
        but here the consumer really did send a bad VALUE, so the values
        sentence is the true one. Only a null reads as 'carried nothing
        forward' rather than 'sent something wrong'.

        This is what stops the widening swallowing the second diagnosis
        whole."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={"variety": _variety(12)})
        adv = gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata("42", {"variety": "12"}),
        )
        assert adv is not None and "does NOT resolve" in adv
        assert "REPLACES the stamp already on disk" not in adv, (
            f"a malformed VALUE was diagnosed as a write-shape defect: {adv!r}"
        )

    def test_an_incoming_write_that_JUNKS_the_only_resolving_key_denies(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """THE DISCRIMINATOR FOR THE DIAGNOSIS SPLIT, and the one same-key
        case in this class. The write overwrites `total` in place: it drops
        NO disk key, so the field list is intact and the VALUES really are
        the defect. It must therefore keep the values sentence while both
        partial arms above take the replace sentence.

        Without this arm the two diagnoses would be restatements — every
        fixture carrying an incoming variety would take the same branch and
        nothing would establish that the gate tells them apart."""
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        _seed_task(tmp_path, TEAM, "42", subject="impl foo",
                   owner="backend-coder", metadata={"variety": {"total": 12}})
        adv = gate._evaluate_dispatch_variety(
            _wiring_update_with_metadata("42", {"variety": {"total": "x"}}),
        )
        assert adv is not None and "does NOT resolve" in adv
        assert "REPLACES the stamp already on disk" not in adv, (
            "a same-key overwrite was diagnosed as a partial replace — it "
            f"drops nothing, so the values are the real defect: {adv!r}"
        )


# The three diagnoses, each keyed on a phrase unique to its sentence. Chosen
# to be mutually exclusive as SUBSTRINGS so a row can assert one is present AND
# the other two absent — deliberately NOT keying SECOND on "does NOT resolve",
# which differs from a phrase inside the THIRD sentence only by letter case.
FIRST_ADD_THE_BLOCK = "no metadata.variety stamp at all"
SECOND_REREAD_VALUES = "so re-read the VALUES"
THIRD_PARTIAL_REPLACE = "REPLACES the stamp already on disk"
ALL_DIAGNOSES = (FIRST_ADD_THE_BLOCK, SECOND_REREAD_VALUES, THIRD_PARTIAL_REPLACE)


class TestDispatchVarietyDiagnosisSplit:
    """ONE TRIGGER, THREE MESSAGES, because the remedies are mutually wrong:
    'write the block' versus 'the block you wrote does not resolve' versus
    'what you sent is fine but it replaced a fuller stamp'. NOT a carve-out —
    all three still fire.

    MUTATION THAT REDDENS: make `_variety_stamp_attempted` return a constant.
    Measured, both directions: forced True reddens the absent rows, forced
    False reddens the unresolvable rows. Neither constant moves a VERDICT —
    every failure is a message assertion — which is what establishes this
    predicate as sentence-load-bearing rather than decision-load-bearing.
    """

    def _adv(self, tmp_path, monkeypatch, pact_context, disk, incoming=None):
        _ctx(pact_context, monkeypatch, tmp_path)
        _seed_team_config(tmp_path, monkeypatch, TEAM)
        kw = {"subject": "impl foo", "owner": "backend-coder"}
        if disk is not None:
            kw["metadata"] = disk
        _seed_task(tmp_path, TEAM, "42", **kw)
        frame = (_wiring_update("42") if incoming is None
                 else _wiring_update_with_metadata("42", incoming))
        return gate._evaluate_dispatch_variety(frame)

    @pytest.mark.parametrize("disk,incoming", [
        pytest.param({}, None, id="no_metadata_content"),
        pytest.param(None, None, id="no_metadata_key"),
        pytest.param({}, {"handoff": {}}, id="incoming_without_variety"),
    ])
    def test_nothing_written_says_ADD_the_block(
        self, tmp_path, monkeypatch, pact_context, disk, incoming,
    ):
        adv = self._adv(tmp_path, monkeypatch, pact_context, disk, incoming)
        assert adv is not None
        assert "no metadata.variety stamp at all" in adv
        assert "does NOT resolve" not in adv

    @pytest.mark.parametrize("disk,incoming", [
        pytest.param({"variety": {}}, None, id="empty_variety_dict"),
        pytest.param({"variety": "12"}, None, id="variety_is_a_string"),
        pytest.param({"variety": {"total": 99}}, None, id="total_out_of_range"),
        pytest.param({"variety": {"total": True}}, None, id="total_is_a_bool"),
        pytest.param({"variety": {"total": "12"}}, None, id="total_is_a_string"),
        pytest.param({"variety_score": "x"}, None, id="sibling_is_junk"),
        pytest.param({}, {"variety": {}}, id="incoming_empty_variety"),
    ])
    def test_something_written_says_RE_READ_the_values(
        self, tmp_path, monkeypatch, pact_context, disk, incoming,
    ):
        adv = self._adv(tmp_path, monkeypatch, pact_context, disk, incoming)
        assert adv is not None
        assert "does NOT resolve" in adv
        assert "no metadata.variety stamp at all" not in adv, (
            "a consumer who DID stamp is being told to add the block — that "
            "sends them hunting for a missing field instead of at the values"
        )

    @pytest.mark.parametrize("disk,incoming,expected", [
        pytest.param(None, {"variety": None}, FIRST_ADD_THE_BLOCK,
                     id="null_wire_no_metadata_key_at_all"),
        pytest.param({}, {"variety": None}, FIRST_ADD_THE_BLOCK,
                     id="null_wire_no_disk_stamp"),
        pytest.param({}, None, FIRST_ADD_THE_BLOCK,
                     id="plain_absent"),
        pytest.param({"variety": _variety(12)}, {"variety": None},
                     THIRD_PARTIAL_REPLACE, id="null_wire_over_a_stamp"),
        pytest.param({"variety": _variety(12)}, {"variety": {}},
                     THIRD_PARTIAL_REPLACE, id="empty_dict_wire_over_a_stamp"),
        pytest.param({"variety": _variety(12)}, {"variety": "12"},
                     SECOND_REREAD_VALUES, id="string_wire_over_a_stamp"),
        pytest.param({}, {"variety": "12"}, SECOND_REREAD_VALUES,
                     id="string_wire_no_disk_stamp"),
        pytest.param({"variety": {"total": 99}}, None, SECOND_REREAD_VALUES,
                     id="bad_total_on_disk_wire_silent"),
        pytest.param({"variety": {}}, {"variety": None}, SECOND_REREAD_VALUES,
                     id="null_wire_over_an_EMPTY_disk_variety"),
        pytest.param({"variety_score": "x"}, {"variety": None},
                     SECOND_REREAD_VALUES, id="null_wire_over_a_disk_sibling"),
    ])
    def test_every_row_selects_EXACTLY_ONE_diagnosis(
        self, tmp_path, monkeypatch, pact_context, disk, incoming, expected,
    ):
        """THE WHOLE SELECTION TABLE, asserted POSITIVELY and EXHAUSTIVELY.

        Each row names the one sentence it must carry, and the row also
        asserts the OTHER TWO ARE ABSENT. That second half is the point of the
        table, and it is here because of how the null-with-no-disk-stamp row
        got shipped wrong:

        AN `assert X not in adv` ON AN N-WAY SELECTOR IS SATISFIED BY EVERY
        BRANCH EXCEPT ONE. The arm that held this exact fixture asserted only
        that the answer was not the THIRD sentence. It was green while the
        answer was the SECOND, which is also wrong. A negative assertion over
        a set with more than two members is a partial claim wearing a total
        one's clothes — so assert the branch you EXPECT, not the branch you
        do not.

        Rows are stated as data rather than prose so a future member is added
        to a table rather than argued about in a docstring.
        """
        adv = self._adv(tmp_path, monkeypatch, pact_context, disk, incoming)
        assert adv is not None, "every row in this table must still DENY"
        assert expected in adv, (
            f"row selected the wrong diagnosis; expected {expected!r}: {adv!r}"
        )
        for other in ALL_DIAGNOSES:
            if other != expected:
                assert other not in adv, (
                    f"two diagnoses in one message — {other!r} leaked into a "
                    f"row that must carry only {expected!r}: {adv!r}"
                )

    def test_BOTH_states_still_deny(
        self, tmp_path, monkeypatch, pact_context,
    ):
        """The non-carve-out. If the unresolvable branch is ever softened into
        a pass, this is what reddens."""
        absent = self._adv(tmp_path, monkeypatch, pact_context, {})
        unresolvable = self._adv(
            tmp_path, monkeypatch, pact_context, {"variety": {"total": 99}})
        assert absent is not None and unresolvable is not None
        assert absent != unresolvable, "the two states must not share a message"
