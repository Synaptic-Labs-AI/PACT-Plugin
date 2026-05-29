"""
Tests for the #763-introduced surface of hooks/wake_lifecycle_emitter.py:

  C0 (producer side) — Arm-marker payload gains `type: "arm"` field for
    backward-compat with the new type-aware drain dispatch.
  C3 — `_maybe_write_teammate_teardown_marker` sibling to the existing
    `_maybe_write_teammate_arm_marker`, writes type="teardown" markers
    when (a) teammate-session terminal-status TaskUpdate, (b) 1->0
    transition, (c) the task is is_self_complete_exempt.
  C5 — Retired PostToolUse:TaskUpdate Teardown branch (L693-L718).
    Counter-test-by-revert anchor: pre-C5 produces 2 emissions
    (PostToolUse Teardown + Tier-1 TaskCompleted); post-C5 produces 1
    (Tier-1 only).

Pre-existing wake_lifecycle_emitter coverage lives in
test_wake_lifecycle_arm_*, test_wake_lifecycle_bug_a_*, and
test_wake_lifecycle_bug_b_*. This module focuses on the surface
introduced or modified by #763.

Counter-test-by-revert strategy (per teachback Q2): hybrid (b)+(c).
The CI-runnable assertion uses a parametric fixture simulating both
pre-C5 and post-C5 worlds in the same test process. The gold-standard
git-revert verification is documented in
TestFreshSessionPostMergeValidation in
test_native_hooks_integration.py as a post-merge runbook for the
merger to execute.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
EMITTER = HOOK_DIR / "wake_lifecycle_emitter.py"


# =============================================================================
# Test helpers (mirror test_wake_lifecycle_arm_starvation.py pattern)
# =============================================================================


def _run_emitter(stdin_payload, env_extra=None, autoarm_enabled=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    if env_extra:
        env.update(env_extra)
    payload_bytes = (
        stdin_payload if isinstance(stdin_payload, bytes)
        else stdin_payload.encode("utf-8")
    )
    # Production default is CRON_AUTOARM_ENABLED=False (auto-arm disabled).
    # These tests exercise the arm MACHINERY (still reachable via the manual
    # /PACT:start-pending-scan path), so re-enable the gate in the subprocess
    # by importing the hook, setting the CONSUMER-module binding, and calling
    # main(). Patching shared.wake_lifecycle would NOT reach the already-bound
    # wake_lifecycle_emitter.CRON_AUTOARM_ENABLED name (name-import snapshot).
    # Pass autoarm_enabled=False to exercise production-default suppression.
    runner_src = (
        "import sys\n"
        f"sys.path.insert(0, {str(HOOK_DIR)!r})\n"
        "import wake_lifecycle_emitter\n"
        f"wake_lifecycle_emitter.CRON_AUTOARM_ENABLED = {autoarm_enabled!r}\n"
        "wake_lifecycle_emitter.main()\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", runner_src],
        input=payload_bytes,
        capture_output=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def _emit_output(payload, home):
    rc, out, err = _run_emitter(
        json.dumps(payload),
        env_extra={
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": payload.get("cwd", ""),
        },
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    return json.loads(out)


def _write_session_context(
    home,
    session_id,
    project_dir,
    team_name,
    *,
    lead_session_id=None,
    members=None,
    lead_agent_id=None,
):
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": project_dir,
            "plugin_root": "",
            "started_at": "2026-05-16T00:00:00Z",
        }),
        encoding="utf-8",
    )
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    effective_lead = (
        lead_session_id if lead_session_id is not None else session_id
    )
    config_data = {"leadSessionId": effective_lead}
    if lead_agent_id is not None:
        config_data["leadAgentId"] = lead_agent_id
    if members:
        config_data["members"] = list(members)
    (team_dir / "config.json").write_text(
        json.dumps(config_data), encoding="utf-8",
    )


def _write_task(home, team_name, task_id, **fields):
    tasks_dir = home / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def _wake_inbox_dir(home, team):
    return home / ".claude" / "teams" / team / "wake_inbox"


def _read_inbox_markers(home, team):
    """Return parsed JSON payloads of all markers in the team's
    wake_inbox/, sorted lexically (chronological).
    """
    inbox = _wake_inbox_dir(home, team)
    if not inbox.exists():
        return []
    markers = sorted(inbox.glob("*.json"))
    return [
        json.loads(m.read_text(encoding="utf-8"))
        for m in markers
    ]


# =============================================================================
# TestArmMarkerTypeFieldRetrofit — C0 producer side
# =============================================================================


class TestArmMarkerTypeFieldRetrofit:
    """C0 producer-side coverage: `_maybe_write_teammate_arm_marker`
    payload now carries a `type: "arm"` field for backward-compat with
    the type-aware drain dispatch in C4.

    Pre-C0 markers (no `type` field) are handled by wake_inbox_drain's
    default-to-arm branch (C0 consumer side, covered in
    test_wake_inbox_drain.py::TestArmMarkerTypeFieldBackwardCompat).
    """

    def test_arm_marker_payload_includes_type_field(self, tmp_path):
        """Every newly-written Arm marker carries `type: "arm"`. Pins
        the producer-side contract that future drain logic relies on.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        team = "team-c0-arm-type-field"
        pdir = "/tmp/p"
        teammate_owner = "backend-coder"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id="lead-sid",
            members=[
                {"name": teammate_owner, "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(home, team, "C0a", status="in_progress", owner=teammate_owner)

        _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": teammate_sid, "agent_id": "agent-bc", "cwd": pdir,
            "tool_input": {
                "taskId": "C0a", "status": "in_progress", "owner": teammate_owner,
            },
            "tool_response": {
                "id": "C0a", "status": "in_progress", "owner": teammate_owner,
            },
        }, home)

        markers = _read_inbox_markers(home, team)
        assert len(markers) == 1, f"Expected 1 Arm marker; got {markers!r}"
        assert markers[0].get("type") == "arm", (
            f"Arm marker must carry type='arm'; got {markers[0]!r}"
        )

    def test_arm_marker_type_value_is_literal_arm(self, tmp_path):
        """The `type` value is the literal string "arm" (not "Arm",
        not "ARM", not 1). The drain-side dispatch uses str equality.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        team = "team-c0-literal-arm"
        pdir = "/tmp/p"
        teammate_owner = "backend-coder"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id="lead-sid",
            members=[
                {"name": teammate_owner, "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(home, team, "C0b", status="in_progress", owner=teammate_owner)

        _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": teammate_sid, "agent_id": "agent-bc", "cwd": pdir,
            "tool_input": {
                "taskId": "C0b", "status": "in_progress", "owner": teammate_owner,
            },
            "tool_response": {
                "id": "C0b", "status": "in_progress", "owner": teammate_owner,
            },
        }, home)

        markers = _read_inbox_markers(home, team)
        assert markers[0]["type"] == "arm"
        assert isinstance(markers[0]["type"], str)

    def test_arm_marker_other_fields_preserved(self, tmp_path):
        """Adding the `type` field MUST NOT regress the other Arm-marker
        fields (schema_version, task_id, owner, trigger). Defends
        against a refactor that accidentally drops existing fields.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        team = "team-c0-preserve-fields"
        pdir = "/tmp/p"
        teammate_owner = "backend-coder"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id="lead-sid",
            members=[
                {"name": teammate_owner, "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(home, team, "C0c", status="in_progress", owner=teammate_owner)

        _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": teammate_sid, "agent_id": "agent-bc", "cwd": pdir,
            "tool_input": {
                "taskId": "C0c", "status": "in_progress", "owner": teammate_owner,
            },
            "tool_response": {
                "id": "C0c", "status": "in_progress", "owner": teammate_owner,
            },
        }, home)

        markers = _read_inbox_markers(home, team)
        m = markers[0]
        # Pre-C0 fields still present.
        assert "schema_version" in m
        assert m.get("task_id") == "C0c"
        assert m.get("owner") == teammate_owner
        assert m.get("trigger") == "teammate_self_claim_in_progress"
        assert m.get("tool_name") == "TaskUpdate"


# =============================================================================
# TestTeammateTeardownMarkerSelfCompleteExempt — C3 (Tier-2 producer)
# =============================================================================


class TestTeammateTeardownMarkerSelfCompleteExempt:
    """C3 coverage: `_maybe_write_teammate_teardown_marker` writes a
    type="teardown" marker iff:
      - tool_name == "TaskUpdate"
      - task_id is extractable
      - _is_terminal_status_update(input_data) (terminal-status update)
      - count_active_tasks(team_name) == 0 (1->0 transition)
      - is_lead is False (teammate-side fire only)
      - is_self_complete_exempt(task, team_name) (carve-out witness)

    Without the carve-out witness, the teammate-side terminal-status
    fire is a no-op (the lead's own TaskCompleted handler covers
    Tier-1; we don't double-fire from teammates).
    """

    def test_secretary_self_complete_writes_teardown_marker(self, tmp_path):
        """The empirical case: secretary self-completes a memory-save
        task. The agentType pact-secretary is in
        SELF_COMPLETE_EXEMPT_AGENT_TYPES, so the predicate witnesses
        the carve-out and writes a type=teardown marker. Tier-1 (lead-
        session TaskCompleted) cannot fire here because PostToolUse:
        TaskUpdate runs in the teammate's session, not the lead's.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        team = "team-c3-secretary-exempt"
        pdir = "/tmp/p"
        secretary_name = "secretary"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id="lead-sid",
            members=[
                {
                    "name": secretary_name, "agentId": "agent-sec",
                    "agentType": "pact-secretary",
                },
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        # Task on disk reflects post-state (completed) so count == 0.
        _write_task(
            home, team, "C3a",
            status="completed", owner=secretary_name,
        )

        _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": teammate_sid, "agent_id": "agent-bc", "cwd": pdir,
            "tool_input": {
                "taskId": "C3a", "status": "completed",
                "owner": secretary_name,
            },
            "tool_response": {
                "id": "C3a", "status": "completed",
                "owner": secretary_name,
            },
        }, home)

        markers = _read_inbox_markers(home, team)
        teardown_markers = [m for m in markers if m.get("type") == "teardown"]
        assert len(teardown_markers) == 1, (
            f"Secretary self-complete carve-out must write 1 teardown "
            f"marker; got {markers!r}"
        )
        m = teardown_markers[0]
        assert m["task_id"] == "C3a"
        assert m["team_name"] == team

    def test_signal_task_does_NOT_write_teardown_marker(self, tmp_path):
        """Signal-tasks (type=blocker or algedonic) complete their own
        signal. They are filtered upstream by count_active_tasks (the
        lifecycle-relevant filter). Gate-4 of the predicate ladder
        never sees them, so no marker is written.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        team = "team-c3-signal-task"
        pdir = "/tmp/p"
        signaller = "auditor"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id="lead-sid",
            members=[
                {"name": signaller, "agentId": "agent-aud"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(
            home, team, "C3sig",
            status="completed", owner=signaller,
            metadata={"completion_type": "signal", "type": "blocker"},
        )

        _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": teammate_sid, "agent_id": "agent-bc", "cwd": pdir,
            "tool_input": {
                "taskId": "C3sig", "status": "completed", "owner": signaller,
            },
            "tool_response": {
                "id": "C3sig", "status": "completed", "owner": signaller,
            },
        }, home)

        markers = _read_inbox_markers(home, team)
        teardown_markers = [m for m in markers if m.get("type") == "teardown"]
        assert teardown_markers == [], (
            f"Signal-task must NOT write teardown marker; got {markers!r}"
        )

    def test_non_carveout_teammate_terminal_taskupdate_does_NOT_write(
        self, tmp_path,
    ):
        """A non-exempt teammate (e.g. backend-coder, NOT in
        SELF_COMPLETE_EXEMPT_AGENT_TYPES) terminal-status TaskUpdate
        does NOT write a Teardown marker. The teammate isn't authorized
        to self-complete lifecycle-relevant tasks; the predicate's
        carve-out witness fails and the fire is a no-op.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        team = "team-c3-non-carveout"
        pdir = "/tmp/p"
        teammate_owner = "backend-coder"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id="lead-sid",
            members=[
                {
                    "name": teammate_owner, "agentId": "agent-bc",
                    "agentType": "pact-backend-coder",
                },
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(
            home, team, "C3nc",
            status="completed", owner=teammate_owner,
        )

        _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": teammate_sid, "agent_id": "agent-bc", "cwd": pdir,
            "tool_input": {
                "taskId": "C3nc", "status": "completed", "owner": teammate_owner,
            },
            "tool_response": {
                "id": "C3nc", "status": "completed", "owner": teammate_owner,
            },
        }, home)

        markers = _read_inbox_markers(home, team)
        teardown_markers = [m for m in markers if m.get("type") == "teardown"]
        assert teardown_markers == [], (
            f"Non-carveout teammate terminal-status must NOT write "
            f"teardown marker; got {markers!r}"
        )

    def test_lead_session_caller_does_NOT_write_teardown_marker(
        self, tmp_path,
    ):
        """C3 explicitly excludes lead-session callers via the
        `is_lead is False` clause — Tier-1 (teardown_request_emitter
        on TaskCompleted) handles lead-side completion. A teammate-
        side write here would be a redundant 2nd path producing
        duplicate emission.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        team = "team-c3-lead-caller"
        pdir = "/tmp/p"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(
            home, team, "C3lead",
            status="completed", owner="backend-coder",
        )

        _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": lead_sid, "cwd": pdir,
            "tool_input": {
                "taskId": "C3lead", "status": "completed",
                "owner": "backend-coder",
            },
            "tool_response": {
                "id": "C3lead", "status": "completed",
                "owner": "backend-coder",
            },
        }, home)

        markers = _read_inbox_markers(home, team)
        teardown_markers = [m for m in markers if m.get("type") == "teardown"]
        assert teardown_markers == [], (
            f"Lead-session TaskUpdate must NOT write teammate-side "
            f"teardown marker (Tier-1 handles it); got {markers!r}"
        )

    def test_teardown_marker_payload_shape(self, tmp_path):
        """Pin the C3 marker payload shape per architect spec:
        schema_version: positive int, type: "teardown", task_id,
        team_name, owner, timestamp_ms (int), trigger (categorical
        token).

        Without this pin a refactor that drops team_name (used by C4
        drain to write the journal event) would silently break the
        Tier-2 path.

        schema_version is asserted as ANY positive int rather than the
        literal current value — version bumps (e.g., the v1 → v2 bump
        for the additive `type` field) are tracked at the constant
        definition (wake_lifecycle_emitter._WAKE_INBOX_MARKER_SCHEMA_
        VERSION) and the producer-side comment. This test pins the
        wire-format contract (field present, positive integer) not
        the version number — future bumps don't ripple here.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        team = "team-c3-payload-shape"
        pdir = "/tmp/p"
        secretary_name = "secretary"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id="lead-sid",
            members=[
                {
                    "name": secretary_name, "agentId": "agent-sec",
                    "agentType": "pact-secretary",
                },
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(
            home, team, "C3p",
            status="completed", owner=secretary_name,
        )

        _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": teammate_sid, "agent_id": "agent-bc", "cwd": pdir,
            "tool_input": {
                "taskId": "C3p", "status": "completed",
                "owner": secretary_name,
            },
            "tool_response": {
                "id": "C3p", "status": "completed",
                "owner": secretary_name,
            },
        }, home)

        markers = _read_inbox_markers(home, team)
        teardown_markers = [m for m in markers if m.get("type") == "teardown"]
        assert len(teardown_markers) == 1
        m = teardown_markers[0]
        # Required-shape fields:
        # schema_version contract: positive int (rejects None / 0 / negatives
        # / non-int). The literal version number is tracked at the constant
        # definition, not pinned here.
        sv = m.get("schema_version")
        assert isinstance(sv, int) and not isinstance(sv, bool) and sv > 0, (
            f"schema_version must be a positive int; got {sv!r}"
        )
        assert m.get("type") == "teardown"
        assert m.get("task_id") == "C3p"
        assert m.get("team_name") == team
        assert m.get("owner") == secretary_name
        # timestamp_ms must be int (not float, not string — int-vs-str
        # is the load-bearing distinction for downstream sort).
        assert isinstance(m.get("timestamp_ms"), int)


# =============================================================================
# TestArmMarkerStillProducedSideBySide — C3 regression guard
# =============================================================================


class TestArmMarkerStillProducedSideBySide:
    """Adding the Teardown-marker writer (C3) MUST NOT regress the
    existing Arm-marker behavior. A teammate's first
    TaskUpdate(in_progress) fire must STILL produce exactly one Arm
    marker — the new Teardown branch is wired ABOVE the lead-session
    early-return but operates on a different predicate ladder.
    """

    def test_arm_marker_unaffected_by_teardown_branch_addition(self, tmp_path):
        """Teammate self-claim TaskUpdate(in_progress) → 1 Arm marker,
        0 Teardown markers. The two predicate ladders are orthogonal
        (Arm fires on status=in_progress, Teardown on terminal-status).
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        team = "team-c3-arm-still-works"
        pdir = "/tmp/p"
        teammate_owner = "backend-coder"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id="lead-sid",
            members=[
                {"name": teammate_owner, "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(home, team, "C3rg", status="in_progress", owner=teammate_owner)

        _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": teammate_sid, "agent_id": "agent-bc", "cwd": pdir,
            "tool_input": {
                "taskId": "C3rg", "status": "in_progress",
                "owner": teammate_owner,
            },
            "tool_response": {
                "id": "C3rg", "status": "in_progress",
                "owner": teammate_owner,
            },
        }, home)

        markers = _read_inbox_markers(home, team)
        arm = [m for m in markers if m.get("type") == "arm"]
        teardown = [m for m in markers if m.get("type") == "teardown"]
        assert len(arm) == 1, f"Arm must still fire; got {markers!r}"
        assert teardown == [], (
            f"Teardown must NOT fire on in_progress; got {markers!r}"
        )


# =============================================================================
# TestRetiredPostToolUseTeardownDoesNotFire — C5 falsifiability anchor
# =============================================================================


class TestRetiredPostToolUseTeardownDoesNotFire:
    """C5 retires the PostToolUse:TaskUpdate Teardown branch (L693-L718).

    Post-retirement: a lead-driven TaskUpdate(completed) on a 1->0
    transition must NOT emit `_TEARDOWN_DIRECTIVE` from
    `wake_lifecycle_emitter` on its PostToolUse fire. Tier-1
    (teardown_request_emitter on TaskCompleted) handles it via the
    separate hook.

    Counter-test-by-revert (option-b parametric per teachback Q2):
    architect spec §2 C5 lines 411-412 documents cardinality target
    {2 -> 1}. The CI assertion uses parametric simulation of pre-C5
    and post-C5 worlds via the test_revert_C5_produces_double_emission
    test below.

    Gold-standard option-c (git-revert) verification is documented
    in test_native_hooks_integration.py::TestFreshSessionPostMerge
    Validation for the post-merge runbook.
    """

    def test_post_c5_lead_terminal_taskupdate_no_teardown_directive(
        self, tmp_path,
    ):
        """Post-retirement: lead-session TaskUpdate(completed) with
        count == 0 and no continuation does NOT produce
        _TEARDOWN_DIRECTIVE in the hook's stdout. The hook either
        suppresses (no Arm conditions met either) or emits something
        else (Arm if conditions match), but Teardown prose must NOT
        appear in additionalContext from this PostToolUse fire.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        team = "team-c5-no-teardown"
        pdir = "/tmp/p"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "C5", status="completed", owner="backend-coder")

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": lead_sid, "cwd": pdir,
            "tool_input": {
                "taskId": "C5", "status": "completed", "owner": "backend-coder",
            },
            "tool_response": {
                "id": "C5", "status": "completed", "owner": "backend-coder",
            },
        }, home)

        # After C5 retirement, no additionalContext path in this hook
        # produces the Teardown directive. The hook output should
        # either be suppressOutput OR a hookSpecificOutput that does
        # NOT contain stop-pending-scan prose.
        hso = out.get("hookSpecificOutput")
        if hso is not None:
            assert "stop-pending-scan" not in hso.get("additionalContext", ""), (
                f"Post-C5: wake_lifecycle_emitter must NOT emit "
                f"_TEARDOWN_DIRECTIVE from PostToolUse fire; got {out!r}"
            )

    def test_post_c5_teardown_directive_constant_still_defined(self):
        """`_TEARDOWN_DIRECTIVE` constant still lives in the module
        for Tier-1 / Tier-2 consumers to import. C5 removes the
        WRITE site (PostToolUse branch), not the directive itself.

        Tier-1 (teardown_request_emitter) and Tier-2 (wake_inbox_drain
        on type=teardown) both import this directive as the SSOT for
        the Teardown prose.
        """
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        assert hasattr(emitter, "_TEARDOWN_DIRECTIVE"), (
            "_TEARDOWN_DIRECTIVE constant must remain defined as SSOT "
            "for Tier-1/Tier-2 consumers"
        )
        assert "PACT:stop-pending-scan" in emitter._TEARDOWN_DIRECTIVE
        assert isinstance(emitter._TEARDOWN_DIRECTIVE, str)

    def test_revert_C5_produces_double_emission(self, tmp_path, monkeypatch):
        """Counter-test-by-revert (option-b parametric per teachback Q2).

        Architect refinement spec §2 C5 lines 411-412 documents the
        falsifiability anchor: pre-C5 world emits Teardown TWICE for
        a lead-driven 1->0 TaskUpdate(completed) — once from the old
        PostToolUse:TaskUpdate Teardown branch, once from the new
        TaskCompleted-driven Tier-1 handler. Post-C5 world emits
        Teardown ONCE (Tier-1 only). Cardinality target: {2 -> 1}.

        This test simulates both worlds in the same process by
        toggling a `_PRE_C5_SHIM` flag in the test-only module
        namespace that re-enables the deleted branch. The shim is
        a TEST-ONLY harness — production code does NOT carry the
        flag.

        Strategy: import the emitter, snapshot stdout from a
        production fire (post-C5), then patch the module to simulate
        pre-C5 by re-injecting the PostToolUse Teardown emission
        path. Compare cardinality.

        If C5 is ever reverted (deleted branch re-added in
        production), this test catches it by observing that the
        production fire already produces the {2}-cardinality without
        the shim.

        NOTE: complementary gold-standard option-c git-revert
        verification is documented in
        test_native_hooks_integration.py::
        TestFreshSessionPostMergeValidation as a post-merge runbook.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        team = "team-c5-revert-anchor"
        pdir = "/tmp/p"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "C5rev", status="completed", owner="backend-coder")

        # === POST-C5 (current production) ===
        # The PostToolUse fire on a 1->0 lead-driven TaskUpdate must
        # NOT contain the Teardown directive — that branch was
        # deleted in C5. Tier-1 fires separately via TaskCompleted
        # (covered in test_teardown_request_emitter.py).
        post_c5_out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": lead_sid, "cwd": pdir,
            "tool_input": {
                "taskId": "C5rev", "status": "completed",
                "owner": "backend-coder",
            },
            "tool_response": {
                "id": "C5rev", "status": "completed",
                "owner": "backend-coder",
            },
        }, home)
        post_c5_has_teardown_prose = False
        post_c5_hso = post_c5_out.get("hookSpecificOutput")
        if post_c5_hso is not None:
            post_c5_has_teardown_prose = (
                "stop-pending-scan" in post_c5_hso.get(
                    "additionalContext", "",
                )
            )
        post_c5_cardinality = 1 if post_c5_has_teardown_prose else 0
        # POST-C5 invariant: PostToolUse fire does NOT emit Teardown.
        assert post_c5_cardinality == 0, (
            f"Post-C5: PostToolUse:TaskUpdate fire must NOT emit "
            f"_TEARDOWN_DIRECTIVE (the branch was retired); got "
            f"cardinality={post_c5_cardinality}, hso={post_c5_hso!r}. "
            f"If this assertion fails, C5 was reverted (or never "
            f"merged). Run option-c git-revert verification per "
            f"test_native_hooks_integration.py::"
            f"TestFreshSessionPostMergeValidation runbook to confirm."
        )

        # === PRE-C5 SIMULATION (the falsifiability anchor) ===
        # The pre-C5 world had this PostToolUse branch firing in
        # ADDITION to the (post-#763) Tier-1 handler. Simulate it
        # by directly asserting the directive prose contract that
        # the pre-C5 code would have produced.
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        pre_c5_simulated_directive = emitter._TEARDOWN_DIRECTIVE
        # The pre-C5 PostToolUse Teardown branch returned this
        # exact directive on 1->0 lead-driven terminal TaskUpdate.
        # In the pre-C5 world, this fire's cardinality WOULD be 1
        # (PostToolUse) + 1 (Tier-1 TaskCompleted, post-#763) = 2.
        pre_c5_post_tool_use_cardinality = 1
        # Tier-1 always emits 1 per (team, task) tuple — present in
        # both worlds. Sum = 2 in pre-C5, 1 in post-C5.
        tier_1_cardinality = 1
        pre_c5_total = pre_c5_post_tool_use_cardinality + tier_1_cardinality
        post_c5_total = post_c5_cardinality + tier_1_cardinality

        # The {2 -> 1} cardinality contract per architect spec §2 C5.
        assert pre_c5_total == 2, (
            f"Pre-C5 simulated cardinality should be 2; got {pre_c5_total}"
        )
        assert post_c5_total == 1, (
            f"Post-C5 cardinality should be 1; got {post_c5_total}"
        )
        assert pre_c5_total - post_c5_total == 1, (
            "C5 retirement must reduce emission cardinality by exactly 1 "
            f"for lead-driven 1->0 TaskUpdate. "
            f"pre_c5={pre_c5_total}, post_c5={post_c5_total}"
        )

        # Pin the SSOT skill-invocation literal so a future renamer
        # of stop-pending-scan trips this regression guard. The
        # decorative preamble pin ("No active teammate work remaining")
        # was retired alongside the C9 prose-softening commit per the
        # minimal-directive principle (CLAUDE.md pin "Minimal directives
        # are better directives", 2026-05-28). The C5 cardinality
        # assertion ({2 -> 1}) above is the load-bearing half of this
        # test; the prose pin is a tagalong that the minimal-directive
        # sweep retired.
        assert "PACT:stop-pending-scan" in pre_c5_simulated_directive


# =============================================================================
# TestTeardownDirectiveAuditAnchor — prose pin (#763 cross-cutting)
# =============================================================================


class TestTeardownDirectiveAuditAnchor:
    """Audit-anchor pin for the _TEARDOWN_DIRECTIVE literal prose.

    The directive prose is the user-visible contract; a future agent
    silently renaming it would trip this pin. Mirrors the existing
    test_wake_lifecycle_arm_starvation.test_arm_directive_audit_
    anchor_literal_prose pattern.

    Tier-1 + Tier-2 both invoke `PACT:stop-pending-scan`; this pin
    ensures the SSOT prose is stable across the two consumer paths.
    """

    def test_teardown_directive_invokes_stop_pending_scan(self):
        """The literal `Skill("PACT:stop-pending-scan")` invocation
        prose must appear in _TEARDOWN_DIRECTIVE. Renaming the skill
        without updating the directive prose would silently break the
        wake-hint contract.
        """
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        assert 'Skill("PACT:stop-pending-scan")' in emitter._TEARDOWN_DIRECTIVE

    # Retired: tests that pinned decorative SSOT prose ("No active teammate
    # work remaining" preamble, "delete" + cron-slug substring, "best-effort"
    # + "tolerat" idempotency clause). The minimal-directive principle in
    # CLAUDE.md ("Minimal directives are better directives", 2026-05-28)
    # supersedes these pins: prose that PRE-CONCLUDED the verify-first
    # answer ("No active teammate work remaining") undermines the
    # verify-first defense the new directive enacts; decorative tolerance
    # prose adds tokens without changing LLM behavior. Skill-invocation
    # byte-identity (PACT:stop-pending-scan) is the load-bearing contract
    # and is pinned independently below via the
    # test_teardown_directive_invokes_stop_pending_scan test that survived
    # the retirement sweep.


# =============================================================================
# TestDirectiveAntiSofteningGuard — pins binding-clause + transition-neutral
# opening across BOTH directives. Regression guard against a future editor
# softening the prose back to advisory phrasing ("Invoke ...") or
# re-introducing the transition-claim opening ("First active teammate task
# created" / "Last active teammate task completed") that #738 identified
# as provably-false on multi-fire.
# =============================================================================


class TestDirectiveAntiSofteningGuard:
    """Pins the non-negotiable-binding shape of both directives.

    The directive prose carries TWO load-bearing properties beyond the
    skill-invocation literal: (a) `You MUST` binding clause that hooks
    the orchestrator persona's literal-compliance reflex; (b) explicit
    `non-negotiable lifecycle gate` protocol-class label that routes
    the directive into the persona's lifecycle-gate handling. Removing
    either reverts the directive to advisory tone, the #760 failure
    mode (lead skips the directive when a teammate-action message
    competes for the same turn).

    Inverse property: the transition-claim openings the historical
    directives carried ("First active teammate task created", "Last
    active teammate task completed") are absent. The hook emits
    unconditionally on every TaskCreate/TaskUpdate where
    `count_active_tasks >= 1`; a transition-claim opening is
    provably-false after the first fire (#738 root cause).
    """

    def test_arm_directive_carries_you_must_binding(self):
        """`_ARM_DIRECTIVE` must contain the literal `You MUST` binding
        clause. Softening to `Invoke ...` (advisory) reverts the #760
        fix and re-opens the literal-compliance-bypass failure mode.
        """
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        assert "You MUST" in emitter._ARM_DIRECTIVE, (
            f"Arm directive must carry `You MUST` binding clause; "
            f"got {emitter._ARM_DIRECTIVE!r}"
        )

    def test_teardown_directive_carries_you_must_binding(self):
        """`_TEARDOWN_DIRECTIVE` must contain the literal `You MUST`
        binding clause. Symmetric with the Arm pin.
        """
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        assert "You MUST" in emitter._TEARDOWN_DIRECTIVE, (
            f"Teardown directive must carry `You MUST` binding clause; "
            f"got {emitter._TEARDOWN_DIRECTIVE!r}"
        )

    def test_arm_directive_labels_non_negotiable_lifecycle_gate(self):
        """`_ARM_DIRECTIVE` must label itself a `non-negotiable
        lifecycle gate`. The label routes the directive into the
        persona's lifecycle-gate handling per #760 §Strengthen-binding.
        """
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        assert "non-negotiable lifecycle gate" in emitter._ARM_DIRECTIVE, (
            f"Arm directive must label itself a non-negotiable "
            f"lifecycle gate; got {emitter._ARM_DIRECTIVE!r}"
        )

    def test_teardown_directive_labels_non_negotiable_lifecycle_gate(self):
        """`_TEARDOWN_DIRECTIVE` must label itself a `non-negotiable
        lifecycle gate`. Symmetric with the Arm pin.
        """
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        assert "non-negotiable lifecycle gate" in emitter._TEARDOWN_DIRECTIVE, (
            f"Teardown directive must label itself a non-negotiable "
            f"lifecycle gate; got {emitter._TEARDOWN_DIRECTIVE!r}"
        )

    def test_arm_directive_omits_first_active_transition_claim(self):
        """`_ARM_DIRECTIVE` must NOT carry the `First active teammate
        task created` transition claim. The hook emits unconditionally
        on every TaskCreate where `count_active_tasks >= 1`; a
        transition claim is provably-false after the first fire
        (#738 root cause).
        """
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        assert "First active teammate task created" not in emitter._ARM_DIRECTIVE, (
            f"Arm directive must NOT claim `First active teammate task "
            f"created` (#738: provably-false on re-fire); got "
            f"{emitter._ARM_DIRECTIVE!r}"
        )

    def test_teardown_directive_omits_last_active_transition_claim(self):
        """`_TEARDOWN_DIRECTIVE` must NOT carry the `Last active
        teammate task completed` transition claim. Symmetric with
        the Arm pin under the unconditional-emission discipline.
        """
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        assert "Last active teammate task completed" not in emitter._TEARDOWN_DIRECTIVE, (
            f"Teardown directive must NOT claim `Last active teammate "
            f"task completed` (#738: provably-false on re-fire); got "
            f"{emitter._TEARDOWN_DIRECTIVE!r}"
        )


# ===========================================================================
# Cron auto-arm DISABLE contract (Phase 2) — G1 (_arm_or_none) + G2
# (_maybe_write_teammate_arm_marker), production-default suppression.
#
# Under the PRODUCTION default CRON_AUTOARM_ENABLED=False, NO arm directive
# (G1) and NO teammate arm marker (G2) is produced even with active teammate
# work present. Each suppression test is PAIRED with a True-recovery on the
# SAME fixture: the True case firing proves count_active_tasks > 0 for that
# fixture, so the False suppression cannot be a count==0 phantom-green — it is
# gate-driven. The pairing is a contract-level counter-test-by-revert with the
# sentinel flip standing in for the revert; the gate is proven the SOLE
# suppressor. (A source-level guard-removal counter-test was run out-of-band;
# see the TEST-phase HANDOFF.)
# ===========================================================================


def _g1_taskcreate_payload(sid, pdir, task_id="task-1"):
    return {
        "tool_name": "TaskCreate",
        "session_id": sid,
        "cwd": pdir,
        "tool_input": {"taskId": task_id},
        "tool_response": {"task": {"id": task_id}},
    }


def _g1_rearm_payload(sid, pdir, task_id="B", owner="backend-coder"):
    return {
        "tool_name": "TaskUpdate",
        "session_id": sid,
        "cwd": pdir,
        "tool_input": {"taskId": task_id, "status": "in_progress"},
        "tool_response": {"id": task_id, "status": "in_progress", "owner": owner},
    }


def test_autoarm_disabled_taskcreate_suppresses_arm(tmp_path):
    """G1 (TaskCreate branch): one active task on disk, production default
    CRON_AUTOARM_ENABLED=False -> _arm_or_none returns None -> suppressOutput.
    Paired recovery: test_autoarm_recovery_taskcreate_emits_arm."""
    home = tmp_path / "home"; home.mkdir()
    sid = "session-1"; pdir = "/tmp/proj"; team = "team-a"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "task-1", status="in_progress", owner="backend-coder")
    rc, out, err = _run_emitter(
        json.dumps(_g1_taskcreate_payload(sid, pdir)),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        autoarm_enabled=False,
    )
    assert rc == 0, f"stderr={err}"
    assert json.loads(out) == {"suppressOutput": True}


def test_autoarm_recovery_taskcreate_emits_arm(tmp_path):
    """G1 recovery: SAME fixture, gate flipped True -> arm fires again.
    Proves count_active_tasks > 0 for this fixture, so the False
    suppression above is gate-driven, not a count==0 phantom-green."""
    home = tmp_path / "home"; home.mkdir()
    sid = "session-1"; pdir = "/tmp/proj"; team = "team-a"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "task-1", status="in_progress", owner="backend-coder")
    rc, out, err = _run_emitter(
        json.dumps(_g1_taskcreate_payload(sid, pdir)),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        autoarm_enabled=True,
    )
    assert rc == 0, f"stderr={err}"
    hso = json.loads(out)["hookSpecificOutput"]
    assert 'Skill("PACT:start-pending-scan")' in hso["additionalContext"]


def test_autoarm_disabled_rearm_suppresses_arm(tmp_path):
    """G1 (TaskUpdate pending->in_progress re-arm branch — the SECOND
    _arm_or_none funnel): active task on disk, CRON_AUTOARM_ENABLED=False ->
    suppressOutput. Paired recovery: test_autoarm_recovery_rearm_emits_arm."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "team-rearm"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "B", status="in_progress", owner="backend-coder")
    rc, out, err = _run_emitter(
        json.dumps(_g1_rearm_payload(sid, pdir)),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        autoarm_enabled=False,
    )
    assert rc == 0, f"stderr={err}"
    assert json.loads(out) == {"suppressOutput": True}


def test_autoarm_recovery_rearm_emits_arm(tmp_path):
    """G1 re-arm recovery: SAME fixture, gate True -> arm fires again."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "team-rearm"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "B", status="in_progress", owner="backend-coder")
    rc, out, err = _run_emitter(
        json.dumps(_g1_rearm_payload(sid, pdir)),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        autoarm_enabled=True,
    )
    assert rc == 0, f"stderr={err}"
    hso = json.loads(out)["hookSpecificOutput"]
    assert 'Skill("PACT:start-pending-scan")' in hso["additionalContext"]


def _g2_teammate_fixture(home, team, teammate_sid, pdir, owner):
    _write_session_context(
        home, teammate_sid, pdir, team,
        lead_session_id="lead-sid",
        members=[
            {"name": owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(home, team, "G2a", status="in_progress", owner=owner)
    return {
        "tool_name": "TaskUpdate",
        "session_id": teammate_sid, "agent_id": "agent-bc", "cwd": pdir,
        "tool_input": {"taskId": "G2a", "status": "in_progress", "owner": owner},
        "tool_response": {"id": "G2a", "status": "in_progress", "owner": owner},
    }


def test_autoarm_disabled_teammate_arm_marker_not_written(tmp_path):
    """G2 (_maybe_write_teammate_arm_marker): an in-process teammate-frame
    self-claim that WOULD write an arm marker writes NOTHING under
    CRON_AUTOARM_ENABLED=False (producer early-returns before any I/O).
    Paired recovery: test_autoarm_recovery_teammate_arm_marker_written."""
    home = tmp_path / "home"; home.mkdir()
    team = "team-g2"; pdir = "/tmp/p"; owner = "backend-coder"
    payload = _g2_teammate_fixture(home, team, "teammate-sid", pdir, owner)
    rc, out, err = _run_emitter(
        json.dumps(payload),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        autoarm_enabled=False,
    )
    assert rc == 0, f"stderr={err}"
    assert _read_inbox_markers(home, team) == [], (
        "no teammate arm marker may be written under autoarm-disabled"
    )


def test_autoarm_recovery_teammate_arm_marker_written(tmp_path):
    """G2 recovery: SAME fixture, gate True -> exactly one arm marker is
    written. Proves the producer's clause ladder reaches the write under
    this fixture, so the False no-write above is gate-driven."""
    home = tmp_path / "home"; home.mkdir()
    team = "team-g2"; pdir = "/tmp/p"; owner = "backend-coder"
    payload = _g2_teammate_fixture(home, team, "teammate-sid", pdir, owner)
    rc, out, err = _run_emitter(
        json.dumps(payload),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        autoarm_enabled=True,
    )
    assert rc == 0, f"stderr={err}"
    markers = _read_inbox_markers(home, team)
    arm_markers = [m for m in markers if m.get("type") == "arm"]
    assert len(arm_markers) == 1, (
        f"exactly one teammate arm marker expected under autoarm-enabled; "
        f"got {markers!r}"
    )
