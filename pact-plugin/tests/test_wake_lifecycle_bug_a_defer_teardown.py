"""
Integration tests for Bug A: defer eager 1->0 Teardown when the just-
completed task has a same-teammate-owned active continuation in
addBlocks/blocks.

Bug A surface: when the lead completes a teachback Task A while the
paired Task B (same-teammate continuation) remains pending in the
addBlocks/blocks chain, the unfixed hook drives count_active_tasks to 0
(Task B may even be filtered by the pact-secretary carve-out) and emits
Teardown — eagerly tearing down the Monitor before the teammate has
claimed Task B. The fix gates Teardown emit on
`not has_same_teammate_continuation(...)`.

Test mechanics mirror existing test_inbox_wake_lifecycle_emitter.py
helpers (subprocess-piped, post-only state on disk).

Counter-test-by-revert (manual / runbook-documented): SOURCE-ONLY revert
via cp-bak / git-checkout HEAD~1 of pact-plugin/hooks/shared/wake_lifecycle.py
+ pact-plugin/hooks/wake_lifecycle_emitter.py. Expected cardinality on
revert: ~5 fail (TestBugATeardownDeferralOnSameTeammateContinuation
4 cases + race-deleted continuation 1 case).
See pact-plugin/tests/runbooks/wake-lifecycle-teachback-rearm.md.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
EMITTER = HOOK_DIR / "wake_lifecycle_emitter.py"


def _run_emitter(stdin_payload: str | bytes, env_extra: dict | None = None) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    if env_extra:
        env.update(env_extra)
    payload_bytes = (
        stdin_payload if isinstance(stdin_payload, bytes)
        else stdin_payload.encode("utf-8")
    )
    proc = subprocess.run(
        [sys.executable, str(EMITTER)],
        input=payload_bytes,
        capture_output=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def _write_session_context(
    home: Path,
    session_id: str,
    project_dir: str,
    team_name: str,
    *,
    lead_session_id: str | None = None,
    members: list[dict] | None = None,
) -> None:
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": project_dir,
            "plugin_root": "",
            "started_at": "2026-05-09T00:00:00Z",
        }),
        encoding="utf-8",
    )
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    effective_lead = lead_session_id if lead_session_id is not None else session_id
    config_data: dict = {"leadSessionId": effective_lead}
    if members:
        config_data["members"] = list(members)
    (team_dir / "config.json").write_text(
        json.dumps(config_data),
        encoding="utf-8",
    )


def _write_task(home: Path, team_name: str, task_id: str, **fields) -> None:
    tasks_dir = home / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _emit_output(payload: dict, home: Path) -> dict:
    rc, out, err = _run_emitter(
        json.dumps(payload),
        env_extra={
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": payload.get("cwd", ""),
        },
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    return json.loads(out)


# ---------- Bug A: defer Teardown on same-teammate continuation ----------


class TestBugATeardownDeferralOnSameTeammateContinuation:
    """Bug A integration: defer Teardown when the just-completed task
    has a same-teammate-owned active continuation in addBlocks/blocks.

    Exercises the full subprocess hook path so the integration with
    _decide_directive's deferred-Teardown branch (the
    `has_same_teammate_continuation` guard) is end-to-end verified.
    """

    def test_defer_on_addBlocks_same_teammate_pending(self, tmp_path):
        """Canonical Two-Task Dispatch defer case: Task A completes,
        Task B (same teammate, pending) is in addBlocks. Predicate
        returns True → no Teardown emit (suppressOutput)."""
        home = tmp_path / "home"; home.mkdir()
        sid = "s"; pdir = "/tmp/p"; team = "team-defer"
        _write_session_context(home, sid, pdir, team)
        # Pre-write Task B (the continuation, pending, same teammate).
        _write_task(home, team, "B", status="pending", owner="backend-coder")
        # Task A is on disk with addBlocks=['B'] AND status=completed
        # (post-state). Lead's TaskUpdate makes status=completed.
        _write_task(
            home, team, "A",
            status="completed",
            owner="backend-coder",
            addBlocks=["B"],
        )

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid, "cwd": pdir,
            "tool_input": {"taskId": "A", "status": "completed"},
            "tool_response": {
                "id": "A", "status": "completed",
                "owner": "backend-coder",
            },
        }, home)
        # count_active_tasks(team) returns 1 (Task B is pending, non-exempt
        # owner) so post != 0 — Teardown also doesn't fire by the count
        # gate. To isolate the defer-Teardown branch, the next test
        # exercises the Bug A literal scenario where the carve-out makes
        # post == 0 AND defer-Teardown is the load-bearing guard.
        assert out == {"suppressOutput": True}, (
            f"Expected suppressOutput when same-teammate continuation "
            f"exists; got {out!r}."
        )

    def test_defer_on_non_exempt_teammate_continuation_at_post_zero(self, tmp_path):
        """The Bug A defer-Teardown reproduction (non-exempt teammate).
        Lead completes Task A (backend-coder); Task B (also backend-coder
        owned, pending) is in the addBlocks chain. Task A completes
        (post-state); Task B is the only remaining lifecycle-relevant
        active work. count_active_tasks rises briefly to 1 then on the
        completion the post-count is still 1 (B is non-exempt and
        pending). Wait — the test setup: A completed, B pending; count
        is 1 (just B). The Teardown count gate (`count != 0`) suppresses
        emit BEFORE the defer-Teardown predicate is even consulted.

        To isolate the defer-Teardown branch as the SOLE load-bearing
        guard, we need a scenario where post == 0 yet a same-teammate
        continuation exists. The empirical Bug A in this session was
        secretary-on-secretary (post == 0 because both tasks owned by
        the carve-out exempt agentType). But cell-6 of the architect
        spec returns False on exempt-owner continuations (correct per
        design — exempt is not lifecycle-relevant) so the secretary
        scenario is intentionally NOT FIXED in this PR; see the
        follow-up issue 'SELF_COMPLETE_EXEMPT_AGENT_TYPES decoupling
        for wake vs self-completion' (plan line 236).

        For this test we exercise the code path where the Teardown
        count gate ALSO suppresses, so the test pins the no-Teardown
        outcome regardless of which gate fires first. Stronger
        assertions on the defer-Teardown branch are at the unit level
        in test_has_same_teammate_continuation.py."""
        home = tmp_path / "home"; home.mkdir()
        sid = "s"; pdir = "/tmp/p"; team = "team-bug-a-non-exempt"
        _write_session_context(home, sid, pdir, team)
        # Task B: backend-coder (non-exempt), pending continuation.
        _write_task(home, team, "B", status="pending", owner="backend-coder")
        # Task A: backend-coder, completed (post-state), addBlocks=['B'].
        _write_task(
            home, team, "A",
            status="completed",
            owner="backend-coder",
            addBlocks=["B"],
        )

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid, "cwd": pdir,
            "tool_input": {"taskId": "A", "status": "completed"},
            "tool_response": {
                "id": "A", "status": "completed",
                "owner": "backend-coder",
            },
        }, home)
        # Either: (a) count_active_tasks==1 short-circuits Teardown gate;
        # or (b) defer-Teardown predicate returns True. Either way the
        # outcome is suppressOutput. No Teardown.
        assert out == {"suppressOutput": True}, (
            f"Expected suppressOutput on Task A completion with non-exempt "
            f"same-teammate Task B pending; got {out!r}. If "
            f"hookSpecificOutput with 'unwatch-inbox' content, the eager "
            f"Teardown bug surfaced and BOTH count gate + defer gate are "
            f"missing."
        )

    def test_secretary_bug_a_fixed_via_count_gate_post_empty_carve_out(self, tmp_path):
        """DOCUMENTATION-IN-CODE test pinning the architectural decision:
        the secretary-on-secretary Bug A scenario (where BOTH Task A and
        Task B are owned by pact-secretary) is now FIXED — by the count
        gate, not by the defer-Teardown predicate.

        BACKGROUND. Pre-empty WAKE_EXCLUDED_AGENT_TYPES contained
        pact-secretary, so count_active_tasks excluded secretary-owned
        tasks from the wake-mechanism active tally. When the lead
        completed a secretary teachback Task A, the count dropped to
        0 (both A and B excluded), Teardown emitted eagerly, and the
        Monitor was torn down before any non-exempt teammate could
        dispatch. The empirical Bug A in the original session was
        exactly this shape: the secretary's session-briefing teachback
        chain is the FIRST teammate work, so the eager Teardown removed
        the Monitor before non-exempt work began. The architect cell-6
        spec for `has_same_teammate_continuation` returned False on
        exempt-agentType continuations (correct per the carve-out
        semantics, but it left the Bug A secretary-window unaddressed
        at the predicate layer). The original PR fixed the secretary-
        window via the Bug B re-Arm branch (which re-armed the Monitor
        when a subsequent non-exempt teammate claimed a task), and
        documented the secretary-window as INTENTIONALLY NOT FIXED at
        the predicate layer.

        POST-EMPTY-CARVE-OUT (cycle 5). WAKE_EXCLUDED_AGENT_TYPES is
        now an empty frozenset. Secretary tasks count toward
        count_active_tasks like any other teammate task. The Bug A
        secretary-window is now fixed BEFORE the defer-Teardown
        predicate is even consulted: when the lead completes secretary
        Task A while Task B (also secretary, pending) remains, the
        post-state count is 1 (only B counts; A is now completed but
        was previously counted), and the Teardown count gate
        (`count_active_tasks(team) != 0`) short-circuits — no Teardown
        emitted. Monitor stays armed for the duration of the secretary
        chain. SELF_COMPLETE_EXEMPT_AGENT_TYPES on the self-completion
        side still contains pact-secretary; only the wake-side carve-
        out is empty (decoupled by design).

        This test pins the post-empty behavior: with both tasks
        secretary-owned and Task B still pending, the post-state count
        is 1 → Teardown count gate suppresses → out == suppressOutput.
        The defer-Teardown predicate (has_same_teammate_continuation)
        ALSO now returns True for secretary→secretary continuations
        (cell-6 inverted in test_has_same_teammate_continuation.py) so
        defense-in-depth is preserved.

        Counter-test-by-revert: a future re-population of
        WAKE_EXCLUDED_AGENT_TYPES = {pact-secretary} flips the
        post-state count back to 0, the count gate falls through, and
        Teardown emits — this test must be inverted in lockstep
        (asserted outcome flips from suppressOutput to Teardown emit).
        """
        home = tmp_path / "home"; home.mkdir()
        sid = "s"; pdir = "/tmp/p"; team = "team-secretary-fixed-post-empty"
        _write_session_context(
            home, sid, pdir, team,
            members=[
                {"name": "secretary", "agentType": "pact-secretary"},
            ],
        )
        _write_task(home, team, "B", status="pending", owner="secretary")
        _write_task(
            home, team, "A",
            status="completed",
            owner="secretary",
            addBlocks=["B"],
        )

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid, "cwd": pdir,
            "tool_input": {"taskId": "A", "status": "completed"},
            "tool_response": {
                "id": "A", "status": "completed", "owner": "secretary",
            },
        }, home)
        # Post-empty: count_active_tasks(team) == 1 (B is pending,
        # secretary now counts since WAKE_EXCLUDED is empty). Teardown
        # count gate (`count != 0`) suppresses BEFORE the defer-Teardown
        # predicate is consulted. Outcome: suppressOutput.
        assert out == {"suppressOutput": True}, (
            f"Post-empty WAKE_EXCLUDED_AGENT_TYPES: secretary-on-"
            f"secretary completion with pending Task B continuation must "
            f"suppress Teardown via the count gate (count == 1 → no "
            f"emit). Got {out!r}. If hookSpecificOutput with Teardown "
            f"directive, the wake-side carve-out has been re-populated "
            f"(secretary tasks excluded from count → count drops to 0 "
            f"→ Teardown gate falls through) and this test must be "
            f"inverted in lockstep."
        )

    def test_no_defer_on_different_owner_continuation(self, tmp_path):
        """Negative pair: Task A's addBlocks includes Task B owned by a
        DIFFERENT teammate. Predicate returns False → Teardown emits if
        count==0. Pins that the same-owner discriminator is load-bearing
        (an over-permissive predicate that defers on any addBlocks chain
        would silently suppress legitimate Teardowns)."""
        home = tmp_path / "home"; home.mkdir()
        sid = "s"; pdir = "/tmp/p"; team = "team-diff-owner"
        _write_session_context(home, sid, pdir, team)
        # Task B: DIFFERENT teammate.
        _write_task(home, team, "B", status="completed", owner="test-engineer")
        # Task A: backend-coder, completed, addBlocks=['B']. Note B is
        # also completed → count_active_tasks == 0.
        _write_task(
            home, team, "A",
            status="completed",
            owner="backend-coder",
            addBlocks=["B"],
        )

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid, "cwd": pdir,
            "tool_input": {"taskId": "A", "status": "completed"},
            "tool_response": {
                "id": "A", "status": "completed",
                "owner": "backend-coder",
            },
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Teardown emit when continuation owner differs; "
            f"got {out!r}."
        )
        assert "Skill(\"PACT:unwatch-inbox\")" in hso["additionalContext"]

    def test_no_defer_on_empty_addBlocks(self, tmp_path):
        """Negative pair: standalone single-task dispatch (no addBlocks).
        Predicate returns False → Teardown emits if count==0."""
        home = tmp_path / "home"; home.mkdir()
        sid = "s"; pdir = "/tmp/p"; team = "team-empty-blocks"
        _write_session_context(home, sid, pdir, team)
        _write_task(
            home, team, "A",
            status="completed",
            owner="backend-coder",
            addBlocks=[],
        )

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid, "cwd": pdir,
            "tool_input": {"taskId": "A", "status": "completed"},
            "tool_response": {
                "id": "A", "status": "completed",
                "owner": "backend-coder",
            },
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Teardown emit on standalone task completion; "
            f"got {out!r}."
        )
        assert "Skill(\"PACT:unwatch-inbox\")" in hso["additionalContext"]


# ---------- P2.1(b) Race-deleted continuation ----------


def test_defer_predicate_handles_race_deleted_continuation(tmp_path):
    """P2.1(b) integration: addBlocks references a task ID that was
    deleted out from under the predicate (race condition). Predicate
    fail-closes (returns False) → Teardown emits if count==0. Pins
    the conservative behavior — fail-open here would silently suppress
    legitimate Teardowns on a race."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "team-race-deleted"
    _write_session_context(home, sid, pdir, team)
    # Task A: addBlocks=['B'] but B was deleted (no file on disk).
    _write_task(
        home, team, "A",
        status="completed",
        owner="backend-coder",
        addBlocks=["B"],
    )
    # Deliberately do NOT write a file for B.

    out = _emit_output({
        "tool_name": "TaskUpdate",
        "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "A", "status": "completed"},
        "tool_response": {
            "id": "A", "status": "completed",
            "owner": "backend-coder",
        },
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Expected Teardown emit when continuation is race-deleted "
        f"(predicate fail-closes); got {out!r}. If suppressOutput, the "
        f"predicate is fail-open on deleted continuations — silent "
        f"Teardown suppression is the worse failure mode."
    )
    assert "Skill(\"PACT:unwatch-inbox\")" in hso["additionalContext"]
