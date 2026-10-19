"""
The lead can realign the session's team with one command.

When the census cannot decide (two live teams both match), the sanctioned
recovery is a lead-run command:
``cd <plugin>/hooks && python3 -m shared.realign_team --team <name>``.
It writes the context file and the project CLAUDE.md ``- Team:`` line, and
journals the move. It refuses, with exit code 3 and a reason on stderr and
without writing anything, any team it cannot corroborate as this session's:
no lead context for the calling session, a path-unsafe name, a config that
names this session as the lead of another team, a team another PACT session
owns, no config, a lead entry that is not this team's, or a lead that runs
somewhere else. A usage error exits 2, which argparse owns.

Each cell runs the command as a real subprocess against a tmp config root.
Each refusal cell is built so that only its own check can refuse: the other
checks would all pass.

Counter-tests, measured by source-only revert of each check in a scratch copy
(cardinality of this file's 31-case run): removing the lead-context check,
the path-safety check, the lead-entry suffix check, the members-is-a-list
check, the identity-match check, or the other-session ownership check -> 1
failed each; removing the cwd check -> 2 failed; comparing the cwd without
realpath -> 1 failed (the symlink cell); skipping the CLAUDE.md write -> 3
failed; dropping its session_start precondition -> 1 failed; comparing names
raw -> 1 failed; returning before the CLAUDE.md write on the 'already' path
-> 1 failed; dropping the kept started_at -> 1 failed; dropping the kept
Started line -> 2 failed; writing a process key from this command -> 1
failed; replacing the journal append with a fixed outcome -> 2 failed;
a refusal exiting 2 -> 18 failed; dropping the lead-only sentence -> 19
failed; restoring pact_context.py to the version without the /clear
successor rule -> 1 failed (the keyless-sibling refusal).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.team_split import (
    FOREIGN_TEAM,
    NEW_SID,
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    SECOND_SID,
    SECOND_TEAM,
    TMUX_SID,
    read_journal,
    saved_team_name,
    seed_team,
    split_world,
    stat_snapshot,
    team_line,
    write_context_file,
    write_journal,
)

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def _realign(world, team, *, session_id=OLD_SID, cwd=None):
    env = dict(os.environ)
    env.update(
        HOME=str(world.tmp),
        CLAUDE_CONFIG_DIR=str(world.root),
        CLAUDE_PROJECT_DIR=str(world.project_dir),
        CLAUDE_CODE_SESSION_ID=session_id,
    )
    return subprocess.run(
        [sys.executable, "-m", "shared.realign_team", "--team", team],
        cwd=str(_HOOKS), env=env, capture_output=True, text=True, timeout=60,
    )


_LIVED = ("task_metadata_snapshot", "session_end")


def _undecided(tmp_path, monkeypatch, *, started=True, **kw):
    """Two live teams match, so only the lead can say which is right. A
    mid-session lead's journal holds a session_start, which the CLAUDE.md
    half of the write waits for."""
    journal = _LIVED + (({"type": "session_start", "session_id": OLD_SID,
                          "project_dir": str(tmp_path / "project"),
                          "source": "resume"},) if started else ())
    return split_world(tmp_path, monkeypatch, second_corroborated=True,
                       claude_md=True, journal=journal, **kw)


def test_without_a_session_start_only_the_record_is_written(tmp_path,
                                                            monkeypatch):
    """Before this session journaled its start, the command moves the record
    but leaves the CLAUDE.md block alone, as the prompt-time write-back does."""
    world = _undecided(tmp_path, monkeypatch, started=False)
    line_before = team_line(world.claude_md)
    result = _realign(world, NEW_TEAM)
    assert result.returncode == 0, result.stderr
    assert saved_team_name(world) == NEW_TEAM
    assert team_line(world.claude_md) == line_before == f"- Team: `{OLD_TEAM}`"


def test_realigning_to_the_recorded_team_changes_nothing(tmp_path,
                                                         monkeypatch):
    """The recorded team is already the requested one: exit 0, no event, no
    rewrite."""
    world = _undecided(tmp_path, monkeypatch, saved_team=NEW_TEAM)
    before = stat_snapshot(world.context_path, world.claude_md)
    result = _realign(world, NEW_TEAM)
    assert result.returncode == 0, result.stderr
    assert stat_snapshot(world.context_path, world.claude_md) == before
    assert [e for e in read_journal(world.session_dir)
            if e.get("type") == "team_realigned"] == []


def test_realign_writes_the_record_the_team_line_and_the_journal(
        tmp_path, monkeypatch):
    """The earlier team left a task store, so the move is a split: the event
    names both teams."""
    world = _undecided(tmp_path, monkeypatch,
                       old_tasks=({"id": "7", "status": "in_progress"},))
    result = _realign(world, NEW_TEAM)
    assert result.returncode == 0, result.stderr
    assert saved_team_name(world) == NEW_TEAM
    assert team_line(world.claude_md) == f"- Team: `{NEW_TEAM}`"
    moves = [e for e in read_journal(world.session_dir)
             if e.get("type") == "team_realigned"]
    assert [(m["from"], m["to"]) for m in moves] == [(OLD_TEAM, NEW_TEAM)]


def test_realign_accepts_a_lead_cwd_reached_through_a_symlink(tmp_path,
                                                              monkeypatch):
    """The lead's recorded cwd is a symlink to the project dir: both sides
    are resolved before the compare, so the team is this session's."""
    world = _undecided(tmp_path, monkeypatch)
    link = tmp_path / "project-link"
    link.symlink_to(world.project_dir, target_is_directory=True)
    seed_team(world.root, "session-11110000", lead_sid="11110000-aaaa-4bbb-8ccc-dddddddddddd",
              lead_cwd=str(link))
    result = _realign(world, "session-11110000")
    assert result.returncode == 0, result.stderr
    assert saved_team_name(world) == "session-11110000"


def _refusal(world, team, **kw):
    before = stat_snapshot(world.context_path, world.claude_md)
    result = _realign(world, team, **kw)
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert result.stderr.strip().startswith("realign refused: "), result.stderr
    assert "Only the lead runs this command" in result.stderr, result.stderr
    assert stat_snapshot(world.context_path, world.claude_md) == before
    assert [e for e in read_journal(world.session_dir)
            if e.get("type") == "team_realigned"] == []
    return result


def test_realign_refuses_a_session_without_a_lead_context(tmp_path,
                                                          monkeypatch):
    """The calling session's context file records ANOTHER session's id: it is
    not this session's lead context. Everything else in it is valid (its
    project_dir matches the requested team's lead), so every later check
    would pass: only the lead-context check can refuse it."""
    world = _undecided(tmp_path, monkeypatch)
    foreign_context = world.session_dir.parent / TMUX_SID / "pact-session-context.json"
    foreign_context.parent.mkdir(parents=True)
    foreign_context.write_text(world.context_path.read_text(encoding="utf-8"),
                               encoding="utf-8")
    _refusal(world, NEW_TEAM, session_id=TMUX_SID)


def test_realign_refuses_a_lead_running_in_the_commands_own_cwd(tmp_path,
                                                                monkeypatch):
    """The command runs from the plugin's hooks dir. A team whose lead runs
    there is not this session's: only the recorded project dir corroborates."""
    world = _undecided(tmp_path, monkeypatch)
    seed_team(world.root, "session-22220000",
              lead_sid="22220000-aaaa-4bbb-8ccc-dddddddddddd",
              lead_cwd=str(_HOOKS.resolve()))
    result = _refusal(world, "session-22220000")
    assert "cwd" in result.stderr


@pytest.mark.parametrize("members", [5, "x", {"a": 1}, None, [1, "s"], []],
                         ids=["int", "str", "dict", "null", "mixed-list", "empty"])
def test_realign_refuses_a_malformed_members_field(tmp_path, monkeypatch,
                                                   members):
    """A members field that is not a list of member objects is refused with a
    reason, never a traceback. Only the non-iterable shape (an int) reaches a
    raise without the list check; the other shapes are refused by the lead
    lookup anyway and stand as controls."""
    world = _undecided(tmp_path, monkeypatch)
    cfg_path = world.teams / NEW_TEAM / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["members"] = members
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    _refusal(world, NEW_TEAM)


@pytest.mark.parametrize("name", ["../evil", "session-a/b", ""],
                         ids=["parent", "slash", "empty"])
def test_realign_refuses_a_path_unsafe_name(tmp_path, monkeypatch, name):
    world = _undecided(tmp_path, monkeypatch)
    _refusal(world, name)


def test_realign_refuses_a_nested_name_even_when_its_config_corroborates(
        tmp_path, monkeypatch):
    """A two-segment name whose config exists and corroborates in every other
    respect: only the path-safety check can refuse it."""
    world = _undecided(tmp_path, monkeypatch)
    seed_team(world.root, "nested/session-33330000",
              lead_sid="33330000-aaaa-4bbb-8ccc-dddddddddddd",
              lead_cwd=str(world.project_dir))
    _refusal(world, "nested/session-33330000")


def test_realign_refuses_a_team_without_config(tmp_path, monkeypatch):
    world = _undecided(tmp_path, monkeypatch)
    (world.tasks / "session-nocfg000").mkdir()
    _refusal(world, "session-nocfg000")


def test_realign_refuses_a_lead_entry_of_another_team(tmp_path, monkeypatch):
    """The config's lead entry is keyed to a different team dir."""
    world = _undecided(tmp_path, monkeypatch)
    cfg_path = world.teams / NEW_TEAM / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["leadAgentId"] = "team-lead@session-other000"
    cfg["members"][0]["agentId"] = "team-lead@session-other000"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    _refusal(world, NEW_TEAM)


def test_realign_refuses_a_team_whose_lead_runs_elsewhere(tmp_path,
                                                          monkeypatch):
    world = split_world(tmp_path, monkeypatch, second_corroborated=True,
                        foreign="foreign-cwd", claude_md=True)
    _refusal(world, FOREIGN_TEAM)


# --- Exit codes, casefolded names, the CLAUDE.md repair, the kept start ----


def test_a_usage_error_exits_2_and_a_refusal_exits_3(tmp_path, monkeypatch):
    """A missing --team is argparse's usage error (2); a refused team is 3.
    The two codes must differ so the caller can tell a typo from a refusal."""
    world = _undecided(tmp_path, monkeypatch)
    env = dict(os.environ, HOME=str(world.tmp), CLAUDE_CONFIG_DIR=str(world.root),
               CLAUDE_PROJECT_DIR=str(world.project_dir),
               CLAUDE_CODE_SESSION_ID=OLD_SID)
    usage = subprocess.run(
        [sys.executable, "-m", "shared.realign_team"],
        cwd=str(_HOOKS), env=env, capture_output=True, text=True, timeout=60,
    )
    assert usage.returncode == 2, usage.stderr
    assert "realign refused" not in usage.stderr
    _refusal(world, "session-nocfg000")


def test_the_help_text_says_lead_only(tmp_path, monkeypatch):
    world = _undecided(tmp_path, monkeypatch)
    env = dict(os.environ, HOME=str(world.tmp), CLAUDE_CONFIG_DIR=str(world.root))
    result = subprocess.run(
        [sys.executable, "-m", "shared.realign_team", "--help"],
        cwd=str(_HOOKS), env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Only the lead runs this command" in " ".join(result.stdout.split())


def test_a_recorded_name_differing_only_in_case_is_already_recorded(
        tmp_path, monkeypatch):
    """The record names the requested team in other case: nothing moves and
    no team_realigned is journalled. Kill: compare raw -> a spurious move."""
    world = _undecided(tmp_path, monkeypatch, saved_team=NEW_TEAM.upper())
    result = _realign(world, NEW_TEAM)
    assert result.returncode == 0, result.stderr
    assert "already" in result.stdout
    assert saved_team_name(world) == NEW_TEAM.upper()
    assert [e for e in read_journal(world.session_dir)
            if e.get("type") == "team_realigned"] == []


def _set_team_line(claude_md, team):
    text = claude_md.read_text(encoding="utf-8")
    lines = [f"- Team: `{team}`" if line.startswith("- Team:") else line
             for line in text.splitlines()]
    claude_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


_EARLIER_START = "- Started: 2026-10-19 07:00:00 UTC"


def _set_started_line(claude_md):
    """Put the Started line in the past, so a rewrite to now cannot match it
    by landing in the same second."""
    text = claude_md.read_text(encoding="utf-8")
    lines = [_EARLIER_START if line.startswith("- Started:") else line
             for line in text.splitlines()]
    claude_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _started_line(claude_md):
    return next(line for line in claude_md.read_text(encoding="utf-8").splitlines()
                if line.startswith("- Started:"))


def test_an_already_recorded_team_repairs_a_stale_team_line(tmp_path,
                                                            monkeypatch):
    """The record already names the team but the CLAUDE.md line is stale (an
    earlier run wrote the record before the journal held a session_start).
    A rerun writes the line, keeps the Started line, and journals no move.
    Kill: return before the CLAUDE.md write on the 'already' path."""
    world = _undecided(tmp_path, monkeypatch, saved_team=NEW_TEAM)
    _set_team_line(world.claude_md, OLD_TEAM)
    _set_started_line(world.claude_md)
    started = _started_line(world.claude_md)
    assert started == _EARLIER_START
    result = _realign(world, NEW_TEAM)
    assert result.returncode == 0, result.stderr
    assert team_line(world.claude_md) == f"- Team: `{NEW_TEAM}`"
    assert _started_line(world.claude_md) == started
    assert [e for e in read_journal(world.session_dir)
            if e.get("type") == "team_realigned"] == []


def test_a_realign_keeps_the_session_start(tmp_path, monkeypatch):
    """A move keeps the context's started_at and the CLAUDE.md Started line.
    Kill: build the context without started_at, or update the block without
    the read Started value -> both reset to now."""
    world = _undecided(tmp_path, monkeypatch)
    started_at = json.loads(world.context_path.read_text(encoding="utf-8"))["started_at"]
    _set_started_line(world.claude_md)
    started = _started_line(world.claude_md)
    assert started == _EARLIER_START
    result = _realign(world, NEW_TEAM)
    assert result.returncode == 0, result.stderr
    assert team_line(world.claude_md) == f"- Team: `{NEW_TEAM}`"
    assert json.loads(world.context_path.read_text(encoding="utf-8"))["started_at"] == started_at
    assert _started_line(world.claude_md) == started


# --- A team this session does not own ---------------------------------------


def test_realign_refuses_a_team_while_this_session_leads_another(
        tmp_path, monkeypatch):
    """A team config names this session as its lead (identity match), so the
    session's team is that one: any other team is refused, even one that
    corroborates in every other respect."""
    world = _undecided(tmp_path, monkeypatch, old_config=True)
    result = _refusal(world, NEW_TEAM)
    assert OLD_TEAM in result.stderr


def test_realign_accepts_the_team_this_session_leads(tmp_path, monkeypatch):
    """Control: the team identity match names is accepted."""
    world = _undecided(tmp_path, monkeypatch, old_config=True,
                       saved_team=NEW_TEAM)
    result = _realign(world, OLD_TEAM)
    assert result.returncode == 0, result.stderr
    assert saved_team_name(world) == OLD_TEAM


def test_a_move_from_a_team_that_never_existed_journals_nothing(
        tmp_path, monkeypatch):
    """The recorded team has neither a config nor a task store: the record
    moves, no team_realigned is journalled, and the command says there is
    nothing to carry across. Control for the split case above."""
    world = _undecided(tmp_path, monkeypatch)
    result = _realign(world, NEW_TEAM)
    assert result.returncode == 0, result.stderr
    assert saved_team_name(world) == NEW_TEAM
    assert "nothing to carry across" in result.stdout
    assert [e for e in read_journal(world.session_dir)
            if e.get("type") == "team_realigned"] == []


def test_realign_refuses_a_team_another_pact_session_owns(tmp_path,
                                                          monkeypatch):
    """The requested team's lead session has its own PACT context: that team
    is the other session's, however well it corroborates. Kill: drop the
    ownership check -> the move lands. Control: the move above, where the
    team's lead session has no PACT context."""
    world = _undecided(tmp_path, monkeypatch)
    other = world.session_dir.parent / NEW_SID / "pact-session-context.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"session_id": NEW_SID}), encoding="utf-8")
    result = _refusal(world, NEW_TEAM)
    assert "another PACT session" in result.stderr


def test_a_realign_keeps_the_recorded_process_key(tmp_path, monkeypatch):
    """The command runs from a shell, which has no claude process key of its
    own: the key a lead hook frame recorded survives the move. Kill: pass a
    computed or empty key to the context write."""
    world = _undecided(tmp_path, monkeypatch)
    context = json.loads(world.context_path.read_text(encoding="utf-8"))
    context["process_key"] = "1262:Mon Oct 19 18:36:20 2026"
    world.context_path.write_text(json.dumps(context), encoding="utf-8")
    result = _realign(world, NEW_TEAM)
    assert result.returncode == 0, result.stderr
    after = json.loads(world.context_path.read_text(encoding="utf-8"))
    assert after["team_name"] == NEW_TEAM
    assert after["process_key"] == "1262:Mon Oct 19 18:36:20 2026"


# --- A team whose leader ended in another session's /clear ------------------

_SIBLING_CLEAR_SID = "b2b2b2b2-1111-4222-8333-444455556666"


def _keyless_sibling_clear(tmp_path, monkeypatch, *, born_at):
    """SECOND_TEAM's leader ended at 15:34:19; a keyless session born by a
    /clear at ``born_at`` is live and records only its own computed team.
    This session has a process key, so that clear was not this session's."""
    world = split_world(tmp_path, monkeypatch, live_store_aged=True,
                        second_corroborated=True)
    write_context_file(OLD_SID, OLD_TEAM, world.project_dir, world.plugin_root,
                       process_key="4242:Mon Oct 19 15:00:00 2026")
    slug_dir = world.session_dir.parent
    for sid, team, events in (
        (SECOND_SID, SECOND_TEAM, [
            {"type": "session_start", "session_id": SECOND_SID,
             "project_dir": str(world.project_dir), "source": "startup",
             "ts": "2026-10-19T15:00:00Z"},
            {"type": "session_end", "ts": "2026-10-19T15:34:19Z"}]),
        (_SIBLING_CLEAR_SID, "session-b2b2b2b2", [
            {"type": "session_start", "session_id": _SIBLING_CLEAR_SID,
             "project_dir": str(world.project_dir), "source": "clear",
             "ts": born_at}]),
    ):
        write_context_file(sid, team, world.project_dir, world.plugin_root,
                           process_key="")
        write_journal(slug_dir / sid, events)
    return world


def test_realign_refuses_a_team_whose_leader_ended_in_another_session_s_clear(
        tmp_path, monkeypatch):
    """The leader ended one second before a live keyless session was born by
    a /clear: that successor still holds the team, so it is not this
    session's. Kill: drop the successor rule from the ownership check -> the
    move lands."""
    world = _keyless_sibling_clear(tmp_path, monkeypatch,
                                   born_at="2026-10-19T15:34:20Z")
    result = _refusal(world, SECOND_TEAM)
    assert "another PACT session owns this team" in result.stderr


def test_realign_accepts_that_team_when_the_clear_came_long_after(
        tmp_path, monkeypatch):
    """Control: the live session's /clear came an hour after the leader
    ended, so it is not the leader's successor and the team is free."""
    world = _keyless_sibling_clear(tmp_path, monkeypatch,
                                   born_at="2026-10-19T16:34:20Z")
    result = _realign(world, SECOND_TEAM)
    assert result.returncode == 0, result.stderr
    assert saved_team_name(world) == SECOND_TEAM
