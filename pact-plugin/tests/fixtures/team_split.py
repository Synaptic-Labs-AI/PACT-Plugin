"""
Team-split world builder: a session whose recorded team no longer matches the
team the platform is using.

After a resume the platform can put a session in a NEW team while every hook
frame keeps the ORIGINAL session id, and the original team's config is gone.
These helpers build that world on disk, under the per-test tmp tree the
conftest isolation already redirects to, through the REAL path derivations
(``CLAUDE_PROJECT_DIR`` + ``Path.home`` -> config root -> ``pact-sessions`` /
``teams`` / ``tasks``). Nothing here stubs a resolver, ``init``, or the context
path, so a test built on it exercises the seams the hooks use in production.

Per the thin-conftest rule these are direct-import helpers, not pytest
fixtures; nothing here is injected. The seeders generalise the ones in
test_resume_team_realign_1507.py and test_team_name_detect_align.py.
"""

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# The session id every hook frame carries. It never changes across a resume.
OLD_SID = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"
OLD_TEAM = "session-0001639f"
# The team the platform provisioned at the resume.
NEW_SID = "aaaa1111-2222-4333-8444-555566667777"
NEW_TEAM = "session-aaaa1111"
# A concurrently live PACT session of ANOTHER project (its lead runs elsewhere).
FOREIGN_SID = "cafe1234-5678-4abc-9def-0123456789ab"
FOREIGN_TEAM = "session-cafe1234"
FOREIGN_CWD = "/elsewhere/another/project"
# A second team that also corroborates as this session's (two broken resumes).
SECOND_SID = "beefbeef-1111-4222-8333-444455556666"
SECOND_TEAM = "session-beefbeef"
# A tmux teammate's own session id.
TMUX_SID = "ffff8888-bbbb-4ccc-9ddd-eeeeeeeeeeee"
# The session a /clear starts: a new id and a new session dir.
CLEAR_SID = "c1ea0000-1111-4222-8333-444455556666"
OTHER_PROCESS_KEY = "99999:Thu Jan  1 00:00:00 1970"

# Journal timestamps and the lead joinedAt values relative to them.
JOURNAL_TS = "2026-08-24T00:00:00Z"            # 1787529600 s
JOINED_AFTER_END_MS = 1787568000000             # 2026-08-24T10:40:00Z
JOINED_BEFORE_END_MS = 1787000000000            # 2026-08-17T20:53:20Z
# An mtime far outside the 900 s census recency window on any clock.
AGED_MTIME = 1

DEFAULT_JOURNAL = ("task_metadata_snapshot", "session_end")


def config_root(tmp_path: Path) -> Path:
    """The config root the conftest isolation resolves to."""
    return tmp_path / ".claude"


def seed_team(root: Path, name: str, *, lead_sid, corroborate=True,
              lead_cwd=None, joined_at_ms=JOINED_AFTER_END_MS, tasks=(),
              store=True, store_mtime=None, members=()):
    """A platform-shaped team: ``teams/<name>/config.json`` and, when
    ``store``, a ``tasks/<name>/`` store holding ``tasks`` (dicts written as
    ``<id>.json``). ``corroborate`` adds the lead entry the census binds on
    (``leadAgentId`` plus the member whose agentId equals it, with cwd and
    joinedAt); without it the config is the member-less shape. ``members``
    adds plain named members after the lead."""
    team_dir = root / "teams" / name
    team_dir.mkdir(parents=True, exist_ok=True)
    config = {"name": name, "members": []}
    if lead_sid is not None:
        config["leadSessionId"] = lead_sid
    if corroborate:
        lead_agent_id = f"team-lead@{name}"
        config["leadAgentId"] = lead_agent_id
        config["members"].append({
            "agentId": lead_agent_id,
            "name": "team-lead",
            "cwd": lead_cwd,
            "joinedAt": joined_at_ms,
        })
    for member in members:
        config["members"].append({"agentId": f"{member}@{name}", "name": member})
    (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    if store:
        tasks_dir = root / "tasks" / name
        tasks_dir.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            write_task(tasks_dir, task)
        if store_mtime is not None:
            os.utime(tasks_dir, (store_mtime, store_mtime))
    return team_dir


def write_task(tasks_dir: Path, task: dict) -> Path:
    """Write one platform-shaped task file ``<id>.json``."""
    body = {"status": "pending", "blockedBy": [], "blocks": [], "metadata": {}}
    body.update(task)
    path = tasks_dir / f"{body['id']}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def write_journal(session_dir: Path, events) -> Path:
    """Write ``session-journal.jsonl``. Each item is an event type (str) or a
    full event dict; bare types get ``v`` and ``ts`` and, for
    ``task_metadata_snapshot``, the schema's required fields."""
    session_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in events:
        if isinstance(item, str):
            event = {"v": 1, "type": item, "ts": JOURNAL_TS}
            if item == "task_metadata_snapshot":
                event.update({"task_id": "17", "metadata": {}})
        else:
            event = {"v": 1, "ts": JOURNAL_TS}
            event.update(item)
        lines.append(json.dumps(event))
    path = session_dir / "session-journal.jsonl"
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def read_journal(session_dir: Path) -> list:
    """Every event in the session journal, in order ([] when absent)."""
    path = Path(session_dir) / "session-journal.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def seed_plugin(plugin_root: Path, agents=("pact-architect",)) -> Path:
    """A minimal plugin root whose agents/ registers ``agents``."""
    agents_dir = plugin_root / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for stem in agents:
        (agents_dir / f"{stem}.md").write_text(f"---\nname: {stem}\n---\n")
    return plugin_root


def stat_snapshot(*paths):
    """``{path: (st_ino, st_mtime_ns, sha256)}``; None for an absent path.
    An atomic rewrite with identical bytes changes the inode, so a write that
    should not have happened is visible even when the bytes match."""
    snap = {}
    for path in paths:
        path = Path(path)
        if not path.exists():
            snap[str(path)] = None
            continue
        st = path.stat()
        snap[str(path)] = (st.st_ino, st.st_mtime_ns,
                           hashlib.sha256(path.read_bytes()).hexdigest())
    return snap


def reset_context_module():
    """Drop pact_context's per-process caches so the next read hits disk."""
    import shared.pact_context as ctx_module
    ctx_module.reset_for_tests()


def write_context_file(session_id, team_name, project_dir, plugin_root,
                       started_at="2026-10-19T07:00:00+00:00", process_key=None):
    """Persist the session context through the production writer halves, so
    its bytes are exactly what ``session_init`` would write for the same
    values. ``process_key`` records the claude process the session ran in
    (None leaves the writer's default). Returns the file path."""
    import shared.pact_context as ctx_module
    ctx_module.reset_for_tests()
    extra = {} if process_key is None else {"process_key": process_key}
    target, context = ctx_module.build_context_cache(
        team_name, session_id, str(project_dir), str(plugin_root),
        started_at=started_at, **extra,
    )
    ctx_module.persist_context(target, context)
    ctx_module.reset_for_tests()
    return target


def write_session_block(session_id, team_name, session_dir, plugin_root):
    """Write the project CLAUDE.md session block through the production
    writer (``CLAUDE_PROJECT_DIR`` must already point at the tmp project)."""
    from shared.session_resume import update_session_info
    return update_session_info(session_id, team_name, str(session_dir),
                               str(plugin_root))


def team_line(claude_md: Path) -> str:
    """The ``- Team:`` line of the CLAUDE.md session block ('' when absent)."""
    if not claude_md.exists():
        return ""
    for line in claude_md.read_text(encoding="utf-8").splitlines():
        if line.startswith("- Team:"):
            return line
    return ""


def split_world(tmp_path, monkeypatch, *, saved_team=OLD_TEAM,
                session_id=OLD_SID, live_team=NEW_TEAM, live_lead_sid=NEW_SID,
                live_store_aged=False, live_tasks=(), old_tasks=(),
                old_config=False, foreign=None, foreign_tasks=(),
                second_corroborated=False, journal=DEFAULT_JOURNAL,
                claude_md=False, live_members=()):
    """Build a team-split world and return its paths and ids.

    * The session's recorded team is ``saved_team`` (the context file and,
      when ``claude_md``, the project CLAUDE.md ``- Team:`` line name it); the
      context file's ``session_id`` is ``session_id``.
    * The OLD team's config is absent (``old_config=True`` writes one keyed
      on ``session_id``); its task store holds ``old_tasks`` when given.
    * ``live_team`` (None to omit) is the team the platform is using: its
      config carries ``live_lead_sid`` and a lead entry that corroborates as
      this session's (cwd = the project dir, joined after the journal's end
      marker); its store is fresh unless ``live_store_aged``.
    * ``foreign`` = 'foreign-cwd' adds a live team whose lead runs in another
      project; 'member-less' adds one whose config has no lead entry. Either
      has a fresh store and never corroborates.
    * ``second_corroborated`` adds another team that corroborates as well.
    * ``journal`` seeds this session's journal (a task_* event and an end
      marker by default, the lived-and-ended shape).
    """
    import shared.pact_context as ctx_module

    root = config_root(tmp_path)
    project_dir = tmp_path / "project"
    (project_dir / ".claude").mkdir(parents=True, exist_ok=True)
    plugin_root = seed_plugin(tmp_path / "plugin")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (root / "teams").mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir(parents=True, exist_ok=True)

    if old_config:
        seed_team(root, OLD_TEAM, lead_sid=session_id, lead_cwd=str(project_dir),
                  tasks=old_tasks)
    elif old_tasks:
        old_store = root / "tasks" / OLD_TEAM
        old_store.mkdir(parents=True, exist_ok=True)
        for task in old_tasks:
            write_task(old_store, task)
        os.utime(old_store, (AGED_MTIME, AGED_MTIME))

    if live_team is not None:
        seed_team(root, live_team, lead_sid=live_lead_sid,
                  lead_cwd=str(project_dir), tasks=live_tasks,
                  store_mtime=(AGED_MTIME if live_store_aged else None),
                  members=live_members)
    if foreign is not None:
        seed_team(root, FOREIGN_TEAM, lead_sid=FOREIGN_SID,
                  corroborate=(foreign == "foreign-cwd"), lead_cwd=FOREIGN_CWD,
                  tasks=foreign_tasks)
    if second_corroborated:
        seed_team(root, SECOND_TEAM, lead_sid=SECOND_SID,
                  lead_cwd=str(project_dir))

    context_path = write_context_file(session_id, saved_team, project_dir,
                                      plugin_root)
    session_dir = context_path.parent
    if journal:
        write_journal(session_dir, journal)
    claude_md_path = project_dir / ".claude" / "CLAUDE.md"
    if claude_md:
        write_session_block(session_id, saved_team, session_dir, plugin_root)
    ctx_module.reset_for_tests()
    return SimpleNamespace(
        tmp=tmp_path, root=root, teams=root / "teams", tasks=root / "tasks",
        project_dir=project_dir, plugin_root=plugin_root,
        session_dir=session_dir, context_path=context_path,
        claude_md=claude_md_path, session_id=session_id,
    )


def saved_team_name(world) -> str:
    """The team_name the context file on disk currently records."""
    return json.loads(world.context_path.read_text(encoding="utf-8"))["team_name"]


HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"


def hook_env(world, **extra):
    """The environment a hook child of this test process runs under: this
    test's environment (the conftest scrubs and the world's variables), its
    home at the world's scratch root, and CLAUDE_PID naming this test
    process, which is the child's real parent and alive for as long as the
    test runs, whoever launched pytest."""
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.update(HOME=str(world.tmp), CLAUDE_PID=str(os.getpid()))
    env.update(extra)
    return env


def run_hook(world, hook, frame):
    """Run hooks/<hook>.py as a child of this test process and return the
    additionalContext it printed ('' when none)."""
    import subprocess
    proc = subprocess.run([sys.executable, str(HOOKS_DIR / f"{hook}.py")],
                          input=json.dumps(frame), capture_output=True,
                          text=True, check=False, env=hook_env(world),
                          cwd=str(world.project_dir), timeout=120)
    assert proc.returncode == 0, (hook, proc.returncode, proc.stderr[-2000:])
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


def child_process_key(world):
    """The process key a hook run by run_hook computes: this test process's
    pid and start time, read by the production helper in a child whose
    parent is this process."""
    import subprocess
    env = hook_env(world, PYTHONPATH=str(HOOKS_DIR))
    proc = subprocess.run(
        [sys.executable, "-c",
         "import shared.pact_context as c; print(c.current_process_key())"],
        capture_output=True, text=True, check=False, env=env,
        cwd=str(HOOKS_DIR), timeout=60)
    return proc.stdout.strip()


def clear_world(tmp_path, monkeypatch, *, prev_team=NEW_TEAM,
                same_process=True, prev_journal=None, **split_kw):
    """A /clear in the measured shape. The session that ran before it (id
    OLD_SID) recorded ``prev_team`` and the claude process it ran in; the
    clear starts a NEW session (id CLEAR_SID) with a new, empty session dir,
    in the same process unless ``same_process`` is False. The CLAUDE.md
    block names the earlier session, and the platform keeps the team.

    Run the clear's hooks through run_hook: the claude process they see is
    this test process (their real parent), so the key does not depend on
    who launched pytest. Returns split_world's namespace plus
    clear_sid, clear_dir and process_key."""
    import shared.pact_context as ctx_module

    if prev_journal is None:
        prev_journal = (
            {"type": "session_start", "session_id": OLD_SID,
             "project_dir": str(tmp_path / "project"), "source": "startup"},
            "task_metadata_snapshot", "session_end")
    world = split_world(tmp_path, monkeypatch, saved_team=prev_team,
                        journal=prev_journal, claude_md=True, **split_kw)
    key = child_process_key(world)
    assert key, "precondition: a hook child of this process computes a key"
    write_context_file(OLD_SID, prev_team, world.project_dir, world.plugin_root,
                       process_key=key if same_process else OTHER_PROCESS_KEY)
    ctx_module.reset_for_tests()
    world.clear_sid = CLEAR_SID
    world.clear_dir = world.session_dir.parent / CLEAR_SID
    world.process_key = key
    return world


def clear_frame(world, factory):
    """A lead SessionStart(source=clear) frame for the session the clear
    starts, built from a captured lead frame ``factory``."""
    frame = factory()
    frame.update(session_id=world.clear_sid, cwd=str(world.project_dir),
                 source="clear")
    return frame


def recorded_team(session_dir) -> str:
    """team_name in a session dir's context file ('' when absent)."""
    path = Path(session_dir) / "pact-session-context.json"
    if not path.exists():
        return ""
    return json.loads(path.read_text(encoding="utf-8")).get("team_name", "")
