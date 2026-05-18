"""
Structural invariants for session_init.py's resume-with-active-tasks
Arm directive under the cron-based pending-scan mechanism.

Source-grep tests: pin the directive prose, the count_active_tasks
call site, and the unconditional-emission discipline. We don't run
session_init.py end-to-end for the structural tier — those are
file-parsing fences. The behavioral tier at the bottom of the file
exercises the emit gate via subprocess.

Lead-Session Guard at Directive Emission Layer 0: directives only emit from the lead session. The
hook-level session guard is verified at the source-grep tier (guard
call site exists in session_init.py) and at the behavior tier
(non-lead-session payloads do not produce Arm prose).
"""

from pathlib import Path

import pytest

SESSION_INIT_PATH = (
    Path(__file__).resolve().parent.parent / "hooks" / "session_init.py"
)


@pytest.fixture(scope="module")
def src() -> str:
    return SESSION_INIT_PATH.read_text(encoding="utf-8")


def test_imports_count_active_tasks_from_wake_lifecycle(src):
    """The lifecycle helper module is `shared.wake_lifecycle.py`. The
    session_init resume-Arm branch imports `count_active_tasks` from
    that module to detect first-active-task transitions at SessionStart."""
    assert "from shared.wake_lifecycle import count_active_tasks" in src


def test_calls_count_active_tasks(src):
    # Single call site at the resume-Arm branch.
    assert src.count("count_active_tasks(team_name)") >= 1


def test_directive_references_start_pending_scan_command_slug(src):
    assert 'Skill("PACT:start-pending-scan")' in src


def test_directive_includes_idempotency_clause(src):
    # Cron mechanism directive prose: "Idempotent — no-op if a valid
    # pending-scan cron entry is already on disk." Source may split
    # across two quoted strings via Python implicit-concat, so
    # substring matches accommodate the line break — pin shorter
    # fragments. The cron entry's existence is the armed-state bit
    # under CronList idempotency.
    assert "idempotent" in src.lower()
    assert "no-op" in src.lower()
    assert "cron" in src.lower()


def test_directive_includes_active_task_trigger_phrase(src):
    """The Tier-0 directive must declare the precondition (active tasks
    on disk) so an LLM reader cannot misread it as unconditional Arm
    on every session start."""
    assert "Active teammate tasks detected" in src


def test_directive_emitted_only_when_count_positive(src):
    """Guard the emission with a positive-count check. The directive
    must NOT fire on sessions with zero active teammate tasks. The
    gate expression may include additional conjuncts (e.g., the Lead-Session Guard at Directive Emission
    Layer 0 lead-session guard `_is_lead_session_at_init(...)`) — what
    is load-bearing is the `active_count > 0` predicate participating
    in the if-test."""
    assert "active_count > 0" in src
    # And the gate must use an `if` statement (not a ternary/while/etc.).
    import re as _re
    assert _re.search(r"\bif\s+active_count\s*>\s*0", src) is not None


def test_directive_appended_to_context_parts(src):
    """The directive flows through Tier-0 additionalContext via the
    context_parts append channel, not via a separate emission path."""
    # Source contains a `context_parts.append(` near the Arm directive.
    assert "context_parts.append(" in src
    # And the directive prose lives in that block.
    assert (
        "Active teammate tasks detected on session start." in src
    )


# ---------- Behavioral: session_init Arm-emit gate fires only when count>0 ----------

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

SESSION_INIT_HOOK = SESSION_INIT_PATH


_ARM_DIRECTIVE_PHRASE = "Active teammate tasks detected on session start."


def _stage_pact_session(home: Path, team: str, sid: str, pdir: str) -> None:
    slug = Path(pdir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / sid
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": team,
            "session_id": sid,
            "project_dir": pdir,
            "plugin_root": "",
            "started_at": "2026-04-30T00:00:00Z",
        }),
        encoding="utf-8",
    )


def _stage_active_task(home: Path, team: str) -> None:
    tasks_dir = home / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "1.json").write_text(
        json.dumps({"id": "1", "status": "in_progress", "owner": "backend-coder"}),
        encoding="utf-8",
    )


def _run_session_init(
    home: Path,
    sid: str,
    pdir: str,
    source: str = "resume",
    agent_type: str | None = None,
) -> dict:
    """Run session_init.py with synthesized SessionStart stdin.

    `agent_type` is the platform-stamped actor-discriminator field for
    SessionStart fires; set to a non-None value to synthesize an
    in-process teammate-frame SessionStart (e.g., the in-process
    subagent's SessionStart fire). Lead-frame fires omit the field
    (default None).
    """
    payload_dict: dict = {"session_id": sid, "cwd": pdir, "source": source}
    if agent_type is not None:
        payload_dict["agent_type"] = agent_type
    payload = json.dumps(payload_dict)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update({"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir})
    proc = subprocess.run(
        [sys.executable, str(SESSION_INIT_HOOK)],
        input=payload.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, f"session_init exited {proc.returncode}; stderr={proc.stderr!r}"
    return json.loads(proc.stdout.decode("utf-8") or "{}")


def test_session_init_omits_arm_directive_when_no_active_tasks(tmp_path):
    """Behavioral pin (B4): Arm-emit gate must fire only when
    count_active_tasks > 0. Pure-structural source-grep is false-RED-prone
    on benign refactor (e.g., extracting a helper); subprocess execution
    confirms the gate's actual emit semantics. With zero active tasks
    on disk, the directive prose must NOT appear in additionalContext."""
    home = tmp_path / "home"; home.mkdir()
    # session_id[:8] filters to [a-f0-9-]; use a pure-hex session_id so
    # generate_team_name returns a predictable team name.
    sid = "abcdef01-no-tasks-here"
    pdir = "/tmp/pi-empty"
    team = "pact-abcdef01"
    _stage_pact_session(home, team, sid, pdir)
    # Stage the team's tasks dir but leave it empty.
    (home / ".claude" / "tasks" / team).mkdir(parents=True)
    out = _run_session_init(home, sid, pdir)
    additional = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert _ARM_DIRECTIVE_PHRASE not in additional, (
        "Arm directive emitted with zero active tasks — gate is broken"
    )


def test_session_init_emits_arm_directive_when_active_tasks_present(tmp_path):
    """Symmetric behavioral pin: with one active task on disk,
    additionalContext must carry the Arm directive's precondition phrase."""
    home = tmp_path / "home"; home.mkdir()
    sid = "deadbeef-active-task-present"
    pdir = "/tmp/pi-active"
    team = "pact-deadbeef"
    _stage_pact_session(home, team, sid, pdir)
    _stage_active_task(home, team)
    out = _run_session_init(home, sid, pdir)
    additional = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert _ARM_DIRECTIVE_PHRASE in additional, (
        "Arm directive missing despite active task on disk — gate is broken"
    )
    # And the directive references the canonical command slug.
    assert 'Skill("PACT:start-pending-scan")' in additional


# ---------- Lead-Session Guard at Directive Emission Layer 0: hook-level session guard ----------

def test_session_init_imports_or_calls_lead_session_guard(src):
    """Lead-Session Guard at Directive Emission Layer 0 (structural
    tier): session_init.py must CALL a lead-session guard before
    emitting the Arm directive. Under Option 5 (#611/#778) the guard
    is the shared helper `is_lead_at_session_start(input_data,
    team_name)` from shared.wake_lifecycle, which checks `agent_type`
    field-presence on stdin (SessionStart uses agent_type rather than
    PostToolUse's agent_id per Claude Code platform docs). A
    teammate-frame fire carrying agent_type suppresses emission so
    teammate sessions never receive the Arm prose.

    The guard symbol must appear within a control-flow construct
    (if-statement or return-statement etc.), NOT just anywhere in
    source. A hostile edit that removes the actual guard call but
    leaves a docstring mention of the guard would pass a permissive
    substring check; the regex-in-code-line check catches the
    wiring-disconnect.

    Tight pin: matches ONLY the canonical post-Option-5 symbol
    `is_lead_at_session_start`. The legacy symbols
    `_is_lead_session_at_init` (inline; consolidated to shared
    helper) and `leadSessionId` (team_config-coupled comparison;
    pre-Option-5 body) are intentionally NOT in the tolerance band:
    the corridor migration is settled, and a regression reintroducing
    either legacy symbol in a control-flow line should fail this
    test loudly rather than silently passing.

    Defense-in-depth Layer 0 (per plan §Architecture Lead-Session
    Guard at Directive Emission) catches misdirected directive
    emission at the source. Layers 1 (skill-body Lead-Session Guard)
    and 2 (platform CronCreate session-scoping) both assume Layer 0
    is in place but must remain effective even if it isn't.
    """
    import re as _re
    # Strict: the guard symbol must appear inside an if/return/elif/while/assert
    # statement in the source. Matches ONLY `is_lead_at_session_start`
    # — legacy alternates dropped post-corridor-migration.
    code_line_pattern = _re.compile(
        r"^\s*(if|return|elif|while|assert)\b.*is_lead_at_session_start",
        _re.MULTILINE,
    )
    assert code_line_pattern.search(src) is not None, (
        "Lead-Session Guard at Directive Emission Layer 0 strict: "
        "session_init.py missing guard CALL within control-flow "
        "construct (if/return/elif/while/assert). Expected pattern "
        "(post-Option-5): `if ... is_lead_at_session_start(...)`."
    )


def test_session_init_does_not_emit_arm_directive_from_in_process_teammate_frame(tmp_path):
    """Lead-Session Guard at Directive Emission Layer 0 (behavior
    tier): a teammate-frame session_init payload must NOT produce
    the Arm directive prose. Under Option 5 (#611/#778) the platform
    stamps `agent_type` on stdin for in-process subagent SessionStart
    fires; is_lead_at_session_start classifies the payload as
    teammate-frame (agent_type present -> not lead) and suppresses
    the Arm prose.

    Discriminative setup (post-peer-review tightening): the in-process
    teammate SHARES the lead's session_id (the actual #611 bug pattern).
    Under the legacy session_id-equality at_init body this test would
    FAIL — the matching session_id classifies the payload as lead-frame
    and emission proceeds. Under Option-5 (#611/#778) the agent_type
    field-presence is the sole actor-discriminator and emission is
    correctly suppressed.

    The earlier formulation synthesized teammate-frame via session_id
    distinct from team_config.leadSessionId AND agent_type set; that
    setup passed under BOTH legacy and Option-5 bodies (DUAL signals)
    so it was not discriminative for the field-presence migration.
    See plan §Components Affected for the corridor split: SessionStart
    uses agent_type (not agent_id) per platform docs.
    """
    home = tmp_path / "home"; home.mkdir()
    # Shared session_id is the #611 bug pattern: in-process teammates
    # inherit the lead's session_id; only agent_type discriminates.
    # NOTE: team_name MUST match what generate_team_name() derives
    # from the session_id (first 8 chars of session_id prefixed with
    # "pact-"); otherwise the active-tasks count is zero and the
    # lead-session guard branch is not reached.
    shared_sid = "abcdef01-shared-session-id"
    pdir = "/tmp/pi-non-lead"
    team = "pact-abcdef01"
    _stage_pact_session(home, team, shared_sid, pdir)
    # Stage team config with the SAME session_id as the lead's; a
    # session_id-equality body would classify this as lead-fire.
    import json as _json
    team_dir = home / ".claude" / "teams" / team
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        _json.dumps({"leadSessionId": shared_sid}),
        encoding="utf-8",
    )
    _stage_active_task(home, team)
    # In-process teammate-frame SessionStart: payload carries agent_type.
    out = _run_session_init(
        home, shared_sid, pdir, agent_type="pact-secretary",
    )
    additional = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert _ARM_DIRECTIVE_PHRASE not in additional, (
        "Lead-Session Guard at Directive Emission Layer 0 broken: "
        "session_init emitted Arm directive from in-process teammate "
        "frame. The hook must check agent_type field-presence and "
        "suppress emission when teammate-frame."
    )
    assert 'Skill("PACT:start-pending-scan")' not in additional, (
        "Lead-Session Guard at Directive Emission Layer 0 broken: "
        "session_init emitted start-pending-scan slug from in-process "
        "teammate frame."
    )
