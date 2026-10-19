"""
A /clear keeps the session on the team it was on.

/clear gives every hook frame a NEW session id and a new session dir, in the
same claude process, while the platform keeps the session on its existing
team, whose config still names the PREVIOUS session's id. Each lead SessionStart
records its claude process's key (the hook's parent pid, which CLAUDE_PID must
agree with, plus that process's start time) in the session context. A clear
SessionStart, and the prompt-time write-back of that same clear-born life,
adopt the team recorded by the newest other session dir of the project that
records the same key, when that team's config is readable. Anything else keeps
the computed default.

The hooks run as direct children of this test process with nothing stubbed:
CLAUDE_PID names this process, which is their real parent whoever launched
pytest, so the cross-check and ps run for real. Only the PID-reuse row writes
a made-up start time. The teammate row runs session_init in process, since a
non-lead frame computes no key.
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fixtures.role_frames import (
    captured_compaction_lead_sessionstart,
    captured_lead_userpromptsubmit_qualified,
)
from fixtures.team_split import (
    HOOKS_DIR,
    config_root,
    hook_env,
    reset_context_module,
    seed_plugin,
    seed_team,
    team_line,
    write_context_file,
    write_journal,
    write_session_block,
)

# The session before the /clear, and the platform team it is on.
PREV_SID = "5578b689-676f-4823-b1c5-a5a4c0a7f300"
PREV_TEAM = "session-5578b689"
# The session id every hook frame carries after the /clear.
CLEAR_SID = "e62f859f-4ddb-471b-a5ef-dbc7fc6d72b3"
# The team PACT computes from the new id: no such team exists.
COMPUTED_TEAM = "session-e62f859f"
# Another live PACT session of the same project, in another claude process.
CONCURRENT_SID = "c0c0c0c0-1111-4222-8333-444455556666"
CONCURRENT_TEAM = "session-c0c0c0c0"
# An earlier session of this same process, whose dir name sorts after the
# predecessor's.
OLDER_SID = "ffff0000-1111-4222-8333-444455556666"

# A pid that is never a hook's parent here (launchd / init).
_NOT_THE_PARENT = "1"


def _start_time(pid):
    """The process start time exactly as current_process_key() reads it."""
    out = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
        text=True, env={**os.environ, "LC_ALL": "C", "TZ": "UTC"}, check=True,
    ).stdout
    return " ".join(out.split())


def _key_of(pid):
    return f"{pid}:{_start_time(pid)}"


def _record_key(session_dir, key):
    """Set the process_key the session's context file records."""
    path = session_dir / "pact-session-context.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["process_key"] = key
    path.write_text(json.dumps(data), encoding="utf-8")


def _clear_world(tmp_path, monkeypatch, *, prev_key=None, team_config=True):
    """The measured world just before a /clear, in a claude process that is
    this test process: the previous session recorded PREV_TEAM and
    ``prev_key`` (default: this process's key)."""
    root = config_root(tmp_path)
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    plugin = seed_plugin(tmp_path / "plugin")
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (root / "teams").mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    if team_config:
        seed_team(root, PREV_TEAM, lead_sid=PREV_SID, lead_cwd=str(project),
                  members=("secretary",))
    prev_dir = write_context_file(
        PREV_SID, PREV_TEAM, project, plugin,
        started_at="2026-10-19T15:00:00+00:00",
    ).parent
    _record_key(prev_dir, _key_of(os.getpid()) if prev_key is None else prev_key)
    write_journal(prev_dir, [
        {"type": "session_start", "session_id": PREV_SID,
         "project_dir": str(project), "source": "startup"},
        "task_metadata_snapshot",
    ])
    write_session_block(PREV_SID, PREV_TEAM, prev_dir, plugin)
    reset_context_module()
    return SimpleNamespace(
        tmp=tmp_path, root=root, project=project, project_dir=project,
        plugin=plugin,
        prev_dir=prev_dir, sessions=prev_dir.parent,
        claude_md=project / ".claude" / "CLAUDE.md",
    )


def _run_hook(world, hook, frame, **env):
    """Run hooks/<hook>.py as a child of this test process (hook_env: its
    CLAUDE_PID names this process). ``env`` overrides variables; None
    removes one."""
    child_env = hook_env(world)
    for name, value in env.items():
        if value is None:
            child_env.pop(name, None)
        else:
            child_env[name] = value
    proc = subprocess.run(
        [sys.executable, str(HOOKS_DIR / f"{hook}.py")],
        input=json.dumps(frame), capture_output=True, text=True,
        env=child_env, cwd=str(world.project), timeout=120,
    )
    assert proc.returncode == 0, (hook, proc.stderr[-2000:])


def _start(world, sid=CLEAR_SID, source="clear", **env):
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=sid, source=source, cwd=str(world.project))
    _run_hook(world, "session_init", frame, **env)


def _prompt(world, sid=CLEAR_SID):
    frame = captured_lead_userpromptsubmit_qualified()
    frame.update(session_id=sid, cwd=str(world.project))
    _run_hook(world, "bootstrap_marker_writer", frame)


def _recorded(world, sid=CLEAR_SID):
    return json.loads(
        (world.sessions / sid / "pact-session-context.json").read_text(
            encoding="utf-8")
    )


# -- the binding ---------------------------------------------------------------


def test_a_clear_records_the_team_its_process_was_on(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _start(world)
    record = _recorded(world)
    assert record["team_name"] == PREV_TEAM
    assert record["process_key"] == _key_of(os.getpid())
    assert team_line(world.claude_md) == f"- Team: `{PREV_TEAM}`"


def test_the_first_prompt_after_a_clear_keeps_the_team_and_writes_the_marker(
        tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _start(world)
    _prompt(world)
    assert _recorded(world)["team_name"] == PREV_TEAM
    assert (world.sessions / CLEAR_SID / "bootstrap-complete").is_file()


def test_a_prompt_repairs_a_clear_record_that_names_the_computed_team(
        tmp_path, monkeypatch):
    """The prompt-time write-back of the same clear-born life finds the team
    through the key the session recorded."""
    world = _clear_world(tmp_path, monkeypatch)
    # This session started after its predecessor and records the same key:
    # it must never count as its own predecessor.
    clear_dir = write_context_file(
        CLEAR_SID, COMPUTED_TEAM, world.project, world.plugin,
        started_at="2026-10-19T15:30:00+00:00").parent
    _record_key(clear_dir, _key_of(os.getpid()))
    write_journal(clear_dir, [{
        "type": "session_start", "session_id": CLEAR_SID,
        "project_dir": str(world.project), "source": "clear",
    }])
    _prompt(world)
    record = _recorded(world)
    assert record["team_name"] == PREV_TEAM
    # The write-back keeps the key the session recorded.
    assert record["process_key"] == _key_of(os.getpid())


def test_the_newest_earlier_session_of_the_process_wins(tmp_path,
                                                        monkeypatch):
    """Two earlier sessions of this process: an older one on another team
    whose dir name sorts last, and the newer predecessor. The clear follows
    the newer one."""
    world = _clear_world(tmp_path, monkeypatch)
    seed_team(world.root, CONCURRENT_TEAM, lead_sid=OLDER_SID,
              lead_cwd=str(world.project))
    older_dir = write_context_file(
        OLDER_SID, CONCURRENT_TEAM, world.project, world.plugin,
        started_at="2026-10-19T09:00:00+00:00",
    ).parent
    _record_key(older_dir, _key_of(os.getpid()))
    _start(world)
    assert _recorded(world)["team_name"] == PREV_TEAM


def test_five_clears_in_one_process_all_keep_the_team(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    sids = [f"{n}{n}{n}{n}{n}{n}{n}{n}-0000-4000-8000-00000000000{n}"
            for n in range(1, 6)]
    for sid in sids:
        _start(world, sid=sid)
        assert _recorded(world, sid)["team_name"] == PREV_TEAM, sid
    assert team_line(world.claude_md) == f"- Team: `{PREV_TEAM}`"


# -- fail-safe controls: the computed default, never a guess -------------------


def test_a_concurrent_session_of_the_same_project_is_never_adopted(
        tmp_path, monkeypatch):
    """The previous record belongs to another claude process (another key),
    even though it is the newest session dir of the project and its team
    config is readable."""
    world = _clear_world(tmp_path, monkeypatch, prev_key="424242:Mon Jan  5 09:00:00 2026")
    _start(world)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_newer_concurrent_session_never_outranks_this_process_s_own(
        tmp_path, monkeypatch):
    """A concurrent session of the same project started after this process's
    previous session and is on a real team: the clear still follows this
    process's own previous session."""
    world = _clear_world(tmp_path, monkeypatch)
    seed_team(world.root, CONCURRENT_TEAM, lead_sid=CONCURRENT_SID,
              lead_cwd=str(world.project))
    concurrent_dir = write_context_file(
        CONCURRENT_SID, CONCURRENT_TEAM, world.project, world.plugin,
        started_at="2026-10-19T16:00:00+00:00",
    ).parent
    _record_key(concurrent_dir, "424242:Mon Jan  5 09:00:00 2026")
    _start(world)
    assert _recorded(world)["team_name"] == PREV_TEAM


def test_a_reused_pid_with_another_start_time_is_never_adopted(
        tmp_path, monkeypatch):
    """Same pid as this process, a different start time: an earlier process
    that held the pid. The start time here is written, not measured."""
    world = _clear_world(
        tmp_path, monkeypatch,
        prev_key=f"{os.getpid()}:Thu Jan  1 00:00:00 1970",
    )
    _start(world)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_missing_claude_pid_keeps_the_default(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _start(world, CLAUDE_PID=None)
    record = _recorded(world)
    assert record["team_name"] == COMPUTED_TEAM
    assert record["process_key"] == ""


def test_a_claude_pid_that_is_not_the_parent_keeps_the_default(
        tmp_path, monkeypatch):
    """A teammate's hook env has been measured carrying another process's
    CLAUDE_PID: the key needs it to agree with the hook's real parent."""
    world = _clear_world(tmp_path, monkeypatch)
    _start(world, CLAUDE_PID=_NOT_THE_PARENT)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_predecessor_team_without_config_keeps_the_default(tmp_path,
                                                            monkeypatch):
    world = _clear_world(tmp_path, monkeypatch, team_config=False)
    _start(world)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_predecessor_dir_outside_pact_sessions_is_refused(tmp_path,
                                                          monkeypatch):
    """The only session dir with this process's key is a symlink to a dir
    outside the pact-sessions tree."""
    world = _clear_world(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere" / PREV_SID
    outside.parent.mkdir()
    world.prev_dir.rename(outside)
    (world.sessions / PREV_SID).symlink_to(outside, target_is_directory=True)
    _start(world)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_resume_start_disarms_the_carry_over(tmp_path, monkeypatch):
    """A clear-born dir later resumed (a new process, a resume start): its
    write-back no longer follows the clear."""
    world = _clear_world(tmp_path, monkeypatch)
    clear_dir = write_context_file(
        CLEAR_SID, COMPUTED_TEAM, world.project, world.plugin).parent
    _record_key(clear_dir, _key_of(os.getpid()))
    write_journal(clear_dir, [
        {"type": "session_start", "session_id": CLEAR_SID,
         "project_dir": str(world.project), "source": "clear"},
        {"type": "session_start", "session_id": CLEAR_SID,
         "project_dir": str(world.project), "source": "resume"},
    ])
    _prompt(world)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_startup_never_follows_the_process_key(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _start(world, source="startup")
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_teammate_frame_never_binds_to_the_lead_s_process(tmp_path,
                                                           monkeypatch):
    """A non-lead SessionStart computes no key, so even with the lead's
    process key on record it resolves the computed team. It runs in process
    to read the frame's in-memory context, with this test process standing
    in as the hook's parent, so a lead frame here would compute the
    recorded key however pytest was launched."""
    import session_init
    import shared.pact_context as ctx_module
    world = _clear_world(tmp_path, monkeypatch)
    monkeypatch.setattr(ctx_module.os, "getppid", os.getpid)
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=CLEAR_SID, source="clear", cwd=str(world.project),
                 agent_type="pact-backend-coder")
    monkeypatch.chdir(world.project)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        try:
            session_init.main()
        except SystemExit:
            pass
    assert ctx_module.get_pact_context()["team_name"] == COMPUTED_TEAM
    assert not (world.sessions / CLEAR_SID / "pact-session-context.json").exists()


# -- a key that cannot be read keeps the recorded one --------------------------


def test_a_compaction_that_reads_no_key_keeps_the_recorded_one(tmp_path,
                                                               monkeypatch):
    """A lead-shaped compaction whose CLAUDE_PID is not the hook's parent
    computes no key. It keeps the key the session recorded, so the next
    /clear of the same process still carries the team."""
    world = _clear_world(tmp_path, monkeypatch)
    key = _key_of(os.getpid())
    _start(world, sid=PREV_SID, source="compact", CLAUDE_PID=_NOT_THE_PARENT)
    assert _recorded(world, PREV_SID)["process_key"] == key
    _start(world)
    assert _recorded(world)["team_name"] == PREV_TEAM


# -- a /clear that read no key: recovery onto the session's own team ------------


def _end_predecessor(world):
    """The SessionEnd a /clear journals in the session it replaces."""
    write_journal(world.prev_dir, [
        {"type": "session_start", "session_id": PREV_SID,
         "project_dir": str(world.project), "source": "startup"},
        "task_metadata_snapshot",
        "session_end",
    ])


def _keyless_clear(monkeypatch, world):
    _start(world, CLAUDE_PID=None)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def _own_team_view(monkeypatch):
    """(census candidate names, owned-by-other, verb refusal) for PREV_TEAM
    as this clear-born session sees it."""
    import shared.pact_context as ctx_module
    from shared import realign_team
    reset_context_module()
    ctx_module.init({"session_id": CLEAR_SID})
    names = [name for name, corroborated, _ in
             ctx_module._resume_census_candidates(
                 config_root(Path.home()) / "teams", CLEAR_SID)
             if corroborated]
    owned = ctx_module.team_owned_by_other_pact_session(PREV_TEAM, CLEAR_SID)
    reset_context_module()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CLEAR_SID)
    refusal = realign_team.refusal_reason(PREV_TEAM)
    reset_context_module()
    return names, owned, refusal


def test_after_a_keyless_clear_the_ended_predecessor_s_team_is_ours(
        tmp_path, monkeypatch):
    """The predecessor ended at the /clear and leads the team this process
    is on. No live session owns it, so the census counts it and the realign
    verb accepts it."""
    world = _clear_world(tmp_path, monkeypatch)
    _end_predecessor(world)
    _keyless_clear(monkeypatch, world)
    assert _own_team_view(monkeypatch) == ([PREV_TEAM], False, "")


def test_after_a_keyless_clear_a_live_predecessor_s_team_is_refused(
        tmp_path, monkeypatch):
    """Control for the cell above: with no session_end the predecessor
    reads as live, and with no key it cannot be told from another session,
    so its team stays excluded."""
    world = _clear_world(tmp_path, monkeypatch)
    _keyless_clear(monkeypatch, world)
    assert _own_team_view(monkeypatch) == (
        [], True, "another PACT session owns this team")


# -- an explicit realign outranks the carry-over ---------------------------------


REALIGNED_TEAM = "session-7e7e7e7e"


def test_a_realign_after_a_clear_is_never_reverted_by_the_carry_over(
        tmp_path, monkeypatch):
    """A clear-born session carries the predecessor's team, then the lead
    realigns it to another live team with the verb. The next prompt's
    write-back keeps the realigned team: the carry-over runs only while the
    recorded team has no config."""
    world = _clear_world(tmp_path, monkeypatch)
    _start(world)
    assert _recorded(world)["team_name"] == PREV_TEAM
    seed_team(world.root, REALIGNED_TEAM,
              lead_sid="7e7e7e7e-1111-4222-8333-444455556666",
              lead_cwd=str(world.project))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(HOME=str(world.tmp), CLAUDE_PROJECT_DIR=str(world.project),
               CLAUDE_CODE_SESSION_ID=CLEAR_SID)
    proc = subprocess.run(
        [sys.executable, "-m", "shared.realign_team", "--team", REALIGNED_TEAM],
        capture_output=True, text=True, env=env, cwd=str(HOOKS_DIR), timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _recorded(world)["team_name"] == REALIGNED_TEAM
    _prompt(world)
    assert _recorded(world)["team_name"] == REALIGNED_TEAM
