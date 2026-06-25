"""Live deny-honor probe for the #865 dispatch-variety enforcement gate.

WHY THIS FILE EXISTS — converting SOURCE-PROOF to RUNTIME-PROOF.
The #865 deny arm (handoff_ordering_gate.py, PACT_DISPATCH_VARIETY_MODE=deny)
emits a PreToolUse `permissionDecision:"deny"` + exit 2 on the terminal
dispatch-wiring write of an unstamped Task B. That deny path is SOURCE-PROVEN
but, until this file, empirically UN-EXERCISED: no PACT hook had ever fired a
TaskUpdate-matcher deny. The in-process unit test
(`test_handoff_ordering_gate.py::TestDispatchVarietyEnvKnobModes::
test_deny_mode_permission_decision_exit_two`) proves the gate FUNCTION returns
the deny verdict, but it monkeypatches the in-memory `DISPATCH_VARIETY_MODE`
constant and patches the context-path — it never crosses a real process / real
env / real disk boundary.

WHAT THIS PROBE ADDS (the closest-to-live, non-mocked seam-integration safely
achievable in-harness, per architecture spec §3.6):
- a REAL subprocess (`subprocess.run([sys.executable, hook])`) — real module
  load, not an in-process import with a pre-warmed `DISPATCH_VARIETY_MODE`;
- the REAL env knob `PACT_DISPATCH_VARIETY_MODE=deny`, read at module import
  from `os.environ` (NOT a `monkeypatch.setattr` of the resolved constant);
- a REAL isolated HOME holding a real on-disk session-context.json, a real
  team config.json (with leadSessionId for the both-modes discriminator), and a
  real unstamped Task-B JSON — the FULL `get_team_name` -> `read_task_json` ->
  `resolve_variety_total` resolution chain, UNSTUBBED.
The probe asserts the exact deny CONTRACT the platform consumes to block a tool
call: exit 2 AND `hookSpecificOutput.permissionDecision == "deny"` AND
`hookEventName == "PreToolUse"` (the fields the platform's PreToolUse executor
reads before `tool.call()`).

DOCUMENTED RESIDUAL (an ACCEPTED by-design boundary, not a skipped gap).
This probe proves the gate EMITS the platform-honored deny contract through the
real environment seam. It does NOT drive the actual Claude Code binary's
PreToolUse executor to observe the TaskUpdate being blocked — doing that would
require wiring a scratch hooks.json/settings into a real binary subprocess,
which edges into live-session-config territory the TEST phase deliberately does
not touch. That final "the platform honors the contract" leg stays
SOURCE-PROVEN (the platform deny branch returns before `tool.call()` with no
`tool.name` carve-out — tool-agnostic). The risk of the residual is bounded by
design: `deny` is OPT-IN (default ships `warn`), and an un-honored deny would
degrade to warn-like (the call proceeds, the advisory is the floor) — so this
is a runtime-CONFIDENCE gate, not a merge blocker.

NON-MOCKED SEAM + NON-VACUITY. The task JSON is read over the real
`read_task_json` disk seam (no stub). `test_deny_does_not_fire_when_task_b_is
_stamped` is the non-vacuity lever: it shares the identical frame/env and flips
ONLY the on-disk task's variety stamp, so a regression that denied on the
composite signature WITHOUT reading the stamp (or a stubbed seam that always
reads "unstamped") would make that test RED. A vacuous always-deny gate cannot
pass both the deny case and the stamped-silent case.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).parent.parent / "hooks" / "handoff_ordering_gate.py"

# Frame agent_type spellings — is_lead reads top-level agent_type directly.
_LEAD = "PACT:pact-orchestrator"
_TEAMMATE = "pact-backend-coder"

_SID = "deadbeef-4242-4242-4242-deadbeef4242"
_TEAM = "scratch-deny-probe-team"
_TASK_ID = "42"

# The production-real owner shape: a BARE specialist name that the team config
# maps to a registered pact agentType. The corrected gate predicate
# (is_pact_specialist_owner) resolves _OWNER → member → _AGENT_TYPE →
# registry(agents/pact-*.md). Real task owners are bare names; `pact-*` is the
# team-config agentType, NEVER the owner — feeding `pact-backend-coder` as the
# owner (the pre-fix probe shape) is a production-impossible frame that the
# corrected predicate (correctly) no longer matches.
_OWNER = "backend-coder"
_AGENT_TYPE = "pact-backend-coder"


def _wiring_frame(*, agent_type=_LEAD, task_id=_TASK_ID):
    """A terminal dispatch-wiring PreToolUse(TaskUpdate) frame: a BARE
    specialist owner (resolving to a pact agentType via team config) AND
    addBlockedBy non-empty in the SAME tool_input (the composite signature).
    agent_type selects the lead/teammate (is_lead) branch."""
    return json.dumps({
        "tool_name": "TaskUpdate",
        "session_id": _SID,
        "agent_type": agent_type,
        "tool_input": {
            "taskId": task_id,
            "owner": _OWNER,
            "addBlockedBy": ["A"],
        },
    })


@pytest.fixture
def isolated_session(tmp_path):
    """A fully isolated on-disk session: real HOME with session-context.json,
    team config.json (leadSessionId == _SID → in-process / is-lead topology),
    a seeded specialist registry (plugin_root/agents/pact-*.md), and an
    UNSTAMPED Task-B JSON owned by the BARE specialist name. Returns
    (env, home, task_path).

    No monkeypatch — every value is read by the subprocess from real env +
    real disk, exactly as a live hook process would. The seeding mirrors the
    corrected predicate's resolution chain: the gate's is_pact_specialist_owner
    resolves the bare owner → team-config member → agentType → registry. So the
    fixture MUST register (a) the member name→agentType mapping in the team
    config AND (b) an agents/pact-*.md file for that agentType, with the
    session-context plugin_root pointing at the seeded plugin root (the
    subprocess resolves the registry via get_plugin_root() → the context-file
    plugin_root field, with a CLAUDE_PLUGIN_ROOT env fallback). plugin_root=""
    (the pre-fix value) yields an EMPTY registry → is_pact_specialist_owner
    returns False → the gate never fires (the vacuity this de-fossilize closes).
    """
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    slug = "proj"  # CLAUDE_PROJECT_DIR basename

    # Seed the specialist registry: one agents/pact-*.md for the agentType the
    # bare owner resolves to. Without this, is_registered_pact_specialist sees
    # an empty registry and the corrected gate cannot identify the dispatch.
    plugin_root = tmp_path / "plugin"
    (plugin_root / "agents").mkdir(parents=True)
    (plugin_root / "agents" / f"{_AGENT_TYPE}.md").write_text(
        f"# {_AGENT_TYPE}\n", encoding="utf-8",
    )

    ctx_dir = home / ".claude" / "pact-sessions" / slug / _SID
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": _TEAM,
            "session_id": _SID,
            "project_dir": str(project),
            "plugin_root": str(plugin_root),  # registry resolves against this
            "started_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )

    team_dir = home / ".claude" / "teams" / _TEAM
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(
        json.dumps({
            "team_name": _TEAM,
            "leadSessionId": _SID,  # == session_id → in-process topology
            "members": [
                # BARE name → pact agentType: the resolution the gate performs.
                {"name": _OWNER, "agentType": _AGENT_TYPE},
            ],
        }),
        encoding="utf-8",
    )

    tasks_dir = home / ".claude" / "tasks" / _TEAM
    tasks_dir.mkdir(parents=True)
    task_path = tasks_dir / f"{_TASK_ID}.json"
    task_path.write_text(
        json.dumps({
            "id": _TASK_ID,
            "subject": "impl foo",
            "owner": _OWNER,  # BARE name (matches the team-config member)
            "metadata": {},  # UNSTAMPED — the missing-stamp gap
        }),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("CLAUDE_CONFIG_DIR", None)  # force the HOME/.claude resolution
    env["CLAUDE_PROJECT_DIR"] = str(project)
    # Belt-and-suspenders: the context-file plugin_root wins, but the env
    # fallback keeps the registry resolvable if that read ever regresses.
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    return env, home, task_path


def _run_hook(env, frame, *, mode):
    """Fire the REAL gate as a subprocess with PACT_DISPATCH_VARIETY_MODE=mode
    read from the environment at module import. Returns (returncode, parsed
    stdout JSON)."""
    run_env = dict(env)
    run_env["PACT_DISPATCH_VARIETY_MODE"] = mode
    result = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=frame,
        capture_output=True,
        text=True,
        env=run_env,
        cwd=str(Path(env["HOME"])),
        timeout=20,
    )
    out = result.stdout.strip()
    parsed = json.loads(out) if out else {}
    return result.returncode, parsed, result.stderr


def test_hook_module_exists():
    assert _HOOK.exists(), f"gate hook missing at {_HOOK}"


# =============================================================================
# THE LIVE DENY-HONOR PROBE — deny mode emits the platform-honored contract.
# =============================================================================
def test_deny_mode_emits_platform_honored_deny_contract(isolated_session):
    """deny mode + lead frame + unstamped Task B → the gate emits, through a
    REAL subprocess / REAL env knob / REAL disk seam, the exact contract the
    platform's PreToolUse executor reads to BLOCK the tool call: exit 2 +
    permissionDecision:"deny" + hookEventName:"PreToolUse"."""
    env, _home, _task = isolated_session
    code, out, stderr = _run_hook(env, _wiring_frame(), mode="deny")

    assert code == 2, (
        f"deny mode must exit 2 (the platform-blocking code); "
        f"got {code}. stderr={stderr!r}"
    )
    hso = out.get("hookSpecificOutput", {})
    assert hso.get("permissionDecision") == "deny", (
        f"deny mode must emit permissionDecision:deny; got {out!r}"
    )
    assert hso.get("hookEventName") == "PreToolUse", (
        f"the deny contract requires hookEventName=PreToolUse; got {out!r}"
    )
    # The actionable reason reaches the dispatcher (the verbatim re-stamp recipe).
    assert "metadata.variety" in hso.get("permissionDecisionReason", "")


def test_warn_mode_emits_advisory_not_deny(isolated_session):
    """Default (warn) on the identical frame/disk → exit 0 + additionalContext
    advisory, NEVER a permissionDecision. Proves the env knob actually selects
    the mode at module import (not a hardcoded deny)."""
    env, _home, _task = isolated_session
    code, out, _stderr = _run_hook(env, _wiring_frame(), mode="warn")

    assert code == 0, "warn mode must exit 0 (never blocks)"
    hso = out.get("hookSpecificOutput", {})
    assert "additionalContext" in hso
    assert "permissionDecision" not in hso, (
        "warn mode must NEVER emit a deny verdict"
    )


def test_shadow_mode_suppresses_through_real_env(isolated_session):
    """shadow mode on the identical frame/disk → suppressOutput, no deny,
    no advisory (journal-only calibration posture)."""
    env, _home, _task = isolated_session
    code, out, _stderr = _run_hook(env, _wiring_frame(), mode="shadow")

    assert code == 0
    assert out == {"suppressOutput": True}, f"shadow must suppress; got {out!r}"


# =============================================================================
# BOTH-MODES — is_lead structural discriminator through the real frame.
# In-process (lead frame) denies; a teammate frame on identical disk does not.
# =============================================================================
def test_deny_does_not_fire_on_teammate_frame(isolated_session):
    """DUAL-MODE: deny mode + TEAMMATE agent_type (is_lead False) on the
    identical unstamped disk → no deny (exit 0, suppressOutput). The gate is
    lead-frame-only; a teammate observing the same wiring write must not block
    it. Paired with the lead-frame deny above, this exercises BOTH topology
    branches of the is_lead discriminator over the real frame."""
    env, _home, _task = isolated_session
    code, out, _stderr = _run_hook(
        env, _wiring_frame(agent_type=_TEAMMATE), mode="deny"
    )

    assert code == 0, "a teammate frame must never deny (is_lead False)"
    assert out == {"suppressOutput": True}, (
        f"teammate frame in deny mode must suppress; got {out!r}"
    )


# =============================================================================
# NON-VACUITY LEVER — the deny is gated on the STRUCTURAL read, not the
# composite signature alone. Flip ONLY the on-disk stamp; the deny must vanish.
# A vacuous always-deny (or a stubbed seam that always reads "unstamped") makes
# this RED while the deny case above stays green — they cannot both pass.
# =============================================================================
def test_deny_does_not_fire_when_task_b_is_stamped(isolated_session):
    """deny mode + lead frame + identical composite signature, but the Task B
    on disk now carries a resolvable variety.total → NO deny (exit 0). This is
    the non-vacuity proof: the deny reads the real stamp over the unstubbed
    disk seam and silences when it resolves."""
    env, _home, task_path = isolated_session
    # Flip ONLY the on-disk stamp — same frame, same env, same everything else.
    task_path.write_text(
        json.dumps({
            "id": _TASK_ID,
            "subject": "impl foo",
            "owner": _OWNER,  # SAME bare owner as the deny case — so the
            # silence is attributable to the stamp resolving, NOT to the owner
            # failing to resolve (which would be a wrong-reason green).
            "metadata": {"variety": {"total": 12}},
        }),
        encoding="utf-8",
    )
    code, out, _stderr = _run_hook(env, _wiring_frame(), mode="deny")

    assert code == 0, (
        "a STAMPED Task B must never be denied even in deny mode — the deny is "
        "gated on the structural read, not the composite signature"
    )
    assert out == {"suppressOutput": True}, (
        f"stamped Task B in deny mode must suppress; got {out!r}"
    )


def test_deny_does_not_fire_at_taskcreate_no_misfire(isolated_session):
    """FIRST-OBSERVABLE-WRITE no-misfire, proven live: a TaskCreate frame (no
    owner+addBlockedBy composite) in deny mode must NOT block — enforcing at
    the initial create would be unsatisfiable-by-construction. Owner is empty
    at TaskCreate (the later wiring TaskUpdate sets it), so the composite
    signature does not match."""
    env, _home, _task = isolated_session
    create_frame = json.dumps({
        "tool_name": "TaskCreate",
        "session_id": _SID,
        "agent_type": _LEAD,
        "tool_input": {
            "subject": "impl foo",
            "owner": _OWNER,
            # NO addBlockedBy → not the terminal wiring write
        },
    })
    code, out, _stderr = _run_hook(env, create_frame, mode="deny")

    assert code == 0, "TaskCreate must never deny (no-misfire invariant)"
    assert "permissionDecision" not in out.get("hookSpecificOutput", {})
