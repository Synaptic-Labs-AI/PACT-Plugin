"""
The resume census picks this session's team when other PACT sessions are live.

After a resume the platform can put the session in a new team while hook frames
keep the original session id and the original team's config is gone. The
census then looks for the live team that corroborates as this session's own
(lead cwd and birth order). These cells pin that it corroborates candidates
BEFORE counting them: a live session of another project must not suppress the
realign, while two teams that both corroborate still fall back to the recorded
team rather than a guess.

Every cell runs over the real path derivations (tests/fixtures/team_split.py):
no resolver, init or context-path stub. The dispatch cells put the owner's task
only in the team the resolution must land on, so an ALLOW is reachable only
through the intended resolution.

Sibling of test_resume_team_realign_1507.py (the census's original matrix) and
test_team_name_detect_align.py (its focused resolver cells).
"""

import io
import json
from unittest.mock import patch

import pytest

from fixtures.team_split import (
    FOREIGN_TEAM,
    NEW_SID,
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    SECOND_SID,
    SECOND_TEAM,
    reset_context_module,
    split_world,
)

_OWNER = "tester"
_OWNER_TASK = {"id": "1", "owner": _OWNER, "status": "pending",
               "subject": "tester: work"}


def _lead_spawn():
    """A lead's in-process Agent spawn: it carries the original session id."""
    return {
        "hook_event_name": "PreToolUse",
        "session_id": OLD_SID,
        "agent_type": "PACT:pact-orchestrator",
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": "pact-architect",
            "name": _OWNER,
            "team_name": "ignored-by-platform",
            "prompt": "Standard mission. Check TaskList for tasks assigned to you.",
        },
    }


def _run_dispatch(capsys):
    from dispatch_gate import main
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(_lead_spawn()))):
        with pytest.raises(SystemExit) as exc:
            main()
    out = capsys.readouterr().out.strip()
    return exc.value.code, (json.loads(out) if out else {})


def _team_name():
    import shared.pact_context as ctx_module
    reset_context_module()
    ctx_module.init({"session_id": OLD_SID})
    return ctx_module.get_team_name()


@pytest.mark.parametrize("foreign", ["foreign-cwd", "member-less"])
def test_a_live_session_of_another_project_does_not_block_the_realign(
        tmp_path, monkeypatch, capsys, foreign):
    """One team corroborates as this session's and another project's session
    is live beside it: the census picks the corroborating team, and a spawn
    whose task exists only there is allowed."""
    split_world(tmp_path, monkeypatch, live_tasks=[_OWNER_TASK],
                foreign=foreign)
    assert _team_name() == NEW_TEAM
    code, out = _run_dispatch(capsys)
    assert code == 0, out
    assert out == {"suppressOutput": True}


def test_two_corroborating_teams_keep_the_recorded_team(tmp_path, monkeypatch,
                                                         capsys):
    """Two live teams both corroborate as this session's (two broken resumes
    of the same project): the census decides nothing, the recorded team
    stands, and the spawn is denied even though one of them holds the task."""
    split_world(tmp_path, monkeypatch, live_tasks=[_OWNER_TASK],
                second_corroborated=True)
    assert _team_name() == OLD_TEAM
    code, out = _run_dispatch(capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no Task assigned" in reason
    assert f"team {OLD_TEAM!r}" in reason


def test_a_lone_uncorroborated_live_team_is_never_picked(tmp_path, monkeypatch,
                                                          capsys):
    """The only live team belongs to another project: the recorded team
    stands, and the spawn whose task sits only in the foreign team is denied."""
    split_world(tmp_path, monkeypatch, live_team=None, foreign="foreign-cwd",
                foreign_tasks=[_OWNER_TASK])
    assert _team_name() == OLD_TEAM
    assert _team_name() != FOREIGN_TEAM
    code, _ = _run_dispatch(capsys)
    assert code == 2


def test_the_second_corroborating_team_sorts_after_the_first(tmp_path):
    """Precondition of the two-team cell: the two corroborating names sort
    so that a take-the-first resolver would return the live team, which the
    cell's recorded-team assertion then rejects."""
    assert sorted([NEW_TEAM, SECOND_TEAM])[0] == NEW_TEAM


# -- a team another PACT session owns is never a candidate ---------------------


def _give_session_a_pact_dir(world, session_id, slug="other-project"):
    """A PACT session context for ``session_id``: that session ran PACT."""
    session_dir = world.root / "pact-sessions" / slug / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(
        json.dumps({"team_name": "x", "session_id": session_id}),
        encoding="utf-8")


def test_a_sibling_owned_team_never_wins_when_this_sessions_store_idles(
        tmp_path, monkeypatch):
    """After a resume, a same-project sibling session's team corroborates
    beside this session's own re-provisioned team. Once this session's own
    store idles past the recency window, the sibling's would be the only
    candidate. The sibling ran PACT, so its team is never a candidate, and
    the recorded team stands rather than the sibling's."""
    world = split_world(tmp_path, monkeypatch, live_store_aged=True,
                        second_corroborated=True)
    _give_session_a_pact_dir(world, SECOND_SID)
    assert _team_name() == OLD_TEAM


def test_without_its_pact_dir_the_lone_sibling_is_still_picked(
        tmp_path, monkeypatch):
    """Control for the cell above: the exclusion is keyed on the sibling's
    PACT session dir. Without one, the lone corroborating sibling wins."""
    split_world(tmp_path, monkeypatch, live_store_aged=True,
                second_corroborated=True)
    assert _team_name() == SECOND_TEAM


def test_a_sibling_owned_team_does_not_make_the_census_ambiguous(
        tmp_path, monkeypatch):
    """This session's own re-provisioned team and a sibling's both
    corroborate. The sibling's leadSessionId owns a PACT session dir, so only
    the own team is a candidate and the census realigns to it."""
    world = split_world(tmp_path, monkeypatch, second_corroborated=True)
    _give_session_a_pact_dir(world, SECOND_SID)
    assert _team_name() == NEW_TEAM


def test_team_ownership_names_only_another_pact_session(tmp_path,
                                                        monkeypatch):
    import shared.pact_context as ctx_module
    world = split_world(tmp_path, monkeypatch, second_corroborated=True)
    _give_session_a_pact_dir(world, SECOND_SID)
    reset_context_module()
    ctx_module.init({"session_id": OLD_SID})
    owned = ctx_module.team_owned_by_other_pact_session
    assert owned(SECOND_TEAM) is True
    # The re-provisioned own team: its lead id never ran PACT.
    assert owned(NEW_TEAM) is False
    # A team whose lead is the asking session itself.
    assert owned(SECOND_TEAM, session_id=SECOND_SID) is False
    assert owned("../escape") is False
    assert owned("session-absent0") is False


# -- the one description of an undecided team ----------------------------------


def _alignment():
    import shared.pact_context as ctx_module
    reset_context_module()
    ctx_module.init({"session_id": OLD_SID})
    return ctx_module.describe_team_alignment()


def test_two_matching_teams_are_described_as_ambiguous(tmp_path, monkeypatch):
    split_world(tmp_path, monkeypatch, second_corroborated=True)
    alignment = _alignment()
    assert alignment.state == "ambiguous"
    assert alignment.candidates == ((NEW_TEAM, True), (SECOND_TEAM, True))
    assert f"recorded team `{OLD_TEAM}` has no config" in alignment.text
    assert "cannot tell which team is this session's" in alignment.text
    assert "Do not spawn" in alignment.text


def test_only_a_foreign_team_is_described_as_unresolved(tmp_path, monkeypatch):
    split_world(tmp_path, monkeypatch, live_team=None, foreign="foreign-cwd")
    alignment = _alignment()
    assert alignment.state == "unresolved"
    assert alignment.candidates == ((FOREIGN_TEAM, False),)
    assert f"`{FOREIGN_TEAM}` (does not match)" in alignment.text
    assert "team-split recovery" in alignment.text
    assert "Do not spawn" not in alignment.text


def test_two_teams_that_both_fail_to_match_are_unresolved_not_ambiguous(
        tmp_path, monkeypatch):
    from fixtures.team_split import FOREIGN_CWD, seed_team
    world = split_world(tmp_path, monkeypatch, live_team=None,
                        foreign="foreign-cwd")
    seed_team(world.root, "session-dddd0000",
              lead_sid="dddd0000-1111-4222-8333-444455556666",
              lead_cwd=FOREIGN_CWD)
    alignment = _alignment()
    assert alignment.state == "unresolved"
    assert [matches for _, matches in alignment.candidates] == [False, False]
    assert "cannot tell" not in alignment.text


def test_no_live_team_is_described_with_its_recovery(tmp_path, monkeypatch):
    split_world(tmp_path, monkeypatch, live_team=None)
    alignment = _alignment()
    assert alignment.state == "no_candidates"
    assert alignment.candidates == ()
    assert "No live candidate teams." in alignment.text
    assert "team-split recovery in `pact-state-recovery.md`" in alignment.text


def test_a_decided_census_is_described_as_resolved(tmp_path, monkeypatch):
    split_world(tmp_path, monkeypatch)
    assert _alignment() == ("resolved", (), "")


def test_a_sibling_owned_team_is_left_out_of_the_description(tmp_path,
                                                              monkeypatch):
    """The description reads the census's own list, so a team another PACT
    session owns appears in neither."""
    world = split_world(tmp_path, monkeypatch, live_team=None,
                        second_corroborated=True)
    _give_session_a_pact_dir(world, SECOND_SID)
    alignment = _alignment()
    assert alignment.state == "no_candidates"
    assert SECOND_TEAM not in alignment.text


# -- the census anchors on the project dir -------------------------------------


def test_a_lead_running_in_the_hooks_cwd_but_not_the_project_is_refused(
        tmp_path, monkeypatch):
    """The census corroborates a lead by this session's project dir only: a
    team whose lead cwd is merely the hook process's working directory is
    not this session's."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    world = split_world(tmp_path, monkeypatch, live_team=None)
    from fixtures.team_split import seed_team
    seed_team(world.root, NEW_TEAM, lead_sid=NEW_SID, lead_cwd=str(elsewhere))
    monkeypatch.chdir(elsewhere)
    assert _team_name() == OLD_TEAM
