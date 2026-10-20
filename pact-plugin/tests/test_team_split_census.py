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
import os
import subprocess
import sys
from datetime import datetime
from unittest.mock import patch

import pytest

from fixtures.team_split import (
    HOOKS_DIR,
    FOREIGN_TEAM,
    NEW_SID,
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    SECOND_SID,
    SECOND_TEAM,
    hook_env,
    reset_context_module,
    split_world,
    write_journal,
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


# -- a team another live PACT session owns is never a candidate ---------------

# A sibling session that /clear started in the sibling's own claude process.
SIBLING_CLEAR_SID = "b2b2b2b2-1111-4222-8333-444455556666"
OWN_KEY = "4242:Mon Oct 19 15:00:00 2026"


def _pact_session(world, session_id, *, team="x", key="", ended=False,
                  resumed=False, born="startup", born_at=None, ended_at=None,
                  compacted=False):
    """A PACT session of this project: its context records ``team`` and
    ``key``; its first session_start has source ``born`` (at ``born_at``);
    its journal ends in a session_end (at ``ended_at``) when ``ended``, then
    a new session_start when ``resumed``; ``compacted`` adds a compaction's
    session_start after the birth."""
    session_dir = world.session_dir.parent / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(json.dumps(
        {"team_name": team, "session_id": session_id, "process_key": key}),
        encoding="utf-8")
    birth = {"type": "session_start", "session_id": session_id,
             "project_dir": str(world.project_dir), "source": born}
    if born_at:
        birth["ts"] = born_at
    events = [birth]
    if compacted:
        events.append(dict(birth, source="compact", ts=_AFTER_END))
    if ended:
        events.append({"type": "session_end", "ts": ended_at}
                      if ended_at else "session_end")
    if resumed:
        events.append(dict(birth, source="resume"))
    journal = write_journal(session_dir, events)
    if ended and ended_at and not resumed:
        # The session_end was the journal's last write.
        written = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        os.utime(journal, (written.timestamp(), written.timestamp()))


def _set_own_key(world, key):
    data = json.loads(world.context_path.read_text(encoding="utf-8"))
    data["process_key"] = key
    world.context_path.write_text(json.dumps(data), encoding="utf-8")


def _owned(team):
    import shared.pact_context as ctx_module
    reset_context_module()
    ctx_module.init({"session_id": OLD_SID})
    return ctx_module.team_owned_by_other_pact_session(team)


_OWNED = "another PACT session owns this team"


def _verb_refusal(monkeypatch, team):
    """The realign command's refusal for ``team`` as this session ('' to
    accept)."""
    from shared import realign_team
    reset_context_module()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OLD_SID)
    try:
        return realign_team.refusal_reason(team)
    finally:
        reset_context_module()


def _aged_sibling_world(tmp_path, monkeypatch):
    """After a resume, this session's own re-provisioned store has idled
    past the recency window, so a corroborating sibling team is the only
    census candidate unless the ownership rule excludes it."""
    return split_world(tmp_path, monkeypatch, live_store_aged=True,
                       second_corroborated=True)


def test_a_team_a_live_sibling_records_stays_excluded_after_its_lead_ended(
        tmp_path, monkeypatch):
    """The sibling's lead session ended in a /clear and the session the
    clear started records the team and is live: the team is still that
    sibling's, so the recorded team stands."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, key="7:sib", ended=True)
    _pact_session(world, SIBLING_CLEAR_SID, team=SECOND_TEAM, key="7:sib")
    assert _team_name() == OLD_TEAM
    assert _owned(SECOND_TEAM) is True


def test_a_team_whose_every_session_ended_is_a_candidate_again(
        tmp_path, monkeypatch):
    """Control for the cell above: once the session that records the team
    has ended too, no live session owns it and the census picks it."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, key="7:sib", ended=True)
    _pact_session(world, SIBLING_CLEAR_SID, team=SECOND_TEAM, key="7:sib",
                  ended=True)
    assert _team_name() == SECOND_TEAM
    assert _owned(SECOND_TEAM) is False


def test_a_sibling_resumed_after_it_ended_is_live_again(tmp_path,
                                                        monkeypatch):
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  resumed=True)
    assert _team_name() == OLD_TEAM
    assert _owned(SECOND_TEAM) is True


def test_a_live_lead_that_records_another_team_still_owns_the_one_it_leads(
        tmp_path, monkeypatch):
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, SECOND_SID, team="session-unrelated0")
    assert _team_name() == OLD_TEAM
    assert _owned(SECOND_TEAM) is True


def test_an_empty_key_on_both_sides_never_makes_a_sibling_ours(
        tmp_path, monkeypatch):
    """This session and a live sibling both record no process key: an
    empty key equals nothing, so the sibling's team stays excluded."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, "")
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, key="")
    assert _team_name() == OLD_TEAM
    assert _owned(SECOND_TEAM) is True


def test_a_live_sibling_with_a_different_key_is_not_ours(tmp_path,
                                                         monkeypatch):
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, OWN_KEY)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, key="7:sib")
    assert _team_name() == OLD_TEAM
    assert _owned(SECOND_TEAM) is True


def test_an_earlier_life_of_this_process_with_the_same_key_is_ours(
        tmp_path, monkeypatch):
    """Control for the two cells above: a live session recording this
    session's own non-empty key is an earlier life of this claude process,
    so its team is this session's and the census picks it."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, OWN_KEY)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, key=OWN_KEY)
    assert _team_name() == SECOND_TEAM
    assert _owned(SECOND_TEAM) is False


def test_without_any_owning_session_the_lone_sibling_is_still_picked(
        tmp_path, monkeypatch):
    """Control: with no PACT session leading or recording it, the lone
    corroborating sibling team wins."""
    _aged_sibling_world(tmp_path, monkeypatch)
    assert _team_name() == SECOND_TEAM


def test_a_session_of_another_project_is_outside_the_ownership_scan(
        tmp_path, monkeypatch):
    """The scan reads this project's session dirs only: a live session
    under another project's slug that leads the sibling team does not
    exclude it."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    other = world.session_dir.parent.parent / "other-project" / SECOND_SID
    other.mkdir(parents=True)
    (other / "pact-session-context.json").write_text(json.dumps(
        {"team_name": SECOND_TEAM, "session_id": SECOND_SID}), encoding="utf-8")
    assert _team_name() == SECOND_TEAM


def test_a_sibling_owned_team_does_not_make_the_census_ambiguous(
        tmp_path, monkeypatch):
    """This session's own re-provisioned team and a live sibling's both
    corroborate. Only the own team is a candidate, so the census realigns
    to it."""
    world = split_world(tmp_path, monkeypatch, second_corroborated=True)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM)
    assert _team_name() == NEW_TEAM


def test_team_ownership_rejects_unsafe_and_unknown_teams(tmp_path,
                                                         monkeypatch):
    world = split_world(tmp_path, monkeypatch, second_corroborated=True)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM)
    import shared.pact_context as ctx_module
    reset_context_module()
    ctx_module.init({"session_id": OLD_SID})
    owned = ctx_module.team_owned_by_other_pact_session
    # The re-provisioned own team: no PACT session leads or records it.
    assert owned(NEW_TEAM) is False
    # The asking session itself leads and records it.
    assert owned(SECOND_TEAM, session_id=SECOND_SID) is False
    assert owned("../escape") is False
    assert owned("session-absent0") is False


def test_an_unsafe_team_name_is_never_owned(tmp_path, monkeypatch):
    """A live session's context records a path-unsafe team name: the name is
    refused before any scan."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, SECOND_SID, team="../escape")
    assert _owned("../escape") is False


def test_a_live_session_recording_the_team_owns_it(tmp_path, monkeypatch):
    """Control for the two symlink cells below: the same live session, in
    this project's session dir, owns the team it records."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, UNRELATED_SID, team=SECOND_TEAM)
    assert _owned(SECOND_TEAM) is True


def test_a_session_dir_symlinked_from_outside_the_project_never_owns(
        tmp_path, monkeypatch):
    """The live session recording the team is reached only through a symlink
    in this project's session dir that points outside it: it is not read."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, UNRELATED_SID, team=SECOND_TEAM)
    linked = world.session_dir.parent / UNRELATED_SID
    outside = tmp_path / "outside-session"
    linked.rename(outside)
    linked.symlink_to(outside, target_is_directory=True)
    assert _owned(SECOND_TEAM) is False


def test_a_project_session_dir_outside_pact_sessions_is_never_read(
        tmp_path, monkeypatch):
    """This project's session dir is itself a symlink out of the
    pact-sessions tree: no session in it is read."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, UNRELATED_SID, team=SECOND_TEAM)
    slug = world.session_dir.parent
    outside = tmp_path / "outside-slug"
    slug.rename(outside)
    slug.symlink_to(outside, target_is_directory=True)
    assert _owned(SECOND_TEAM) is False


# -- a keyless /clear successor still holds its predecessor's team --------------

_CLEAR_AT = "2026-10-19T15:34:20Z"
_ONE_SECOND_EARLIER = "2026-10-19T15:34:19Z"
_ONE_SECOND_LATER = "2026-10-19T15:34:21Z"
_AFTER_END = "2026-10-19T15:44:20Z"
_AN_HOUR_LATER = "2026-10-19T16:34:20Z"
_FOUR_SECONDS_LATER = "2026-10-19T15:34:24Z"
_TWO_SECONDS_EARLIER = "2026-10-19T15:34:18Z"
_ELEVEN_SECONDS_AFTER_END = "2026-10-19T15:34:30Z"
# A live session of this project born by a startup.
NEAR_SID = "dddd0000-1111-4222-8333-444455556666"
# This process's first clear-born life, before the /clear that began ours.
FIRST_LIFE_SID = "a1a1a1a1-1111-4222-8333-444455556666"
# A third leader of this project, whose team also corroborates.
THIRD_SID = "eeee0000-1111-4222-8333-444455556666"
THIRD_TEAM = "session-eeee0000"
# An unrelated session of this project whose dir sorts before SECOND_SID's.
UNRELATED_SID = "00000000-1111-4222-8333-444455556666"


def _keyless_sibling_clear(world, *, ended_at=_ONE_SECOND_EARLIER,
                           born_at=_CLEAR_AT, compacted=False):
    """Sibling B led SECOND_TEAM and ended in a /clear; the keyless session
    the clear started is live and records only its own computed team."""
    _set_own_key(world, OWN_KEY)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  ended_at=ended_at)
    _pact_session(world, SIBLING_CLEAR_SID, team="session-b2b2b2b2",
                  born="clear", born_at=born_at, compacted=compacted)


def test_a_keyless_clear_successor_keeps_its_predecessor_s_team(
        tmp_path, monkeypatch):
    """The leader ended and nothing live records its team, but a live
    session was born by a /clear one second after that end: the team is
    still that sibling's, so the census does not adopt it and the realign
    command refuses it."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _keyless_sibling_clear(world)
    assert _team_name() == OLD_TEAM
    assert _owned(SECOND_TEAM) is True
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == _OWNED


def test_a_successor_that_compacted_after_its_clear_still_holds_the_team(
        tmp_path, monkeypatch):
    """The successor's latest start is a compaction; its birth is what
    links it."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _keyless_sibling_clear(world, compacted=True)
    assert _team_name() == OLD_TEAM
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == _OWNED


def test_an_end_journalled_after_the_successor_s_start_still_links(
        tmp_path, monkeypatch):
    """SessionEnd is async and can land after the clear's SessionStart."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _keyless_sibling_clear(world, ended_at=_ONE_SECOND_LATER)
    assert _team_name() == OLD_TEAM
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == _OWNED


def test_a_clear_born_session_far_from_the_end_is_not_a_successor(
        tmp_path, monkeypatch):
    """Control: the live session's clear came an hour after the leader
    ended, so nothing holds the team: the census picks it and the realign
    command accepts it."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _keyless_sibling_clear(world, born_at=_AN_HOUR_LATER)
    assert _team_name() == SECOND_TEAM
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == ""


def test_a_clear_birth_just_outside_the_window_is_not_a_successor(
        tmp_path, monkeypatch):
    """Control at the window's edge: a clear 11 s after the end does not
    link; 10 s is the window."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _keyless_sibling_clear(world, born_at=_ELEVEN_SECONDS_AFTER_END)
    assert _team_name() == SECOND_TEAM


def _own_keyless_clear(world, *, sibling_born_at):
    """This session was born by a keyless /clear one second after its
    predecessor (SECOND_SID, which leads SECOND_TEAM) ended; a sibling's
    /clear started another keyless session at ``sibling_born_at``."""
    _set_own_key(world, "")
    write_journal(world.session_dir, [
        {"type": "session_start", "session_id": OLD_SID, "ts": _CLEAR_AT,
         "project_dir": str(world.project_dir), "source": "clear"},
        "task_metadata_snapshot",
    ])
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  ended_at=_ONE_SECOND_EARLIER)
    _pact_session(world, SIBLING_CLEAR_SID, team="session-b2b2b2b2",
                  born="clear", born_at=sibling_born_at)


@pytest.mark.parametrize("sibling_born_at", [
    _FOUR_SECONDS_LATER, _CLEAR_AT, _ONE_SECOND_EARLIER,
], ids=["ours-nearer", "tie", "sibling-nearer"])
def test_a_sibling_clear_near_our_predecessor_s_end_excludes_our_team(
        tmp_path, monkeypatch, sibling_born_at):
    """Our own clear birth never counts: a live sibling born by a /clear
    within the window of our predecessor's end may be that predecessor's
    successor, so the census keeps the recorded team and the realign
    command refuses, however near our own birth is (the fail-safe cost of
    keyless clears close together)."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _own_keyless_clear(world, sibling_born_at=sibling_born_at)
    assert _team_name() == OLD_TEAM
    assert _owned(SECOND_TEAM) is True
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == _OWNED


def test_without_a_sibling_clear_near_its_end_our_predecessor_s_team_is_ours(
        tmp_path, monkeypatch):
    """Control: the sibling's clear came an hour later, so nothing but us
    follows our predecessor: the census picks its team and the realign
    command accepts it."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _own_keyless_clear(world, sibling_born_at=_AN_HOUR_LATER)
    assert _team_name() == SECOND_TEAM
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == ""


def test_two_keyless_clears_together_exclude_both_teams(tmp_path,
                                                        monkeypatch):
    """Two keyless leads of the project /clear within a few seconds: our
    predecessor (leader of NEW_TEAM) ended 2 s before our clear birth, and
    the sibling leader (SECOND_TEAM) ended at it, its successor born 1 s
    later. Neither team is taken: both stay excluded, and the realign
    command run for the sibling's team exits 3."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, "")
    write_journal(world.session_dir, [
        {"type": "session_start", "session_id": OLD_SID, "ts": _CLEAR_AT,
         "project_dir": str(world.project_dir), "source": "clear"},
        "task_metadata_snapshot",
    ])
    _pact_session(world, NEW_SID, team=NEW_TEAM, ended=True,
                  ended_at=_TWO_SECONDS_EARLIER)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  ended_at=_CLEAR_AT)
    _pact_session(world, SIBLING_CLEAR_SID, team="session-b2b2b2b2",
                  born="clear", born_at=_ONE_SECOND_LATER)
    assert _team_name() == OLD_TEAM
    assert _owned(NEW_TEAM) is True
    assert _owned(SECOND_TEAM) is True
    proc = subprocess.run(
        [sys.executable, "-m", "shared.realign_team", "--team", SECOND_TEAM],
        capture_output=True, text=True, cwd=str(HOOKS_DIR), timeout=60,
        env=hook_env(world, PYTHONPATH=str(HOOKS_DIR),
                     CLAUDE_CODE_SESSION_ID=OLD_SID),
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert _OWNED in proc.stdout + proc.stderr


def _unrelated_end_beside_a_keyless_sibling(world, *, unrelated_end):
    """reviewer-sec's tie and near arms: an unrelated session of the project
    (its dir sorts before the sibling leader's) ended at ``unrelated_end``,
    beside the keyless sibling /clear of _keyless_sibling_clear."""
    _pact_session(world, UNRELATED_SID, team="session-00000000", ended=True,
                  ended_at=unrelated_end)
    _keyless_sibling_clear(world, ended_at=unrelated_end, born_at=unrelated_end)


def test_an_unrelated_end_in_the_same_second_never_frees_the_team(
        tmp_path, monkeypatch):
    """The unrelated end, the sibling leader's end and the successor's
    birth share one second: the successor still holds the leader's team."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _unrelated_end_beside_a_keyless_sibling(world, unrelated_end=_CLEAR_AT)
    assert _team_name() == OLD_TEAM
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == _OWNED


def test_an_unrelated_end_nearer_the_birth_never_frees_the_team(
        tmp_path, monkeypatch):
    """The unrelated end is 1 s nearer the successor's birth than the
    sibling leader's end: the team stays the sibling's."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, UNRELATED_SID, team="session-00000000", ended=True,
                  ended_at=_CLEAR_AT)
    _keyless_sibling_clear(world, ended_at=_ONE_SECOND_EARLIER,
                           born_at=_CLEAR_AT)
    assert _team_name() == OLD_TEAM
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == _OWNED


def _second_clear_beside_a_sibling(world, *, sibling_successor=True):
    """Two /clears in this process: the first clear-born life (same key)
    was born 0 s from the keyless sibling leader's end and ended at our
    own clear an hour later. The sibling's keyless successor was born 4 s
    from that end and is live (when ``sibling_successor``)."""
    _set_own_key(world, OWN_KEY)
    write_journal(world.session_dir, [
        {"type": "session_start", "session_id": OLD_SID, "ts": _AN_HOUR_LATER,
         "project_dir": str(world.project_dir), "source": "clear"},
        "task_metadata_snapshot",
    ])
    _pact_session(world, FIRST_LIFE_SID, team="session-a1a1a1a1", key=OWN_KEY,
                  born="clear", born_at=_CLEAR_AT, ended=True,
                  ended_at=_AN_HOUR_LATER)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  ended_at=_CLEAR_AT)
    if sibling_successor:
        _pact_session(world, SIBLING_CLEAR_SID, team="session-b2b2b2b2",
                      born="clear", born_at=_FOUR_SECONDS_LATER)


def test_an_ended_earlier_life_is_never_our_birth(tmp_path, monkeypatch):
    """Our first clear-born life is ended and is not our own dir, so its
    birth, though nearest the sibling leader's end, never claims that
    leader as our predecessor: the live sibling successor holds the team."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _second_clear_beside_a_sibling(world)
    assert _team_name() == OLD_TEAM
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == _OWNED


def test_without_the_sibling_successor_the_ended_leader_s_team_is_free(
        tmp_path, monkeypatch):
    """Control: the same two /clears with no live sibling successor; nothing
    holds the ended leader's team, so the census picks it."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _second_clear_beside_a_sibling(world, sibling_successor=False)
    assert _team_name() == SECOND_TEAM
    assert _verb_refusal(monkeypatch, SECOND_TEAM) == ""


@pytest.mark.parametrize("life_key, held", [
    (OWN_KEY, False), ("7:another", True),
], ids=["same-key", "other-key"])
def test_a_live_same_key_life_is_never_a_successor(tmp_path, monkeypatch,
                                                   life_key, held):
    """A live clear-born session recording this session's key is an
    earlier life of this process (its SessionEnd lost): it is ours, so it
    never holds the ended sibling leader's team. With another key it is a
    foreign birth and does."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, OWN_KEY)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  ended_at=_ONE_SECOND_EARLIER)
    _pact_session(world, FIRST_LIFE_SID, team="session-a1a1a1a1",
                  key=life_key, born="clear", born_at=_CLEAR_AT)
    assert _owned(SECOND_TEAM) is held
    assert _team_name() == (OLD_TEAM if held else SECOND_TEAM)


def test_the_clear_births_are_read_once_per_census(tmp_path, monkeypatch):
    """Two candidate teams, each led by an ended session: the census builds
    the clear births once and checks both teams' owners against them."""
    import shared.pact_context as ctx_module
    from fixtures.team_split import seed_team
    world = _aged_sibling_world(tmp_path, monkeypatch)
    seed_team(world.root, THIRD_TEAM, lead_sid=THIRD_SID,
              lead_cwd=str(world.project_dir))
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  ended_at=_ONE_SECOND_EARLIER)
    _pact_session(world, THIRD_SID, team=THIRD_TEAM, ended=True,
                  ended_at=_ONE_SECOND_EARLIER)
    calls = []
    real = ctx_module._clear_births
    monkeypatch.setattr(ctx_module, "_clear_births",
                        lambda *a: calls.append(a) or real(*a))
    reset_context_module()
    ctx_module.init({"session_id": OLD_SID})
    names = {name for name, _, _ in ctx_module._resume_census_candidates(
        world.root / "teams", OLD_SID)}
    assert {SECOND_TEAM, THIRD_TEAM} <= names
    assert len(calls) == 1


def test_a_journal_with_no_clear_birth_is_never_tail_read(tmp_path,
                                                          monkeypatch):
    """With an ended leader, a live session born by a startup is decided by
    its first line alone; a clear-born one within the window needs its
    liveness, a tail read."""
    import shared.pact_context as ctx_module
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  ended_at=_ONE_SECOND_EARLIER)
    _pact_session(world, NEAR_SID, team="x", born="startup", born_at=_CLEAR_AT)
    _pact_session(world, SIBLING_CLEAR_SID, team="x", born="clear",
                  born_at=_CLEAR_AT)
    tail_reads = []
    real = ctx_module._last_lifecycle_event
    monkeypatch.setattr(ctx_module, "_last_lifecycle_event",
                        lambda journal: tail_reads.append(journal.parent.name)
                        or real(journal))
    assert _owned(SECOND_TEAM) is True
    assert NEAR_SID not in tail_reads
    assert SIBLING_CLEAR_SID in tail_reads


def test_an_ended_clear_born_session_is_not_a_successor(tmp_path,
                                                       monkeypatch):
    """Control: the session the /clear started has ended too, so nothing
    live holds the team and the census picks it."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, OWN_KEY)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  ended_at=_ONE_SECOND_EARLIER)
    _pact_session(world, SIBLING_CLEAR_SID, team="session-b2b2b2b2",
                  born="clear", born_at=_CLEAR_AT, ended=True,
                  ended_at=_AN_HOUR_LATER)
    assert _team_name() == SECOND_TEAM


def test_this_session_s_own_clear_birth_is_never_a_successor(tmp_path,
                                                             monkeypatch):
    """Control: this session itself was born by a /clear one second after
    the leader ended; it is ours, so it never holds the leader's team."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    write_journal(world.session_dir, [
        {"type": "session_start", "session_id": OLD_SID, "ts": _CLEAR_AT,
         "project_dir": str(world.project_dir), "source": "clear"},
        "task_metadata_snapshot",
    ])
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, ended=True,
                  ended_at=_ONE_SECOND_EARLIER)
    assert _team_name() == SECOND_TEAM


# -- a lead that reads no key resolves with the key it recorded -----------------


def _compact(monkeypatch, world):
    import session_init
    from fixtures.role_frames import captured_compaction_lead_sessionstart
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=OLD_SID, source="compact",
                 cwd=str(world.project_dir))
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit):
            session_init.main()
    reset_context_module()
    return json.loads(world.context_path.read_text(encoding="utf-8"))


def test_a_keyless_compaction_resolves_with_the_recorded_key(tmp_path,
                                                            monkeypatch):
    """A compaction whose CLAUDE_PID is not the hook's parent computes no
    key. The resolver uses the key this session recorded, so a live earlier
    life of the same process stays ours and its team is adopted."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, OWN_KEY)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, key=OWN_KEY)
    record = _compact(monkeypatch, world)
    assert record["team_name"] == SECOND_TEAM
    assert record["process_key"] == OWN_KEY


def test_a_keyless_compaction_with_another_recorded_key_keeps_its_team(
        tmp_path, monkeypatch):
    """Control: the live session records a different key, so it is not
    ours and its team stays excluded."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, OWN_KEY)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, key="7:sib")
    assert _compact(monkeypatch, world)["team_name"] == OLD_TEAM


# -- after a resume, only this process's live key is ours -----------------------

NEW_PROCESS_KEY = "5151:Tue Oct 20 09:00:00 2026"


def _resume(monkeypatch, world, key):
    """A resume SessionStart whose lead frame reads ``key`` as this claude
    process's key."""
    import session_init
    from fixtures.role_frames import captured_lead_sessionstart_qualified
    frame = captured_lead_sessionstart_qualified()
    frame.update(session_id=OLD_SID, source="resume",
                 cwd=str(world.project_dir))
    monkeypatch.setattr(session_init, "current_process_key", lambda: key)
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit):
            session_init.main()
    reset_context_module()
    return json.loads(world.context_path.read_text(encoding="utf-8"))


def test_a_crashed_life_of_the_resumed_process_is_another_session(
        tmp_path, monkeypatch):
    """Before the resume, an earlier /clear life of this lineage recorded
    the old process's key, leads the sibling team and never journalled an
    end. The resumed process has a new key, so that life is not ours: it
    reads as live and its team stays excluded. The context then records the
    new key."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, OWN_KEY)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, key=OWN_KEY,
                  born="clear")
    record = _resume(monkeypatch, world, NEW_PROCESS_KEY)
    assert record["team_name"] == OLD_TEAM
    assert record["process_key"] == NEW_PROCESS_KEY


def test_a_resume_that_reads_the_recorded_key_adopts_that_life_s_team(
        tmp_path, monkeypatch):
    """Control: a resume whose process key equals the recorded one treats
    the same life as an earlier life of this process and adopts its team."""
    world = _aged_sibling_world(tmp_path, monkeypatch)
    _set_own_key(world, OWN_KEY)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM, key=OWN_KEY,
                  born="clear")
    assert _resume(monkeypatch, world, OWN_KEY)["team_name"] == SECOND_TEAM


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
    """The description reads the census's own list, so a team another live
    PACT session owns appears in neither."""
    world = split_world(tmp_path, monkeypatch, live_team=None,
                        second_corroborated=True)
    _pact_session(world, SECOND_SID, team=SECOND_TEAM)
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
