"""
Location: pact-plugin/tests/test_dispatch_gate_integration.py
Summary: NON-MOCKED L2 integration coverage for dispatch_gate's team-roster seam
— rule ⑦ (name-uniqueness) resolves live team members by reading the REAL
~/.claude/teams/{team_name}/config.json via _team_member_names. dispatch_gate is
fail-CLOSED and L2-only (it makes no get_task_list call; its uncertainty path is
exit(2) DENY, so it fails LOUD, never silent-inert — hence no L3). This L2 drives
evaluate_dispatch end-to-end through the REAL config read + the REAL plugin
agents/ registry, NO seam stubbed.

================================ ANTI-MOCK INVARIANT ===========================
MUST NOT monkeypatch _team_member_names / get_team_name / get_plugin_root /
is_registered_pact_specialist. The real config-file read IS the seam. The ONLY
doubles are Path.home redirection (the teams/ config dir lives under it), the
pact_context fixture (session team + the REAL plugin_root so ⑤ passes against the
real agents/ tree), and the constructed tool_input frame.

============================ NON-VACUITY (source-revert) =======================
The uniqueness DENY is DOWNSTREAM of _team_member_names reading the real config.
Source-revert _team_member_names' path resolution (hooks/dispatch_gate.py — e.g.
break the `Path.home()/".claude"/"teams"/team_name/"config.json"` join) so it can
no longer find the roster, then run:
    python -m pytest tests/test_dispatch_gate_integration.py -k non_vacuity_gate
EXPECTED cardinality: {1 failed} — _team_member_names returns set() -> no
collision -> the dispatch is ALLOWED (or falls to a later rule) instead of the
expected uniqueness DENY. Restore -> green. The same-fixture NEGATIVE control (roster WITHOUT the colliding
name -> not a uniqueness DENY) proves the DENY is coupled to the real roster read.
================================================================================
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import dispatch_gate  # noqa: E402

PLUGIN_ROOT = str(Path(__file__).parent.parent)  # the real pact-plugin/ (has agents/)
TEAM = "pact-testteam"
SID = "aaaaaaaa-1111-2222-3333-444444444444"
SPECIALIST = "pact-test-engineer"  # a real registered specialist (agents/pact-*.md)


def _write_team_config(home: Path, team: str, member_names: list[str]) -> None:
    cfg_dir = home / ".claude" / "teams" / team
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"members": [{"name": n} for n in member_names]}),
        encoding="utf-8",
    )


@pytest.fixture
def live_env(tmp_path, monkeypatch, pact_context):
    """Path.home -> tmp (real teams/ config dir); pact_context with the REAL
    plugin_root so the ⑤ agents/ + registry checks pass against the real tree."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir="/test/project",
                 plugin_root=PLUGIN_ROOT)
    return tmp_path


class TestDispatchGateRosterSeam:
    def test_non_vacuity_gate_duplicate_name_denied_via_real_config(self, live_env):
        # Real roster on disk already contains "architect"; dispatching a new
        # teammate also named "architect" must DENY on rule ⑦ (uniqueness),
        # which can ONLY be reached if _team_member_names resolves the real
        # config.
        _write_team_config(live_env, TEAM, ["architect", "backend"])
        decision, reason, rule = dispatch_gate.evaluate_dispatch({
            "subagent_type": SPECIALIST, "name": "architect", "team_name": TEAM,
        })
        assert decision == "DENY", (
            f"expected uniqueness DENY from the real roster read; got "
            f"{decision} / {rule} / {reason}"
        )
        assert "architect" in (reason or "")

    def test_negative_control_unique_name_not_a_uniqueness_deny(self, live_env):
        # Same fixture; the dispatched name is NOT in the roster -> the
        # uniqueness rule does NOT fire. Proves the DENY above is coupled to the
        # real roster read, not an always-DENY.
        _write_team_config(live_env, TEAM, ["architect", "backend"])
        decision, reason, rule = dispatch_gate.evaluate_dispatch({
            "subagent_type": SPECIALIST, "name": "frontend", "team_name": TEAM,
        })
        # frontend is unique -> not a uniqueness DENY (ALLOW, or a later-rule
        # outcome, but NOT the uniqueness rule).
        assert not (decision == "DENY" and "already a live member" in (reason or ""))

    def test_team_member_names_resolves_real_config(self, live_env):
        # The seam read in isolation: a real config -> the real member set.
        _write_team_config(live_env, TEAM, ["architect", "backend", "secretary"])
        members = dispatch_gate._team_member_names(TEAM)
        assert members == {"architect", "backend", "secretary"}

    def test_team_member_names_absent_config_returns_empty(self, live_env):
        # No config on disk -> tolerant empty set (the seam's documented
        # fail-open for the roster read).
        assert dispatch_gate._team_member_names(TEAM) == set()
