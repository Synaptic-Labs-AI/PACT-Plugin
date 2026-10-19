"""
The lead can realign the session's team with one command.

When the census cannot decide (two live teams both match), the sanctioned
recovery is a lead-run command:
``cd <plugin>/hooks && python3 -m shared.realign_team --team <name>``.
It writes the context file and the project CLAUDE.md ``- Team:`` line, and
journals the move. It refuses, with exit code 2 and a reason on stderr and
without writing anything, any team it cannot corroborate as this session's:
no lead context for the calling session, a path-unsafe name, no config, a
lead entry that is not this team's, or a lead that runs somewhere else.

Each cell runs the command as a real subprocess against a tmp config root.
Each refusal cell is built so that only its own check can refuse: the other
checks would all pass.

Counter-tests, measured by source-only revert of each check in a scratch copy
(cardinality of this file's 19-case run): removing the lead-context check,
the path-safety check, the lead-entry suffix check, or the members-is-a-list
check -> 1 failed each; removing the cwd check -> 2 failed; comparing the
cwd without realpath -> 1 failed (the symlink cell); restoring the command's
own cwd as an accepted anchor -> 1 failed; skipping the CLAUDE.md write, or
its session_start precondition -> 1 failed each; skipping the journal event
-> 1 failed; rewriting on a no-op realign -> 1 failed.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.team_split import (
    FOREIGN_TEAM,
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    TMUX_SID,
    read_journal,
    saved_team_name,
    seed_team,
    split_world,
    stat_snapshot,
    team_line,
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
    world = _undecided(tmp_path, monkeypatch)
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
    assert result.returncode == 2, (result.returncode, result.stderr)
    assert result.stderr.strip().startswith("realign refused: "), result.stderr
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
