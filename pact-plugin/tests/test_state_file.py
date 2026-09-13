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
