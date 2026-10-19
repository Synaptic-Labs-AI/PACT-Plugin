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

In-process rows run the hooks' main() with nothing stubbed: CLAUDE_PID is set
to this test process's real parent pid, so the cross-check and ps run for
real. Only the PID-reuse row writes a made-up start time. The subprocess row
runs the hooks as direct children of this test process.
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
    config_root,
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

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def _start_time(pid):
    """The process start time exactly as current_process_key() reads it."""
    out = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
        text=True, env={**os.environ, "LC_ALL": "C"}, check=True,
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
    """The measured world just before a /clear, in a process whose key is
    the test process's parent's: the previous session recorded PREV_TEAM and
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
    monkeypatch.setenv("CLAUDE_PID", str(os.getppid()))
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
    _record_key(prev_dir, _key_of(os.getppid()) if prev_key is None else prev_key)
    write_journal(prev_dir, [
        {"type": "session_start", "session_id": PREV_SID,
         "project_dir": str(project), "source": "startup"},
        "task_metadata_snapshot",
    ])
    write_session_block(PREV_SID, PREV_TEAM, prev_dir, plugin)
    reset_context_module()
    return SimpleNamespace(
        tmp=tmp_path, root=root, project=project, plugin=plugin,
        prev_dir=prev_dir, sessions=prev_dir.parent,
        claude_md=project / ".claude" / "CLAUDE.md",
    )


def _run_main(monkeypatch, world, module, frame):
    monkeypatch.chdir(world.project)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        try:
            module.main()
        except SystemExit:
            pass
    reset_context_module()


def _start(monkeypatch, world, sid=CLEAR_SID, source="clear"):
    import session_init
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=sid, source=source, cwd=str(world.project))
    _run_main(monkeypatch, world, session_init, frame)


def _prompt(monkeypatch, world, sid=CLEAR_SID):
    import bootstrap_marker_writer
    frame = captured_lead_userpromptsubmit_qualified()
    frame.update(session_id=sid, cwd=str(world.project))
    _run_main(monkeypatch, world, bootstrap_marker_writer, frame)


def _recorded(world, sid=CLEAR_SID):
    return json.loads(
        (world.sessions / sid / "pact-session-context.json").read_text(
            encoding="utf-8")
    )


# -- the binding ---------------------------------------------------------------


def test_a_clear_records_the_team_its_process_was_on(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _start(monkeypatch, world)
    record = _recorded(world)
    assert record["team_name"] == PREV_TEAM
    assert record["process_key"] == _key_of(os.getppid())
    assert team_line(world.claude_md) == f"- Team: `{PREV_TEAM}`"


def test_the_first_prompt_after_a_clear_keeps_the_team_and_writes_the_marker(
        tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _start(monkeypatch, world)
    _prompt(monkeypatch, world)
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
    _record_key(clear_dir, _key_of(os.getppid()))
    write_journal(clear_dir, [{
        "type": "session_start", "session_id": CLEAR_SID,
        "project_dir": str(world.project), "source": "clear",
    }])
    _prompt(monkeypatch, world)
    record = _recorded(world)
    assert record["team_name"] == PREV_TEAM
    # The write-back keeps the key the session recorded.
    assert record["process_key"] == _key_of(os.getppid())


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
    _record_key(older_dir, _key_of(os.getppid()))
    _start(monkeypatch, world)
    assert _recorded(world)["team_name"] == PREV_TEAM


def test_five_clears_in_one_process_all_keep_the_team(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    sids = [f"{n}{n}{n}{n}{n}{n}{n}{n}-0000-4000-8000-00000000000{n}"
            for n in range(1, 6)]
    for sid in sids:
        _start(monkeypatch, world, sid=sid)
        assert _recorded(world, sid)["team_name"] == PREV_TEAM, sid
    assert team_line(world.claude_md) == f"- Team: `{PREV_TEAM}`"


# -- fail-safe controls: the computed default, never a guess -------------------


def test_a_concurrent_session_of_the_same_project_is_never_adopted(
        tmp_path, monkeypatch):
    """The previous record belongs to another claude process (another key),
    even though it is the newest session dir of the project and its team
    config is readable."""
    world = _clear_world(tmp_path, monkeypatch, prev_key="424242:Mon Jan  5 09:00:00 2026")
    _start(monkeypatch, world)
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
    _start(monkeypatch, world)
    assert _recorded(world)["team_name"] == PREV_TEAM


def test_a_reused_pid_with_another_start_time_is_never_adopted(
        tmp_path, monkeypatch):
    """Same pid as this process, a different start time: an earlier process
    that held the pid. The start time here is written, not measured."""
    world = _clear_world(
        tmp_path, monkeypatch,
        prev_key=f"{os.getppid()}:Thu Jan  1 00:00:00 1970",
    )
    _start(monkeypatch, world)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_missing_claude_pid_keeps_the_default(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    monkeypatch.delenv("CLAUDE_PID")
    _start(monkeypatch, world)
    record = _recorded(world)
    assert record["team_name"] == COMPUTED_TEAM
    assert record["process_key"] == ""


def test_a_claude_pid_that_is_not_the_parent_keeps_the_default(
        tmp_path, monkeypatch):
    """A teammate's hook env has been measured carrying another process's
    CLAUDE_PID: the key needs it to agree with the hook's real parent."""
    world = _clear_world(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
    _start(monkeypatch, world)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_predecessor_team_without_config_keeps_the_default(tmp_path,
                                                            monkeypatch):
    world = _clear_world(tmp_path, monkeypatch, team_config=False)
    _start(monkeypatch, world)
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
    _start(monkeypatch, world)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_resume_start_disarms_the_carry_over(tmp_path, monkeypatch):
    """A clear-born dir later resumed (a new process, a resume start): its
    write-back no longer follows the clear."""
    world = _clear_world(tmp_path, monkeypatch)
    clear_dir = write_context_file(
        CLEAR_SID, COMPUTED_TEAM, world.project, world.plugin).parent
    _record_key(clear_dir, _key_of(os.getppid()))
    write_journal(clear_dir, [
        {"type": "session_start", "session_id": CLEAR_SID,
         "project_dir": str(world.project), "source": "clear"},
        {"type": "session_start", "session_id": CLEAR_SID,
         "project_dir": str(world.project), "source": "resume"},
    ])
    _prompt(monkeypatch, world)
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_startup_never_follows_the_process_key(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _start(monkeypatch, world, source="startup")
    assert _recorded(world)["team_name"] == COMPUTED_TEAM


def test_a_teammate_frame_never_binds_to_the_lead_s_process(tmp_path,
                                                           monkeypatch):
    """A non-lead SessionStart computes no key, so even with the lead's
    process key on record it resolves the computed team."""
    import session_init
    import shared.pact_context as ctx_module
    world = _clear_world(tmp_path, monkeypatch)
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


# -- through real processes ---------------------------------------------------


def test_a_clear_carries_the_team_through_real_hook_processes(tmp_path,
                                                             monkeypatch):
    """The hooks run as direct children of this test process, which is the
    process the key names."""
    world = _clear_world(tmp_path, monkeypatch, prev_key=_key_of(os.getpid()))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(HOME=str(world.tmp), CLAUDE_PROJECT_DIR=str(world.project),
               CLAUDE_PLUGIN_ROOT=str(world.plugin), CLAUDE_PID=str(os.getpid()))
    start = captured_compaction_lead_sessionstart()
    start.update(session_id=CLEAR_SID, source="clear", cwd=str(world.project))
    prompt = captured_lead_userpromptsubmit_qualified()
    prompt.update(session_id=CLEAR_SID, cwd=str(world.project))
    for hook, frame in (("session_init.py", start),
                        ("bootstrap_marker_writer.py", prompt)):
        proc = subprocess.run(
            [sys.executable, str(_HOOKS / hook)], input=json.dumps(frame),
            capture_output=True, text=True, env=env, cwd=str(world.project),
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
    record = _recorded(world)
    assert record["team_name"] == PREV_TEAM
    assert record["process_key"] == _key_of(os.getpid())
    assert team_line(world.claude_md) == f"- Team: `{PREV_TEAM}`"
    assert (world.sessions / CLEAR_SID / "bootstrap-complete").is_file()
