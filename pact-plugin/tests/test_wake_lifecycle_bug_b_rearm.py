"""
Integration tests for Bug B re-Arm preservation under cron-based pending-scan:
emit Arm on TaskUpdate(status=in_progress) when count_active_tasks >= 1.

Bug B surface: after an eager Teardown (cold-start, mid-session resume,
session-end cleanup), when the teammate claims Task B
(TaskUpdate status=in_progress), the hook must re-fire the Arm
directive (now /PACT:start-pending-scan) — pending->in_progress falls
through the existing TaskUpdate branch's terminal-status guard by
design. The fix adds an Arm branch on TaskUpdate(status=='in_progress')
transitions; idempotency lives in the skill body (CronList match), not
in a hook-level freshness short-circuit.

Per cron mechanism (Re-Arm on Pending→In-Progress mechanical branch): STATE_FILE-based freshness
short-circuit is REMOVED. CronList-as-state is the single idempotency
source of truth at the skill body. The Arm directive may fire redundantly
without harm — start-pending-scan idempotently no-ops if its cron entry
already exists.

Also includes:
- Audit-anchor regression guards for _ARM_DIRECTIVE / _TEARDOWN_DIRECTIVE
  literal prose (per memory feedback_491 literal-phrase regression
  guard pattern).
- Parallel test_no_op_on_taskupdate_owned_by_exempt_agent for parity
  with existing test_no_op_on_create_owned_by_exempt_agent.
- Sequencing test: Teardown then claim → re-Arm fires.

Counter-test-by-revert (manual / runbook-documented): SOURCE-ONLY revert
via git-checkout HEAD~1 of pact-plugin/hooks/wake_lifecycle_emitter.py.
Expected cardinality on revert: ~4 fail (TestBugBReArmOnTeammateClaim
cases) + sequence test fails on Step 2 + audit-anchor tests pass.
See pact-plugin/tests/runbooks/wake-lifecycle-teachback-rearm.md.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
EMITTER = HOOK_DIR / "wake_lifecycle_emitter.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "wake_lifecycle"


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


def _load_fixture(name: str) -> dict:
    """Load a captured-from-prod fixture and strip the diagnostic _meta
    sibling — the hook tolerates unknown top-level keys but pipe a clean
    payload to mirror what the platform actually sends."""
    data = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    data.pop("_meta", None)
    return data


# ---------- Bug B: re-Arm on pending->in_progress under cron mechanism ----------


class TestBugBReArmOnTeammateClaim:
    """Bug B integration: emit Arm directive when teammate claims a task
    off the queue (TaskUpdate status=in_progress) AND count_active_tasks
    >= 1.

    Categorically covers cold-start, post-Teardown recovery, mid-session
    resume — under cron mechanism, idempotency is enforced at the skill
    body (CronList match), not at the hook layer. The hook MUST emit
    Arm whenever the lifecycle transition warrants it; redundant emits
    are absorbed by start-pending-scan's CronList idempotency check.
    """

    def test_rearm_on_claim_after_eager_teardown(self, tmp_path):
        """Recovery case: a prior Teardown ran (cold-start, mid-session
        resume, etc.). Teammate claims Task B (status=in_progress);
        count==1; the hook MUST emit Arm. Under cron mechanism, no
        hook-level freshness short-circuit exists — idempotency lives
        in the skill body's CronList match."""
        home = tmp_path / "home"; home.mkdir()
        sid = "s"; pdir = "/tmp/p"; team = "team-rearm-recovery"
        _write_session_context(home, sid, pdir, team)
        # Task on disk is now in_progress (post-state).
        _write_task(home, team, "B", status="in_progress", owner="backend-coder")

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid, "cwd": pdir,
            "tool_input": {"taskId": "B", "status": "in_progress"},
            "tool_response": {
                "id": "B", "status": "in_progress",
                "owner": "backend-coder",
            },
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Arm emit on pending->in_progress (recovery case); "
            f"got {out!r}. If suppressOutput, the Bug B re-Arm branch "
            f"is missing from the TaskUpdate hook path."
        )
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]

    def test_no_rearm_on_zero_count(self, tmp_path):
        """Lower-bound regression guard: a pending->in_progress
        at count==0 must NOT emit Arm. Pins the composition order:
        count check is the gating predicate; under cron mechanism it
        is the SOLE gating predicate (no STATE_FILE freshness layer)."""
        home = tmp_path / "home"; home.mkdir()
        sid = "s"; pdir = "/tmp/p"; team = "team-zero-count"
        _write_session_context(home, sid, pdir, team)
        # Deliberately do NOT write any task files. The hook's
        # _extract_task_id will succeed from tool_input but
        # count_active_tasks reads the tasks dir and returns 0.

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid, "cwd": pdir,
            "tool_input": {"taskId": "B", "status": "in_progress"},
            "tool_response": {
                "id": "B", "status": "in_progress",
                "owner": "backend-coder",
            },
        }, home)
        assert out == {"suppressOutput": True}, (
            f"Expected suppressOutput when count==0; got {out!r}."
        )

    def test_no_rearm_on_metadata_only_taskupdate(self, tmp_path):
        """Cheap-predicate-first ordering preserved: metadata-only
        TaskUpdate (no status field) must NOT trigger the Bug B branch
        (and must NOT call count_active_tasks, but that's covered
        separately by the perf test in test_inbox_wake_lifecycle_emitter.py).
        Pins that the in_progress probe is gated on tool_input.status."""
        home = tmp_path / "home"; home.mkdir()
        sid = "s"; pdir = "/tmp/p"; team = "team-metadata-only"
        _write_session_context(home, sid, pdir, team)
        _write_task(home, team, "B", status="in_progress", owner="backend-coder")

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid, "cwd": pdir,
            "tool_input": {"taskId": "B", "owner": "backend-coder"},
            "tool_response": {"id": "B"},
        }, home)
        assert out == {"suppressOutput": True}, (
            f"Expected suppressOutput on metadata-only TaskUpdate; "
            f"got {out!r}."
        )

    def test_rearm_on_captured_teammate_claim_fixture(self, tmp_path):
        """End-to-end Bug B reproduction using the captured-from-prod
        fixture (teammate_claim_in_progress_shape.json). The fixture
        encodes the canonical TaskUpdate(status=in_progress) shape from
        a real PACT session. Predicate must classify this as the re-Arm
        trigger; with count==1, Arm emits.

        Counter-test-by-revert: revert the Bug B re-Arm branch and this
        test FAILS — the captured production payload no longer triggers
        Arm, demonstrating the live regression."""
        fixture = _load_fixture("teammate_claim_in_progress_shape.json")
        home = tmp_path / "home"; home.mkdir()
        sid = fixture["session_id"]
        pdir = fixture["cwd"]
        team = "team-claim-fixture"
        _write_session_context(home, sid, pdir, team)
        # Pre-write the task that the teammate is claiming.
        task_id = fixture["tool_input"]["taskId"]
        _write_task(home, team, task_id, status="in_progress", owner="backend-coder")

        out = _emit_output(fixture, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Arm emit on captured teammate-claim production "
            f"payload; got {out!r}. Bug B re-Arm branch may be missing."
        )
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]


# ---------- Audit-anchor regression guards ----------


class TestAuditAnchorRegressionGuards:
    """Pin load-bearing directive prose so a future 'simplification' LLM
    cannot accidentally widen / shrink the contract.

    Under cron mechanism (Re-Arm on Pending→In-Progress mechanical branch): the STATE_FILE
    freshness-window constant pin is REMOVED — no STATE_FILE exists,
    no freshness window applies, idempotency is enforced at the skill
    body via CronList match."""

    def test_arm_directive_constant_unchanged(self):
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        # The exact directive prose — pin per memory feedback_491
        # literal-phrase regression guard pattern.
        assert "First active teammate task created" in emitter._ARM_DIRECTIVE
        assert 'Skill("PACT:start-pending-scan")' in emitter._ARM_DIRECTIVE
        assert "Idempotent" in emitter._ARM_DIRECTIVE

    def test_teardown_directive_constant_unchanged(self):
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        assert "Last active teammate task completed" in emitter._TEARDOWN_DIRECTIVE
        assert 'Skill("PACT:stop-pending-scan")' in emitter._TEARDOWN_DIRECTIVE
        assert "Best-effort" in emitter._TEARDOWN_DIRECTIVE


# ---------- Parallel TaskUpdate-side test for parity ----------


def test_rearm_on_taskupdate_owned_by_secretary_post_empty_carve_out(tmp_path):
    """POST-EMPTY-CARVE-OUT: parallel to
    test_arm_on_create_owned_by_secretary_post_empty_carve_out (L308
    of test_inbox_wake_lifecycle_emitter.py). The Bug B re-Arm branch
    fires for secretary-owned TaskUpdate(status=in_progress) because
    WAKE_EXCLUDED_AGENT_TYPES is empty and secretary tasks count
    toward the active tally.

    Pre-empty: this test asserted suppressOutput (the wake-side carve-
    out excluded secretary-owned tasks from the count, so post < 1
    suppressed Arm). Post-empty: secretary tasks DO count, so claim
    transitions trigger re-Arm under cron mechanism.

    Pins parity between the TaskCreate Arm branch (already inverted at
    test_arm_on_create_owned_by_secretary_post_empty_carve_out) and
    the TaskUpdate Arm branch — both must respect the post-empty
    semantics consistently. SELF_COMPLETE_EXEMPT_AGENT_TYPES on the
    self-completion side still contains pact-secretary (self-completion
    authority preserved); only the wake-side carve-out is empty.

    Counter-test-by-revert: a future re-population of
    WAKE_EXCLUDED_AGENT_TYPES = {pact-secretary} flips this back to
    suppressOutput; this test must be inverted in lockstep."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "team-exempt-update"
    _write_session_context(
        home, sid, pdir, team,
        members=[{"name": "session-secretary", "agentType": "pact-secretary"}],
    )
    _write_task(home, team, "B", status="in_progress", owner="session-secretary")

    out = _emit_output({
        "tool_name": "TaskUpdate",
        "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "B", "status": "in_progress"},
        "tool_response": {
            "id": "B", "status": "in_progress",
            "owner": "session-secretary",
        },
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Post-empty WAKE_EXCLUDED_AGENT_TYPES: secretary TaskUpdate("
        f"in_progress) must emit Arm directive "
        f"(count_active_tasks >= 1). Got {out!r}. If suppressOutput, "
        f"the wake-side carve-out has been re-populated and this test "
        f"must be inverted in lockstep."
    )
    assert hso["hookEventName"] == "PostToolUse"
    assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]


# ---------- Sequencing: Teardown then claim ----------


def test_sequence_teardown_then_claim_emits_rearm(tmp_path):
    """Sequence test: simulate the Bug A+B coupled scenario across two
    consecutive hook fires.

      Step 1: Lead completes Task A (addBlocks=['B'], B is non-exempt
              teammate, pending). With the fix, defer-Teardown branch
              suppresses the eager 1->0 emit.
      Step 2: Teammate claims Task B (status=in_progress). Re-Arm
              branch fires (no prior Arm emitted; cron mechanism
              idempotency lives in skill body, not hook layer).

    Pins that both branches compose correctly when the canonical
    Two-Task Dispatch lifecycle runs end-to-end. If either branch is
    missing, the sequence breaks: Step 1 missing → Teardown fires
    eagerly; Step 2 missing → no re-Arm even though count is now >=1.
    """
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "team-sequence"
    _write_session_context(home, sid, pdir, team)
    # Pre-state: Task B pending, Task A in_progress.
    _write_task(home, team, "B", status="pending", owner="backend-coder")
    _write_task(
        home, team, "A",
        status="in_progress",
        owner="backend-coder",
        addBlocks=["B"],
    )

    # Step 1: Lead completes Task A. State on disk: A completed,
    # B still pending.
    _write_task(
        home, team, "A",
        status="completed",
        owner="backend-coder",
        addBlocks=["B"],
    )
    out_step1 = _emit_output({
        "tool_name": "TaskUpdate",
        "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "A", "status": "completed"},
        "tool_response": {
            "id": "A", "status": "completed", "owner": "backend-coder",
        },
    }, home)
    # Defer-Teardown should fire. count_active_tasks==1 (B is pending,
    # non-exempt). The Teardown branch is `count != 0` first, so
    # suppressOutput from that gate alone would also satisfy. To
    # surface the defer-Teardown ALSO acts under count==0, see
    # test_secretary_bug_a_documented_not_fixed_in_this_pr in
    # test_wake_lifecycle_bug_a_defer_teardown.py.
    assert out_step1 == {"suppressOutput": True}, (
        f"Step 1 expected suppressOutput (no Teardown); got {out_step1!r}."
    )

    # Step 2: Teammate claims Task B (in_progress). State on disk:
    # A completed, B in_progress.
    _write_task(home, team, "B", status="in_progress", owner="backend-coder")
    out_step2 = _emit_output({
        "tool_name": "TaskUpdate",
        "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "B", "status": "in_progress"},
        "tool_response": {
            "id": "B", "status": "in_progress",
            "owner": "backend-coder",
        },
    }, home)
    hso = out_step2.get("hookSpecificOutput")
    assert hso is not None, (
        f"Step 2 expected Arm emit (re-Arm on teammate claim); "
        f"got {out_step2!r}. The Bug B re-Arm branch did not fire."
    )
    assert hso["hookEventName"] == "PostToolUse"
    assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]
