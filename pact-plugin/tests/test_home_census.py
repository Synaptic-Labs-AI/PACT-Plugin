"""
Location: pact-plugin/tests/test_home_census.py
Summary: Pins the HOME census: which launches it refuses and accepts, its one
         exemption, that it is live in this session, that finish() fails the
         session, and that under real xdist a refusal a test swallows still
         fails the run.
Used by: the suite; the census itself is tests/home_census.py, wired in
         tests/conftest.py.
"""
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import home_census as census

PY = sys.executable
_CONFTEST = Path(__file__).resolve().with_name("conftest.py")
_HOME = {"HOME": "/sandbox/home"}


@pytest.fixture
def census_state():
    """Restore the census's recorded lines, so a deliberate refusal here does
    not fail the session that runs this module."""
    saved = {key: list(census._state[key]) for key in ("misses", "forwarded")}
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


def test_the_conftest_enters_and_leaves_around_each_test(request, census_state):
    """MUTANT: tests/conftest.py's pytest_runtest_protocol stops calling enter()
    or leave()."""
    suite = _CONFTEST
    plugins = [p for p in request.config.pluginmanager.get_plugins()
               if getattr(p, "__file__", None) and Path(p.__file__).resolve() == suite]
    assert len(plugins) == 1, plugins
    wrapper = plugins[0].pytest_runtest_protocol(item=_item({"reason": "r"}), nextitem=None)
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
        "HOME CENSUS: 1 child(ren) started without HOME", "  " + _LINE]


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
    before = len(census_state["misses"])
    census._audit("subprocess.Popen", (None, ["true"], None, Opaque()))
    added = census_state["misses"][before:]
    assert len(added) == 1 and "could not inspect the environment" in added[0], added
    assert "LookupError('these keys cannot be read')" in added[0], added
    assert "test_an_env_whose_keys_cannot_be_read" in added[0], added


def test_the_census_is_live_and_refuses_before_the_child_starts(tmp_path, census_state):
    assert census.installed(), "tests/conftest.py did not install the census"
    marker = tmp_path / "started"
    line = _refused(lambda: subprocess.run(
        [PY, "-c", "import pathlib, sys; pathlib.Path(sys.argv[1]).touch()", str(marker)],
        env={"PATH": os.defpath}), census_state)
    assert not marker.exists(), "the child started before the refusal"
    assert "test_the_census_is_live" in line and "pathlib.Path(sys.argv[1]).touch()" in line, line


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
    assert reporter.lines == ["HOME CENSUS: 1 child(ren) started without HOME", "  " + _LINE]


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


_RIG_CONFTEST = '''
import importlib.util

_spec = importlib.util.spec_from_file_location("pact_suite_conftest", {conftest!r})
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)
pytest_configure = _suite.pytest_configure
pytest_runtest_protocol = _suite.pytest_runtest_protocol
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


def _run_rig(tmp_path, env_literal):
    (tmp_path / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (tmp_path / "test_swallow.py").write_text(
        _RIG_TESTS.format(env=env_literal), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_XDIST_") and k not in ("PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")}
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
    assert "test_swallows_the_census_error" in output and census.REMEDY in output, output


def test_under_xdist_a_child_with_home_passes_the_run(tmp_path):
    rc, output = _run_rig(tmp_path, '{"PATH": os.defpath, "HOME": os.environ["HOME"]}')
    assert "2 passed" in output, output
    assert rc == 0, output
    assert "started without HOME" not in output, output
    # The workers' launches (the test's own and any its session makes) reach
    # the controller's information line.
    assert re.search(r"HOME CENSUS: installed, [1-9][0-9]* launches checked, 0 refused",
                     output), output


def _git_rig(*args, cwd):
    subprocess.run(["git", "-c", "user.name=rig", "-c", "user.email=rig@invalid", *args],
                   cwd=cwd, check=True, capture_output=True, timeout=120,
                   env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})


@pytest.mark.parametrize("changed", ["pact-plugin/tests/home_census.py",
                                     "pact-plugin/tests/conftest.py"])
def test_a_change_to_the_census_or_conftest_selects_this_module_in_the_fast_suite(
        tmp_path, changed):
    """OUTCOME PIN. A change to the census, or to the conftest that wires it,
    re-selects this module in the fast suite. Runs on a copy of the tracked tree
    in a fresh repository with no remote; the real checkout is only read."""
    import importlib.util
    import uuid

    root = Path(__file__).resolve().parents[2]
    if not (root / "scripts" / "fast_suite_selection.py").is_file():
        pytest.skip("SKIPPED, NOT PASSED: not a PACT source checkout, so there is no selector to ask")
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True,
                            timeout=120).stdout.decode("utf-8").split("\0")
    rig = tmp_path / "repo"
    for rel in filter(None, listed):
        source = root / rel
        if source.is_file() and not source.is_symlink():
            target = rig / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    _git_rig("init", "-q", cwd=rig)
    _git_rig("add", "-A", cwd=rig)
    _git_rig("commit", "-qm", "base", cwd=rig)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=rig, check=True, capture_output=True,
                          text=True, timeout=60).stdout.strip()
    # A name this module's source cannot contain, so the change reaches this
    # module only through how it depends on the changed file.
    probe = "_probe_" + uuid.uuid4().hex
    changed_file = rig / changed
    changed_file.write_text(changed_file.read_text(encoding="utf-8") + "\n%s = 1\n" % probe,
                            encoding="utf-8")
    _git_rig("commit", "-qam", "change it", cwd=rig)

    spec = importlib.util.spec_from_file_location(
        "_home_census_selection_probe", rig / "scripts" / "fast_suite_selection.py")
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core  # dataclass annotations resolve through sys.modules
    try:
        spec.loader.exec_module(core)
        manifest = core.load_manifest(rig / "scripts" / "fast_suite_exclusions.json")
        selection, changed_paths, status = core.select_for_change(rig, manifest, base)
    finally:
        sys.modules.pop(spec.name, None)
    assert (status, changed_paths) == ("ok", [changed])
    assert "tests/test_home_census.py" in selection.included, selection
