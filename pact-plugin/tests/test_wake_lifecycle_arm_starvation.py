"""
Integration tests for the teammate-Arm pre-branch in
wake_lifecycle_emitter._decide_directive.

Surface: under v4.2.4, the lead-session early-return at the top of
_decide_directive symmetrically suppressed BOTH teammate-side Teardown
emit (correct) AND teammate-side Arm signaling (wrong — starves the
natural trigger source for teammate self-claim transitions). The fix
inserts a pre-branch ABOVE the lead-session early-return that, when the
6-clause asymmetric-guard predicate ladder holds, writes a per-marker
JSON file to ~/.claude/teams/{team}/wake_inbox/ via O_CREAT|O_EXCL
atomic write. The lead-side wake_inbox_drain.py UserPromptSubmit hook
consumes the marker on the next lead prompt.

Teardown stays lead-only-gated (the existing branches BELOW the
lead-session early-return are unchanged), preserving the #737 Layer 0
correctness for Teardown emission.

Counter-test-by-revert: see
tests/runbooks/wake-lifecycle-arm-starvation.md.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
EMITTER = HOOK_DIR / "wake_lifecycle_emitter.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "wake_lifecycle"


def _run_emitter(stdin_payload, env_extra=None):
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
            "started_at": "2026-05-14T00:00:00Z",
        }),
        encoding="utf-8",
    )
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    effective_lead_session = (
        lead_session_id if lead_session_id is not None else session_id
    )
    config_data = {"leadSessionId": effective_lead_session}
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


def _load_fixture(name):
    """Load a fixture and strip the diagnostic _meta sibling."""
    data = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    data.pop("_meta", None)
    return data


def _wake_inbox_dir(home, team):
    return home / ".claude" / "teams" / team / "wake_inbox"


# ─── Teammate-Arm pre-branch: positive + negative cases ────────────────


def test_teammate_self_claim_writes_inbox_marker(tmp_path):
    """Test 1 — Symmetric-guard regression guard. Captured teammate
    self-claim TaskUpdate(status=in_progress) shape fires in a teammate
    session (session_id != leadSessionId). The pre-branch writes
    exactly one inbox marker; the lead-session-gated branches return
    suppressOutput; nothing reaches stdout but the marker is on disk.
    """
    fixture = _load_fixture("teammate_claim_in_progress_shape.json")
    home = tmp_path / "home"; home.mkdir()
    teammate_sid = fixture["session_id"]
    lead_sid = "lead-session-id"
    team = "team-arm-self-claim"
    pdir = fixture["cwd"]
    teammate_owner = fixture["tool_input"]["owner"]
    _write_session_context(
        home, teammate_sid, pdir, team,
        lead_session_id=lead_sid,
        members=[
            {"name": teammate_owner, "agentId": "agent-teammate"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, fixture["tool_input"]["taskId"],
        status="in_progress", owner=teammate_owner,
    )

    out = _emit_output(fixture, home)
    assert out == {"suppressOutput": True}, (
        f"Teammate session must not emit any PostToolUse directive; "
        f"got {out!r}"
    )

    inbox_dir = _wake_inbox_dir(home, team)
    assert inbox_dir.exists(), "wake_inbox directory must be created"
    markers = list(inbox_dir.glob("*.json"))
    assert len(markers) == 1, (
        f"Expected exactly 1 inbox marker; got {len(markers)}: "
        f"{[m.name for m in markers]}"
    )
    payload = json.loads(markers[0].read_text(encoding="utf-8"))
    assert payload["trigger"] == "teammate_self_claim_in_progress"
    assert payload["tool_name"] == "TaskUpdate"
    assert payload["task_id"] == fixture["tool_input"]["taskId"]
    assert payload["owner"] == teammate_owner
    assert payload["writer_session_id"] == teammate_sid


def test_teammate_self_claim_no_marker_when_owner_is_lead(tmp_path):
    """Test 2 — Clause 4 defense: even if a TaskUpdate carries
    status=in_progress, an owner equal to the team's lead must NOT
    trigger a marker write. Defends against hypothetical re-assignment
    to the lead and against lead self-claim.
    """
    home = tmp_path / "home"; home.mkdir()
    teammate_sid = "teammate-sid"
    lead_sid = "lead-sid"
    team = "team-arm-lead-owner"
    pdir = "/tmp/p"
    _write_session_context(
        home, teammate_sid, pdir, team,
        lead_session_id=lead_sid,
        members=[
            {"name": "the-lead", "agentId": "agent-lead"},
            {"name": "backend-coder", "agentId": "agent-bc"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(home, team, "L", status="in_progress", owner="the-lead")

    out = _emit_output({
        "tool_name": "TaskUpdate",
        "session_id": teammate_sid,
        "cwd": pdir,
        "tool_input": {
            "taskId": "L", "status": "in_progress", "owner": "the-lead",
        },
        "tool_response": {
            "id": "L", "status": "in_progress", "owner": "the-lead",
        },
    }, home)
    assert out == {"suppressOutput": True}

    inbox_dir = _wake_inbox_dir(home, team)
    if inbox_dir.exists():
        markers = list(inbox_dir.glob("*.json"))
        assert markers == [], (
            f"Lead-owned TaskUpdate must not write a marker; got "
            f"{[m.name for m in markers]}"
        )


def test_teammate_metadata_only_update_no_marker(tmp_path):
    """Test 3 — Clause 3 defense: TaskUpdate with no status field
    (metadata-only edit such as teachback_submit, intentional_wait,
    handoff) must NOT trigger a marker write. The pending->in_progress
    transition is the load-bearing trigger.
    """
    fixture = _load_fixture("teammate_metadata_only_update_shape.json")
    home = tmp_path / "home"; home.mkdir()
    teammate_sid = fixture["session_id"]
    lead_sid = "lead-sid"
    team = "team-arm-metadata-only"
    pdir = fixture["cwd"]
    _write_session_context(
        home, teammate_sid, pdir, team,
        lead_session_id=lead_sid,
        members=[
            {"name": "backend-coder", "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )

    out = _emit_output(fixture, home)
    assert out == {"suppressOutput": True}

    inbox_dir = _wake_inbox_dir(home, team)
    if inbox_dir.exists():
        markers = list(inbox_dir.glob("*.json"))
        assert markers == [], (
            f"Metadata-only TaskUpdate must not write a marker; got "
            f"{[m.name for m in markers]}"
        )


# ─── Lead-session regression guards: existing emit paths still fire ────


def test_lead_session_arm_still_fires_on_taskcreate(tmp_path):
    """Test 4 — Lead-session Arm path not broken by the new pre-branch.
    A lead-session TaskCreate with count >= 1 still emits _ARM_DIRECTIVE
    via additionalContext.
    """
    home = tmp_path / "home"; home.mkdir()
    sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-lead-arm-create"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "X", status="in_progress", owner="backend-coder")

    out = _emit_output({
        "tool_name": "TaskCreate",
        "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "X"},
        "tool_response": {"task": {"id": "X"}},
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Lead-session TaskCreate must emit Arm; got {out!r}"
    )
    assert hso["hookEventName"] == "PostToolUse"
    assert 'Skill("PACT:start-pending-scan")' in hso["additionalContext"]


def test_lead_session_arm_still_fires_on_taskupdate_in_progress(tmp_path):
    """Test 5 — Lead-session re-Arm on TaskUpdate(in_progress) path not
    broken by the new pre-branch. Mirrors Bug B re-Arm semantics under
    the asymmetric-guard rewrite.
    """
    home = tmp_path / "home"; home.mkdir()
    sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-lead-arm-update"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "Y", status="in_progress", owner="backend-coder")

    out = _emit_output({
        "tool_name": "TaskUpdate",
        "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "Y", "status": "in_progress"},
        "tool_response": {
            "id": "Y", "status": "in_progress", "owner": "backend-coder",
        },
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Lead-session TaskUpdate(in_progress) must emit Arm; got {out!r}"
    )
    assert hso["hookEventName"] == "PostToolUse"
    assert 'Skill("PACT:start-pending-scan")' in hso["additionalContext"]


def test_lead_session_teardown_still_fires_on_terminal_status(tmp_path):
    """Test 6 — Teardown lead-only gate preserved (#737 Layer 0). Lead-
    session TaskUpdate(status=completed) with count=0 emits Teardown.
    """
    home = tmp_path / "home"; home.mkdir()
    sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-lead-teardown"
    _write_session_context(home, sid, pdir, team)
    # Task on disk reflects post-state (completed) so count == 0.
    _write_task(home, team, "Z", status="completed", owner="backend-coder")

    out = _emit_output({
        "tool_name": "TaskUpdate",
        "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "Z", "status": "completed"},
        "tool_response": {
            "id": "Z", "status": "completed", "owner": "backend-coder",
        },
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Lead-session terminal TaskUpdate must emit Teardown; got {out!r}"
    )
    assert hso["hookEventName"] == "PostToolUse"
    assert 'Skill("PACT:stop-pending-scan")' in hso["additionalContext"]


def test_teammate_terminal_status_no_marker_and_no_directive(tmp_path):
    """Test 7 — Teammate-side Teardown still suppressed by lead-session
    early-return; pre-branch's clause-3 also rejects terminal-status
    TaskUpdates so no marker is written either.
    """
    home = tmp_path / "home"; home.mkdir()
    teammate_sid = "teammate-sid"
    lead_sid = "lead-sid"
    team = "team-teammate-terminal"
    pdir = "/tmp/p"
    _write_session_context(
        home, teammate_sid, pdir, team,
        lead_session_id=lead_sid,
        members=[
            {"name": "backend-coder", "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(home, team, "T", status="completed", owner="backend-coder")

    out = _emit_output({
        "tool_name": "TaskUpdate",
        "session_id": teammate_sid, "cwd": pdir,
        "tool_input": {
            "taskId": "T", "status": "completed", "owner": "backend-coder",
        },
        "tool_response": {
            "id": "T", "status": "completed", "owner": "backend-coder",
        },
    }, home)
    assert out == {"suppressOutput": True}

    inbox_dir = _wake_inbox_dir(home, team)
    if inbox_dir.exists():
        markers = list(inbox_dir.glob("*.json"))
        assert markers == [], (
            f"Teammate terminal-status update must not write a marker; "
            f"got {[m.name for m in markers]}"
        )


# ─── Marker shape / atomicity pins ─────────────────────────────────────


def test_marker_filename_schema(tmp_path):
    """Test 8 — Filename encoding pin. The marker filename matches
    {ISO-8601 compact UTC}-{session_id}-{task_id}.json so lexical sort
    is chronological order for the drain side.
    """
    fixture = _load_fixture("teammate_claim_in_progress_shape.json")
    home = tmp_path / "home"; home.mkdir()
    teammate_sid = fixture["session_id"]
    team = "team-marker-schema"
    pdir = fixture["cwd"]
    teammate_owner = fixture["tool_input"]["owner"]
    _write_session_context(
        home, teammate_sid, pdir, team,
        lead_session_id="lead-sid",
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, fixture["tool_input"]["taskId"],
        status="in_progress", owner=teammate_owner,
    )

    _emit_output(fixture, home)

    inbox_dir = _wake_inbox_dir(home, team)
    markers = list(inbox_dir.glob("*.json"))
    assert len(markers) == 1
    # {timestamp}-{session_id}-{task_id}.json — timestamp YYYYMMDDTHHMMSSZ
    # session_id is a UUID; allow the general session_id alphabet
    # (hex + dashes); task_id allows the platform's compact alphabet.
    pattern = re.compile(
        r"^\d{8}T\d{6}Z-[A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.json$"
    )
    assert pattern.match(markers[0].name), (
        f"Marker filename {markers[0].name!r} does not match expected "
        f"schema {pattern.pattern!r}"
    )


def test_marker_o_excl_collision_silent(tmp_path):
    """Test 9 — O_EXCL discipline. Pre-create a marker file at a path
    the helper would write to, then invoke the helper. The collision
    must be silently swallowed (FileExistsError fail-open); no raise,
    no duplicate.

    Implementation: invoke the helper directly via import (faster +
    more focused than a subprocess fire that re-derives the timestamp
    on its own).

    SCOPE TRADEOFF (test-engineer note): this test is intra-process
    and monkeypatches `emitter.datetime` to force a deterministic
    timestamp collision. It pins the FileExistsError fail-open
    contract in os.open(O_CREAT|O_EXCL), which IS the correct
    falsifiable target for this helper. It does NOT directly probe
    the scenario of two concurrent teammate subprocess fires racing
    on the same wall-clock second. The intra-process approach was
    chosen over subprocess concurrency because the latter is flaky
    on slow CI. The cross-process race is structurally impossible
    under the {timestamp, session_id, task_id} encoding (each
    teammate session has a unique session_id) — O_EXCL is
    belt-and-suspenders, this test pins the belt.
    """
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    home = tmp_path / "home"; home.mkdir()
    teammate_sid = "teammate-sid"
    team = "team-o-excl"
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
    _write_task(home, team, "ABC", status="in_progress", owner=teammate_owner)

    # Pre-populate the wake_inbox directory with a placeholder file at
    # a path the helper will compute via the same timestamp — to force
    # a collision deterministically we monkeypatch datetime.now to
    # return a fixed instant.
    from datetime import datetime, timezone
    fixed = datetime(2026, 5, 14, 16, 5, 26, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    original_dt = emitter.datetime
    emitter.datetime = _FixedDateTime
    try:
        os.environ["HOME"] = str(home)
        payload = {
            "tool_name": "TaskUpdate",
            "session_id": teammate_sid,
            "cwd": pdir,
            "tool_input": {
                "taskId": "ABC", "status": "in_progress",
                "owner": teammate_owner,
            },
            "tool_response": {
                "id": "ABC", "status": "in_progress",
                "owner": teammate_owner,
            },
        }
        # First call writes the marker.
        emitter._maybe_write_teammate_arm_marker(payload, team)
        inbox_dir = _wake_inbox_dir(home, team)
        markers_after_first = list(inbox_dir.glob("*.json"))
        assert len(markers_after_first) == 1

        # Second call MUST silently no-op via O_EXCL collision.
        emitter._maybe_write_teammate_arm_marker(payload, team)
        markers_after_second = list(inbox_dir.glob("*.json"))
        assert len(markers_after_second) == 1, (
            f"Second invocation must collision-no-op; got "
            f"{[m.name for m in markers_after_second]}"
        )
    finally:
        emitter.datetime = original_dt


# ─── Audit-anchor regression guards ────────────────────────────────────


def test_arm_directive_audit_anchor_literal_prose():
    """Test 10 — Audit-anchor pin for the _ARM_DIRECTIVE literal prose.
    The directive prose is the user-visible contract; a future agent
    renaming it silently must trip this pin. Mirrors the existing
    test_wake_lifecycle_bug_b_rearm.py audit-anchor coverage; this pin
    is needed here because wake_inbox_drain.py imports _ARM_DIRECTIVE
    from the emitter (single SSOT) and the drain hook's behavior
    depends on the literal being stable.
    """
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter
    assert "First active teammate task created" in emitter._ARM_DIRECTIVE
    assert 'Skill("PACT:start-pending-scan")' in emitter._ARM_DIRECTIVE
    assert "Idempotent" in emitter._ARM_DIRECTIVE
