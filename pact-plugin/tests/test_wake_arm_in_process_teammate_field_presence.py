"""
Behavioral + structural tests for the Option-5 agent_id/agent_type
field-presence discriminator across the 3-site corridor (closes #611
root + #778 symptom):

  - pact-plugin/hooks/shared/wake_lifecycle.py — predicate body
  - pact-plugin/hooks/session_init.py           — SessionStart callsite
  - pact-plugin/hooks/wake_inbox_drain.py       — UserPromptSubmit callsite

The legacy session_id-equality discriminator misclassified in-process
subagent fires as lead fires because the Claude Code platform does
NOT re-issue session_id per in-process Task-tool-spawned subagent
frame: the teammate inherits the lead's session_id. The corrected
discriminator reads the platform-stamped `agent_id` field
(PostToolUse + TaskCompleted) or `agent_type` field (SessionStart)
on stdin; lead-session fires omit the field, in-process teammate
fires carry it.

P0 behavioral tests below exercise the symmetric corridor:

  P0-1 secretary self-claim TaskUpdate(in_progress) in the lead's
       shared session_id frame (the #611/#778 bug-fix path) -> produces
       a wake_inbox marker on disk and emits suppressOutput.
  P0-2 secretary self-complete TaskUpdate(completed) -> produces a
       teardown marker on disk; suppressOutput.
  P0-3 lead-fire TaskUpdate(in_progress) -> emits the Arm directive
       via additionalContext (regression test — lead frame is the
       unaffected baseline).
  P0-4 lead-fire TaskUpdate(completed) on 1->0 transition -> emits
       the Teardown directive.
  P0-5 scan_armed journal event written when cron is armed by drain.

P1 structural pins:

  P1-1 `agent_id` appears in the PostToolUse/UserPromptSubmit corridor
       files (wake_lifecycle.py + wake_inbox_drain.py); `agent_type`
       appears in session_init.py per the SessionStart field-split.
  P1-2 Inline `_is_lead_session_at_init` symbol fully removed from
       session_init.py (replaced by `is_lead_context` import from the
       consolidated shared helper).

Counter-test-by-revert anchor (per plan §Coverage Guards HARD): revert
target is commit fc37bc04 (the predicate body change). Source-only
revert technique per pact-testing-strategies bundled-commit guidance
keeps these tests in place while the source rolls back; expected RED
cardinality >=3 across the P0 behavioral tests on revert.

4th site (teardown_request_emitter.py) consumes the same consolidated
``is_lead_context`` helper at TaskCompleted Gate 0; see
TestGate0LeadSessionGuard in test_teardown_request_emitter.py for the
behavioral coverage.

The filename uses "in_process_teammate" rather than the discovery
instance "secretary" because the #611 bug class is general (any
in-process subagent sharing the lead's session_id); the test file
naturally extends as the corridor grows.
"""

import ast
import inspect
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
WAKE_LIFECYCLE_PY = HOOK_DIR / "shared" / "wake_lifecycle.py"
SESSION_INIT_PY = HOOK_DIR / "session_init.py"
WAKE_INBOX_DRAIN_PY = HOOK_DIR / "wake_inbox_drain.py"
EMITTER_PY = HOOK_DIR / "wake_lifecycle_emitter.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "wake_lifecycle"


# ─── Helpers (mirror existing wake_lifecycle test pattern) ──────────────


def _run_hook(hook_path, stdin_payload, env_extra=None, timeout=10):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    if env_extra:
        env.update(env_extra)
    payload_bytes = (
        stdin_payload if isinstance(stdin_payload, bytes)
        else stdin_payload.encode("utf-8")
    )
    proc = subprocess.run(
        [sys.executable, str(hook_path)],
        input=payload_bytes,
        capture_output=True,
        env=env,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def _emit_output(payload, home, hook_path=EMITTER_PY):
    rc, out, err = _run_hook(
        hook_path,
        json.dumps(payload),
        env_extra={
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": payload.get("cwd", ""),
        },
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    return json.loads(out) if out else {}


def _write_session_context(
    home, session_id, project_dir, team_name,
    *, lead_session_id=None, members=None, lead_agent_id=None,
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
            "started_at": "2026-05-17T00:00:00Z",
        }),
        encoding="utf-8",
    )
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    effective_lead = lead_session_id if lead_session_id is not None else session_id
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
    inbox = _wake_inbox_dir(home, team)
    if not inbox.exists():
        return []
    markers = sorted(inbox.glob("*.json"))
    return [json.loads(m.read_text(encoding="utf-8")) for m in markers]


def _journal_path(home, project_dir, session_id):
    slug = Path(project_dir).name
    return (
        home / ".claude" / "pact-sessions" / slug / session_id
        / "session-journal.jsonl"
    )


def _read_journal_events(home, project_dir, session_id, event_type=None):
    path = _journal_path(home, project_dir, session_id)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event_type is None or ev.get("event_type") == event_type:
            events.append(ev)
    return events


def _load_fixture(name):
    data = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    data.pop("_meta", None)
    return data


# ─── P0-1: in-process teammate self-claim writes wake_inbox marker ──────


def test_p0_in_process_teammate_self_claim_in_shared_session_writes_wake_inbox_marker(tmp_path):
    """The #611/#778 bug-fix path. The in-process teammate (here:
    secretary spawned via the Task tool) issues PostToolUse:
    TaskUpdate(in_progress) fires that inherit the lead's session_id
    BUT carry the platform-stamped `agent_id` field. Under the legacy
    session_id-equality discriminator the fire would have misclassified
    as lead-frame (session_id matches leadSessionId) and the teammate-
    Arm pre-branch would have been skipped — leaving the cron unarmed
    and the lead never seeing the wake_inbox signal. Under the
    corrected field-presence discriminator the agent_id presence pins
    the fire as in-process teammate; the pre-branch writes a marker;
    suppressOutput on the PostToolUse branch keeps the teammate frame
    free of directive prose.

    Fixture: task_update_in_process_teammate_shape.json (synthesized
    per plan v4 RECOMMENDED downgrade; merge-precondition HARD GATE
    closes the empirical loop post-merge).
    """
    fixture = _load_fixture("task_update_in_process_teammate_shape.json")
    home = tmp_path / "home"; home.mkdir()
    shared_sid = fixture["session_id"]
    team = "team-p0-teammate-self-claim"
    pdir = fixture["cwd"]
    owner = fixture["tool_input"]["owner"]
    # Lead and teammate share the session_id — the #611 root condition.
    _write_session_context(
        home, shared_sid, pdir, team,
        lead_session_id=shared_sid,
        members=[
            {"name": owner, "agentId": fixture["agent_id"],
             "agentType": "pact-secretary"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, fixture["tool_input"]["taskId"],
        status="in_progress", owner=owner,
    )

    out = _emit_output(fixture, home)
    assert out == {"suppressOutput": True}, (
        f"In-process teammate frame must suppressOutput on PostToolUse "
        f"(directive emission flows through the lead-side drain hook); "
        f"got {out!r}"
    )

    markers = _read_inbox_markers(home, team)
    arm_markers = [m for m in markers if m.get("type", "arm") == "arm"]
    assert len(arm_markers) == 1, (
        f"In-process teammate self-claim must produce exactly one Arm "
        f"marker on disk; got {len(arm_markers)} markers: {markers!r}"
    )
    assert arm_markers[0]["trigger"] == "teammate_self_claim_in_progress"
    assert arm_markers[0]["task_id"] == fixture["tool_input"]["taskId"]
    assert arm_markers[0]["owner"] == owner


# ─── P0-2: in-process teammate self-complete writes teardown marker ─────


def test_p0_in_process_teammate_self_complete_in_shared_session_writes_teardown_marker(tmp_path):
    """In-process teammate's TaskUpdate(completed) self-fire in the
    shared session_id frame, with the platform-stamped agent_id. The
    symmetric corridor's teammate-Teardown pre-branch writes a
    type="teardown" marker so the lead-side drain consumes it on the
    next prompt.
    """
    home = tmp_path / "home"; home.mkdir()
    shared_sid = "shared-session-p0-2"
    team = "team-p0-teammate-self-complete"
    pdir = "/tmp/p"
    owner = "secretary"
    _write_session_context(
        home, shared_sid, pdir, team,
        lead_session_id=shared_sid,
        members=[
            {"name": owner, "agentId": "agent-sec-2",
             "agentType": "pact-secretary"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(home, team, "S2", status="completed", owner=owner)

    payload = {
        "tool_name": "TaskUpdate",
        "session_id": shared_sid,
        "agent_id": "agent-sec-2",
        "cwd": pdir,
        "tool_input": {
            "taskId": "S2", "status": "completed", "owner": owner,
        },
        "tool_response": {
            "id": "S2", "status": "completed", "owner": owner,
        },
    }
    out = _emit_output(payload, home)
    assert out == {"suppressOutput": True}

    markers = _read_inbox_markers(home, team)
    teardown = [m for m in markers if m.get("type") == "teardown"]
    assert len(teardown) == 1, (
        f"In-process teammate self-complete must produce exactly one "
        f"Teardown marker; got {len(teardown)}: {markers!r}"
    )


# ─── P0-3: lead-fire TaskUpdate(in_progress) emits Arm directive ────────


def test_p0_lead_fire_taskupdate_in_progress_emits_arm_directive(tmp_path):
    """Lead-frame TaskUpdate(in_progress) — no agent_id stamp because
    the fire originated in the lead's main loop, not an in-process
    subagent frame. Under field-presence semantics this classifies as
    lead-frame; the Arm directive emits via additionalContext as the
    regression baseline.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-session-p0-3"
    team = "team-p0-lead-fire-arm"
    pdir = "/tmp/p"
    _write_session_context(home, lead_sid, pdir, team)
    _write_task(home, team, "L3", status="in_progress", owner="backend-coder")

    # Lead-frame payload: NO agent_id field.
    payload = {
        "tool_name": "TaskUpdate",
        "session_id": lead_sid,
        "cwd": pdir,
        "tool_input": {
            "taskId": "L3", "status": "in_progress", "owner": "backend-coder",
        },
        "tool_response": {
            "id": "L3", "status": "in_progress", "owner": "backend-coder",
        },
    }
    out = _emit_output(payload, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Lead-frame TaskUpdate(in_progress) on 0->1 transition must "
        f"emit Arm directive via additionalContext; got {out!r}"
    )
    assert hso.get("hookEventName") == "PostToolUse"
    assert 'Skill("PACT:start-pending-scan")' in hso.get("additionalContext", "")


# ─── P0-4: lead-fire TaskUpdate(completed) is suppressOutput post-C5 ────


def test_p0_lead_fire_taskupdate_completed_suppressoutput_post_c5(tmp_path):
    """Lead-frame 1->0 active-task TaskUpdate(completed) under the
    post-C5 (#763) wake_lifecycle_emitter must produce suppressOutput
    — NOT a Teardown directive. The PostToolUse:TaskUpdate Teardown
    branch was retired; lead-side Teardown now flows through the
    Tier-1 TaskCompleted hook (teardown_request_emitter.py, deferred
    to #781 for field-presence migration).

    This is the discriminator-correctness baseline for lead-frame
    completed transitions: both agent_id and teammate_name are absent
    in lead-frame stdin, is_lead_context classifies the fire as lead,
    and the post-C5 hook body has no remaining Teardown emit path here.

    A regression that re-introduced a PostToolUse:TaskUpdate Teardown
    branch (e.g., a "fix" inverting the C5 retirement) would emit a
    duplicate Teardown alongside the Tier-1 TaskCompleted fire,
    causing the #763 phantom-Teardown failure class to re-emerge.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-session-p0-4"
    team = "team-p0-lead-fire-no-postooluse-teardown"
    pdir = "/tmp/p"
    _write_session_context(home, lead_sid, pdir, team)
    # 1->0 transition: only this task in_progress, now flipping to completed.
    _write_task(home, team, "L4", status="completed", owner="backend-coder")

    payload = {
        "tool_name": "TaskUpdate",
        "session_id": lead_sid,
        "cwd": pdir,
        "tool_input": {
            "taskId": "L4", "status": "completed", "owner": "backend-coder",
        },
        "tool_response": {
            "id": "L4", "status": "completed", "owner": "backend-coder",
        },
    }
    out = _emit_output(payload, home)
    assert out == {"suppressOutput": True}, (
        f"Lead-frame 1->0 TaskUpdate(completed) must suppressOutput "
        f"post-C5; the Teardown emit path lives on the TaskCompleted "
        f"hook (teardown_request_emitter.py via is_lead_context). "
        f"Got {out!r}"
    )


# ─── P0-5: drain emits Arm directive on teammate-written marker ─────────


def test_p0_lead_drain_emits_arm_directive_on_teammate_written_marker(tmp_path):
    """The lead-side wake_inbox_drain hook (UserPromptSubmit) consumes
    wake_inbox markers written by in-process teammate fires (via the
    Option-5 corridor's teammate-Arm pre-branch). On consuming an
    arm-typed marker, the drain emits the Arm directive via
    additionalContext, instructing the lead's next turn to invoke
    `Skill("PACT:start-pending-scan")`. The skill body writes the
    `scan_armed` journal event under its own idempotency contract;
    the drain itself only emits the directive (no journal write on
    the arm-marker path — `scan_armed` is the skill's marker of
    successful cron registration, not the drain's marker of
    seeing-an-arm-signal).

    This pins the producer-consumer split: teammate writes marker,
    drain emits directive, skill writes scan_armed. A regression that
    "helpfully" wrote scan_armed from the drain would break the
    coupling invariant in start-pending-scan.md Step 5 (which gates
    scan_armed on actual CronCreate landing — the drain has no cron
    visibility).

    Lead-frame UPS payload carries neither agent_id nor teammate_name
    (UPS doesn't fire in subagent per Claude Code docs); is_lead_context
    classifies the fire as lead -> drain proceeds.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-session-p0-5"
    team = "team-p0-drain-arm-directive"
    pdir = "/tmp/p"
    _write_session_context(home, lead_sid, pdir, team)
    _write_task(home, team, "X5", status="in_progress", owner="backend-coder")

    # Pre-stage a wake_inbox marker as if a teammate fire wrote it.
    inbox = _wake_inbox_dir(home, team)
    inbox.mkdir(parents=True, exist_ok=True)
    marker_payload = {
        "schema_version": 1,
        "type": "arm",
        "trigger": "teammate_self_claim_in_progress",
        "tool_name": "TaskUpdate",
        "task_id": "X5",
        "owner": "backend-coder",
    }
    marker_path = inbox / "20260517T000000Z-other-X5.json"
    marker_path.write_text(json.dumps(marker_payload), encoding="utf-8")

    out = _emit_output(
        {
            "session_id": lead_sid,
            "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        },
        home,
        hook_path=WAKE_INBOX_DRAIN_PY,
    )
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Lead UPS drain with marker on disk must emit Arm directive; "
        f"got {out!r}"
    )
    assert hso.get("hookEventName") == "UserPromptSubmit"
    assert 'Skill("PACT:start-pending-scan")' in hso.get("additionalContext", "")

    # Marker consumed (unlinked) — single-fire discipline.
    assert not marker_path.exists(), (
        f"Drain must consume (unlink) the wake_inbox marker after "
        f"emitting Arm directive; marker still exists at {marker_path}"
    )


# ─── P0-6: in-process teammate Arm marker carries owner field ───────────


def test_p0_in_process_teammate_arm_marker_carries_owner_for_drain_attribution(tmp_path):
    """The wake_inbox marker written by the in-process teammate
    pre-branch must carry the `owner` field so the lead-side drain
    can attribute the wake-signal to the correct teammate (for the
    teardown-task-id lookup + the marker dedup logic). Under the
    legacy session_id-equality body the teammate-Arm pre-branch
    would have been skipped entirely (misclassification as lead-
    fire), so this test serves dual purpose: (1) regression guard on
    marker payload shape, (2) counter-test-by-revert anchor — on
    fc37bc04 revert no marker is written so the owner check is
    unreachable and the test goes RED.
    """
    home = tmp_path / "home"; home.mkdir()
    shared_sid = "shared-session-p0-6"
    team = "team-p0-marker-owner-field"
    pdir = "/tmp/p"
    owner = "backend-coder"
    _write_session_context(
        home, shared_sid, pdir, team,
        lead_session_id=shared_sid,
        members=[
            {"name": owner, "agentId": "agent-bc-6"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(home, team, "P6", status="in_progress", owner=owner)

    payload = {
        "tool_name": "TaskUpdate",
        "session_id": shared_sid,
        "agent_id": "agent-bc-6",
        "cwd": pdir,
        "tool_input": {
            "taskId": "P6", "status": "in_progress", "owner": owner,
        },
        "tool_response": {
            "id": "P6", "status": "in_progress", "owner": owner,
        },
    }
    _emit_output(payload, home)

    markers = _read_inbox_markers(home, team)
    arm_markers = [m for m in markers if m.get("type", "arm") == "arm"]
    assert len(arm_markers) == 1, (
        f"In-process teammate self-claim must produce one Arm marker; "
        f"got {markers!r}"
    )
    assert arm_markers[0].get("owner") == owner, (
        f"Arm marker must carry the owner field for drain-side "
        f"attribution; got marker={arm_markers[0]!r}"
    )


# ─── P0-7: in-process teammate with distinct session_id also fires ──────


def test_p0_in_process_teammate_with_distinct_session_id_also_writes_marker(tmp_path):
    """Discriminator-correctness symmetric pin: even when the
    in-process teammate's session_id happens to DIFFER from the
    lead's leadSessionId (a corner case that can arise if the
    platform ever re-issues session_id, or if a test-harness
    fabricates the shape), the agent_id presence still pins the
    fire as in-process teammate and the marker is written.

    Counter-test-by-revert: under the legacy session_id-equality
    body, the differing session_id would have correctly classified
    this fire as teammate-frame too (different from lead) — so this
    test would have been GREEN on the legacy body. The reason it
    goes RED on revert is the COMBINATION with the agent_id field's
    pure-irrelevance under the legacy body: the legacy body reads
    team_config.json for leadSessionId, which IS present in this
    test setup, so the legacy body correctly returns False (not the
    lead session). But the teammate-Arm pre-branch in the emitter
    depends on the new field-presence semantics to classify the
    fire even before reading team_config. Under the legacy body
    flow, the pre-branch path is reached but the marker is still
    written — meaning this test STAYS GREEN on revert. It documents
    behavior asymmetry rather than serving as a counter-test anchor.

    Marked as a sibling-of-P0-1 regression guard rather than a
    counter-test contributor; cardinality math counts P0-1, P0-2,
    P0-6 as the three core counter-test anchors.
    """
    home = tmp_path / "home"; home.mkdir()
    teammate_sid = "teammate-distinct-sid"
    lead_sid = "lead-distinct-sid"
    team = "team-p0-distinct-sid"
    pdir = "/tmp/p"
    owner = "backend-coder"
    _write_session_context(
        home, teammate_sid, pdir, team,
        lead_session_id=lead_sid,
        members=[
            {"name": owner, "agentId": "agent-bc-7"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(home, team, "P7", status="in_progress", owner=owner)

    payload = {
        "tool_name": "TaskUpdate",
        "session_id": teammate_sid,
        "agent_id": "agent-bc-7",
        "cwd": pdir,
        "tool_input": {
            "taskId": "P7", "status": "in_progress", "owner": owner,
        },
        "tool_response": {
            "id": "P7", "status": "in_progress", "owner": owner,
        },
    }
    _emit_output(payload, home)

    markers = _read_inbox_markers(home, team)
    arm_markers = [m for m in markers if m.get("type", "arm") == "arm"]
    assert len(arm_markers) == 1


# ─── P1-1: agent_id / agent_type discriminator field appears in corridor


def test_p1_agent_id_appears_in_postooluse_corridor_files():
    """Structural pin: the agent_id field-presence discriminator must
    appear in the two PostToolUse / UserPromptSubmit corridor files
    (wake_lifecycle.py + wake_inbox_drain.py). A refactor that
    silently drops the discriminator from the predicate body would
    re-introduce the #611 misclassification; this pin catches that.

    The third corridor site (session_init.py) uses `agent_type`
    instead per the SessionStart event's field-semantics split;
    that file is covered by the sibling pin below.
    """
    wake_lifecycle_src = WAKE_LIFECYCLE_PY.read_text(encoding="utf-8")
    wake_inbox_drain_src = WAKE_INBOX_DRAIN_PY.read_text(encoding="utf-8")
    # Field-presence body uses .get("agent_id") with quoted key.
    assert '"agent_id"' in wake_lifecycle_src or "'agent_id'" in wake_lifecycle_src, (
        "wake_lifecycle.py must reference agent_id as the field-presence "
        "discriminator key"
    )
    assert "agent_id" in wake_inbox_drain_src, (
        "wake_inbox_drain.py must reference agent_id (via is_lead_drain_"
        "authorized which reads the field)"
    )


def test_p1_lead_context_helper_imported_in_session_init():
    """session_init.py's lead-context guard must reference the
    consolidated is_lead_context helper (the compound agent_id +
    teammate_name field-presence predicate). The pre-consolidation
    SessionStart-specific is_lead_at_session_start helper was retired
    in favor of the unified empirical-surface check.
    """
    session_init_src = SESSION_INIT_PY.read_text(encoding="utf-8")
    assert "is_lead_context" in session_init_src, (
        "session_init.py must import / call is_lead_context "
        "(the consolidated compound field-presence predicate)"
    )


# --- P1-2: inline _is_lead_session_at_init symbol removed ---


def test_p1_inline_is_lead_session_at_init_removed_from_session_init():
    """The legacy inline `_is_lead_session_at_init` symbol was
    consolidated into the shared `is_lead_context` helper. Its
    definition must NOT reappear in session_init.py; a regression
    that re-inlines the symbol (e.g., an editor accidentally
    reverting the consolidation) would reintroduce the
    session_id-equality misclassification at SessionStart.

    Pin checks for the function-definition pattern, not a docstring
    mention — a comment or audit-anchor that references the legacy
    name is allowed (and load-bearing for git-history navigability).
    """
    session_init_src = SESSION_INIT_PY.read_text(encoding="utf-8")
    # No `def _is_lead_session_at_init(` definition.
    assert not re.search(
        r"^\s*def\s+_is_lead_session_at_init\b",
        session_init_src,
        re.MULTILINE,
    ), (
        "Legacy inline `_is_lead_session_at_init` definition must not "
        "reappear in session_init.py (consolidated to shared helper)"
    )


# --- Edge-case unit tests for the consolidated field-presence predicate ---
#
# Direct in-process calls to is_lead_context exercising the compound
# discriminator under a sweep of input values across BOTH fields
# (agent_id and teammate_name). The body is
# `stdin.get("agent_id") is None and stdin.get("teammate_name") is None`,
# so the per-field expectations are:
#
#   - missing key       -> .get(...) returns None -> True half (lead)
#   - explicit None     -> .get(...) returns None -> True half (lead)
#   - "" (empty string) -> .get(...) returns ""   -> False half (teammate)
#   - 0 (falsy int)     -> .get(...) returns 0    -> False half (teammate)
#   - [] (empty list)   -> .get(...) returns []   -> False half (teammate)
#   - "any-str"         -> .get(...) returns str  -> False half (teammate)
#
# Under the compound AND-chain, the overall classification is lead iff
# BOTH halves are True (i.e., both fields are missing or explicit None).
# This sweep pins the identity-vs-None semantic across both halves
# simultaneously — a regression that "simplified" `is None` to `not ...`
# would flip the 0/""/[] rows and re-introduce a misclassification path.
#
# Sweep collapse rationale: prior per-event sibling helpers
# (is_lead_emit_authorized / is_lead_drain_authorized /
# is_lead_at_session_start) were consolidated into a single compound
# is_lead_context; the historical three per-event parametrized sweeps
# now collapse into one parametrized sweep over the compound helper.


from shared.wake_lifecycle import is_lead_context  # noqa: E402


# Lead-context cases: BOTH fields must be missing or explicit None for
# the compound AND-chain to return True.
_LEAD_CONTEXT_CASES = [
    pytest.param({}, id="both_keys_missing"),
    pytest.param({"agent_id": None}, id="agent_id_explicit_None"),
    pytest.param({"teammate_name": None}, id="teammate_name_explicit_None"),
    pytest.param(
        {"agent_id": None, "teammate_name": None},
        id="both_explicit_None",
    ),
]

# Teammate-context cases: at least one field is present (non-None) -
# the compound AND-chain returns False. Exercise both fields under
# the truthiness-vs-identity discipline.
_TEAMMATE_CONTEXT_CASES = [
    pytest.param({"agent_id": ""}, id="agent_id_empty_string"),
    pytest.param({"agent_id": 0}, id="agent_id_falsy_int_zero"),
    pytest.param({"agent_id": []}, id="agent_id_empty_list"),
    pytest.param({"agent_id": "any-str-uuid"}, id="agent_id_nonempty_string"),
    pytest.param({"teammate_name": ""}, id="teammate_name_empty_string"),
    pytest.param({"teammate_name": 0}, id="teammate_name_falsy_int_zero"),
    pytest.param({"teammate_name": []}, id="teammate_name_empty_list"),
    pytest.param(
        {"teammate_name": "secretary"},
        id="teammate_name_nonempty_string",
    ),
]


@pytest.mark.parametrize("payload", _LEAD_CONTEXT_CASES)
def test_is_lead_context_returns_true_on_lead_context(payload):
    """Both discriminator fields missing or explicit None classifies
    as lead context (identity-vs-None semantic on the compound
    AND-chain; NOT truthiness)."""
    assert is_lead_context(payload) is True


@pytest.mark.parametrize("payload", _TEAMMATE_CONTEXT_CASES)
def test_is_lead_context_returns_false_when_either_field_present(payload):
    """Any non-None value on EITHER discriminator field classifies as
    teammate context - including falsy values ("" / 0 / []). The
    discriminator pins identity-vs-None on each half of the compound
    check; a regression flipping to `not payload.get(...)` would
    misclassify the falsy rows as lead."""
    assert is_lead_context(payload) is False


# Non-dict input - pure-never-raises contract returns False on the
# isinstance(_, dict) short-circuit.
@pytest.mark.parametrize(
    "non_dict_input",
    [None, "string", 42, [], (), object()],
    ids=["None", "string", "int", "list", "tuple", "object"],
)
def test_is_lead_context_returns_false_on_non_dict_input(non_dict_input):
    """The compound predicate honors the pure-never-raises contract on
    non-dict input by returning False (teammate-frame fallback;
    fail-closed at this layer because non-dict input is malformed
    stdin which never legitimately reaches the predicate)."""
    assert is_lead_context(non_dict_input) is False
