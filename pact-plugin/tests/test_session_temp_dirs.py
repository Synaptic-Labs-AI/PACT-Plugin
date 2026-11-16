"""
Location: pact-plugin/tests/test_session_temp_dirs.py
Summary: A process that loads tests/conftest.py creates a sandbox HOME
(pact-home-suite-*) and a fallback memory store (pact-memory-suite-*) under the
temp root, and removes both when it exits: in a serial session, in an xdist
controller and each of its workers, and in a session that errors at collection.
Used by: pytest.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parent.parent
_PREFIXES = ("pact-home-suite-", "pact-memory-suite-")

# A plugin for the child session. Each process records the two directories its
# tests/conftest.py created, so the test can check they were made under the
# child's private temp root before it checks they are gone.
_RECORDER = """
import json
import os
from pathlib import Path


def pytest_sessionstart(session):
    record = {"home": os.environ["HOME"],
              "memory": os.environ.get("PACT_TEST_MEMORY_DIR", "")}
    out = Path(os.environ["PACT_TEMP_DIR_RECORD"]) / ("%d.json" % os.getpid())
    out.write_text(json.dumps(record), encoding="utf-8")


def pytest_pycollect_makeitem(collector, name, obj):
    # Raised inside the module's collection, so pytest reports a collection
    # error and the session stops with exit status 2.
    if os.environ.get("PACT_TEMP_DIR_BREAK") and name.startswith("test_"):
        raise RuntimeError("a collection error on purpose")
"""


def _child_env(tmp_path, temp_root, records):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTEST_", "GITHUB_")) and key != "PACT_TEMP_DIR_BREAK"}
    env["TMPDIR"] = str(temp_root)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tmp_path), env.get("PYTHONPATH")]))
    env["PACT_TEMP_DIR_RECORD"] = str(records)
    return env


@pytest.mark.parametrize("shape", ["serial", "xdist", "collection-error"])
def test_a_session_removes_the_temp_dirs_its_processes_created(tmp_path, shape):
    """MUTANT: tests/conftest.py no longer removes its directories at exit.
    Every process of the child session then leaves a pact-home-suite-* and a
    pact-memory-suite-* directory under its temp root."""
    if shape == "xdist":
        pytest.importorskip("xdist")
    (tmp_path / "pact_temp_dir_record.py").write_text(_RECORDER, encoding="utf-8")
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    records = tmp_path / "records"
    records.mkdir()
    args = ["tests/test_session_home_sandbox.py::test_the_session_home_is_not_the_real_home"]
    if shape == "xdist":
        args = ["-n", "2", "tests/test_session_home_sandbox.py"]
    elif shape == "collection-error":
        args = ["tests/test_session_home_sandbox.py"]  # a node id would be "not found": status 4
    env = _child_env(tmp_path, temp_root, records)
    if shape == "collection-error":
        env["PACT_TEMP_DIR_BREAK"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "-p", "pact_temp_dir_record", *args],
        cwd=_PLUGIN, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    assert proc.returncode == (2 if shape == "collection-error" else 0), output
    if shape == "collection-error":
        assert "error during collection" in output, output
    made = [json.loads(path.read_text(encoding="utf-8")) for path in records.glob("*.json")]
    assert len(made) == (3 if shape == "xdist" else 1), (made, output)
    for record in made:
        home = Path(record["home"])
        assert home.parent == temp_root and home.name.startswith(_PREFIXES[0]), record
        memory = Path(record["memory"]).parent
        assert memory.parent == temp_root and memory.name.startswith(_PREFIXES[1]), record
    left = sorted(path.name for path in temp_root.iterdir() if path.name.startswith(_PREFIXES))
    assert left == [], (left, output)


def _suite_conftest(request):
    suite = Path(__file__).resolve().with_name("conftest.py")
    found = [plugin for plugin in request.config.pluginmanager.get_plugins()
             if getattr(plugin, "__file__", None) and Path(plugin.__file__).resolve() == suite]
    assert len(found) == 1, found
    return found[0]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="SKIPPED, NOT PASSED: no os.fork here")
def test_a_forked_process_does_not_remove_its_parents_directory(request, tmp_path):
    """MUTANT: the exit handler removes the directory in any process. A process
    forked from one that loaded the conftest inherits its exit handlers, and
    must leave the parent's directory alone; the parent itself removes it."""
    conftest = _suite_conftest(request)
    target = tmp_path / "owned"
    target.mkdir()
    pid = os.fork()
    if pid == 0:  # the forked child
        try:
            conftest._remove_own_dir(str(target))
        finally:
            os._exit(0)
    os.waitpid(pid, 0)
    assert target.is_dir(), "a forked child removed its parent's directory"
    conftest._remove_own_dir(str(target))
    assert not target.exists(), "the creating process did not remove its directory"
