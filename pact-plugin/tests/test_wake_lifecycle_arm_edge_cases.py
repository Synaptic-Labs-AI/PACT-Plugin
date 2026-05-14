"""
Edge-case coverage for the wake-lifecycle Arm starvation fix.

These tests probe gaps not covered by the primary 10+6 surface in
test_wake_lifecycle_arm_starvation.py + test_wake_inbox_drain.py:

- Drain hook fail-open when team config is missing (no team_name
  resolvable) → suppressOutput, no crash, no false-positive Arm emit.
- Drain hook path-traversal defense: a team_name that fails
  is_safe_path_component is rejected at _wake_inbox_path resolution.
  (Emitter-side path-traversal defense is via clause 1 of the
  predicate ladder, tested via the marker-not-written contract.)
- Marker writer boundary inputs on task_id and session_id (empty
  string, path-separator-bearing) — verify clause 6 (task_id) +
  pre-marker session_id guard reject without raising.

These are HIGH-tier additions per the test-engineer risk assessment
(novel cross-session filesystem-bridge primitive). They probe failure
modes the spec acknowledged as fail-open but did not pin in tests.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
EMITTER = HOOK_DIR / "wake_lifecycle_emitter.py"
DRAIN = HOOK_DIR / "wake_inbox_drain.py"


def _run(target, stdin_payload, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    if env_extra:
        env.update(env_extra)
    payload_bytes = (
        stdin_payload if isinstance(stdin_payload, bytes)
        else stdin_payload.encode("utf-8")
    )
    proc = subprocess.run(
        [sys.executable, str(target)],
        input=payload_bytes,
        capture_output=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


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
            "started_at": "2026-05-14T00:00:00Z",
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


# ─── Drain hook: missing/unreadable team config ──────────────────────


def test_drain_suppresses_when_team_name_unresolvable(tmp_path):
    """Drain hook fail-open guard: when get_team_name() returns empty
    (no pact-session-context.json on disk), the hook MUST suppressOutput
    silently — no crash, no Arm emit, no advisory.

    This pins the documented degradation posture for sessions that
    have lost their session context mid-run (or for non-PACT Claude
    Code sessions where this hook still fires per hooks.json
    UserPromptSubmit registration).
    """
    home = tmp_path / "home"; home.mkdir()
    # Deliberately NOT writing pact-session-context.json or team config.

    rc, out, err = _run(DRAIN, json.dumps({
        "session_id": "any-sid",
        "cwd": "/tmp/p",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }), env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/p"})
    assert rc == 0, f"non-zero exit; stderr={err}"
    payload = json.loads(out)
    assert payload.get("suppressOutput") is True, (
        f"Missing team config must suppressOutput; got {payload!r}"
    )
    # Must NOT carry an additionalContext with Arm prose.
    hso = payload.get("hookSpecificOutput", {})
    if "additionalContext" in hso:
        assert "First active teammate task created" not in (
            hso["additionalContext"]
        ), "Missing team config must not emit a false-positive Arm"


# ─── Emitter teammate-Arm: path-traversal on team_name ───────────────


def test_emitter_rejects_path_traversal_team_name(tmp_path):
    """Clause 1 of the predicate ladder: a team_name containing path
    separators or relative-path tokens must NOT result in a marker
    written outside the wake_inbox directory.

    Setup: write a session context with a deliberately-unsafe
    team_name. The session_init path-safety guard would normally
    reject this upstream, but the emitter's clause 1 is the defense-
    in-depth pin we want.

    Test invokes the helper directly (subprocess would require a
    valid team_name to even reach _decide_directive without short-
    circuiting elsewhere; the helper's clause-1 contract is the
    falsifiable target).
    """
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    home = tmp_path / "home"; home.mkdir()
    os.environ["HOME"] = str(home)

    payload = {
        "tool_name": "TaskUpdate",
        "session_id": "teammate-sid",
        "cwd": "/tmp/p",
        "tool_input": {
            "taskId": "X", "status": "in_progress", "owner": "backend-coder",
        },
        "tool_response": {
            "id": "X", "status": "in_progress", "owner": "backend-coder",
        },
    }

    # Path-traversal attempts.
    for unsafe in ("../etc", "../../passwd", "team/with/slash",
                   "team\\with\\backslash", ".", ".."):
        emitter._maybe_write_teammate_arm_marker(payload, unsafe)

    # No marker file written anywhere under ~/.claude/teams/.
    teams_root = home / ".claude" / "teams"
    if teams_root.exists():
        markers = list(teams_root.rglob("*.json"))
        assert markers == [], (
            f"Path-traversal team_name must not write any marker; got "
            f"{[str(m) for m in markers]}"
        )
    # Primary assertion above (teams_root.rglob empty) is the load-bearing
    # falsifiable check; this earlier non-falsifiable belt-and-suspenders
    # assertion was removed in favor of the clean rglob check.


# ─── Emitter teammate-Arm: boundary task_id / session_id ─────────────


def test_emitter_rejects_empty_task_id(tmp_path):
    """Clause 6 of the predicate ladder: missing or empty task_id
    blocks marker write.

    Setup: full fixture-shape teammate self-claim TaskUpdate but with
    taskId mutated to an empty string. The helper must return without
    writing any marker and without raising.
    """
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    home = tmp_path / "home"; home.mkdir()
    os.environ["HOME"] = str(home)
    teammate_sid = "teammate-sid"
    team = "team-empty-task-id"
    _write_session_context(
        home, teammate_sid, "/tmp/p", team,
        lead_session_id="lead-sid",
        members=[
            {"name": "backend-coder", "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    tasks_dir = home / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "X.json").write_text(json.dumps(
        {"id": "X", "status": "in_progress", "owner": "backend-coder"}
    ), encoding="utf-8")

    payload = {
        "tool_name": "TaskUpdate",
        "session_id": teammate_sid,
        "cwd": "/tmp/p",
        "tool_input": {
            "taskId": "", "status": "in_progress", "owner": "backend-coder",
        },
        "tool_response": {
            "id": "", "status": "in_progress", "owner": "backend-coder",
        },
    }
    # Must not raise.
    emitter._maybe_write_teammate_arm_marker(payload, team)

    inbox = home / ".claude" / "teams" / team / "wake_inbox"
    if inbox.exists():
        markers = list(inbox.glob("*.json"))
        assert markers == [], (
            f"Empty taskId must not produce a marker; got {markers}"
        )


def test_emitter_rejects_empty_session_id(tmp_path):
    """Pre-marker guard: an empty/missing session_id blocks marker
    write. Defense-in-depth against malformed PostToolUse stdin
    payloads.
    """
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    home = tmp_path / "home"; home.mkdir()
    os.environ["HOME"] = str(home)
    team = "team-empty-session-id"
    _write_session_context(
        home, "real-sid", "/tmp/p", team,
        lead_session_id="lead-sid",
        members=[
            {"name": "backend-coder", "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    tasks_dir = home / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "X.json").write_text(json.dumps(
        {"id": "X", "status": "in_progress", "owner": "backend-coder"}
    ), encoding="utf-8")

    # session_id missing entirely.
    payload_missing = {
        "tool_name": "TaskUpdate",
        "cwd": "/tmp/p",
        "tool_input": {
            "taskId": "X", "status": "in_progress", "owner": "backend-coder",
        },
        "tool_response": {
            "id": "X", "status": "in_progress", "owner": "backend-coder",
        },
    }
    emitter._maybe_write_teammate_arm_marker(payload_missing, team)

    # session_id empty string.
    payload_empty = dict(payload_missing, session_id="")
    emitter._maybe_write_teammate_arm_marker(payload_empty, team)

    inbox = home / ".claude" / "teams" / team / "wake_inbox"
    if inbox.exists():
        markers = list(inbox.glob("*.json"))
        assert markers == [], (
            f"Empty/missing session_id must not produce a marker; got "
            f"{markers}"
        )


def test_emitter_sanitizes_separators_in_task_id_and_session_id(tmp_path):
    """Defense-in-depth pin: the helper replaces path separators in
    task_id and session_id before forming the marker filename. Even
    if upstream guards somehow let a separator-bearing id through,
    the resulting filename must NOT escape the wake_inbox directory.
    """
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    home = tmp_path / "home"; home.mkdir()
    os.environ["HOME"] = str(home)
    team = "team-separator-sanitize"
    _write_session_context(
        home, "weird/sid", "/tmp/p", team,
        lead_session_id="lead-sid",
        members=[
            {"name": "backend-coder", "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    tasks_dir = home / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    weird_task_id = "weird/task\\id"
    (tasks_dir / "weird_task_id.json").write_text(json.dumps(
        {"id": weird_task_id, "status": "in_progress", "owner": "backend-coder"}
    ), encoding="utf-8")

    payload = {
        "tool_name": "TaskUpdate",
        "session_id": "weird/sid",
        "cwd": "/tmp/p",
        "tool_input": {
            "taskId": weird_task_id, "status": "in_progress",
            "owner": "backend-coder",
        },
        "tool_response": {
            "id": weird_task_id, "status": "in_progress",
            "owner": "backend-coder",
        },
    }
    emitter._maybe_write_teammate_arm_marker(payload, team)

    inbox = home / ".claude" / "teams" / team / "wake_inbox"
    # If a marker was written at all, it must be inside inbox/ — no
    # escape via separators in the filename.
    if inbox.exists():
        markers = list(inbox.iterdir())
        for m in markers:
            assert m.parent == inbox, (
                f"Marker {m} escaped the inbox directory"
            )
            # No '/' or '\\' in the filename itself.
            assert "/" not in m.name and "\\" not in m.name, (
                f"Separators in filename {m.name!r}"
            )


# ─── Clause-2: tool_name allowlist falsifiable coverage ─────────────


def test_emitter_rejects_disallowed_tool_name(tmp_path):
    """Clause 2 of the predicate ladder: tool_name must be in
    {TaskCreate, TaskUpdate}. A PostToolUse fire from any other tool
    (Bash, Read, Write, Edit, etc.) must NOT write a marker even if
    every other clause would otherwise hold.

    Falsifiability: stripping the clause-2 early-return in
    `_maybe_write_teammate_arm_marker` flips this test RED while
    leaving all other tests GREEN. Closes the coverage gap where
    every other fixture happens to use TaskCreate or TaskUpdate,
    leaving clause 2 belt-and-suspenders without a test.
    """
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    home = tmp_path / "home"; home.mkdir()
    os.environ["HOME"] = str(home)
    teammate_sid = "teammate-sid"
    team = "team-disallowed-tool"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, teammate_sid, "/tmp/p", team,
        lead_session_id="lead-sid",
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    tasks_dir = home / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "X.json").write_text(json.dumps(
        {"id": "X", "status": "in_progress", "owner": teammate_owner}
    ), encoding="utf-8")

    # Shape mirrors a passing teammate self-claim BUT with tool_name=Bash.
    # All other predicate-ladder fields are populated to ensure clause 2
    # is the ONLY discriminator under test.
    payload = {
        "tool_name": "Bash",
        "session_id": teammate_sid,
        "cwd": "/tmp/p",
        "tool_input": {
            "taskId": "X", "status": "in_progress", "owner": teammate_owner,
        },
        "tool_response": {
            "id": "X", "status": "in_progress", "owner": teammate_owner,
        },
    }
    emitter._maybe_write_teammate_arm_marker(payload, team)

    inbox = home / ".claude" / "teams" / team / "wake_inbox"
    if inbox.exists():
        markers = list(inbox.glob("*.json"))
        assert markers == [], (
            f"Disallowed tool_name must not produce a marker; got {markers}"
        )


# ─── Clause-3: pending->in_progress transition discriminator ────────


def test_emitter_rejects_taskupdate_without_status_transition(tmp_path):
    """Clause 3 discriminator: a TaskUpdate WITHOUT a `status` field but
    WITH a teammate `owner` in tool_input must NOT write a marker. Pins
    that the transition check is load-bearing independent of the
    owner-empty-string check in clause 4.

    Falsifiability: stripping clause 3 (the pending->in_progress
    transition check) flips this test RED. The sibling test 3
    (`test_teammate_metadata_only_update_no_marker`) does NOT discriminate
    clause 3 alone because its fixture has no `tool_input.owner`, so
    clause 4 also rejects — the metadata-only fixture is doubly-guarded.
    This test fills that gap with a non-status payload that DOES carry
    an owner.
    """
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    home = tmp_path / "home"; home.mkdir()
    os.environ["HOME"] = str(home)
    teammate_sid = "teammate-sid"
    team = "team-no-status-with-owner"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, teammate_sid, "/tmp/p", team,
        lead_session_id="lead-sid",
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    tasks_dir = home / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "X.json").write_text(json.dumps(
        {"id": "X", "status": "in_progress", "owner": teammate_owner}
    ), encoding="utf-8")

    # TaskUpdate with NO status field but WITH owner. Represents a
    # metadata-only update (e.g. owner reassignment, intentional_wait
    # set) that nonetheless carries an owner string in tool_input.
    payload = {
        "tool_name": "TaskUpdate",
        "session_id": teammate_sid,
        "cwd": "/tmp/p",
        "tool_input": {
            "taskId": "X", "owner": teammate_owner,
        },
        "tool_response": {
            "id": "X", "owner": teammate_owner,
        },
    }
    emitter._maybe_write_teammate_arm_marker(payload, team)

    inbox = home / ".claude" / "teams" / team / "wake_inbox"
    if inbox.exists():
        markers = list(inbox.glob("*.json"))
        assert markers == [], (
            f"TaskUpdate without status transition must not produce a "
            f"marker; got {markers}"
        )
