"""TEST-phase empirical ACCEPTANCE (P0 MERGE GATE) for the Family E relocation
(#806 Item 3): does a separate-process (tmux/iterm2) teammate, booted under the
branch code, actually RECEIVE the marker-free peer body in its own-process
SessionStart additionalContext?

WHY THIS FILE IS SEPARATE FROM THE UNIT SUITE
---------------------------------------------
test_session_init_teammate_peer_inject.py (the commit-4 unit suite) STUBS
``get_peer_context`` -> sentinel and patches out every heavy collaborator
(setup_plugin_symlinks, build_context_cache, ...). That isolates the FORK
DECISION but, by design, never exercises:
  * the real ``from shared.peer_context import get_peer_context`` import
    resolving in a fresh interpreter,
  * a real team-config read off disk producing a real peer list, or
  * the real session_init assembly composing that body into additionalContext.
Those three together are the "composition / import / config" gap. This file
closes it by running the REAL branch session_init.py as a SUBPROCESS (exactly
how Claude Code invokes the hook: a fresh ``python session_init.py`` process
with a JSON stdin frame), with a REAL team config on disk and a teammate-shaped
stdin, then asserting the REAL additionalContext output.

THE 3-LEG COMPOSITION (satisfies dual-mode Consequence-3 — empirically grounded,
not synthetic-assumed — WITHOUT installing unmerged code into a live session):
  * LEG 1 (sweep #3.5 target a, pact-memory 7eac1047): a real separate-process
    teammate's OWN SessionStart stdin carries ``agent_type`` ->
    classify_session_role == "teammate".  [empirical, done in PREPARE]
  * LEG 2 (sweep #3.5 target b / probe1, 7eac1047): session_init's primary-hook
    additionalContext is DELIVERED into a real teammate's LLM transcript
    (hook_additional_context attachment). The platform injects whatever string
    session_init emits, regardless of content -> delivery is content-agnostic.
    [empirical, done in PREPARE]
  * LEG 3 (THIS FILE): the REAL branch session_init.py EMITS the marker-free
    peer body in its additionalContext for a teammate frame (and keeps the
    orchestrator block for lead/unknown frames).  [empirical, here]
LEG1 then LEG2 then LEG3 ==> the marker-free peer body reaches a real teammate.

PASS BAR = DELIVERY, transcript-sufficient (team-lead ruling, Task #18). The fix
is INFORMATIONAL peer-context: it needs DELIVERY, not COMPLIANCE, so no live
SendMessage-back echo is required. LEG 3's assertion that the real code EMITS the
body, composed with LEG 2's proven delivery, is the delivery proof.

RESIDUAL GAP (honest disclosure): LEG 3 runs in the worktree's import env, not
the installed cache's. LOW risk — the ``from shared.X import Y`` pattern is
already cache-proven (session_init already imports classify_session_role from
shared.pact_context in the live cache; the new shared/peer_context.py sits in
the same shared/ dir). If a concrete cache-load import difference ever surfaces,
escalate to the full live-cache tmux-flip capture (devops revert discipline).

HERMETICITY: every run is HOME-isolated to a pytest tmp_path; the teammate frame
is not a lead, so the is_lead-gated disk writes (build_context_cache persist,
journal anchor) never fire and nothing is written outside tmp.

NON-VACUITY (counter-test-by-revert, source-only): restoring session_init.py to
the pre-feat parent 822d47f1 (removes the teammate-branch) and re-running this
file yields {7 failed, 3 passed} — the 6 teammate-receive rows + the 1 fail-safe
row FAIL (teammate frames fall back to the orchestrator else-branch), while the
2 lead rows + 1 plain/unknown row still PASS (they expect the orchestrator block
regardless). That discriminating cardinality proves these assertions are coupled
to the relocation, not green-by-construction.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# tests/ lives under pact-plugin/, so parents[1] is the plugin root (which holds
# hooks/ and the agents/ registry the unknown-role notice checks against).
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SESSION_INIT = _PLUGIN_ROOT / "hooks" / "session_init.py"

# generate_team_name() == "pact-" + session_id[:8]; the team config must live at
# the path that derived name resolves to.
_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"
_TEAM_NAME = "pact-" + _SESSION_ID[:8]

_MEMBERS = [
    {"name": "architect", "agentType": "pact-architect"},
    {"name": "frontend-coder", "agentType": "pact-frontend-coder"},
    {"name": "backend-coder", "agentType": "pact-backend-coder"},
]

_ORCH_MARKER = "YOUR PACT ROLE: orchestrator"
_TEAMMATE_MARKER = "YOUR PACT ROLE: teammate"
_PEER_LIST_PREFIX = "Active teammates on your team"


def _run_real_session_init(frame: dict, tmp_path, *, write_team: bool = True):
    """Invoke the REAL branch session_init.py as a subprocess (no stubs) with a
    HOME-isolated tmp tree + (optionally) a real team config; return
    (additionalContext, systemMessage)."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    team_dir = home / ".claude" / "teams" / _TEAM_NAME
    team_dir.mkdir(parents=True)
    project.mkdir(parents=True)
    if write_team:
        (team_dir / "config.json").write_text(
            json.dumps({"members": _MEMBERS}), encoding="utf-8"
        )

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    # Real plugin root so the live agents/pact-*.md registry resolves (otherwise
    # the #878 unknown-role notice false-fires on an empty registry — a systemMessage
    # artifact that does not touch additionalContext, but we keep the run realistic).
    env["CLAUDE_PLUGIN_ROOT"] = str(_PLUGIN_ROOT)

    proc = subprocess.run(
        [sys.executable, str(_SESSION_INIT)],
        input=json.dumps(frame),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"session_init.py exited {proc.returncode}; stderr=\n{proc.stderr}"
    )
    out = json.loads(proc.stdout)
    additional = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    return additional, out.get("systemMessage", "")


def _teammate_frame(agent_type: str) -> dict:
    """A separate-process teammate SessionStart frame (keys mirror sweep #3.5a's
    captured shape: agent_type present, agent_name ABSENT under tmux)."""
    return {
        "session_id": _SESSION_ID,
        "source": "startup",
        "agent_type": agent_type,
        "cwd": "/tmp/pact-acceptance",
        "hook_event_name": "SessionStart",
        "model": "claude",
    }


class TestTeammateReceivesMarkerFreePeerBody:
    """LEG 3 — the acceptance: a real teammate frame, run through the real
    session_init.py, EMITS the marker-free peer body end-to-end."""

    def test_teammate_frame_emits_full_marker_free_peer_body(self, tmp_path):
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-backend-coder"), tmp_path
        )
        # Real, agentType-filtered peer list (under tmux there is no agent_name,
        # so the builder self-excludes by agentType: backend-coder drops out).
        assert f"{_PEER_LIST_PREFIX}: architect, frontend-coder" in additional
        # Charter cross-ref + the two trailing reminders compose in.
        assert "pact-communication-charter.md" in additional
        assert "TEACHBACK TIMING" in additional
        assert "COMPLETION AUTHORITY" in additional
        # The mis-roling fix: a teammate must NOT be told it is the orchestrator.
        assert _ORCH_MARKER not in additional
        # Marker-free: session_init does not re-claim the teammate role marker.
        assert _TEAMMATE_MARKER not in additional

    @pytest.mark.parametrize(
        "agent_type",
        ["pact-architect", "pact-frontend-coder", "pact-devops-engineer",
         "pact-test-engineer", "pact-secretary"],
    )
    def test_various_specialist_teammates_all_receive_peer_body(
        self, agent_type, tmp_path
    ):
        additional, _ = _run_real_session_init(_teammate_frame(agent_type), tmp_path)
        assert _PEER_LIST_PREFIX in additional
        assert _ORCH_MARKER not in additional
        assert _TEAMMATE_MARKER not in additional


class TestLeadAndUnknownKeepOrchestratorBlock:
    """LEG 3 — real-composition regression: lead and unknown/plain frames keep
    the orchestrator-directive block and get NO peer body (the 3-way fail-safe
    gate, end-to-end)."""

    @pytest.mark.parametrize(
        "agent_type", ["PACT:pact-orchestrator", "pact-orchestrator"]
    )
    def test_lead_frame_emits_orchestrator_block_not_peer_body(
        self, agent_type, tmp_path
    ):
        additional, _ = _run_real_session_init(_teammate_frame(agent_type), tmp_path)
        assert _ORCH_MARKER in additional
        assert _PEER_LIST_PREFIX not in additional

    def test_plain_unknown_frame_emits_orchestrator_block_not_peer_body(self, tmp_path):
        # No agent_type -> classify "unknown" -> else-branch -> orchestrator
        # ladder unchanged (fail-safe: only a genuine teammate injects).
        frame = {
            "session_id": _SESSION_ID,
            "source": "startup",
            "cwd": "/tmp/pact-acceptance",
            "hook_event_name": "SessionStart",
        }
        additional, _ = _run_real_session_init(frame, tmp_path)
        assert _ORCH_MARKER in additional
        assert _PEER_LIST_PREFIX not in additional


class TestFailSafeRealComposition:
    """LEG 3 — fail-safe through real composition: a teammate frame whose team
    has no config gets NEITHER a peer body NOR the orchestrator block, and never
    raises (the branch is taken on classify, the builder returns None)."""

    def test_teammate_no_team_config_injects_nothing_keeps_no_orchestrator(
        self, tmp_path
    ):
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-backend-coder"), tmp_path, write_team=False
        )
        assert _PEER_LIST_PREFIX not in additional
        assert _ORCH_MARKER not in additional
