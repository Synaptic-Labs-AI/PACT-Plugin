"""TEST-phase empirical ACCEPTANCE (P0 MERGE GATE) for the Family E relocation
(#806 Item 3): does a separate-process (tmux/iterm2) teammate, booted under the
branch code, actually RECEIVE the marker-free peer body in its own-process
SessionStart additionalContext?

WHY THIS FILE IS SEPARATE FROM THE UNIT SUITE
---------------------------------------------
test_session_init_teammate_peer_inject.py (the unit suite) STUBS
``get_peer_context`` -> sentinel and ``resolve_lead_team_by_pane`` and patches
out every heavy collaborator. That isolates the FORK DECISION + the resolver
wiring but, by design, never exercises:
  * the real ``from shared.peer_context import ...`` imports resolving in a
    fresh interpreter,
  * the REAL pane-id resolver scanning real team-config files off disk,
  * a real team-config read producing a real peer list, or
  * the real session_init assembly composing that body into additionalContext.
Those together are the "composition / import / config / resolution" gap. This
file closes it by running the REAL branch session_init.py as a SUBPROCESS
(exactly how Claude Code invokes the hook: a fresh ``python session_init.py``
process with a JSON stdin frame), with a REAL team config on disk + a real pane
env, then asserting the REAL additionalContext output.

REAL RESOLUTION, NOT A TAUTOLOGY (O1 remediation)
-------------------------------------------------
The PRIOR version of this file placed the team config at
``generate_team_name(session_id)`` — the frame's OWN-session-derived name. That
was the exact tautology the O1 blocker exploited: the (buggy) teammate-branch
resolved team_name = generate_team_name(input_data), so the config was always
found AT the derived name regardless of whether real resolution worked. It
proved nothing about production resolution.

This version proves PRODUCTION resolution. The team config lives at ``_LEAD_TEAM``
(``pact-realfound``), which is DELIBERATELY DIFFERENT from
``generate_team_name(_SESSION_ID)`` (``pact-aabb1122``). The teammate is given a
pane env (``ITERM_SESSION_ID``) whose UUID matches its own member's
``tmuxPaneId`` in that config. So the peer body is emitted ONLY if
``resolve_lead_team_by_pane`` actually finds the lead's team by pane-id match —
if resolution failed, the generate_team_name fallback would look at
``pact-aabb1122`` (NO config there) and emit NOTHING. The fallback rows below
(no pane env / non-matching pane -> no body) are the built-in non-vacuity proof:
without real resolution, the teammate rows go dark.

THE 3-LEG COMPOSITION (dual-mode Consequence-3 — empirically grounded, not
synthetic-assumed — WITHOUT installing unmerged code into a live session):
  * LEG 1 (sweep #3.5 target a, 7eac1047): a real separate-process teammate's
    OWN SessionStart stdin carries ``agent_type`` -> classify == "teammate".
  * LEG 2 (sweep #3.5 target b / probe1, 7eac1047): session_init's primary-hook
    additionalContext is DELIVERED into a real teammate's LLM transcript,
    content-agnostically.
  * LEG 3 (THIS FILE): the REAL branch session_init.py RESOLVES the lead's team
    by pane-id match and EMITS the marker-free peer body for a teammate frame
    (and keeps the orchestrator block for lead/unknown frames).
LEG1 then LEG2 then LEG3 ==> the marker-free peer body reaches a real teammate.

PASS BAR = DELIVERY, transcript-sufficient (team-lead ruling, Task #18): the fix
is INFORMATIONAL peer-context — it needs DELIVERY, not COMPLIANCE.

RESIDUAL GAP (honest disclosure):
  * LEG 3 runs in the worktree's import env, not the installed cache's — LOW
    risk (the ``from shared.X import Y`` pattern is already cache-proven).
  * The resolver is exercised here under the iTerm2 pane-env family
    (ITERM_SESSION_ID). The literal-tmux ``$TMUX_PANE`` family is symmetric but
    unverified from this iTerm2 session; the resolver reads both + is fail-safe
    (miss -> generate_team_name fallback -> no-op = pre-fix dormancy = no
    regression), so an unconfirmed literal-tmux ships safely. #885
    self-registration (match-by-session_id) is the backend-agnostic superseder.

HERMETICITY: every run is HOME-isolated to a pytest tmp_path; the teammate frame
is not a lead, so the is_lead-gated disk writes never fire and nothing is
written outside tmp.

NON-VACUITY (empirically counter-tested): reverting the O1 resolver wiring
(teammate-branch -> generate_team_name only) and re-running this file yields
{6 failed, 6 passed} — the 6 TestTeammateReceivesPeerBodyViaRealResolution rows
FAIL (no resolver -> derived fallback name -> no config there -> no body), while
the 2 fallback rows + 3 lead/unknown rows + 1 fail-safe row stay green. That
discriminating cardinality proves these assertions are coupled to REAL
pane-resolution, not green-by-construction. The TestFallbackGoesDarkWithout-
Resolution rows exercise the same no-resolution path in-tree (no revert needed).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SESSION_INIT = _PLUGIN_ROOT / "hooks" / "session_init.py"

_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"
# generate_team_name(session_id) == "pact-" + session_id[:8]. The FALLBACK looks
# here; we deliberately DO NOT put the config here, so a body can only come from
# real pane-resolution finding the DISTINCT lead team below.
_DERIVED_FALLBACK_NAME = "pact-" + _SESSION_ID[:8]  # pact-aabb1122
_LEAD_TEAM = "pact-realfound"  # the lead's actual team — only pane-match finds it
assert _LEAD_TEAM != _DERIVED_FALLBACK_NAME

_PANE_UUID = "F26F1088-AA28-4D03-AE9B-0D12EE62034E"
_OWN_NAME = "devops-probe"  # this teammate's member name (drives exact-name self-exclusion)

# Own member carries the matching pane id; a SAME-agentType sibling lets us prove
# EXACT-name self-exclusion (the under-exposure fix) — the sibling is RETAINED.
_MEMBERS = [
    {"name": _OWN_NAME, "agentType": "pact-devops-engineer", "tmuxPaneId": _PANE_UUID},
    {"name": "devops-sibling", "agentType": "pact-devops-engineer", "tmuxPaneId": "SIBLING-GUID"},
    {"name": "architect", "agentType": "pact-architect", "tmuxPaneId": "ARCH-GUID"},
    {"name": "frontend-coder", "agentType": "pact-frontend-coder", "tmuxPaneId": "FE-GUID"},
]

_ORCH_MARKER = "YOUR PACT ROLE: orchestrator"
_TEAMMATE_MARKER = "YOUR PACT ROLE: teammate"
_PEER_LIST_PREFIX = "Active teammates on your team"


def _run_real_session_init(
    frame, tmp_path, *, write_team=True, pane_uuid=_PANE_UUID, own_pane_in_config=_PANE_UUID
):
    """Invoke the REAL branch session_init.py as a subprocess (no stubs) with a
    HOME-isolated tmp tree. The team config (if written) lives at _LEAD_TEAM
    (DISTINCT from the derived fallback name) with the own member's tmuxPaneId =
    ``own_pane_in_config``; the process env carries ITERM_SESSION_ID = the
    ``pane_uuid`` (or no pane env when pane_uuid is None). Returns
    (additionalContext, systemMessage)."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir(parents=True)
    if write_team:
        team_dir = home / ".claude" / "teams" / _LEAD_TEAM
        team_dir.mkdir(parents=True)
        members = [dict(m) for m in _MEMBERS]
        members[0]["tmuxPaneId"] = own_pane_in_config  # may be set non-matching
        (team_dir / "config.json").write_text(
            json.dumps({"members": members}), encoding="utf-8"
        )
    else:
        (home / ".claude" / "teams").mkdir(parents=True)

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env["CLAUDE_PLUGIN_ROOT"] = str(_PLUGIN_ROOT)
    # Control the pane env deterministically (don't inherit the runner's pane).
    for var in ("ITERM_SESSION_ID", "TERM_SESSION_ID", "TMUX_PANE"):
        env.pop(var, None)
    if pane_uuid:
        env["ITERM_SESSION_ID"] = f"w0t0p0:{pane_uuid}"

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


class TestTeammateReceivesPeerBodyViaRealResolution:
    """LEG 3 — the acceptance: a real teammate frame, run through the real
    session_init.py, RESOLVES the lead team by pane-id match and EMITS the
    marker-free peer body. The config is at a DISTINCT name, so this passes ONLY
    via real resolution (not the tautological derived-name alignment)."""

    def test_teammate_emits_marker_free_peer_body_via_pane_resolution(self, tmp_path):
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-devops-engineer"), tmp_path
        )
        assert _PEER_LIST_PREFIX in additional
        # Charter cross-ref + the two trailing reminders compose in.
        assert "pact-communication-charter.md" in additional
        assert "TEACHBACK TIMING" in additional
        assert "COMPLETION AUTHORITY" in additional
        # Mis-roling fix: a teammate must NOT be told it is the orchestrator,
        # and session_init does not re-claim the teammate role marker.
        assert _ORCH_MARKER not in additional
        assert _TEAMMATE_MARKER not in additional

    def test_exact_name_self_exclusion_retains_same_type_sibling(self, tmp_path):
        """The resolver recovers the EXACT member name (devops-probe), so
        self-exclusion is by name — a SAME-agentType sibling (devops-sibling) is
        RETAINED (the under-exposure fix). The agentType fallback would have
        dropped both pact-devops-engineer members."""
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-devops-engineer"), tmp_path
        )
        assert "devops-sibling" in additional  # same-type peer retained
        assert "architect" in additional
        assert "frontend-coder" in additional
        assert _OWN_NAME not in additional  # self excluded by exact name

    @pytest.mark.parametrize(
        "agent_type",
        ["pact-architect", "pact-frontend-coder", "pact-test-engineer", "pact-secretary"],
    )
    def test_resolution_is_pane_driven_not_agent_type(self, agent_type, tmp_path):
        """Resolution keys on the PANE (matching the own member), independent of
        the frame's agent_type — so any teammate-typed frame at this pane gets
        the body."""
        additional, _ = _run_real_session_init(_teammate_frame(agent_type), tmp_path)
        assert _PEER_LIST_PREFIX in additional
        assert _ORCH_MARKER not in additional
        assert _TEAMMATE_MARKER not in additional


class TestFallbackGoesDarkWithoutResolution:
    """Built-in non-vacuity: when real resolution does NOT find the lead team,
    the generate_team_name fallback looks at the derived name (no config there)
    and the teammate row goes DARK — no body, and crucially NO orchestrator
    mis-roling. Proves the body above is driven by real resolution."""

    def test_no_pane_env_falls_back_to_no_body(self, tmp_path):
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-devops-engineer"), tmp_path, pane_uuid=None
        )
        assert _PEER_LIST_PREFIX not in additional
        assert _ORCH_MARKER not in additional

    def test_non_matching_pane_falls_back_to_no_body(self, tmp_path):
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-devops-engineer"), tmp_path,
            own_pane_in_config="A-DIFFERENT-NON-MATCHING-GUID",
        )
        assert _PEER_LIST_PREFIX not in additional
        assert _ORCH_MARKER not in additional


class TestLeadAndUnknownKeepOrchestratorBlock:
    """LEG 3 — real-composition regression: lead and unknown/plain frames take
    the else-branch (no resolver call), keep the orchestrator-directive block,
    and get NO peer body."""

    @pytest.mark.parametrize(
        "agent_type", ["PACT:pact-orchestrator", "pact-orchestrator"]
    )
    def test_lead_frame_emits_orchestrator_block_not_peer_body(self, agent_type, tmp_path):
        additional, _ = _run_real_session_init(_teammate_frame(agent_type), tmp_path)
        assert _ORCH_MARKER in additional
        assert _PEER_LIST_PREFIX not in additional

    def test_plain_unknown_frame_emits_orchestrator_block_not_peer_body(self, tmp_path):
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
    """LEG 3 — fail-safe: a teammate frame whose lead team has NO config gets
    NEITHER a peer body NOR the orchestrator block, and never raises (branch
    taken on classify; resolver None; fallback None)."""

    def test_teammate_no_team_config_injects_nothing(self, tmp_path):
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-devops-engineer"), tmp_path, write_team=False
        )
        assert _PEER_LIST_PREFIX not in additional
        assert _ORCH_MARKER not in additional
