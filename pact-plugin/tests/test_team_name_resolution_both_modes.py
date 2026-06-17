"""Standing BOTH-MODES merge gate for the platform `session-<id8>` team name.

The PACT plugin MUST function under either operator-chosen teammateMode — the
DUAL-MODE PERMANENT CONTRACT:

  * in-process : 1 process, the running session_id == the team's leadSessionId
  * tmux       : N processes, each teammate session_id != the leadSessionId

Both modes are first-class; neither is legacy. After the platform-team-name
adoption, the team name is the platform's `session-<first 8 of the LEAD's
session id>`, persisted as the single source of truth (SSOT) and used to
resolve `teams/session-<id8>/` (member roster) + `tasks/session-<id8>/`
(task store). The dispatch gate reads that SSOT via `get_team_name()` — NEVER
the platform-ignored `Agent(team_name=)` spawn arg, and NEVER a recomputation
from the acting frame's own session_id.

These tests pin two invariants as a STANDING merge gate (do NOT collapse to one
leg, and do NOT re-key the structural branch on a mode flag — there is none):

  1. CI TRIPWIRE — the resolver's output addresses the REAL on-disk platform
     team dir under the documented `session-<first8>` convention. A future
     platform rename (or a resolver refactor) fails LOUDLY here in CI rather
     than silently deadlocking bootstrap in prod.
  2. BOTH-MODES resolution — the gate resolves the LEAD's `session-<lead8>`
     store correctly whether the running frame's session_id == leadSessionId
     (in-process) or != leadSessionId (tmux). The branch is keyed STRUCTURALLY
     on the session_id-vs-leadSessionId topology, via the SSOT.

Template: tests/test_task_claim_gate.py T1 legs (the canonical both-modes shape).

VERIFICATION MATRIX — gate-deletion non-vacuity (counter-test-by-revert)
-----------------------------------------------------------------------
The dropped dispatch_gate equality (rule ⑥ `team_name == session_team`) and
the split rule ③ team_name-presence are LOAD-BEARING — the both-modes gate
legs below (which pass a deliberately-wrong/ignored spawn team_name arg) only
ALLOW because the arg is now inert. Measured by reverting the source to the
pre-fix parent and re-running:

  git checkout <pre-fix> -- hooks/dispatch_gate.py   # parent of the fix commit
  pytest test_dispatch_gate.py::test_inert_team_name_arg_resolves_against_ssot \
         test_dispatch_gate.py::test_missing_team_name_arg_resolves_via_ssot \
         test_team_name_resolution_both_modes.py
    → {5 failed, 3 passed}
      RED (coupled to the deletion):
        - test_inert_team_name_arg_resolves_against_ssot   (old rule ⑥ → team_name_mismatch)
        - test_missing_team_name_arg_resolves_via_ssot     (old rule ③ → team_name_required)
        - test_in_process_leg_resolves_lead_team           (old rule ⑥, wrong arg != SSOT)
        - test_tmux_leg_resolves_lead_team                 (old rule ⑥, wrong arg != SSOT)
        - test_structural_keying_same_store_branches_on_topology_only (old rule ⑥, both legs)
      GREEN (correctly fix-INDEPENDENT — exercise generate_team_name / the
      preserved empty-SSOT fail-closed, NOT the dropped equality):
        - test_resolver_output_addresses_real_platform_store
        - test_resolver_keyed_on_lead_session_not_teammate
        - test_empty_ssot_team_fails_closed_both_modes
  git checkout <fix> -- hooks/dispatch_gate.py        # restore; git diff --quiet exits 0

  # AC-1 CRUX — the ⑦/⑧ rebind itself (highest-value proof). Surgically revert
  # ONLY the session-team source so ⑦/⑧ read the platform-ignored caller spawn
  # arg again (keep rule ⑥ deleted), isolating the rebind from the ⑥-deletion:
  #   session_team = pact_context.get_team_name()  →  tool_input.get("team_name","") or ""
  pytest test_dispatch_gate.py::test_inert_team_name_arg_resolves_against_ssot
    → {1 failed}  inert arg team_name="wrong-team" now drives the member/task
      reads → has_task_assigned("wrong-team", "tester") is False (tasks live
      under the SSOT 'pact-test', not 'wrong-team') → flips to a no_task_assigned
      DENY: "no Task assigned to owner='tester' in team 'wrong-team'". This
      proves ⑦/⑧ resolve via the SSOT, not the caller arg — the rebind is
      load-bearing independently of the ⑥-equality deletion. Restore via
      git checkout <fix> -- hooks/dispatch_gate.py; git diff --quiet exits 0.

  git checkout <pre-fix> -- hooks/bootstrap_gate.py
  pytest test_bootstrap_gate.py::TestCanonicalSecretarySpawnCarveOut::test_secretary_spawn_ignores_caller_team_name
    → {1 failed}  (old carve-out binding-4 `tool_input.team_name == expected_team`
                   DENYs the wrong-team secretary spawn the fix now ALLOWs)
  git checkout <fix> -- hooks/bootstrap_gate.py        # restore; git diff --quiet exits 0
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

_SUPPRESS_EXPECTED = {"suppressOutput": True}
_NAME = "tester"

# Two real-shaped session ids. The team is keyed on the LEAD's id; the tmux
# teammate runs under a DISTINCT id so the two topologies are structurally
# different (== vs != leadSessionId).
LEAD_SID = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"
TMUX_SID = "ffff8888-bbbb-4ccc-9ddd-eeeeeeeeeeee"
LEAD_TEAM = "session-0001639f"   # platform convention, hand-encoded (not from the SUT)
TMUX_OWN_TEAM = "session-ffff8888"  # what a teammate keyed on its OWN sid would (wrongly) pick


# ─── seeding helpers (self-contained; modeled on test_dispatch_gate.py) ───────


def _seed_plugin(plugin_root: Path, agents=("pact-architect",)):
    agents_dir = plugin_root / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for stem in agents:
        (agents_dir / f"{stem}.md").write_text(f"---\nname: {stem}\n---\n")


def _write_context(monkeypatch, tmp_path, plugin_root, *, team_name, session_id):
    """Persist the pact-session-context the gate reads as the SSOT. team_name
    is the platform `session-<id8>`; session_id is the RUNNING frame's id
    (== leadSessionId in-process, != in tmux)."""
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx_path = tmp_path / "pact-session-context.json"
    ctx_path.write_text(
        json.dumps(
            {
                "team_name": team_name,
                "session_id": session_id,
                "project_dir": str(tmp_path / "project"),
                "plugin_root": str(plugin_root),
                "started_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ctx_module, "_context_path", ctx_path)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "init", lambda input_data: None)
    import shared.dispatch_helpers as dh
    dh._specialist_registry.cache_clear()


def _seed_team_store(tmp_path, *, team_name, lead_session_id, members=(), tasks=()):
    """Write the platform-shaped team store: teams/{team_name}/config.json (with
    the platform `name` + `leadSessionId` fields) + tasks/{team_name}/."""
    team_dir = tmp_path / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps(
            {
                "name": team_name,
                "leadSessionId": lead_session_id,
                "members": [{"name": m} for m in members],
            }
        ),
        encoding="utf-8",
    )
    tasks_dir = tmp_path / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for i, (owner, status) in enumerate(tasks):
        (tasks_dir / f"task_{i}.json").write_text(
            json.dumps({"id": str(i), "owner": owner, "status": status}),
            encoding="utf-8",
        )


def _make_spawn(team_name_arg="ignored-by-platform"):
    """A teammate spawn frame. The team_name arg is platform-ignored post-fix —
    we pass a deliberately WRONG value to confirm it is never a path component."""
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "spawn-frame-session",
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": "pact-architect",
            "name": _NAME,
            "team_name": team_name_arg,
            "prompt": "Standard mission. Check TaskList for tasks assigned to you.",
        },
    }


def _run_dispatch(spawn, capsys):
    from dispatch_gate import main
    with patch("sys.stdin", io.StringIO(json.dumps(spawn))):
        with pytest.raises(SystemExit) as exc:
            main()
    out = capsys.readouterr().out.strip()
    return exc.value.code, (json.loads(out) if out else {})


def _setup_leg(monkeypatch, tmp_path, *, frame_session_id):
    """Seed a complete env for ONE topology leg. The SSOT team is always the
    LEAD's `session-0001639f`; only the running frame's session_id changes."""
    plugin_root = tmp_path / "plugin"
    _seed_plugin(plugin_root)
    _write_context(
        monkeypatch, tmp_path, plugin_root,
        team_name=LEAD_TEAM, session_id=frame_session_id,
    )
    _seed_team_store(
        tmp_path, team_name=LEAD_TEAM, lead_session_id=LEAD_SID,
        members=(), tasks=((_NAME, "pending"),),
    )


# ─── 1. CI TRIPWIRE — resolver output addresses the real platform store ───────


def test_resolver_output_addresses_real_platform_store(tmp_path, monkeypatch):
    """CI TRIPWIRE (rename-risk hedge for the deterministic-resolver decision).

    generate_team_name's output MUST address the platform's real on-disk team
    dir. The platform names its implicit team `session-<first 8 of session id>`.
    We encode that convention INDEPENDENTLY as a literal (NOT computed from the
    SUT), seed a config.json there, and assert the resolver lands on it. If a
    future platform rename or resolver refactor breaks the convention, this
    fails LOUDLY in CI instead of silently deadlocking bootstrap in prod.

    Counter-test: change `generate_team_name` to any other prefix/truncation and
    this flips RED (the seeded dir no longer matches the derived name)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Simulate the platform-created team store under the hand-encoded convention.
    teams_dir = tmp_path / ".claude" / "teams" / LEAD_TEAM
    teams_dir.mkdir(parents=True, exist_ok=True)
    (teams_dir / "config.json").write_text(
        json.dumps({"name": LEAD_TEAM, "members": []}), encoding="utf-8"
    )

    from session_init import generate_team_name
    derived = generate_team_name({"session_id": LEAD_SID})

    # (a) resolver output == the independent convention literal …
    assert derived == LEAD_TEAM, (
        f"resolver produced {derived!r}, expected {LEAD_TEAM!r} "
        "(session-<first8>) — resolver/platform-convention divergence"
    )
    # (b) … and it addresses a real on-disk platform dir whose own name matches.
    resolved_cfg = tmp_path / ".claude" / "teams" / derived / "config.json"
    assert resolved_cfg.exists(), (
        f"resolver produced {derived!r} but no teams/{derived}/config.json "
        "exists — rename tripwire"
    )
    assert json.loads(resolved_cfg.read_text(encoding="utf-8"))["name"] == LEAD_TEAM


def test_resolver_keyed_on_lead_session_not_teammate(tmp_path, monkeypatch):
    """The team is keyed on the LEAD's session id. A teammate keyed on its OWN
    distinct session id would derive a DIFFERENT (non-existent) dir — proving why
    resolution must go through the lead-written SSOT, never the teammate's sid."""
    from session_init import generate_team_name
    assert generate_team_name({"session_id": LEAD_SID}) == LEAD_TEAM
    assert generate_team_name({"session_id": TMUX_SID}) == TMUX_OWN_TEAM
    assert LEAD_TEAM != TMUX_OWN_TEAM  # the two topologies derive distinct names


# ─── 2. BOTH-MODES gate resolution — keyed on topology, via the SSOT ──────────


def test_in_process_leg_resolves_lead_team(tmp_path, monkeypatch, capsys):
    """In-process (frame session_id == leadSessionId): the gate resolves the
    LEAD's `session-0001639f` store via the SSOT and ALLOWs against the seeded
    member/task store. The platform-ignored (wrong) spawn team_name arg is never
    a path component."""
    _setup_leg(monkeypatch, tmp_path, frame_session_id=LEAD_SID)
    code, out = _run_dispatch(_make_spawn(team_name_arg="wrong-team"), capsys)
    assert code == 0
    assert out == _SUPPRESS_EXPECTED


def test_tmux_leg_resolves_lead_team(tmp_path, monkeypatch, capsys):
    """Tmux (frame session_id != leadSessionId): the running frame's own session
    id differs from the lead's, but the gate STILL resolves the LEAD's
    `session-0001639f` store (the one platform team) via the SSOT — it does not
    recompute the team from the frame's own session_id. ALLOWs identically."""
    _setup_leg(monkeypatch, tmp_path, frame_session_id=TMUX_SID)
    code, out = _run_dispatch(_make_spawn(team_name_arg="wrong-team"), capsys)
    assert code == 0
    assert out == _SUPPRESS_EXPECTED
    # Load-bearing: the teammate's OWN-sid dir does NOT exist — a resolver that
    # (wrongly) used the frame's session_id would MISS the store and DENY.
    assert not (tmp_path / ".claude" / "teams" / TMUX_OWN_TEAM).exists()


def test_structural_keying_same_store_branches_on_topology_only(
    tmp_path, monkeypatch, capsys
):
    """THE hard-gate assertion: identical seeded `session-0001639f` store; the
    ONLY difference between the legs is the running frame's session_id == vs !=
    leadSessionId. Resolution is driven by the SSOT (lead-keyed), so BOTH legs
    resolve the same `session-0001639f` store and ALLOW — never a per-frame
    recomputation, never a mode flag."""
    # in-process leg
    _setup_leg(monkeypatch, tmp_path, frame_session_id=LEAD_SID)
    code_in, out_in = _run_dispatch(_make_spawn(), capsys)
    assert code_in == 0 and out_in == _SUPPRESS_EXPECTED

    # tmux leg — same store, flip ONLY the running frame's session_id
    _setup_leg(monkeypatch, tmp_path, frame_session_id=TMUX_SID)
    code_tmux, out_tmux = _run_dispatch(_make_spawn(), capsys)
    assert code_tmux == 0 and out_tmux == _SUPPRESS_EXPECTED


def test_empty_ssot_team_fails_closed_both_modes(tmp_path, monkeypatch, capsys):
    """Fail-closed guard preserved post-fix: when the SSOT team_name is empty,
    the member/task reads have no path segment, so the gate DENYs
    (team_name_unavailable) — in BOTH topologies. The platform-ignored spawn arg
    cannot substitute for the missing SSOT."""
    for frame_sid in (LEAD_SID, TMUX_SID):
        plugin_root = tmp_path / "plugin"
        _seed_plugin(plugin_root)
        _write_context(
            monkeypatch, tmp_path, plugin_root,
            team_name="", session_id=frame_sid,
        )
        _seed_team_store(
            tmp_path, team_name=LEAD_TEAM, lead_session_id=LEAD_SID,
            members=(), tasks=((_NAME, "pending"),),
        )
        code, out = _run_dispatch(_make_spawn(team_name_arg=LEAD_TEAM), capsys)
        assert code == 2, f"empty SSOT must fail-closed (frame_sid={frame_sid})"
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        assert "session team_name is unavailable" in reason
