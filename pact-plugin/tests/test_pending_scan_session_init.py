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
    gate expression may include additional conjuncts (e.g., the
    Lead-Context Guard at Directive Emission Layer 0
    `is_lead_context(...)`) — what is load-bearing is the
    `active_count > 0` predicate participating in the if-test."""
    assert "active_count > 0" in src
    # And the gate must use an `if` statement (not a ternary/while/etc.).
    # Per this test's own contract, the gate expression may carry additional
    # conjuncts; one such conjunct (the auto-arm sentinel
    # `CRON_AUTOARM_ENABLED and ...`) precedes `active_count > 0`, so allow
    # any same-line prefix between `if` and the load-bearing predicate.
    import re as _re
    assert _re.search(r"\bif\b[^\n]*\bactive_count\s*>\s*0", src) is not None


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
    agent_id: str | None = None,
    teammate_name: str | None = None,
    autoarm_enabled: bool = True,
    managed_teammate_mode: str | None = None,
) -> dict:
    """Run session_init.py with synthesized SessionStart stdin.

    Under the consolidated is_lead_context discriminator, an
    in-process teammate-frame is synthesized by setting either
    `agent_id` or `teammate_name`. The pre-consolidation
    `agent_type` parameter is retained for callers that exercise
    auxiliary platform-frame schema bits (it does not influence the
    actor-discriminator under the compound check). Lead-frame fires
    omit all three (default None).

    `managed_teammate_mode` controls the (otherwise OS-absolute, #867)
    enterprise managed-settings source that the step-0b in-process notice
    consults at top precedence: None neutralizes it to an ABSENT path under
    the isolated HOME (source[0] inert; behavior-neutral where the real file
    is absent); a value (e.g. "tmux") writes a managed-settings.json carrying
    that teammateMode so a test can exercise the managed-precedence direction.
    """
    payload_dict: dict = {"session_id": sid, "cwd": pdir, "source": source}
    if agent_type is not None:
        payload_dict["agent_type"] = agent_type
    if agent_id is not None:
        payload_dict["agent_id"] = agent_id
    if teammate_name is not None:
        payload_dict["teammate_name"] = teammate_name
    payload = json.dumps(payload_dict)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update({"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir})
    # Hermeticity (#867): the effective-teammateMode resolver consulted by the
    # step-0b in-process notice reads an OS-ABSOLUTE enterprise managed-settings
    # path as its HIGHEST-precedence source (shared.teammate_mode.
    # _managed_settings_path() -> /Library/.../managed-settings.json etc.).
    # HOME/CLAUDE_PROJECT_DIR env isolation does NOT cover it, so without
    # neutralization the notice's firing would be contingent on the real
    # machine's managed settings (green-by-luck on a dev box; false-RED on a
    # managed fleet that sets teammateMode=tmux). monkeypatch cannot cross the
    # subprocess boundary, so rebind _managed_settings_path INSIDE runner_src to
    # a path UNDER the isolated HOME. Default (managed_teammate_mode=None) points
    # at an ABSENT path -> source[0] inert. A value writes a managed-settings.json
    # carrying that teammateMode so a test can exercise managed precedence.
    managed_dir = Path(home) / ".claude" / "_managed_test"
    managed_path = managed_dir / "managed-settings.json"
    if managed_teammate_mode is not None:
        managed_dir.mkdir(parents=True, exist_ok=True)
        managed_path.write_text(
            json.dumps({"teammateMode": managed_teammate_mode}), encoding="utf-8",
        )
    # Production default is CRON_AUTOARM_ENABLED=False (auto-arm disabled).
    # G3 gates the session-start arm directive on it; these tests exercise
    # the arm MACHINERY (still reachable via the manual
    # /PACT:start-pending-scan path), so re-enable the gate in the subprocess
    # by importing the hook, setting the CONSUMER-module binding, and calling
    # main(). Patching shared.wake_lifecycle would NOT reach the already-bound
    # session_init.CRON_AUTOARM_ENABLED name (name-import snapshot). Pass
    # autoarm_enabled=False to exercise production-default suppression.
    #
    # The _managed_settings_path rebind lands BEFORE main(): session_init imports
    # should_emit_inprocess_notice lazily inside main()'s step-0b block, and that
    # function reaches _managed_settings_path() by module-bare-name at call time,
    # so the rebind on the shared.teammate_mode module is honored.
    runner_src = (
        "import sys\n"
        f"sys.path.insert(0, {str(SESSION_INIT_HOOK.parent)!r})\n"
        "import session_init\n"
        f"session_init.CRON_AUTOARM_ENABLED = {autoarm_enabled!r}\n"
        "import shared.teammate_mode as _tm\n"
        "import pathlib as _pl\n"
        f"_tm._managed_settings_path = lambda: _pl.Path({str(managed_path)!r})\n"
        "session_init.main()\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", runner_src],
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

def test_session_init_imports_or_calls_lead_context_guard(src):
    """Lead-Context Guard at Directive Emission Layer 0 (structural
    tier): session_init.py must CALL a lead-context guard before
    emitting the Arm directive. The consolidated helper is
    `is_lead_context(stdin, team_name)` from shared.wake_lifecycle,
    which checks compound `agent_id` + `teammate_name` field-presence
    on stdin. A teammate-frame fire carrying either field suppresses
    emission so teammate sessions never receive the Arm prose.

    The guard symbol must appear within a control-flow construct
    (if-statement or return-statement etc.), NOT just anywhere in
    source. A hostile edit that removes the actual guard call but
    leaves a docstring mention of the guard would pass a permissive
    substring check; the regex-in-code-line check catches the
    wiring-disconnect.

    Tight pin: matches ONLY the canonical consolidated symbol
    `is_lead_context`. The legacy symbols `_is_lead_session_at_init`
    (inline; consolidated to shared helper) and `leadSessionId`
    (team_config-coupled comparison; pre-consolidation body) are
    intentionally NOT in the tolerance band: the consolidation is
    settled, and a regression reintroducing either legacy symbol in
    a control-flow line should fail this test loudly rather than
    silently passing.

    Defense-in-depth Layer 0 (per plan §Architecture Lead-Context
    Guard at Directive Emission) catches misdirected directive
    emission at the source. Layers 1 (skill-body Lead-Context Guard)
    and 2 (platform CronCreate session-scoping) both assume Layer 0
    is in place but must remain effective even if it isn't.
    """
    import re as _re
    # Strict: the guard symbol must appear inside an
    # if/return/elif/while/assert statement in the source. Matches
    # ONLY `is_lead_context` — legacy alternates dropped post-
    # consolidation.
    code_line_pattern = _re.compile(
        r"^\s*(if|return|elif|while|assert)\b.*is_lead_context",
        _re.MULTILINE,
    )
    assert code_line_pattern.search(src) is not None, (
        "Lead-Context Guard at Directive Emission Layer 0 strict: "
        "session_init.py missing guard CALL within control-flow "
        "construct (if/return/elif/while/assert). Expected pattern: "
        "`if ... is_lead_context(...)`."
    )


def test_session_init_does_not_emit_arm_directive_from_in_process_teammate_frame(tmp_path):
    """Lead-Context Guard at Directive Emission Layer 0 (behavior
    tier): a teammate-frame session_init payload must NOT produce
    the Arm directive prose. Under the consolidated discriminator
    the platform stamps `agent_id` (or `teammate_name`) on stdin for
    in-process subagent fires; is_lead_context classifies the
    payload as teammate-frame (either field present -> not lead) and
    suppresses the Arm prose.

    Discriminative setup (post-peer-review tightening): the in-process
    teammate SHARES the lead's session_id (the actual #611 bug pattern).
    Under the legacy session_id-equality at_init body this test would
    FAIL — the matching session_id classifies the payload as lead-frame
    and emission proceeds. Under the consolidated is_lead_context the
    compound field-presence on `agent_id` / `teammate_name` is the
    actor-discriminator and emission is correctly suppressed.

    The earlier formulation synthesized teammate-frame via session_id
    distinct from team_config.leadSessionId AND auxiliary platform
    fields; that setup passed under BOTH legacy and consolidated
    bodies (DUAL signals) so it was not discriminative for the
    field-presence migration.
    """
    home = tmp_path / "home"; home.mkdir()
    # Shared session_id is the #611 bug pattern: in-process teammates
    # inherit the lead's session_id; only the compound field-presence
    # discriminates.
    # NOTE: team_name MUST match what generate_team_name() derives
    # from the session_id (first 8 chars of session_id prefixed with
    # "pact-"); otherwise the active-tasks count is zero and the
    # lead-context guard branch is not reached.
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
    # In-process teammate-frame SessionStart: payload carries agent_id
    # (the compound discriminator's primary field for in-process
    # subagent fires per the empirical capture campaign).
    out = _run_session_init(
        home, shared_sid, pdir, agent_id="subagent-some-uuid",
    )
    additional = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert _ARM_DIRECTIVE_PHRASE not in additional, (
        "Lead-Context Guard at Directive Emission Layer 0 broken: "
        "session_init emitted Arm directive from in-process teammate "
        "frame. The hook must check compound agent_id+teammate_name "
        "field-presence and suppress emission when teammate-frame."
    )
    assert 'Skill("PACT:start-pending-scan")' not in additional, (
        "Lead-Context Guard at Directive Emission Layer 0 broken: "
        "session_init emitted start-pending-scan slug from in-process "
        "teammate frame."
    )


# ===========================================================================
# Cron auto-arm DISABLE contract (Phase 2) — G3 (session_init resume/startup
# arm block) + the step-0b in-process teammateMode notice regression guard.
#
# G3 extended the SAME main() arm `if` that sits just below the step-0b notice
# append. Under the PRODUCTION default CRON_AUTOARM_ENABLED=False the arm
# directive is suppressed; the True-recovery anchor is the existing
# test_session_init_emits_arm_directive_when_active_tasks_present. The step-0b
# tests pin that the G3 edit caused NO collateral damage: the notice still
# fires when arm is suppressed (regression), and both surface together when
# arm is enabled (coexistence — the gate toggles ARM only).
#
# _INPROCESS_NOTICE_FRAGMENT is a distinctive substring of the step-0b notice,
# taken verbatim from the hook's emitted systemMessage and confirmed
# empirically to fire under BOTH gate states in the subprocess harness.
# ===========================================================================

_INPROCESS_NOTICE_FRAGMENT = "unattended runs may stall in in-process teammate mode"


def test_session_init_suppresses_arm_when_autoarm_disabled(tmp_path):
    """G3: with >=1 active task on disk and a lead-context SessionStart, the
    arm directive is SUPPRESSED under CRON_AUTOARM_ENABLED=False. Recovery
    anchor: test_session_init_emits_arm_directive_when_active_tasks_present
    (which runs the same shape at the default autoarm_enabled=True)."""
    home = tmp_path / "home"; home.mkdir()
    sid = "abcdef01-autoarm-off"
    pdir = "/tmp/pi-autoarm-g3"
    team = "pact-abcdef01"
    _stage_pact_session(home, team, sid, pdir)
    _stage_active_task(home, team)
    result = _run_session_init(home, sid, pdir, source="resume", autoarm_enabled=False)
    ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert _ARM_DIRECTIVE_PHRASE not in ctx, (
        f"G3 must suppress the session-start arm directive under "
        f"autoarm-disabled; got additionalContext={ctx!r}"
    )


def test_step0b_notice_still_fires_when_autoarm_disabled(tmp_path):
    """Regression guard: the step-0b in-process teammateMode notice MUST
    still fire under CRON_AUTOARM_ENABLED=False, while the adjacent G3 arm
    directive is suppressed. Pins that the G3 edit to the shared main() arm
    block did not collaterally break the Phase-1 step-0b notice."""
    home = tmp_path / "home"; home.mkdir()
    sid = "abcdef01-step0b-off"
    pdir = "/tmp/pi-step0b-off"
    team = "pact-abcdef01"
    _stage_pact_session(home, team, sid, pdir)
    _stage_active_task(home, team)
    result = _run_session_init(home, sid, pdir, source="startup", autoarm_enabled=False)
    assert _INPROCESS_NOTICE_FRAGMENT in result.get("systemMessage", ""), (
        "step-0b in-process notice must still fire under autoarm-disabled"
    )
    ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert _ARM_DIRECTIVE_PHRASE not in ctx, (
        "G3 arm directive must be suppressed under autoarm-disabled"
    )


def test_step0b_notice_and_arm_coexist_when_autoarm_enabled(tmp_path):
    """Coexistence/recovery: with the gate True, BOTH the step-0b notice and
    the G3 arm directive fire — confirming the gate toggles the ARM path
    ONLY; the step-0b notice is independent."""
    home = tmp_path / "home"; home.mkdir()
    sid = "abcdef01-step0b-on"
    pdir = "/tmp/pi-step0b-on"
    team = "pact-abcdef01"
    _stage_pact_session(home, team, sid, pdir)
    _stage_active_task(home, team)
    result = _run_session_init(home, sid, pdir, source="startup", autoarm_enabled=True)
    assert _INPROCESS_NOTICE_FRAGMENT in result.get("systemMessage", "")
    ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert _ARM_DIRECTIVE_PHRASE in ctx


def test_step0b_notice_suppressed_when_managed_teammate_mode_tmux(tmp_path):
    """Managed-precedence pin (suppression direction): the enterprise
    managed-settings source — source[0], the HIGHEST-precedence layer — setting
    teammateMode="tmux" SUPPRESSES the step-0b in-process notice, even though no
    lower source defines the key. The rest of the step-0b suite only asserts the
    notice FIRES (the absence/auto direction); this pins the opposite direction
    AND that _managed_settings_path is actually read at top precedence.

    Hermetic by construction: the managed source is fixture-controlled under the
    isolated HOME via the runner_src override (#867) — the assertion depends ONLY
    on the injected teammateMode, never on the host's real managed-settings."""
    home = tmp_path / "home"; home.mkdir()
    sid = "abcdef01-managed-tmux"
    pdir = "/tmp/pi-managed-tmux"
    team = "pact-abcdef01"
    _stage_pact_session(home, team, sid, pdir)
    _stage_active_task(home, team)
    result = _run_session_init(
        home, sid, pdir, source="startup", managed_teammate_mode="tmux",
    )
    assert _INPROCESS_NOTICE_FRAGMENT not in result.get("systemMessage", ""), (
        "managed teammateMode=tmux (top-precedence source) must suppress the "
        "step-0b in-process notice"
    )
