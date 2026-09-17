"""Behaviour pins for shared/state_file.py and the writers routed through it.

Location: pact-plugin/tests/test_state_file.py
Summary: pins that a state-file write is all-or-nothing, that no state writer
         can write without a lock, that the sidecar lock is held across the
         whole update, that concurrent writers lose nothing, and that a no-op
         update creates nothing.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.
"""

from __future__ import annotations

import ast
import fcntl
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from shared import background_work as bw
from shared import state_file

HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
TEAM = "state-team"


@pytest.fixture
def config_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _record(task_id: str, agent: str = "probe-coder") -> dict:
    return {
        "agent_name": agent,
        "session_id": "sid",
        "task_ids": [task_id],
        "registered_at": bw.iso_now(),
    }


def _leftover_temps(directory: Path) -> list:
    return [p.name for p in directory.iterdir() if p.name.endswith(".tmp")]


class _WritesFail:
    """A file object whose `write` raises, as a full disk would. Reads pass through."""

    def __init__(self, f):
        self._f = f

    def write(self, _data):
        raise OSError("simulated write failure")

    def __getattr__(self, name):
        return getattr(self._f, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()
        return False


def test_a_write_that_fails_midway_leaves_the_previous_registry_intact(
    config_root, monkeypatch
):
    """A write that fails part-way must leave the registry as it was.

    Truncating in place empties the file before the new bytes go in, so a
    failing write leaves nothing. Writing a temp file and swapping it in
    leaves the previous file untouched until the new one is complete.
    """
    assert bw.save_records([_record("1")], team_name=TEAM) is True
    registry = bw.registry_path(TEAM)

    real_fdopen = os.fdopen

    def fdopen(fd, mode="r", *args, **kwargs):
        f = real_fdopen(fd, mode, *args, **kwargs)
        return _WritesFail(f) if ("w" in mode or "+" in mode) else f

    monkeypatch.setattr(os, "fdopen", fdopen)
    assert bw.append_record(_record("2"), team_name=TEAM) is False
    monkeypatch.setattr(os, "fdopen", real_fdopen)

    records = json.loads(registry.read_text(encoding="utf-8"))["records"]
    assert [r["task_ids"] for r in records] == [["1"]], (
        "a failed write changed the registry; the previous content must "
        "survive until a complete replacement is swapped in"
    )
    assert _leftover_temps(registry.parent) == [], "a failed write left its temp file behind"


# The modules that write state files. Each must lock every write; the list
# grows as writers are routed through state_file.
STATE_WRITER_MODULES = (
    "shared/state_file.py",
    "shared/background_work.py",
    "teammate_idle.py",
    "track_files.py",
    "file_tracker.py",
)


def _unlocked_write_shapes(source: str) -> list:
    """Every shape in `source` that lets a state file be written without a lock."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id == "HAS_FLOCK":
            found.append("HAS_FLOCK")
        elif isinstance(node, ast.Try):
            catches_import_error = any(
                isinstance(h.type, ast.Name)
                and h.type.id in ("ImportError", "ModuleNotFoundError")
                for h in node.handlers
            )
            imports_fcntl = any(
                isinstance(inner, ast.Import)
                and any(alias.name == "fcntl" for alias in inner.names)
                for stmt in node.body
                for inner in ast.walk(stmt)
            )
            if catches_import_error and imports_fcntl:
                found.append("fcntl import with an ImportError fallback")
        elif (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "fcntl" for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and node.value.value is None
        ):
            found.append("fcntl = None")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "truncate"
        ):
            found.append(".truncate(")
    return found


@pytest.mark.parametrize("module", STATE_WRITER_MODULES)
def test_no_state_writer_can_write_without_a_lock(module):
    """No lock fallback, no `fcntl = None`, and no truncate-in-place write."""
    source = (HOOKS_DIR / module).read_text(encoding="utf-8")
    assert _unlocked_write_shapes(source) == [], (
        f"{module} can write a state file without a lock or in place"
    )
    if module == "shared/state_file.py":
        top_level_imports = {
            alias.name
            for node in ast.parse(source).body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert "fcntl" in top_level_imports, "state_file.py must import fcntl at module level"


def test_the_unlocked_write_detector_sees_each_forbidden_shape():
    """Non-vacuity for the arm above: each forbidden shape is detected."""
    source = (
        "try:\n    import fcntl\n    HAS_FLOCK = True\n"
        "except ImportError:\n    fcntl = None\n"
        "def w(f):\n    f.truncate()\n"
    )
    assert sorted(set(_unlocked_write_shapes(source))) == sorted(
        {"HAS_FLOCK", "fcntl import with an ImportError fallback", "fcntl = None", ".truncate("}
    )


def test_the_sidecar_is_locked_for_the_whole_update(tmp_path, monkeypatch):
    """Guard arm: LOCK_EX on the sidecar before `apply`, LOCK_UN after the replace."""
    target = tmp_path / "teams" / "t" / "state.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    events = []
    real_flock = fcntl.flock
    real_replace = os.replace

    def spy_flock(fd, op):
        events.append(("flock", op, os.fstat(fd).st_ino))
        return real_flock(fd, op)

    def spy_replace(src, dst):
        events.append(("replace",))
        return real_replace(src, dst)

    def apply(_text):
        events.append(("apply",))
        return '{"n": 1}', True, "done"

    monkeypatch.setattr(state_file.fcntl, "flock", spy_flock)
    monkeypatch.setattr(state_file.os, "replace", spy_replace)
    assert state_file.locked_update(target, apply, tmp_path / "teams") == "done"

    sidecar_inode = (target.parent / "state.json.lock").stat().st_ino
    assert [e[0] for e in events] == ["flock", "apply", "replace", "flock"], events
    assert events[0][1] == fcntl.LOCK_EX and events[3][1] == fcntl.LOCK_UN
    assert events[0][2] == sidecar_inode and events[3][2] == sidecar_inode, (
        "the lock must be taken on the sidecar, never on the replaced data file"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 1}


_APPENDER = '''
import os, sys, time
from pathlib import Path
os.environ["CLAUDE_CONFIG_DIR"] = sys.argv[1]
from shared import background_work as bw
tag, n, rendezvous = sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
(rendezvous / tag).write_text("ready")
deadline = time.time() + 30
while len(list(rendezvous.iterdir())) < 2 and time.time() < deadline:
    time.sleep(0.005)
for i in range(n):
    bw.append_record({"agent_name": tag, "session_id": "sid",
                      "task_ids": [f"{tag}-{i}"],
                      "registered_at": bw.iso_now()},
                     team_name="state-team")
'''


def test_concurrent_writers_lose_no_update(tmp_path):
    """Guard arm: two processes appending at once keep every record."""
    per_process = 40
    script = tmp_path / "appender.py"
    script.write_text(_APPENDER, encoding="utf-8")
    rendezvous = tmp_path / "rendezvous"
    rendezvous.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(HOOKS_DIR), env.get("PYTHONPATH", "")) if p
    )
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(tmp_path), tag, str(per_process), str(rendezvous)],
            env=env,
        )
        for tag in ("alpha", "beta")
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    registry = tmp_path / "teams" / TEAM / "background_work.json"
    records = json.loads(registry.read_text(encoding="utf-8"))["records"]
    assert len(records) == 2 * per_process


def test_a_no_op_update_on_an_absent_file_creates_nothing(tmp_path):
    """Guard arm: no file, no directory and no sidecar for an update that changes nothing."""
    target = tmp_path / "teams" / "t" / "state.json"
    result = state_file.locked_update(target, lambda text: (text, False, "untouched"), tmp_path / "teams")
    assert result == "untouched"
    assert not (tmp_path / "teams").exists()


# ---------------------------------------------------------------------------
# Containment: a state file must stay inside its root.
# ---------------------------------------------------------------------------


def _link_team_outside(config: Path) -> Path:
    """Make teams/<TEAM> a symlink to a directory outside teams/; return that directory."""
    outside = config / "outside"
    outside.mkdir()
    (config / "teams").mkdir()
    (config / "teams" / TEAM).symlink_to(outside, target_is_directory=True)
    return outside


def test_a_symlinked_team_directory_refuses_the_write(config_root):
    """A team directory linked outside teams/ is refused, and nothing lands outside."""
    outside = _link_team_outside(config_root)
    assert bw.append_record(_record("1"), team_name=TEAM) is False
    assert sorted(p.name for p in outside.iterdir()) == [], (
        "a write through a symlinked team directory reached a directory outside teams/"
    )


def test_a_symlinked_team_directory_reads_nothing(config_root):
    """A registry reached through a team directory linked outside teams/ reads as absent."""
    outside = _link_team_outside(config_root)
    (outside / bw.REGISTRY_FILENAME).write_text(
        json.dumps({"records": [_record("1")]}), encoding="utf-8"
    )
    assert bw.load_records_for_discharge(TEAM) == [], (
        "a read followed a symlinked team directory to a registry outside teams/"
    )


def test_a_symlinked_config_root_still_writes(tmp_path, monkeypatch):
    """Guard arm: resolving both sides keeps a linked config directory working."""
    real_config = tmp_path / "real-config"
    real_config.mkdir()
    linked_config = tmp_path / "linked-config"
    linked_config.symlink_to(real_config, target_is_directory=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(linked_config))
    assert bw.append_record(_record("1"), team_name=TEAM) is True
    assert (real_config / "teams" / TEAM / bw.REGISTRY_FILENAME).is_file()


def test_a_team_link_to_a_folder_inside_teams_still_writes(config_root):
    """Guard arm: a team directory linked to another folder inside teams/ is allowed."""
    real_team = config_root / "teams" / "real-team"
    real_team.mkdir(parents=True)
    (config_root / "teams" / TEAM).symlink_to(real_team, target_is_directory=True)
    assert bw.append_record(_record("1"), team_name=TEAM) is True
    assert (real_team / bw.REGISTRY_FILENAME).is_file()


def test_a_file_directly_in_its_root_still_writes(tmp_path):
    """Guard arm: a file whose directory IS the root is inside it."""
    root = tmp_path / "session-tracking"
    state_file.write_text(root / "session.json", '{"files": []}', root)
    assert state_file.read_text(root / "session.json", root) == '{"files": []}'


# ---------------------------------------------------------------------------
# The older state writers: all-or-nothing writes, and production containment.
# ---------------------------------------------------------------------------

OLDER_WRITER_SEEDS = {
    "write_idle_counts": '{"seed": 1}',
    "_atomic_update_idle_counts": '{"seed": 1}',
    "save_tracked_files": '{"files": [], "session_id": "seed"}',
    "track_file": '{"files": [], "session_id": "seed"}',
    "track_edit": "[]",
}


def _json_that_cannot_serialise():
    """A `json` stand-in whose loads is real and whose dump/dumps raise."""

    def refuse(*_args, **_kwargs):
        raise ValueError("simulated serialiser fault")

    return types.SimpleNamespace(
        loads=json.loads,
        load=json.load,
        JSONDecodeError=json.JSONDecodeError,
        dump=refuse,
        dumps=refuse,
    )


@pytest.mark.parametrize("writer", sorted(OLDER_WRITER_SEEDS))
def test_a_serialisation_failure_leaves_the_previous_file_intact(writer, tmp_path, monkeypatch):
    """A writer whose serialiser fails must leave the file exactly as it was.

    Truncating in place empties the file before the new content is produced,
    so a serialiser fault leaves nothing behind.
    """
    import file_tracker
    import teammate_idle
    import track_files

    path = tmp_path / "state.json"
    path.write_text(OLDER_WRITER_SEEDS[writer], encoding="utf-8")
    stand_in = _json_that_cannot_serialise()
    if writer == "write_idle_counts":
        monkeypatch.setattr(teammate_idle, "json", stand_in)
        call = lambda: teammate_idle.write_idle_counts(str(path), {"coder": 1})  # noqa: E731
    elif writer == "_atomic_update_idle_counts":
        monkeypatch.setattr(teammate_idle, "json", stand_in)
        call = lambda: teammate_idle._atomic_update_idle_counts(  # noqa: E731
            str(path), lambda counts: {**counts, "coder": 1}
        )
    elif writer in ("save_tracked_files", "track_file"):
        monkeypatch.setattr(track_files, "get_session_tracking_file", lambda: path)
        monkeypatch.setattr(track_files, "json", stand_in)
        if writer == "save_tracked_files":
            call = lambda: track_files.save_tracked_files({"files": [], "session_id": "x"})  # noqa: E731
        else:
            call = lambda: track_files.track_file("/src/app.py", "Edit")  # noqa: E731
    else:
        monkeypatch.setattr(file_tracker, "json", stand_in)
        call = lambda: file_tracker.track_edit("/src/app.py", "coder", "Edit", str(path))  # noqa: E731
    try:
        call()
    except ValueError:
        pass
    assert path.read_text(encoding="utf-8") == OLDER_WRITER_SEEDS[writer], (
        f"{writer} changed the file when its serialiser failed"
    )


# Functions that write or read the older state files through a caller-owned
# path. Their `root` defaults to the path's own directory, so a production call
# that omits `root=` would silently give up containment.
OLDER_WRITER_HELPERS = frozenset({
    "write_idle_counts",
    "_atomic_update_idle_counts",
    "read_idle_counts",
    "check_idle_cleanup",
    "reset_idle_count",
    "track_edit",
    "check_conflict",
    "get_environment_delta",
})


def _older_writer_calls():
    """(file:line name, passes_root) for every call to an older-writer helper under hooks/."""
    calls = []
    for path in sorted(HOOKS_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if name in OLDER_WRITER_HELPERS:
                site = f"{path.relative_to(HOOKS_DIR)}:{node.lineno} {name}"
                calls.append((site, any(k.arg == "root" for k in node.keywords)))
    return calls


def test_every_production_call_to_an_older_state_writer_passes_a_root():
    """Production code must pass `root=`, so the tests-only default never ships."""
    calls = _older_writer_calls()
    assert len(calls) >= 5, f"found only {len(calls)} production calls; the scan is not seeing hooks/"
    missing = [site for site, passes_root in calls if not passes_root]
    assert missing == [], (
        "these production calls omit root=, so their writes are not contained "
        f"to the config root: {missing}"
    )


def test_file_tracker_main_writes_nothing_through_a_symlinked_team_directory(
    config_root, monkeypatch, capsys
):
    """The production entry point refuses a team directory linked outside teams/,
    exits 0 and prints no traceback."""
    import io

    import file_tracker

    outside = _link_team_outside(config_root)
    monkeypatch.setattr(file_tracker.pact_context, "init", lambda _data: None)
    monkeypatch.setattr(file_tracker, "frame_team_and_name", lambda _data: (TEAM, ""))
    monkeypatch.setattr(file_tracker, "resolve_agent_name", lambda _data: "coder")
    monkeypatch.setattr(file_tracker, "get_session_id", lambda: "sid")
    frame = {"tool_name": "Edit", "tool_input": {"file_path": "/src/app.py"}, "session_id": "sid"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(frame)))
    with pytest.raises(SystemExit) as exc:
        file_tracker.main()
    assert exc.value.code == 0
    assert "Traceback" not in capsys.readouterr().err
    assert sorted(p.name for p in outside.iterdir()) == [], (
        "file_tracker.main wrote through a symlinked team directory"
    )


def test_teammate_idle_main_writes_nothing_through_a_symlinked_team_directory(
    config_root, monkeypatch
):
    """The production entry point refuses idle counts in a team directory linked outside teams/."""
    import io

    import teammate_idle

    outside = _link_team_outside(config_root)
    monkeypatch.setattr(teammate_idle.pact_context, "init", lambda _data: None)
    monkeypatch.setattr(teammate_idle, "frame_team_and_name", lambda _data: (TEAM, ""))
    monkeypatch.setattr(
        teammate_idle, "iter_team_task_jsons",
        lambda _team: iter([{"id": "3", "status": "completed", "owner": "coder"}]),
    )
    frame = {"hook_event_name": "TeammateIdle", "teammate_name": "coder"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(frame)))
    with pytest.raises(SystemExit) as exc:
        teammate_idle.main()
    assert exc.value.code == 0
    assert sorted(p.name for p in outside.iterdir()) == [], (
        "teammate_idle.main wrote idle counts through a symlinked team directory"
    )
