"""Smoke tests for session_end's leadSessionId retirement pass
(_retire_session_leadsessionid_claims).

CODE-phase smoke coverage (backend-coder) for Commit 2 of the resume-path
split-brain fix: on session end, retire (leadSessionId=None) the ending
session's claim on its NON-LIVE same-id sibling dirs — skipping the live team
and foreign-id dirs, rewriting config.json only (never rmtree), fail-closed
when either the session id or team name is unknown, idempotent on re-run, and
best-effort (a write error on one dir does not abort the pass).

The COMPREHENSIVE §6 retirement matrix + the both-topologies integration test
are the test-engineer's deliverable — this file is intentionally small and
pins the invariants the lead named plus the load-bearing safety predicates.
"""

import json

import pytest

from session_end import _retire_session_leadsessionid_claims

SID = "0be9512d-5f6d-490e-bca3-b4ccd68f11f8"
FOREIGN_SID = "64f7b112-ef89-434c-a0c0-00a038002136"
LIVE_TEAM = "session-0be9512d"


@pytest.fixture
def retire_env(tmp_path):
    """Isolated ~/.claude/teams tree. Returns a helper namespace to seed team
    dirs with a chosen leadSessionId and to run the retirement pass."""
    teams_dir = tmp_path / ".claude" / "teams"
    teams_dir.mkdir(parents=True)

    class _Env:
        teams = teams_dir

        @staticmethod
        def seed(name, *, lead_session_id, extra=None):
            d = teams_dir / name
            d.mkdir(parents=True, exist_ok=True)
            config = {"name": name, "leadSessionId": lead_session_id,
                      "members": [], "createdAt": 1771543196549}
            if extra:
                config.update(extra)
            (d / "config.json").write_text(json.dumps(config), encoding="utf-8")
            return d

        @staticmethod
        def lead_of(name):
            data = json.loads(
                (teams_dir / name / "config.json").read_text(encoding="utf-8")
            )
            return data.get("leadSessionId")

        @staticmethod
        def config_of(name):
            return json.loads(
                (teams_dir / name / "config.json").read_text(encoding="utf-8")
            )

        @staticmethod
        def retire(session_id=SID, team_name=LIVE_TEAM):
            return _retire_session_leadsessionid_claims(
                current_session_id=session_id,
                current_team_name=team_name,
                teams_dir=teams_dir,
            )

    return _Env


def test_skips_live_team(retire_env):
    """The live team KEEPS its leadSessionId (pact-align step 3 parity)."""
    retire_env.seed(LIVE_TEAM, lead_session_id=SID)
    retire_env.seed("agile-swinging-shore", lead_session_id=SID)
    retired = retire_env.retire()
    assert retired == 1
    assert retire_env.lead_of(LIVE_TEAM) == SID           # live untouched
    assert retire_env.lead_of("agile-swinging-shore") is None


def test_nulls_competing_claims(retire_env):
    """Every NON-live dir sharing our session id is retired to None."""
    retire_env.seed(LIVE_TEAM, lead_session_id=SID)
    retire_env.seed("aaa-sibling", lead_session_id=SID)
    retire_env.seed("zzz-sibling", lead_session_id=SID)
    retired = retire_env.retire()
    assert retired == 2
    assert retire_env.lead_of("aaa-sibling") is None
    assert retire_env.lead_of("zzz-sibling") is None


def test_ignores_foreign_session_claims(retire_env):
    """A dir claimed by a DIFFERENT (live) session is left untouched."""
    retire_env.seed(LIVE_TEAM, lead_session_id=SID)
    retire_env.seed("other-session-team", lead_session_id=FOREIGN_SID)
    retired = retire_env.retire()
    assert retired == 0
    assert retire_env.lead_of("other-session-team") == FOREIGN_SID


def test_idempotent_second_run_is_noop(retire_env):
    """A re-run is a no-op: already-None claims are != our id, so skipped."""
    retire_env.seed(LIVE_TEAM, lead_session_id=SID)
    retire_env.seed("sibling", lead_session_id=SID)
    assert retire_env.retire() == 1
    assert retire_env.lead_of("sibling") is None
    # Second pass finds nothing to retire.
    assert retire_env.retire() == 0
    assert retire_env.lead_of("sibling") is None


def test_fail_closed_on_empty_session_id(retire_env):
    """No session id → retire NOTHING (cannot guarantee skipping live team)."""
    retire_env.seed(LIVE_TEAM, lead_session_id=SID)
    retire_env.seed("sibling", lead_session_id=SID)
    assert retire_env.retire(session_id="") == 0
    assert retire_env.lead_of("sibling") == SID           # untouched


def test_fail_closed_on_empty_team_name(retire_env):
    """No live team name → retire NOTHING (would risk nulling the live team)."""
    retire_env.seed(LIVE_TEAM, lead_session_id=SID)
    retire_env.seed("sibling", lead_session_id=SID)
    assert retire_env.retire(team_name="") == 0
    assert retire_env.lead_of("sibling") == SID           # untouched


def test_skips_live_team_case_insensitively(retire_env):
    """Live-team skip is case-insensitive (get_team_name lowercases its value)."""
    retire_env.seed("Session-0BE9512D", lead_session_id=SID)  # mixed-case dir
    retire_env.seed("sibling", lead_session_id=SID)
    retired = retire_env.retire(team_name="session-0be9512d")
    assert retired == 1
    assert retire_env.lead_of("Session-0BE9512D") == SID  # still the live claim
    assert retire_env.lead_of("sibling") is None


def test_does_not_rmtree_preserves_all_fields(retire_env):
    """Retirement rewrites ONLY leadSessionId — the dir survives and every
    other config field is preserved (pins 'retire the claim, not the dir')."""
    retire_env.seed(LIVE_TEAM, lead_session_id=SID)
    retire_env.seed("sibling", lead_session_id=SID,
                    extra={"description": "PACT session team", "custom": 42})
    retire_env.retire()
    # Dir still present.
    assert (retire_env.teams / "sibling").is_dir()
    cfg = retire_env.config_of("sibling")
    assert cfg["leadSessionId"] is None
    assert cfg["description"] == "PACT session team"  # preserved
    assert cfg["custom"] == 42                          # preserved
    assert cfg["name"] == "sibling"                     # preserved


def test_best_effort_on_unreadable_config(retire_env):
    """A dir with a malformed config is skipped; the pass still retires the
    readable competitor and never raises."""
    retire_env.seed(LIVE_TEAM, lead_session_id=SID)
    retire_env.seed("good-sibling", lead_session_id=SID)
    bad = retire_env.teams / "bad-sibling"
    bad.mkdir()
    (bad / "config.json").write_text("{not valid json", encoding="utf-8")
    retired = retire_env.retire()  # must not raise
    assert retired == 1
    assert retire_env.lead_of("good-sibling") is None


def test_missing_teams_root_returns_zero(tmp_path):
    """Absent teams root → clean 0, no raise."""
    absent = tmp_path / "nope" / "teams"
    assert _retire_session_leadsessionid_claims(
        current_session_id=SID, current_team_name=LIVE_TEAM, teams_dir=absent
    ) == 0
