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
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
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
