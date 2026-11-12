"""
Location: pact-plugin/tests/test_child_pytest_census.py
Summary: Pins the child-pytest census: which launches it refuses and accepts,
         that it is live in this session, that finish() fails the session, and
         that under real xdist a miss a test swallows still fails the run.
Used by: the suite; the census itself is tests/child_pytest_census.py, wired in
         tests/conftest.py.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import child_pytest_census as census

PY = sys.executable
_CONFTEST = Path(__file__).resolve().with_name("conftest.py")
_BLOCK = ["-p", "no:randomly"]


@pytest.fixture
def census_state():
    """Restore the census's recorded lines, so a deliberate miss here does not
    fail the session that runs this module."""
    saved = {key: list(census._state[key]) for key in ("misses", "forwarded")}
    try:
        yield census._state
    finally:
        for key, value in saved.items():
            census._state[key][:] = value


@pytest.mark.parametrize("argv", [
    [PY, "-m", "pytest", "-q"],
    "python -m pytest -q",
    ["/bin/sh", "-c", "python3 -m pytest -q"],
    ["bash", "-c", "cd x && python -m pytest tests"],
    [PY, "-mpytest"],
    [PY, "-Im", "pytest"],
    [PY, "-W", "ignore", "-m", "pytest"],
    ["pytest", "-q"],
    ["/opt/venv/bin/py.test"],
    ["env", "A=1", PY, "-m", "pytest"],
    ["A=1", "python3", "-m", "pytest"],
    [Path(PY), b"-m", "pytest"],
])
def test_an_unblocked_pytest_child_is_refused(argv):
    line = census._miss(argv, "/rig", {})
    assert line is not None, argv
    assert line.endswith(census.REMEDY) and "(cwd /rig)" in line, line


@pytest.mark.parametrize("extra", [
    ["-p", "no:randomly"],
    ["-pno:randomly"],
    ["-p", "randomly"],
    ["-prandomly"],
])
def test_each_flag_opt_out_is_accepted(extra):
    assert census._miss([PY, "-m", "pytest", "-q", *extra], None, {}) is None
    assert census._miss("pytest -q " + " ".join(extra), None, {}) is None


def test_an_explicit_env_disabling_autoload_is_accepted():
    argv = [PY, "-m", "pytest"]
    assert census._miss(argv, None, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}) is None
    assert census._miss(argv, None, {b"PYTEST_DISABLE_PLUGIN_AUTOLOAD": b"1"}) is None
    assert census._miss(argv, None, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": ""}) is not None


def test_without_an_explicit_env_the_inherited_env_decides(monkeypatch):
    argv = [PY, "-m", "pytest"]
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert census._miss(argv, None, None) is None
    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
    assert census._miss(argv, None, None) is not None
    # An explicit env replaces the inherited one, even when the parent disables it.
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert census._miss(argv, None, {}) is not None


@pytest.mark.parametrize("argv", [
    ["git", "status"],
    ["git", "commit", "-m", "pytest"],
    [PY, "-c", "print(1)"],
    [PY, "script.py", "-m", "pytest"],
    [PY, "-m", "pip", "install", "pytest"],
    ["bash", "scripts/verify-gate-tree.sh", "repo", "capture", "-q"],
])
def test_a_launch_that_is_not_a_pytest_child_is_ignored(argv):
    assert census._miss(argv, None, {}) is None


@pytest.mark.parametrize("argv", [
    ["timeout", "60", PY, "-m", "pytest"],
    ["timeout", "-s", "KILL", "1.5m", PY, "-m", "pytest"],
    ["nice", "-n", "10", PY, "-m", "pytest"],
    ["nohup", PY, "-m", "pytest"],
    ["sudo", "-u", "bob", PY, "-m", "pytest"],
    ["stdbuf", "-oL", PY, "-m", "pytest"],
    ["xvfb-run", "-a", PY, "-m", "pytest"],
    ["uv", "run", "python", "-m", "pytest"],
    ["poetry", "run", "python", "-m", "pytest"],
    ["pypy3", "-m", "pytest"],
    ["/opt/pypy/bin/pypy", "-I", "-m", "pytest"],
    [PY, "--check-hash-based-pycs", "always", "-m", "pytest"],
    [PY, "-X", "dev", "--check-hash-based-pycs", "never", "-Wd", "-m", "pytest"],
    # A long option CPython may add later is stepped over, not taken as a script.
    [PY, "--a-future-flag", "-m", "pytest"],
    # An unlisted wrapper still exposes the interpreter form.
    ["hatch", "run", "python", "-m", "pytest"],
    ["chrt", "-f", "10", PY, "-m", "pytest"],
    ["uv", "run", "pytest"],
    ["uv", "run", "--with", "x", "pytest", "-q"],
    ["poetry", "run", "pytest"],
    ["timeout", "60", "pytest"],
    ["nice", "py.test"],
    ["env", "-u", "HOME", "pytest"],
    ["timeout", "60", "nice", "-n", "5", "pytest"],
])
def test_a_wrapped_or_unusual_interpreter_launch_is_refused(argv):
    assert census._miss(argv, None, {}) is not None, argv


@pytest.mark.parametrize("argv", [
    ["git", "grep", "pytest"],
    ["pip", "install", "pytest"],
    ["timeout", "60", "git", "grep", "pytest"],
    ["uv", "run", "python", "script.py", "-m", "pytest"],
])
def test_a_mention_of_pytest_that_launches_no_pytest_is_ignored(argv):
    assert census._miss(argv, None, {}) is None


def test_the_census_is_live_and_refuses_before_the_child_starts(tmp_path, census_state):
    assert census.installed(), "tests/conftest.py did not install the census"
    before = len(census_state["misses"])
    with pytest.raises(census.ChildPytestCensusError) as caught:
        subprocess.run([PY, "-m", "pytest", "--version"], cwd=tmp_path,
                       capture_output=True, timeout=60)
    message = str(caught.value)
    assert census.REMEDY in message and str(tmp_path) in message, message
    assert "test_the_census_is_live" in message, message
    assert census_state["misses"][before:] == [message]


def test_a_blocked_child_runs(tmp_path, census_state):
    before = list(census_state["misses"])
    proc = subprocess.run([PY, "-m", "pytest", "--version", *_BLOCK], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert census_state["misses"] == before


def test_a_non_pytest_launch_is_untouched(census_state):
    before = list(census_state["misses"])
    proc = subprocess.run([PY, "-c", "print('ok')"], capture_output=True, text=True,
                          timeout=60)
    assert proc.stdout.strip() == "ok"
    assert census_state["misses"] == before


class _Reporter:
    def __init__(self):
        self.lines = []

    def write_line(self, line):
        self.lines.append(line)


class _PluginManager:
    def __init__(self, reporter):
        self.reporter = reporter

    def get_plugin(self, name):
        return self.reporter if name == "terminalreporter" else None


class _Config:
    def __init__(self, reporter, workeroutput=None):
        self.pluginmanager = _PluginManager(reporter)
        if workeroutput is not None:
            self.workeroutput = workeroutput


class _Session:
    def __init__(self, config):
        self.config = config
        self.exitstatus = 0


def test_finish_fails_the_session_and_reports_each_miss(census_state):
    census_state["misses"][:] = ["t::a: pytest child [x] ...; " + census.REMEDY]
    census_state["forwarded"][:] = []
    reporter = _Reporter()
    session = _Session(_Config(reporter))
    census.finish(session)
    assert session.exitstatus == 1
    assert reporter.lines[0].startswith("CHILD PYTEST CENSUS: 1 ")
    assert reporter.lines[1] == "  " + census_state["misses"][0]


def test_finish_with_no_miss_leaves_the_session_alone(census_state):
    census_state["misses"][:] = []
    census_state["forwarded"][:] = []
    reporter = _Reporter()
    session = _Session(_Config(reporter))
    census.finish(session)
    assert session.exitstatus == 0 and reporter.lines == []


def test_a_worker_hands_its_misses_to_the_controller(census_state):
    line = "t::b: pytest child [y] ...; " + census.REMEDY
    census_state["misses"][:] = [line]
    census_state["forwarded"][:] = []
    workeroutput = {}
    worker = _Session(_Config(_Reporter(), workeroutput=workeroutput))
    census.finish(worker)
    assert worker.exitstatus == 0, "a worker's exit status is ignored by xdist"
    assert workeroutput[census.WORKEROUTPUT_KEY] == [line]

    census_state["misses"][:] = []  # the controller process recorded nothing itself
    node = type("Node", (), {"workeroutput": workeroutput,
                             "gateway": type("Gateway", (), {"id": "gw3"})()})()
    census.collect(node)
    reporter = _Reporter()
    controller = _Session(_Config(reporter))
    census.finish(controller)
    assert controller.exitstatus == 1
    assert reporter.lines[1] == "  [gw3] " + line


_RIG_CONFTEST = '''
import importlib.util

_spec = importlib.util.spec_from_file_location("pact_suite_conftest", {conftest!r})
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)
pytest_configure = _suite.pytest_configure
pytest_testnodedown = _suite.pytest_testnodedown
pytest_sessionfinish = _suite.pytest_sessionfinish
'''

_RIG_TESTS = '''
import subprocess
import sys


def test_swallows_the_census_error():
    try:
        subprocess.run([sys.executable, "-m", "pytest", "--version"{flag}],
                       capture_output=True, timeout=60)
    except Exception:
        pass


def test_plain():
    pass
'''


def _run_rig(tmp_path, flag):
    (tmp_path / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (tmp_path / "test_swallow.py").write_text(
        _RIG_TESTS.format(flag=flag), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_XDIST_") and k not in ("PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")}
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *_BLOCK, "-n", "2",
         "test_swallow.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


def test_under_xdist_a_swallowed_miss_still_fails_the_run(tmp_path):
    rc, output = _run_rig(tmp_path, "")
    assert "2 passed" in output, output
    assert rc == 1, output
    assert "CHILD PYTEST CENSUS: 1 " in output, output
    assert "test_swallows_the_census_error" in output and census.REMEDY in output, output


def test_under_xdist_a_blocked_child_passes_the_run(tmp_path):
    rc, output = _run_rig(tmp_path, ', "-p", "no:randomly"')
    assert "2 passed" in output, output
    assert rc == 0, output
    assert "CHILD PYTEST CENSUS" not in output, output
