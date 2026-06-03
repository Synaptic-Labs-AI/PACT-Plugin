"""TEST-phase empirical ACCEPTANCE (P0 MERGE GATE) for the self-registration
team-resolution path: does a separate-process (tmux) teammate, booted under the
real branch code, actually RECEIVE the marker-free peer body in its own-process
SessionStart additionalContext — resolving the LEAD's team from the
self-registration registry by self-looking-up its OWN session_id?

WHY THIS FILE IS SEPARATE FROM THE UNIT SUITE
---------------------------------------------
test_session_init_teammate_peer_inject.py (the unit suite) STUBS
``get_peer_context`` -> sentinel and ``_registry_resolve`` and patches out every
heavy collaborator. That isolates the FORK DECISION + the resolver wiring but,
by design, never exercises:
  * the real ``from shared.X import ...`` imports resolving in a fresh
    interpreter,
  * the REAL registry resolver scanning a real ``.teammate-registry.jsonl`` off
    disk + validating against a real team config,
  * a real team-config read producing a real peer list, or
  * the real session_init assembly composing that body into additionalContext.
Those together are the "composition / import / config / resolution" gap. This
file closes it by running the REAL branch session_init.py as a SUBPROCESS
(exactly how Claude Code invokes the hook: a fresh ``python session_init.py``
process with a JSON stdin frame), with a REAL registry + team config on disk,
then asserting the REAL additionalContext output.

REAL RESOLUTION, NOT A TAUTOLOGY (carries the O1 remediation forward)
--------------------------------------------------------------------
The team config lives at ``_LEAD_TEAM`` (``pact-realfound``), DELIBERATELY
DIFFERENT from ``generate_team_name(_SESSION_ID)`` (``pact-aabb1122``). The
registry maps this teammate's OWN session_id -> ``<own-name>@_LEAD_TEAM``. So
the peer body is emitted ONLY if ``_registry_resolve`` actually self-looks-up the
own session_id, recovers the @team half (the lead's team), AND the name half
validates as a member of that team. If resolution failed, the generate_team_name
fallback would look at ``pact-aabb1122`` (NO config there) and emit NOTHING. The
fallback rows below (no registry entry / non-matching session_id -> no body) are
the built-in non-vacuity proof: without real resolution, the teammate rows go
dark.

THE 3-LEG COMPOSITION (dual-mode Consequence-3 — empirically grounded, not
synthetic-assumed — WITHOUT installing unmerged code into a live session):
  * LEG 1: a real separate-process teammate's OWN SessionStart stdin carries
    ``agent_type`` -> classify == "teammate".
  * LEG 2: session_init's primary-hook additionalContext is DELIVERED into a real
    teammate's LLM transcript, content-agnostically.
  * LEG 3 (THIS FILE): the REAL branch session_init.py RESOLVES the lead's team
    from the registry by own-session_id self-lookup and EMITS the marker-free
    peer body for a teammate frame (and keeps the orchestrator block for
    lead/unknown frames).
LEG1 then LEG2 then LEG3 ==> the marker-free peer body reaches a real teammate.

PASS BAR = DELIVERY, transcript-sufficient: the fix is INFORMATIONAL
peer-context — it needs DELIVERY, not COMPLIANCE.

RESIDUAL GAP (honest disclosure):
  * LEG 3 runs in the worktree's import env, not the installed cache's — LOW
    risk (the ``from shared.X import Y`` pattern is already cache-proven).
  * This file pre-populates the registry directly (write side-channel), proving
    the RESOLVE + composition path. The full register->resolve round-trip
    through the real ``session_registry.py register`` CLI under a real tmux
    teammate is the separate LEG-4 pre-merge smoke (the agent-def invocation).

HERMETICITY: every run is HOME-isolated to a pytest tmp_path; the teammate frame
is not a lead, so the is_lead-gated disk writes never fire and nothing is written
outside tmp.

NON-VACUITY: the TestFallbackGoesDarkWithoutResolution rows exercise the
no-resolution path in-tree (no registry entry / a non-matching session_id ->
derived fallback name -> no config there -> no body), discriminating these
assertions from green-by-construction. The teammate-body rows pass ONLY when the
registry self-lookup really resolves the DISTINCT lead team.
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
# real registry resolution finding the DISTINCT lead team below.
_DERIVED_FALLBACK_NAME = "pact-" + _SESSION_ID[:8]  # pact-aabb1122
_LEAD_TEAM = "pact-realfound"  # the lead's actual team — only registry self-lookup finds it
assert _LEAD_TEAM != _DERIVED_FALLBACK_NAME

_OWN_NAME = "devops-probe"  # this teammate's member name (drives exact-name self-exclusion)

# Own member + a SAME-agentType sibling to prove EXACT-name self-exclusion (the
# under-exposure fix) — the sibling is RETAINED.
_MEMBERS = [
    {"name": _OWN_NAME, "agentType": "pact-devops-engineer"},
    {"name": "devops-sibling", "agentType": "pact-devops-engineer"},
    {"name": "architect", "agentType": "pact-architect"},
    {"name": "frontend-coder", "agentType": "pact-frontend-coder"},
]

_ORCH_MARKER = "YOUR PACT ROLE: orchestrator"
_TEAMMATE_MARKER = "YOUR PACT ROLE: teammate"
_PEER_LIST_PREFIX = "Active teammates on your team"


def _run_real_session_init(
    frame, tmp_path, *, write_team=True, registry_session_id=_SESSION_ID,
    registry_value=None,
):
    """Invoke the REAL branch session_init.py as a subprocess (no stubs) with a
    HOME-isolated tmp tree. The team config (if written) lives at _LEAD_TEAM
    (DISTINCT from the derived fallback name). A registry line mapping
    ``registry_session_id`` -> ``registry_value`` (default
    ``<own-name>@_LEAD_TEAM``) is written to the global registry path so the real
    resolver can self-look-up. Pass ``registry_session_id`` != the frame's
    session_id to model a non-matching entry, or ``write_team=False`` for the
    no-config fail-safe. Returns (additionalContext, systemMessage)."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir(parents=True)
    if write_team:
        team_dir = home / ".claude" / "teams" / _LEAD_TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"members": [dict(m) for m in _MEMBERS]}), encoding="utf-8"
        )
    else:
        (home / ".claude" / "teams").mkdir(parents=True)

    # Pre-populate the registry (the resolve side-channel for this LEG). The
    # register->resolve round-trip via the CLI is the separate LEG-4 smoke.
    if registry_value is None:
        registry_value = f"{_OWN_NAME}@{_LEAD_TEAM}"
    if registry_session_id is not None:
        reg_dir = home / ".claude" / "pact-sessions"
        reg_dir.mkdir(parents=True, exist_ok=True)
        (reg_dir / ".teammate-registry.jsonl").write_text(
            json.dumps({"session_id": registry_session_id, "value": registry_value}) + "\n",
            encoding="utf-8",
        )

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["CLAUDE_PROJECT_DIR"] = str(project)
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
    """A separate-process teammate SessionStart frame (agent_type present,
    agent_name ABSENT under tmux — the structural tmux gap)."""
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
    session_init.py, RESOLVES the lead team from the registry by own-session_id
    self-lookup and EMITS the marker-free peer body. The config is at a DISTINCT
    name, so this passes ONLY via real resolution (not a tautological derived-name
    alignment)."""

    def test_teammate_emits_marker_free_peer_body_via_registry_resolution(self, tmp_path):
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
        """The registry value carries the EXACT member name (devops-probe), so
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
    def test_resolution_is_registry_driven_not_agent_type(self, agent_type, tmp_path):
        """Resolution keys on the registry self-lookup (by own session_id),
        independent of the frame's agent_type — so any teammate-typed frame at
        this session_id gets the body."""
        additional, _ = _run_real_session_init(_teammate_frame(agent_type), tmp_path)
        assert _PEER_LIST_PREFIX in additional
        assert _ORCH_MARKER not in additional
        assert _TEAMMATE_MARKER not in additional


class TestFallbackGoesDarkWithoutResolution:
    """Built-in non-vacuity: when real resolution does NOT find the lead team,
    the generate_team_name fallback looks at the derived name (no config there)
    and the teammate row goes DARK — no body, and crucially NO orchestrator
    mis-roling. Proves the body above is driven by real resolution."""

    def test_no_registry_entry_falls_back_to_no_body(self, tmp_path):
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-devops-engineer"), tmp_path, registry_session_id=None
        )
        assert _PEER_LIST_PREFIX not in additional
        assert _ORCH_MARKER not in additional

    def test_non_matching_session_id_falls_back_to_no_body(self, tmp_path):
        # A registry entry exists, but keyed on a DIFFERENT session_id → the
        # teammate's own-session_id self-lookup misses → fallback → dark.
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-devops-engineer"), tmp_path,
            registry_session_id="some-other-session-id-0000",
        )
        assert _PEER_LIST_PREFIX not in additional
        assert _ORCH_MARKER not in additional

    def test_name_not_in_team_members_falls_back_to_no_body(self, tmp_path):
        # The registry value self-supplies a name that is NOT a member of the
        # @team config → members[]-validation rejects on read → fallback → dark.
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-devops-engineer"), tmp_path,
            registry_value=f"mallory@{_LEAD_TEAM}",
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
    taken on classify; resolver rejects on members-validation miss; fallback
    None)."""

    def test_teammate_no_team_config_injects_nothing(self, tmp_path):
        additional, _ = _run_real_session_init(
            _teammate_frame("pact-devops-engineer"), tmp_path, write_team=False
        )
        assert _PEER_LIST_PREFIX not in additional
        assert _ORCH_MARKER not in additional
