"""
Location: pact-plugin/tests/test_home_census.py
Summary: Pins the HOME census: which launches it refuses and accepts, its one
         exemption, that it is live in this session, that finish() fails the
         session, and that under real xdist a refusal a test swallows still
         fails the run.
Used by: the suite; the census itself is tests/home_census.py, wired in
         pact-plugin/conftest.py.
"""
import ast
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import is_git_redirecting_variable
import home_census as census

PY = sys.executable
_CONFTEST = Path(__file__).resolve().with_name("conftest.py")
_HOME = {"HOME": "/sandbox/home"}


@pytest.fixture
def census_state():
    """Restore the census's recorded lines, so a deliberate refusal here does
    not fail the session that runs this module."""
    saved = {key: list(census._state[key])
             for key in ("misses", "forwarded", "faults", "forwarded_faults")}
    counts = {key: census._state[key] for key in ("checked", "forwarded_checked")}
    try:
        yield census._state
    finally:
        for key, value in saved.items():
            census._state[key][:] = value
        census._state.update(counts)


def _refused(launch, census_state):
    """Run `launch` and return the refusal line it recorded."""
    before = len(census_state["misses"])
    with pytest.raises(census.HomeCensusError) as caught:
        launch()
    added = census_state["misses"][before:]
    assert added == [str(caught.value)], added
    return added[0]


def _accepted(launch, census_state):
    before = list(census_state["misses"])
    launch()
    assert census_state["misses"] == before


# EACH ENUMERATED LAUNCH API, with an explicit env that lacks HOME, is refused
# before the child starts.
def _popen():
    subprocess.Popen(["true"], env={"PATH": os.defpath}).wait()


def _run():
    subprocess.run(["true"], env={"PATH": os.defpath})


def _call():
    subprocess.call(["true"], env={"PATH": os.defpath})


def _check_call():
    subprocess.check_call(["true"], env={"PATH": os.defpath})


def _check_output():
    subprocess.check_output(["true"], env={"PATH": os.defpath})


def _posix_spawn():
    os.waitpid(os.posix_spawn("/bin/true", ["true"], {"PATH": os.defpath}), 0)


def _posix_spawnp():
    os.waitpid(os.posix_spawnp("true", ["true"], {"PATH": os.defpath}), 0)


def _asyncio_exec():
    async def go():
        proc = await asyncio.create_subprocess_exec("true", env={"PATH": os.defpath})
        await proc.wait()
    asyncio.run(go())


def _asyncio_shell():
    async def go():
        proc = await asyncio.create_subprocess_shell("true", env={"PATH": os.defpath})
        await proc.wait()
    asyncio.run(go())


def _bytes_keys():
    subprocess.run(["true"], env={b"PATH": os.defpath.encode()})


@pytest.mark.parametrize("launch", [_popen, _run, _call, _check_call, _check_output,
                                    _posix_spawn, _posix_spawnp, _asyncio_exec,
                                    _asyncio_shell, _bytes_keys],
                         ids=lambda launch: launch.__name__.lstrip("_"))
def test_each_launch_api_with_an_env_lacking_home_is_refused(launch, census_state):
    line = _refused(launch, census_state)
    assert "is given an environment without HOME" in line, line
    assert census.REMEDY in line, line
    assert "test_each_launch_api_with_an_env_lacking_home" in line, line


@pytest.mark.parametrize("env", [
    {"PATH": os.defpath, "HOME": "/sandbox/home"},
    {b"PATH": os.defpath.encode(), b"HOME": b"/sandbox/home"},
    {"PATH": os.defpath, "HOME": ""},
], ids=["str-key", "bytes-key", "empty-value"])
def test_an_env_with_a_home_key_is_accepted(env, census_state):
    _accepted(lambda: subprocess.run(["true"], env=env), census_state)
    _accepted(lambda: os.waitpid(os.posix_spawn("/bin/true", ["true"], env), 0),
              census_state)


def test_an_inherited_env_is_accepted_while_the_suite_has_home(census_state):
    assert "HOME" in os.environ
    _accepted(lambda: subprocess.run(["true"]), census_state)


def test_an_inherited_env_is_refused_once_home_is_deleted(monkeypatch, census_state):
    monkeypatch.delenv("HOME")
    line = _refused(lambda: subprocess.run(["true"]), census_state)
    assert "inherits an environment without HOME" in line, line


def test_os_environ_given_explicitly_is_judged_like_any_env(monkeypatch, census_state):
    _accepted(lambda: subprocess.run(["true"], env=os.environ), census_state)
    monkeypatch.delenv("HOME")
    line = _refused(lambda: subprocess.run(["true"], env=os.environ), census_state)
    assert "is given an environment without HOME" in line, line


def test_os_popen_is_seen(monkeypatch, census_state):
    monkeypatch.delenv("HOME")
    line = _refused(lambda: os.popen("true").close(), census_state)
    assert "inherits an environment without HOME" in line, line


# THE ONE EXEMPTION.
@pytest.mark.child_env_without_home(reason="pins the exemption itself")
def test_the_opt_out_with_a_reason_exempts_both_rules(monkeypatch, census_state):
    _accepted(lambda: subprocess.run(["true"], env={"PATH": os.defpath}), census_state)
    monkeypatch.delenv("HOME")
    _accepted(lambda: subprocess.run(["true"]), census_state)


@pytest.mark.child_env_without_home("pins the positional reason")
def test_the_opt_out_takes_its_reason_positionally(census_state):
    _accepted(lambda: subprocess.run(["true"], env={"PATH": os.defpath}), census_state)


@pytest.mark.child_env_without_home
def test_the_opt_out_without_a_reason_exempts_nothing(census_state):
    line = _refused(lambda: subprocess.run(["true"], env={"PATH": os.defpath}), census_state)
    assert "marker has no reason" in line, line


@pytest.mark.child_env_without_home(reason="  ")
def test_the_opt_out_with_a_blank_reason_exempts_nothing(census_state):
    line = _refused(lambda: subprocess.run(["true"], env={"PATH": os.defpath}), census_state)
    assert "marker has no reason" in line, line


@pytest.mark.child_env_without_home(reason=1)
def test_the_opt_out_with_a_non_text_reason_exempts_nothing(census_state):
    line = _refused(lambda: subprocess.run(["true"], env={"PATH": os.defpath}), census_state)
    assert "marker has no reason" in line, line


@pytest.mark.child_env_without_home(reason="a class marker reaches its tests")
class TestAClassMarker:
    def test_a_class_marker_exempts_its_tests(self, census_state):
        _accepted(lambda: subprocess.run(["true"], env={"PATH": os.defpath}), census_state)


def test_the_opt_out_marker_is_registered(request):
    assert any(line.startswith(census.MARKER + "(reason)")
               for line in request.config.getini("markers"))


def test_the_exemption_ends_with_its_test(census_state):
    """This test carries no marker, so no exemption a marked test set may still
    be in force while it runs."""
    assert census_state["exempt"] is None and census_state["unreasoned"] is False
    line = _refused(lambda: subprocess.run(["true"], env={"PATH": os.defpath}), census_state)
    assert "marker has no reason" not in line, line


def _item(kwargs):
    mark = type("Mark", (), {"args": (), "kwargs": kwargs})()
    return type("Item", (), {"get_closest_marker": lambda self, name: mark})()


@pytest.mark.parametrize("kwargs, exempt, unreasoned", [
    ({"reason": "r"}, "r", False),
    ({}, None, True),
], ids=["reasoned", "reasonless"])
def test_leave_clears_what_enter_set(census_state, kwargs, exempt, unreasoned):
    census.enter(_item(kwargs))
    try:
        assert (census_state["exempt"], census_state["unreasoned"]) == (exempt, unreasoned)
    finally:
        census.leave()
    assert (census_state["exempt"], census_state["unreasoned"]) == (None, False)
    line = _refused(lambda: subprocess.run(["true"], env={"PATH": os.defpath}), census_state)
    assert "marker has no reason" not in line, line


def test_the_plugin_enters_and_leaves_around_each_test(request, census_state):
    """MUTANT: the census Plugin's pytest_runtest_protocol stops calling enter()
    or leave()."""
    plugin = request.config.pluginmanager.get_plugin(census.PLUGIN_NAME)
    assert isinstance(plugin, census.Plugin), plugin
    wrapper = plugin.pytest_runtest_protocol(item=_item({"reason": "r"}), nextitem=None)
    next(wrapper)
    assert census_state["exempt"] == "r"
    with pytest.raises(StopIteration):
        next(wrapper)
    assert census_state["exempt"] is None


def test_a_second_install_adds_no_second_hook(monkeypatch):
    calls = []
    monkeypatch.setattr(census.sys, "addaudithook", calls.append)
    census.install()
    assert calls == [], "the census is already installed in this session"
    monkeypatch.setitem(census._state, "installed", False)
    census.install()
    assert calls == [census._audit] and census._state["installed"] is True


def test_a_refusal_outside_a_test_says_so(monkeypatch, census_state):
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    line = _refused(lambda: census._audit("subprocess.Popen", (None, ["true"], None, {})),
                    census_state)
    assert line.startswith("<outside a test>: child [true]"), line


@pytest.mark.parametrize("argv, shown", [
    ("echo  hi", "[echo hi]"),
    (b"echo hi", "[echo hi]"),
    (["true", 7], "[true 7]"),
    (Path("/bin/true"), "[/bin/true]"),
    (5, "[5]"),
], ids=["shell-text", "shell-bytes", "non-text-word", "path", "not-iterable"])
def test_the_refusal_shows_the_launch_as_given(argv, shown, census_state):
    line = _refused(lambda: census._audit("subprocess.Popen", (None, argv, None, {})),
                    census_state)
    assert shown in line, line


def test_collect_takes_nothing_from_a_node_without_output(census_state):
    census_state["forwarded"][:] = []
    census.collect(type("Node", (), {})())
    census.collect(type("Node", (), {"workeroutput": None})())
    assert census_state["forwarded"] == []


def test_finish_without_a_terminal_reporter_writes_to_stderr(census_state, capsys):
    census_state["misses"][:] = [_LINE]
    census_state["forwarded"][:] = []
    session = _Session(_Config(None))
    census.finish(session)
    assert session.exitstatus == 1
    assert capsys.readouterr().err.splitlines() == [
        "HOME CENSUS: 1 distinct child launch(es) without HOME refused", "  " + _LINE]


def test_other_audit_events_are_ignored(census_state):
    before = list(census_state["misses"])
    census._audit("os.system", ("true",))
    census._audit("open", ("/x", "r", 0))
    assert census_state["misses"] == before


def test_an_env_whose_keys_cannot_be_read_is_a_fault_that_fails_the_run(census_state):
    """The census reads an env's keys by iterating it; an env whose iteration
    raises is recorded as a fault, and the fault names that error."""
    class Opaque:
        def __iter__(self):
            raise LookupError("these keys cannot be read")
    before, misses = len(census_state["faults"]), list(census_state["misses"])
    census._audit("subprocess.Popen", (None, ["true"], None, Opaque()))
    added = census_state["faults"][before:]
    assert len(added) == 1 and "could not inspect the environment" in added[0], added
    assert "LookupError('these keys cannot be read')" in added[0], added
    assert "test_an_env_whose_keys_cannot_be_read" in added[0], added
    assert census_state["misses"] == misses, "a fault is not a launch refused for lacking HOME"


_FAULT = "t::f (call): HOME census could not inspect the environment of [true]: LookupError()"


@pytest.mark.parametrize("refusals, faults, expected", [
    ([], [_FAULT], ["HOME CENSUS: 1 launch(es) the census could not inspect", "  " + _FAULT]),
    (["t::r (call): child [true] is given an environment without HOME"], [_FAULT],
     ["HOME CENSUS: 1 distinct child launch(es) without HOME refused",
      "  t::r (call): child [true] is given an environment without HOME",
      "HOME CENSUS: 1 launch(es) the census could not inspect", "  " + _FAULT]),
], ids=["fault-only", "refusal-and-fault"])
def test_a_fault_is_counted_under_its_own_header(census_state, refusals, faults, expected):
    """MUTANT: a fault line is counted with the launches refused for lacking HOME.
    The refusal header then counts a launch that was never judged, and a run
    with only a fault reads as a run with a HOME-less child."""
    census_state["misses"][:], census_state["forwarded"][:] = refusals, []
    census_state["faults"][:], census_state["forwarded_faults"][:] = faults, []
    reporter = _Reporter()
    session = _Session(_Config(reporter))
    census.finish(session)
    assert reporter.lines == expected
    assert session.exitstatus == 1


def test_a_worker_hands_its_faults_to_the_controller(census_state):
    census_state["misses"][:], census_state["forwarded"][:] = [], []
    census_state["faults"][:], census_state["forwarded_faults"][:] = [_FAULT], []
    workeroutput = {}
    census.finish(_Session(_Config(_Reporter(), workeroutput=workeroutput)))
    assert workeroutput[census.WORKEROUTPUT_FAULTS_KEY] == [_FAULT]
    assert workeroutput[census.WORKEROUTPUT_KEY] == []
    census_state["faults"][:] = []  # the controller process recorded nothing itself
    census.collect(type("Node", (), {"workeroutput": workeroutput,
                                     "gateway": type("Gateway", (), {"id": "gw2"})()})())
    reporter = _Reporter()
    census.finish(_Session(_Config(reporter)))
    assert reporter.lines == ["HOME CENSUS: 1 launch(es) the census could not inspect",
                              "  [gw2] " + _FAULT]


def _interpreter(tmp_path, where):
    """The running interpreter, or a link to it at a path whose length alone
    crosses the refusal line's 200-character launch text."""
    if where == "as-installed":
        return PY
    link = tmp_path / ("d" * 100) / ("e" * 100) / "python3"
    link.parent.mkdir(parents=True)
    link.symlink_to(PY)
    return str(link)


@pytest.mark.parametrize("where", ["as-installed", "long-path"])
def test_the_census_is_live_and_refuses_before_the_child_starts(tmp_path, census_state, where):
    """The refusal names the test and shows the launch as given, however long
    the interpreter's path, and the child never starts."""
    assert census.installed(), "pact-plugin/conftest.py did not install the census"
    marker = tmp_path / "started"
    argv = [_interpreter(tmp_path, where), "-c",
            "import pathlib, sys; pathlib.Path(sys.argv[1]).touch()", str(marker)]
    line = _refused(lambda: subprocess.run(argv, env={"PATH": os.defpath}), census_state)
    assert not marker.exists(), "the child started before the refusal"
    shown = census._shown(argv)
    assert shown and " ".join(argv).startswith(shown), shown
    assert "test_the_census_is_live" in line and "child [%s]" % shown in line, line


@pytest.mark.parametrize("length, shown_length", [(199, 199), (200, 200), (201, 200),
                                                  (450, 200)])
def test_a_launch_is_shown_cut_to_200_characters(length, shown_length):
    """MUTANTS: the cap is dropped, or moved off 200. A launch text at the cap
    is shown whole; a longer one is cut to the cap."""
    argv = ["x" * (length - 3), "-c"]  # joined with a space: exactly `length` characters
    text = " ".join(argv)
    assert len(text) == length
    assert census._shown(argv) == text[:shown_length]
    assert len(census._shown(argv)) == shown_length


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


_LINE = "t::a: child [x] is given an environment without HOME; " + census.REMEDY


def test_finish_fails_the_session_and_reports_each_refusal(census_state):
    census_state["misses"][:] = [_LINE]
    census_state["forwarded"][:] = []
    reporter = _Reporter()
    session = _Session(_Config(reporter))
    census.finish(session)
    assert session.exitstatus == 1
    assert reporter.lines == ["HOME CENSUS: 1 distinct child launch(es) without HOME refused", "  " + _LINE]


def test_an_identical_refusal_is_counted_once(census_state):
    """The header counts distinct refusal lines, as it says."""
    census_state["misses"][:] = []
    census_state["forwarded"][:] = []
    census._record(_LINE)
    census._record(_LINE)
    reporter = _Reporter()
    census.finish(_Session(_Config(reporter)))
    assert reporter.lines == ["HOME CENSUS: 1 distinct child launch(es) without HOME refused", "  " + _LINE]


@pytest.mark.parametrize("prior, after", [(0, 1), (1, 1), (2, 2), (3, 3), (4, 4)])
def test_a_refusal_raises_only_a_clean_status(census_state, prior, after):
    census_state["misses"][:] = [_LINE]
    census_state["forwarded"][:] = []
    session = _Session(_Config(_Reporter()))
    session.exitstatus = prior
    census.finish(session)
    assert session.exitstatus == after


def test_finish_with_no_refusal_reports_the_launches_checked(census_state):
    census_state["misses"][:] = []
    census_state["forwarded"][:] = []
    census_state["checked"], census_state["forwarded_checked"] = 3, 4
    reporter = _Reporter()
    session = _Session(_Config(reporter))
    census.finish(session)
    assert session.exitstatus == 0
    assert reporter.lines == ["HOME CENSUS: installed, 7 launches checked, 0 refused"]


def test_one_launch_checked_is_reported_in_the_singular(census_state):
    census_state["misses"][:] = []
    census_state["forwarded"][:] = []
    census_state["checked"], census_state["forwarded_checked"] = 1, 0
    reporter = _Reporter()
    census.finish(_Session(_Config(reporter)))
    assert reporter.lines == ["HOME CENSUS: installed, 1 launch checked, 0 refused"]


def test_no_launch_checked_is_reported_in_the_plural(census_state):
    census_state["misses"][:] = []
    census_state["forwarded"][:] = []
    census_state["checked"], census_state["forwarded_checked"] = 0, 0
    reporter = _Reporter()
    census.finish(_Session(_Config(reporter)))
    assert reporter.lines == ["HOME CENSUS: installed, 0 launches checked, 0 refused"]


def test_a_census_never_installed_reports_nothing_on_a_clean_run(census_state, monkeypatch):
    census_state["misses"][:] = []
    census_state["forwarded"][:] = []
    monkeypatch.setitem(census._state, "installed", False)
    reporter = _Reporter()
    session = _Session(_Config(reporter))
    census.finish(session)
    assert session.exitstatus == 0 and reporter.lines == []


def test_each_judged_launch_is_counted_and_an_exempt_one_is_not(census_state):
    before = census_state["checked"]
    subprocess.run(["true"])
    os.waitpid(os.posix_spawn("/bin/true", ["true"], dict(os.environ)), 0)
    census._audit("os.system", ("true",))
    assert census_state["checked"] - before == 2
    census.enter(_item({"reason": "r"}))
    try:
        subprocess.run(["true"], env={"PATH": os.defpath})
    finally:
        census.leave()
    assert census_state["checked"] - before == 2


def test_a_worker_hands_its_refusals_to_the_controller(census_state):
    census_state["misses"][:] = [_LINE]
    census_state["forwarded"][:] = []
    workeroutput = {}
    census_state["checked"] = 5
    worker = _Session(_Config(_Reporter(), workeroutput=workeroutput))
    census.finish(worker)
    assert worker.exitstatus == 0, "a worker's exit status is ignored by xdist"
    assert workeroutput[census.WORKEROUTPUT_KEY] == [_LINE]
    assert workeroutput[census.WORKEROUTPUT_COUNT_KEY] == 5

    census_state["misses"][:] = []  # the controller process recorded nothing itself
    node = type("Node", (), {"workeroutput": workeroutput,
                             "gateway": type("Gateway", (), {"id": "gw3"})()})()
    census.collect(node)
    reporter = _Reporter()
    controller = _Session(_Config(reporter))
    census.finish(controller)
    assert controller.exitstatus == 1
    assert reporter.lines[1] == "  [gw3] " + _LINE


def test_the_controller_sums_every_workers_launch_count(census_state):
    census_state["misses"][:] = []
    census_state["forwarded"][:] = []
    census_state["checked"], census_state["forwarded_checked"] = 0, 0
    for name, count in (("gw0", 2), ("gw1", 9)):
        census.collect(type("Node", (), {
            "workeroutput": {census.WORKEROUTPUT_KEY: [], census.WORKEROUTPUT_COUNT_KEY: count},
            "gateway": type("Gateway", (), {"id": name})()})())
    census.collect(type("Node", (), {"workeroutput": {census.WORKEROUTPUT_KEY: []}})())
    reporter = _Reporter()
    census.finish(_Session(_Config(reporter)))
    assert reporter.lines == ["HOME CENSUS: installed, 11 launches checked, 0 refused"]


def test_a_repeated_refusal_is_recorded_once(census_state):
    before = len(census_state["misses"])
    for _ in range(2):
        with pytest.raises(census.HomeCensusError):
            subprocess.run(["true"], env={"PATH": os.defpath})
    assert len(census_state["misses"]) == before + 1


# A rig session loads tests/conftest.py and registers the HOME census as
# pact-plugin/conftest.py does, and refuses to run unless exactly one census
# is installed, so no rig arm that expects no refusal can pass with none.
_RIG_CONFTEST = '''
import importlib.util

import pytest

_spec = importlib.util.spec_from_file_location("pact_suite_conftest", {conftest!r})
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)

import home_census  # noqa: E402  (tests/ is on sys.path once the suite conftest ran)


def pytest_configure(config):
    _suite.pytest_configure(config)
    home_census.register(config)
    plugins = [p for p in config.pluginmanager.get_plugins()
               if isinstance(p, home_census.Plugin)]
    if not home_census.installed() or len(plugins) != 1:
        raise pytest.UsageError("rig: expected one installed HOME census, found %d "
                                "plugin(s), installed=%s" % (len(plugins), home_census.installed()))


pytest_testnodedown = _suite.pytest_testnodedown
pytest_sessionfinish = _suite.pytest_sessionfinish
'''

_RIG_TESTS = '''
import os
import subprocess


def test_swallows_the_census_error():
    try:
        subprocess.run(["true"], env={env})
    except Exception:
        pass


def test_plain():
    pass
'''


def _child_environ():
    """This process's environment without any PYTEST_* name, so a child pytest
    takes no PYTEST_ADDOPTS, PYTEST_PLUGINS or PYTEST_DISABLE_PLUGIN_AUTOLOAD
    (nor this session's xdist or current-test names) from the shell that runs
    the suite. A rig adds back what it sets on purpose."""
    return {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}


def _run_rig(tmp_path, env_literal):
    (tmp_path / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (tmp_path / "test_swallow.py").write_text(
        _RIG_TESTS.format(env=env_literal), encoding="utf-8")
    env = _child_environ()
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-n", "2",
         "test_swallow.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


def test_under_xdist_a_swallowed_refusal_still_fails_the_run(tmp_path):
    rc, output = _run_rig(tmp_path, '{"PATH": os.defpath}')
    assert "2 passed" in output, output
    assert rc == 1, output
    assert "HOME CENSUS: 1 " in output, output
    # The report starts its own line, not glued onto xdist's progress line.
    assert re.search(r"^HOME CENSUS: 1 distinct child launch\(es\) without HOME refused$", output, re.M), output
    assert "test_swallows_the_census_error" in output and census.REMEDY in output, output


def test_under_xdist_a_child_with_home_passes_the_run(tmp_path):
    rc, output = _run_rig(tmp_path, '{"PATH": os.defpath, "HOME": os.environ["HOME"]}')
    assert "2 passed" in output, output
    assert rc == 0, output
    assert "without HOME refused" not in output, output
    # The workers' launches (the test's own and any its session makes) reach
    # the controller's information line.
    assert re.search(r"^HOME CENSUS: installed, [1-9][0-9]* launch(es)? checked, 0 refused$",
                     output, re.M), output


# A wider-scoped fixture whose finalizer, registered on a wider node, launches a
# child without HOME. It runs in the teardown of whichever test is last in that
# node, so the order rows put the marked test first and last.
_NODE_FINALIZER_RIG = '''
import os
import subprocess

import pytest


def _launch():
    try:
        subprocess.run(["true"], env={{"PATH": os.defpath}})
    except Exception:
        pass


@pytest.fixture(scope="{scope}")
def shared(request):
    request.{owner}.addfinalizer(_launch)
'''
_MARKED_TEST = '''
@pytest.mark.child_env_without_home(reason="rig: marked")
def test_marked(shared):
    pass
'''
_PLAIN_TEST = '''
def test_plain(shared):
    pass
'''


def _run_order_rig(tmp_path, owner, scope, marked_first, xdist=False):
    (tmp_path / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    tests = (_MARKED_TEST + _PLAIN_TEST) if marked_first else (_PLAIN_TEST + _MARKED_TEST)
    (tmp_path / "test_order.py").write_text(
        _NODE_FINALIZER_RIG.format(owner=owner, scope=scope) + tests, encoding="utf-8")
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         *(["-n", "1"] if xdist else []), "test_order.py"],
        cwd=tmp_path, env=_child_environ(), capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


@pytest.mark.parametrize("owner, scope", [("node", "module"), ("session", "session")])
@pytest.mark.parametrize("marked_first", [True, False], ids=["marked-first", "marked-last"])
def test_a_wider_node_finalizer_is_never_exempt_whatever_the_order(tmp_path, owner, scope,
                                                                   marked_first):
    """MUTANT: the census lets the exemption of the test running at the time cover
    a finalizer registered on a wider node. With the marked test last, that
    finalizer runs in the marked test's teardown and passes; first, it is refused."""
    rc, output = _run_order_rig(tmp_path, owner, scope, marked_first)
    assert "2 passed" in output, output
    assert rc == 1, output
    assert output.count("HOME CENSUS: 1 distinct child launch(es) without HOME refused") == 1, output
    lines = [line for line in output.splitlines() if ": child [" in line]
    assert len(lines) == 1, lines
    if marked_first:  # the finalizer runs in the unmarked test's teardown
        assert "test_plain (teardown)" in lines[0], lines
    else:
        assert "test_marked (teardown)" in lines[0], lines
        assert "wider-scoped fixture or node finalizer" in lines[0], lines


def test_under_xdist_a_wider_node_finalizer_after_the_last_test_is_refused(tmp_path):
    """The worker's last test has no next test, so its teardown tears down every
    node; the marked test is last, and the module finalizer is still refused."""
    pytest.importorskip("xdist")
    rc, output = _run_order_rig(tmp_path, "node", "module", marked_first=False, xdist=True)
    assert "2 passed" in output, output
    assert rc == 1, output
    assert output.count("HOME CENSUS: 1 distinct child launch(es) without HOME refused") == 1, output


def test_a_marked_tests_own_finalizer_stays_exempt(tmp_path):
    """The control: a function-scoped fixture's request.node is the test itself,
    so its finalizer runs before the wider teardown and the marker covers it."""
    rc, output = _run_order_rig(tmp_path, "node", "function", marked_first=False)
    assert "2 passed" in output, output
    assert rc == 1, output  # the unmarked test's own finalizer is refused
    assert output.count("HOME CENSUS: 1 distinct child launch(es) without HOME refused") == 1, output
    lines = [line for line in output.splitlines() if ": child [" in line]
    assert len(lines) == 1 and "test_plain" in lines[0], lines


def _home_census_registration(source):
    """(whether `source` imports home_census, the census register calls its
    pytest_configure makes, as source text)."""
    tree = ast.parse(source)
    imports = any(isinstance(node, ast.Import)
                  and any(alias.name == "home_census" and alias.asname is None
                          for alias in node.names)
                  for node in ast.walk(tree))
    calls = [ast.unparse(node)
             for function in tree.body
             if isinstance(function, ast.FunctionDef) and function.name == "pytest_configure"
             for node in ast.walk(function)
             if isinstance(node, ast.Call) and ast.unparse(node.func) == "home_census.register"]
    return imports, calls


def test_the_rig_registers_the_census_as_the_root_conftest_does():
    """The xdist rig copies the root conftest's census wiring (see _RIG_CONFTEST):
    both import home_census and call its register(config) exactly once from
    pytest_configure, so the rig exercises the real install path."""
    root = (Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8")
    expected = (True, ["home_census.register(config)"])
    assert _home_census_registration(root) == expected, _home_census_registration(root)
    rig = _RIG_CONFTEST.format(conftest="conftest.py")
    assert _home_census_registration(rig) == expected, _home_census_registration(rig)


# The xdist twin of the rig arms above, through the real pact-plugin/conftest.py
# and tests/conftest.py rather than _RIG_CONFTEST. A plugin, not a test file, so
# the session collects only real suite tests. It launches one child while one
# named test runs, swallows whatever the census raises, and lets the test pass.
_XDIST_TARGET = "tests/test_session_home_sandbox.py::test_the_session_home_is_not_the_real_home"
_XDIST_PROBE = """
import os
import subprocess


def pytest_runtest_call(item):
    # The node id is relative to the rootdir, which may sit above the plugin root.
    target = os.environ["HOME_CENSUS_PROBE_TARGET"]
    if item.nodeid != target and not item.nodeid.endswith("/" + target):
        return
    env = {"PATH": os.defpath}
    if os.environ.get("HOME_CENSUS_PROBE_HOME"):
        env["HOME"] = os.environ["HOME"]
    try:
        subprocess.run(["true"], env=env, capture_output=True, timeout=60)
        outcome = "ran"
    except Exception as exc:
        outcome = "refused: %r" % (exc,)
    with open(os.environ["HOME_CENSUS_PROBE_MARKER"], "w", encoding="utf-8") as handle:
        handle.write("%s %s" % (os.environ.get("PYTEST_XDIST_WORKER", "-"), outcome))
"""


@pytest.mark.parametrize("with_home", [False, True], ids=["without-home", "with-home"])
def test_under_xdist_the_real_root_conftest_fails_a_swallowed_refusal(tmp_path, with_home):
    """A real `-n 2` session at the plugin root loads pact-plugin/conftest.py and
    tests/conftest.py as the suite does. One named test launches a child without
    HOME on its worker and swallows the refusal: the worker hands it to the
    controller, which fails the session with one HOME census report naming that
    test and its worker. With HOME, the same child runs and the session passes.
    GITHUB_* is removed so the CI environment binding and the full-run recorder,
    both gated on GITHUB_ACTIONS, stay inert; the census reads no GITHUB_ name
    and the root conftest registers it unconditionally."""
    pytest.importorskip("xdist")
    plugin_root = Path(__file__).resolve().parents[1]
    (tmp_path / "home_census_xdist_probe.py").write_text(_XDIST_PROBE, encoding="utf-8")
    marker = tmp_path / "outcome"
    env = {key: value for key, value in _child_environ().items()
           if not key.startswith("GITHUB_")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tmp_path), env.get("PYTHONPATH")]))
    env["HOME_CENSUS_PROBE_MARKER"] = str(marker)
    env["HOME_CENSUS_PROBE_TARGET"] = _XDIST_TARGET
    if with_home:
        env["HOME_CENSUS_PROBE_HOME"] = "1"
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-n", "2",
         "-p", "home_census_xdist_probe", _XDIST_TARGET.split("::")[0]],
        cwd=plugin_root, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    worker, _, outcome = marker.read_text(encoding="utf-8").partition(" ")
    assert worker.startswith("gw"), (worker, output)  # the launch ran on a worker
    if with_home:
        assert outcome == "ran", output
        assert proc.returncode == 0, output
        assert "without HOME refused" not in output, output
    else:
        assert outcome.startswith("refused: HomeCensusError"), output
        assert proc.returncode == 1, output
        assert output.count("HOME CENSUS: 1 distinct child launch(es) without HOME refused") == 1, output
        lines = [line for line in output.splitlines() if ": child [" in line]
        assert len(lines) == 1 and _XDIST_TARGET in lines[0], lines
        assert ("[%s] " % worker) in lines[0], lines


_SKILLS_RIG_TESTS = '''
import os
import subprocess

import pytest


@pytest.mark.child_env_without_home(reason="rig: a marked skills test")
def test_marked_in_skills():
    subprocess.run(["true"], env={"PATH": os.defpath})


def test_unmarked_in_skills():
    try:
        subprocess.run(["true"], env={"PATH": os.defpath})
    except Exception:
        pass
'''


@pytest.mark.parametrize("order", [("tests", "skills"), ("skills", "tests")],
                         ids=["tests-first", "skills-first"])
def test_the_marker_exempts_a_skills_test_in_a_session_that_loads_the_conftest(tmp_path, order):
    """The census Plugin's pytest_runtest_protocol runs for every test of the
    session, so a marked test under skills/ is exempt and its unmarked twin is
    refused, whichever directory is collected first. The rig mirrors the plugin's
    layout: under tests/, a conftest that registers the census as
    pact-plugin/conftest.py does; the tests under skills/<skill>/."""
    plugin = tmp_path / "pact-plugin"
    (plugin / "tests").mkdir(parents=True)
    (plugin / "skills" / "rig-skill").mkdir(parents=True)
    (plugin / "tests" / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (plugin / "tests" / "test_plain.py").write_text("def test_plain():\n    pass\n",
                                                    encoding="utf-8")
    (plugin / "skills" / "rig-skill" / "test_rig_skill.py").write_text(
        _SKILLS_RIG_TESTS, encoding="utf-8")
    env = _child_environ()
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", *order],
        cwd=plugin, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    assert "3 passed" in output, output
    assert proc.returncode == 1, output
    assert "HOME CENSUS: 1 distinct child launch(es) without HOME refused" in output, output
    assert "test_unmarked_in_skills" in output and "test_marked_in_skills" not in output, output


_SCOPE_RIG_TESTS = '''
import os
import subprocess

import pytest


def _launch(where):
    try:
        subprocess.run(["true"], env={"PATH": os.defpath})
        outcome = "ran"
    except Exception:
        outcome = "refused"
    with open(os.environ["HOME_CENSUS_SCOPE_LOG"], "a", encoding="utf-8") as log:
        log.write("%s: %s\\n" % (where, outcome))


@pytest.fixture(scope="module")
def shared():
    _launch("module setup")
    yield
    _launch("module teardown")


@pytest.fixture
def per_test():
    _launch("function setup")
    yield
    _launch("function teardown")


@pytest.mark.child_env_without_home(reason="rig: a marked test that triggers a module fixture")
def test_marked(shared, per_test):
    _launch("call")
'''


_SCOPE_RIG_AFTER = '''
from test_scope import _launch

import pytest


@pytest.mark.child_env_without_home(reason="rig: a marked test after the module fixture")
def test_marked_after():
    _launch("later call")
'''


def test_the_marker_never_covers_a_wider_scoped_fixture(tmp_path):
    """A module-scoped fixture first triggered by a marked test is refused in its
    setup and in its teardown, while the marked test's call and its
    function-scoped fixture stay exempt, and a marked test in the next module is
    exempt again once that fixture is torn down."""
    (tmp_path / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (tmp_path / "test_scope.py").write_text(_SCOPE_RIG_TESTS, encoding="utf-8")
    (tmp_path / "test_scope_after.py").write_text(_SCOPE_RIG_AFTER, encoding="utf-8")
    log = tmp_path / "launches.log"
    env = _child_environ()
    env["HOME_CENSUS_SCOPE_LOG"] = str(log)
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "test_scope.py", "test_scope_after.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "module setup: refused", "function setup: ran", "call: ran",
        "function teardown: ran", "module teardown: refused", "later call: ran"], output
    assert "HOME CENSUS: 2 distinct child launch(es) without HOME refused" in output, output
    assert "in a wider-scoped fixture" in output, output
    assert proc.returncode == 1, output


_LAYOUT_LAUNCH = '''
import os
import subprocess

import pytest


def _launch(where):
    try:
        subprocess.run(["true"], env={"PATH": os.defpath})
        outcome = "ran"
    except Exception:
        outcome = "refused"
    with open(os.environ["HOME_CENSUS_SCOPE_LOG"], "a", encoding="utf-8") as log:
        log.write("%s: %s\\n" % (where, outcome))
'''

_LAYOUT_TESTS = _LAYOUT_LAUNCH + '''

@pytest.fixture(scope="session")
def shared():
    _launch("session setup")
    yield
    _launch("session teardown")


@pytest.mark.child_env_without_home(reason="rig: a marked test that triggers a session fixture")
def test_marked(shared, request):
    plugins = [p for p in request.config.pluginmanager.get_plugins()
               if type(p).__name__ == "Plugin" and type(p).__module__ == "home_census"]
    assert len(plugins) == 1, plugins
    _launch("tests call")
'''

_LAYOUT_SKILL = _LAYOUT_LAUNCH + '''

@pytest.fixture(scope="module")
def mod():
    _launch("skills module setup")
    yield
    _launch("skills module teardown")


@pytest.mark.child_env_without_home(reason="rig: a marked skills test")
def test_marked_skill(mod):
    _launch("skills call")
'''


def test_the_marker_never_covers_a_session_or_skills_fixture(tmp_path):
    """The plugin's layout: the rootdir above tests/conftest.py. A session
    fixture's node is the rootdir and a skills/ module is outside tests/, so a
    conftest hook would not see either; the registered plugin does. Both
    fixtures are refused in setup and teardown under a marked test, whose own
    calls stay exempt, and exactly one census Plugin is registered."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "skills" / "rig-skill").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "tests" / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (root / "tests" / "test_layout.py").write_text(_LAYOUT_TESTS, encoding="utf-8")
    (root / "skills" / "rig-skill" / "test_rig_skill.py").write_text(_LAYOUT_SKILL,
                                                                     encoding="utf-8")
    log = tmp_path / "launches.log"
    env = _child_environ()
    env["HOME_CENSUS_SCOPE_LOG"] = str(log)
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "tests", "skills"],
        cwd=root, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    assert "2 passed" in output, output
    assert sorted(log.read_text(encoding="utf-8").splitlines()) == sorted([
        "session setup: refused", "tests call: ran", "skills module setup: refused",
        "skills call: ran", "skills module teardown: refused",
        "session teardown: refused"]), output
    assert proc.returncode == 1, output


def test_this_session_registers_one_census_plugin(request):
    plugins = [p for p in request.config.pluginmanager.get_plugins()
               if isinstance(p, census.Plugin)]
    assert len(plugins) == 1 and census.installed(), plugins
    assert request.config.pluginmanager.get_plugin(census.PLUGIN_NAME) is plugins[0]


def _real_session(*paths, plugin_dir=None, marker=None):
    """A child pytest session over real tracked paths, rooted at the plugin.
    With `plugin_dir`, it also loads the launch probe plugin written there, which
    writes the outcome of one HOME-less launch to `marker`."""
    env = _child_environ()
    extra = []
    if plugin_dir is not None:
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(plugin_dir), env.get("PYTHONPATH")]))
        env["HOME_CENSUS_PROBE_MARKER"] = str(marker)
        extra = ["-p", "home_census_launch_probe"]
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", *extra, *paths],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True,
        timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


_SKILLS_TEST = "skills/pact-handoff-harvest/test_skill_loading_harvest.py"

# A plugin, not a test file: a test file outside the plugin tree would make the
# child collect from the common ancestor of the two paths. At the end of the
# session it makes one launch without HOME and records whether it ran.
_LAUNCH_PROBE = '''
import os
import subprocess

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session):
    try:
        subprocess.run(["true"], env={"PATH": os.defpath}, check=True)
        outcome = "ran"
    except Exception as exc:
        outcome = "refused: %r" % (exc,)
    with open(os.environ["HOME_CENSUS_PROBE_MARKER"], "w", encoding="utf-8") as handle:
        handle.write(outcome)
'''


@pytest.mark.parametrize("paths", [(_SKILLS_TEST,),
                                   (_SKILLS_TEST, "tests/test_session_home_sandbox.py")],
                         ids=["skills-alone", "skills-and-tests"])
def test_a_session_with_or_without_the_tests_conftest_has_one_census(tmp_path, paths):
    """A real session over a skills/ test alone loads pact-plugin/conftest.py but
    not tests/conftest.py, and its twin loads both. Each installs one census:
    one census line, and a launch without HOME made in that session is refused
    before its child starts."""
    if not (Path(__file__).resolve().parents[1] / _SKILLS_TEST).is_file():
        pytest.skip("SKIPPED, NOT PASSED: %s is not in this tree" % _SKILLS_TEST)
    rc, output = _real_session(*paths)
    assert rc == 0 and re.search(r"^[0-9]+ passed", output, re.M), output
    assert len(re.findall(r"HOME CENSUS: installed, [0-9]+ launch(es)? checked, 0 refused",
                          output)) == 1, output
    assert output.count("HOME CENSUS") == 1, output

    (tmp_path / "home_census_launch_probe.py").write_text(_LAUNCH_PROBE, encoding="utf-8")
    marker = tmp_path / "outcome"
    rc, output = _real_session(*paths, plugin_dir=tmp_path, marker=marker)
    assert marker.read_text(encoding="utf-8").startswith("refused: HomeCensusError"), output
    assert "HOME CENSUS: 1 distinct child launch(es) without HOME refused" in output, output
    assert output.count("HOME CENSUS") == 1, output
    assert rc == 1, output


# tests/conftest.py removes these from the session's environment; the rig also
# drops them per call, so a test that exports one itself (the decoy rows below)
# cannot reach the rig either.
def _git_rig_env():
    env = {key: value for key, value in os.environ.items()
           if not is_git_redirecting_variable(key)}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git_out(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          timeout=120, env=_git_rig_env()).stdout.strip()


def _select_after_changing(rig, base, changed):
    """Append a name this module's source cannot contain to `changed`, so the
    change reaches this module only through how it depends on that file; commit
    it, and ask the rig's own selector. The selector runs git through
    os.environ, so the repository-locating variables are out of it meanwhile."""
    import importlib.util
    import uuid

    changed_file = rig / changed
    changed_file.write_text(changed_file.read_text(encoding="utf-8")
                            + "\n_probe_%s = 1\n" % uuid.uuid4().hex, encoding="utf-8")
    _git_rig("commit", "-qam", "change it", cwd=rig)
    spec = importlib.util.spec_from_file_location(
        "_home_census_selection_probe", rig / "scripts" / "fast_suite_selection.py")
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core  # dataclass annotations resolve through sys.modules
    saved = {name: os.environ.pop(name) for name in list(os.environ)
             if is_git_redirecting_variable(name)}
    try:
        spec.loader.exec_module(core)
        manifest = core.load_manifest(rig / "scripts" / "fast_suite_exclusions.json")
        selection, changed_paths, status = core.select_for_change(rig, manifest, base)
    finally:
        os.environ.update(saved)
        sys.modules.pop(spec.name, None)
    return selection, changed_paths, status


def _git_rig(*args, cwd):
    subprocess.run(["git", "-c", "user.name=rig", "-c", "user.email=rig@invalid", *args],
                   cwd=cwd, check=True, capture_output=True, timeout=120, env=_git_rig_env())


def test_the_git_rig_ignores_a_repository_the_shell_points_git_at(tmp_path, monkeypatch):
    """MUTANT: _git_rig passes the shell's GIT_DIR, GIT_WORK_TREE and
    GIT_INDEX_FILE through. The rig then commits into the decoy repository and
    leaves its own directory without one."""
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, capture_output=True,
                   timeout=60, env=_git_rig_env())
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "a.txt").write_text("a\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))
    _git_rig("init", "-q", cwd=rig)
    _git_rig("add", "-A", cwd=rig)
    _git_rig("commit", "-qm", "base", cwd=rig)
    assert (rig / ".git" / "HEAD").is_file()
    clean = _git_rig_env()
    log = subprocess.run(["git", "log", "--oneline"], cwd=rig, capture_output=True, text=True,
                         timeout=60, env=clean)
    assert log.returncode == 0 and log.stdout.count("\n") == 1, log
    decoy_log = subprocess.run(["git", "log", "--oneline"], cwd=decoy, capture_output=True,
                               text=True, timeout=60, env=clean)
    assert decoy_log.returncode != 0, ("the decoy repository received a commit", decoy_log.stdout)


@pytest.fixture(scope="module")
def _tracked_tree_rig(tmp_path_factory):
    """One copy of the tracked tree, committed as base in a fresh repository
    with no remote, for every row of the selection pin below. The tree is about
    29 MB, so a copy per row would write that much again for each row. The real
    checkout is only read."""
    root = Path(__file__).resolve().parents[2]
    if not (root / "scripts" / "fast_suite_selection.py").is_file():
        pytest.skip("SKIPPED, NOT PASSED: not a PACT source checkout, so there is no selector to ask")
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True,
                            timeout=120, env=_git_rig_env()).stdout.decode("utf-8").split("\0")
    rig = tmp_path_factory.mktemp("selection") / "repo"
    for rel in filter(None, listed):
        source = root / rel
        if source.is_file() and not source.is_symlink():
            target = rig / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    _git_rig("init", "-q", cwd=rig)
    _git_rig("add", "-A", cwd=rig)
    _git_rig("commit", "-qm", "base", cwd=rig)
    base = _git_out("rev-parse", "HEAD", cwd=rig)
    return rig, base


@pytest.fixture
def _rig_at_base(_tracked_tree_rig):
    """The shared rig, reset to base so an earlier row's change and commit do
    not reach this row. The selector reads base...HEAD, the blobs at those two
    commits, and test files found by walking the tree; ignored files, such as
    the __pycache__ its import leaves, are not test files, so clean keeps them."""
    rig, base = _tracked_tree_rig
    _git_rig("reset", "-q", "--hard", base, cwd=rig)
    _git_rig("clean", "-fdq", cwd=rig)
    head = _git_out("rev-parse", "HEAD", cwd=rig)
    status = _git_out("status", "--porcelain", "--untracked-files=all", cwd=rig)
    assert (head, status) == (base, ""), "the shared rig is not at base before this row"
    return rig, base


@pytest.mark.parametrize("changed", ["pact-plugin/tests/home_census.py",
                                     "pact-plugin/tests/conftest.py",
                                     "pact-plugin/conftest.py"])
def test_a_change_to_the_census_or_conftest_selects_this_module_in_the_fast_suite(
        _rig_at_base, changed):
    """OUTCOME PIN. A change to the census, to the root conftest that wires it,
    or to tests/conftest.py, re-selects this module in the fast suite. Runs on
    the shared copy of the tracked tree, reset to base for this row."""
    rig, base = _rig_at_base
    selection, changed_paths, status = _select_after_changing(rig, base, changed)
    assert (status, changed_paths) == ("ok", [changed])
    assert "tests/test_home_census.py" in selection.included, selection


def test_the_selection_rig_ignores_a_repository_the_shell_points_git_at(
        _rig_at_base, tmp_path, monkeypatch):
    """MUTANT: a git call of the selection rig, or the selector it drives,
    passes the shell's GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE through. The
    change then lands in the decoy repository, or the selector reads it."""
    rig, base = _rig_at_base
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, capture_output=True,
                   timeout=60, env=_git_rig_env())
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))
    changed = "pact-plugin/tests/home_census.py"
    selection, changed_paths, status = _select_after_changing(rig, base, changed)
    assert (status, changed_paths) == ("ok", [changed])
    assert "tests/test_home_census.py" in selection.included, selection
    decoy_log = subprocess.run(["git", "log", "--oneline"], cwd=decoy, capture_output=True,
                               text=True, timeout=60, env=_git_rig_env())
    assert decoy_log.returncode != 0, ("the decoy repository received a commit", decoy_log.stdout)
    assert os.environ["GIT_DIR"] == str(decoy / ".git"), "the shell's variables are restored"


_GATED_RIG_TESTS = '''
import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("HOME_CENSUS_RIG_GATE") != "run",
                    reason="rig: runs only where its gate is open")
def test_gated_launch_without_home():
    subprocess.run([sys.executable, "-c",
                    "import pathlib, sys; pathlib.Path(sys.argv[1]).touch()",
                    os.environ["HOME_CENSUS_RIG_MARKER"]],
                   env={"PATH": os.defpath})


def test_plain():
    pass
'''


@pytest.mark.parametrize("gate", ["closed", "run"])
def test_a_gated_test_is_judged_where_it_runs_and_stopped_before_its_child(tmp_path, gate):
    """A test that does not run in one session is judged in a session where it
    does run, by the census that session's conftest registers as
    pact-plugin/conftest.py does, and its refused launch never starts the child. Closed gate: skipped, no refusal.
    Open gate: refused, the run fails, and the child's marker is never made."""
    (tmp_path / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (tmp_path / "test_gated.py").write_text(_GATED_RIG_TESTS, encoding="utf-8")
    marker = tmp_path / "child-started"
    env = _child_environ()
    env.update(HOME_CENSUS_RIG_GATE=gate, HOME_CENSUS_RIG_MARKER=str(marker))
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "test_gated.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    assert not marker.exists(), "the child started: " + output
    if gate == "closed":
        assert "1 passed, 1 skipped" in output, output
        assert proc.returncode == 0, output
        assert "without HOME refused" not in output, output
    else:
        assert proc.returncode == 1, output
        assert "HOME CENSUS: 1 distinct child launch(es) without HOME refused" in output, output
        assert "test_gated_launch_without_home" in output, output


def _not_seen_item(start):
    """The NOT SEEN bullet of the census docstring that begins with `start`,
    with its wrapped lines joined."""
    section = census.__doc__.split("NOT SEEN, AND OUTSIDE THE CENSUS:", 1)[1]
    items = [" ".join(item.split()) for item in section.split("\n- ")]
    return next(item for item in items if item.startswith(start))


def test_the_docstring_says_a_test_that_does_not_run_is_judged_where_it_runs():
    item = _not_seen_item("a test that does not run")
    assert ("Such a test is judged on the machine where it does run, in any session "
            "that loads pact-plugin/conftest.py") in item, item
    assert ("a launch it makes there that the census refuses is stopped before the "
            "child starts.") in item, item
    assert " not judged" not in item and " never " not in item, item


def test_the_terminal_writer_attribute_the_censuses_read_counts_the_open_line(request):
    """The HOME, child-pytest and clock-shift censuses start their report on a
    new line when the terminal reporter's writer is mid-line, read through
    pytest's private TerminalWriter.width_of_current_line. Under the locked
    pytest that attribute exists on this session's reporter and counts the
    characters of an unfinished line, so a pytest that drops it fails here
    rather than quietly gluing the reports back onto the progress line."""
    import io

    from _pytest._io import TerminalWriter

    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    assert isinstance(reporter._tw, TerminalWriter), type(reporter._tw)
    assert isinstance(reporter._tw.width_of_current_line, int)
    writer = TerminalWriter(file=io.StringIO())
    writer.write("....[100%]")
    assert writer.width_of_current_line == len("....[100%]")
    writer.write("\n")
    assert writer.width_of_current_line == 0
