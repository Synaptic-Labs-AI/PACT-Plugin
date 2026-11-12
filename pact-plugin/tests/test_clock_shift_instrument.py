"""
Location: pact-plugin/tests/test_clock_shift_instrument.py
Summary: Proves the clock-shift instrument reaches every kind of child process,
         and that its census fails a sweep in which a child ran on the real clock.
Used by: the suite, so the instrument is tested on every run, not only in a sweep.

Each arm runs a child pytest over a synthetic test in tmp_path, with an
environment built here. The synthetic conftest imports the census from
tests/clock_shift through pytest's `pythonpath` option, so the arms drive the
same module tests/conftest.py installs. The shift is ten years, far outside the
day a sweep puts on this process's own clock.
"""
import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from clock_shift import clock_shift_census
from fixtures.tracked_files import tracked_files as _tracked_files

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _tracked_plugin_python_files(plugin_root: Path) -> list[Path]:
    repo_root = plugin_root.parent
    relative = plugin_root.relative_to(repo_root).as_posix()
    return sorted(path for path in _tracked_files(repo_root, relative)
                  if path.suffix == ".py")


def _exempt_users(plugin_root: Path) -> set[str]:
    users = set()
    for path in _tracked_plugin_python_files(plugin_root):
        data = path.read_bytes()
        if CENSUS_EXEMPT.encode() not in data and b"EXEMPT_ENV" not in data:
            continue
        for node in ast.walk(ast.parse(data)):
            named = (
                isinstance(node, ast.Constant) and isinstance(node.value, str) and CENSUS_EXEMPT in node.value
            ) or (isinstance(node, ast.Name) and node.id == "EXEMPT_ENV") or (
                isinstance(node, ast.Attribute) and node.attr == "EXEMPT_ENV"
            )
            if named:
                users.add(path.relative_to(plugin_root).as_posix())
                break
    return users
SHIM_DIR = Path(__file__).resolve().parent / "clock_shift"
# Set on a launch that runs unshifted or without the shim on purpose, so a real
# sweep's census does not count it as a miss.
CENSUS_EXEMPT = "PACT_TEST_CLOCK_SHIFT_CENSUS_EXEMPT"
TEN_YEARS = 10 * 365 * 86400
# Holds a sweep's shift on this process's clock (a day) plus the child's run time.
SLACK = 2 * 86400 + 3600

CONFTEST = '''\
from clock_shift_census import finish, install


def pytest_configure(config):
    install()


def pytest_sessionfinish(session):
    finish(session)
'''

PRINT_DATETIME = "import datetime; print(datetime.datetime.now().timestamp())"
PRINT_TIME = "import time; print(time.time())"

# A launch whose printed clock must fall in [CLOCK_LOW, CLOCK_HIGH]. `site` is
# written as a sitecustomize ahead of the shim when it is not None.
LAUNCH = '''\
import os
import subprocess


def test_launch(tmp_path):
    env = None
    site = {site!r}
    if site is not None:
        shadow = tmp_path / "shadow"
        shadow.mkdir()
        (shadow / "sitecustomize.py").write_text(site)
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(shadow), os.environ["PYTHONPATH"]]))
    out = subprocess.run({argv!r}, env=env, capture_output=True, text=True).stdout
    value = float(out.split()[-1])
    low, high = float(os.environ["CLOCK_LOW"]), float(os.environ["CLOCK_HIGH"])
    assert low <= value <= high, (low, value, high)
'''

NOT_INSTALLED = '''

def test_the_census_is_not_installed():
    import clock_shift_census

    assert not clock_shift_census.installed()
'''

DROPPED_ENV = '''\
import os
import subprocess


def test_launch():
    subprocess.run(["python3", "-c", "pass"], env={"PATH": os.environ["PATH"]})
'''

INHERITED = '''\
import subprocess
import sys


def test_launch():
    subprocess.run([sys.executable, "-c", "pass"], check=True)
'''

NODE = "test_synthetic.py::test_launch"

CLASS_ATTRIBUTE = """\
import os

LOW, HIGH = float(os.environ["CLOCK_LOW"]), float(os.environ["CLOCK_HIGH"])


class Holder:
    stat = os.stat
    lstat = os.lstat
    fstat = os.fstat
    scandir = os.scandir
    utime = os.utime


def test_a_class_attribute_call_is_unbound_and_shifted(tmp_path):
    path = tmp_path / "f"
    path.write_text("x")
    holder = Holder()
    assert LOW <= holder.stat(str(path)).st_mtime <= HIGH
    assert LOW <= holder.lstat(str(path)).st_mtime <= HIGH
    with open(path, "rb") as fh:
        assert LOW <= holder.fstat(fh.fileno()).st_mtime <= HIGH
    with holder.scandir(str(tmp_path)) as entries:
        assert LOW <= [e for e in entries][0].stat().st_mtime <= HIGH
    holder.utime(str(path), (1_000_000_000, 1_000_000_000))
    assert os.stat(path).st_mtime == 1_000_000_000
"""

SUPPORTS_PARITY = """\
import os
import posix

import pytest

SETS = ("supports_dir_fd", "supports_fd", "supports_follow_symlinks", "supports_effective_ids")


@pytest.mark.parametrize("name", ["stat", "lstat", "fstat", "scandir", "utime"])
def test_the_wrapper_is_in_every_set_its_real_function_is_in(name):
    assert getattr(os, name) is not getattr(posix, name), "the shim is not installed"
    for support in SETS:
        members = getattr(os, support)
        assert (getattr(os, name) in members) == (getattr(posix, name) in members), (name, support)
"""

FD_PARITY = """\
import shutil


def test_shutil_takes_the_same_rmtree_path():
    assert shutil._use_fd_functions is @EXPECTED@
"""

MULTI_LINE_LAUNCH = '''\
import os
import subprocess


def test_launch():
    subprocess.run(["python3", "-c", "pass\\npass"], env={"PATH": os.environ["PATH"]})
'''

STAT_ROUTES = """\
import importlib._bootstrap_external
import os
import posix

import pytest

LOW, HIGH = float(os.environ["CLOCK_LOW"]), float(os.environ["CLOCK_HIGH"])
ROUTES = ("os.stat", "os.lstat", "os.fstat", "os.scandir", "pathlib")


def _read(path, route):
    if route == "os.stat":
        return os.stat(path)
    if route == "os.lstat":
        return os.lstat(path)
    if route == "os.fstat":
        with open(path, "rb") as fh:
            return os.fstat(fh.fileno())
    if route == "os.scandir":
        with os.scandir(path.parent) as entries:
            return [e for e in entries if e.name == path.name][0].stat()
    return path.stat()


@pytest.mark.parametrize("route", ROUTES)
def test_a_fresh_file_reads_shifted(tmp_path, route):
    path = tmp_path / "fresh"
    path.write_text("x")
    st = _read(path, route)
    fields = ["st_atime", "st_mtime", "st_ctime"] + (["st_birthtime"] if hasattr(st, "st_birthtime") else [])
    for field in fields:
        assert LOW <= getattr(st, field) <= HIGH, (route, field, getattr(st, field))
        if hasattr(st, field + "_ns"):
            assert LOW <= getattr(st, field + "_ns") / 1e9 <= HIGH, (route, field + "_ns")


@pytest.mark.parametrize("getter", ["getmtime", "getatime", "getctime"])
def test_os_path_getters_read_shifted(tmp_path, getter):
    path = tmp_path / "fresh"
    path.write_text("x")
    assert LOW <= getattr(os.path, getter)(path) <= HIGH, getter


def test_importlib_still_holds_the_real_stat():
    assert importlib._bootstrap_external._os is posix
    assert type(posix.stat).__name__ == "builtin_function_or_method"
"""

UTIME_READBACK = """\
import os

LOW, HIGH = float(os.environ["CLOCK_LOW"]), float(os.environ["CLOCK_HIGH"])


def test_explicit_times_read_back(tmp_path):
    path = tmp_path / "f"
    path.write_text("x")
    os.utime(path, (1_000_000_000, 1_000_000_000))
    st = os.stat(path)
    assert (st.st_atime, st.st_mtime) == (1_000_000_000, 1_000_000_000)


def test_explicit_ns_read_back(tmp_path):
    path = tmp_path / "f"
    path.write_text("x")
    os.utime(path, ns=(1_000_000_000_123_456_789, 1_000_000_000_123_456_789))
    assert os.stat(path).st_mtime_ns == 1_000_000_000_123_456_789


def test_a_bare_utime_reads_as_shifted_now(tmp_path):
    path = tmp_path / "f"
    path.write_text("x")
    os.utime(path)
    assert LOW <= os.stat(path).st_mtime <= HIGH
"""

IDENTITY = """\
import datetime
import os
import posix
import time

import pytest


@pytest.mark.parametrize("name", ["stat", "lstat", "fstat", "scandir", "utime"])
def test_the_os_function_is_the_real_one(name):
    assert getattr(os, name) is getattr(posix, name)


def test_the_clock_is_the_real_one():
    assert type(time.time).__name__ == "builtin_function_or_method"
    assert datetime.datetime.__module__ == "datetime"
"""


def _launch(argv, site=None):
    return LAUNCH.format(argv=argv, site=site)


def _run_child(tmp_path, source, shift=TEN_YEARS, shim_on_path=True):
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / "test_synthetic.py").write_text(source)
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("PACT_TEST_CLOCK_SHIFT_")
        and k not in ("PYTHONPATH", "PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS")
    }
    env["PACT_TEST_CLOCK_SHIFT_LEDGER"] = str(tmp_path / "ledger")
    if shift is not None:
        env["PACT_TEST_CLOCK_SHIFT_SECONDS"] = str(shift)
    if shim_on_path:
        env["PYTHONPATH"] = str(SHIM_DIR)
    if shift is None or not shim_on_path:
        env[CENSUS_EXEMPT] = "1"
    expected = time.time() + (shift or 0)
    env["CLOCK_LOW"], env["CLOCK_HIGH"] = str(expected - SLACK), str(expected + SLACK)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "no:randomly",
         "-o", "pythonpath=%s" % SHIM_DIR, "test_synthetic.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180,
    )
    return result.returncode, result.stdout + result.stderr


def test_a_bare_python3_child_sees_the_shifted_clock(tmp_path):
    """MUTANT: sitecustomize.py stops importing the shim. The grandchild prints
    the real clock, ten years below the window."""
    rc, out = _run_child(tmp_path, _launch(["python3", "-c", PRINT_DATETIME]))
    assert (rc, "1 passed" in out) == (0, True), out


def test_a_bash_c_grandchild_sees_the_shifted_clock(tmp_path):
    """MUTANT: the same. A `bash -c` launch inherits the environment and nothing else."""
    rc, out = _run_child(tmp_path, _launch(["bash", "-c", "python3 -c '%s'" % PRINT_DATETIME]))
    assert (rc, "1 passed" in out) == (0, True), out


def test_time_time_is_shifted(tmp_path):
    """MUTANT: the shim stops wrapping time.time. datetime.now reads the C clock,
    so only this arm reddens."""
    rc, out = _run_child(tmp_path, _launch(["python3", "-c", PRINT_TIME]))
    assert (rc, "1 passed" in out) == (0, True), out


def test_the_census_fails_a_child_whose_env_drops_the_shim(tmp_path):
    """MUTANT: the census stops recording explicit-env misses. The run still fails
    on the empty ledger, but no line names the test, so this arm reddens."""
    rc, out = _run_child(tmp_path, DROPPED_ENV)
    line = "%s (call): python3 -c pass -- its env drops PACT_TEST_CLOCK_SHIFT_SECONDS" % NODE
    assert (rc != 0, line in out) == (True, True), out


def test_the_census_fails_when_children_never_loaded_the_shim(tmp_path):
    """MUTANT: the ledger check is removed. The PYTHONPATH miss still fails the run,
    but the ledger line is absent."""
    rc, out = _run_child(tmp_path, INHERITED, shim_on_path=False)
    assert (rc != 0, "the shim did not load in children" in out) == (True, True), out


def test_without_the_shift_variable_the_shim_is_inert(tmp_path):
    """GUARD. The shim is on PYTHONPATH and the variable is unset: the grandchild
    reads the real clock and no audit hook is installed."""
    rc, out = _run_child(tmp_path, _launch(["python3", "-c", PRINT_DATETIME]) + NOT_INSTALLED, shift=None)
    assert (rc, "2 passed" in out) == (0, True), out


def test_the_census_fails_a_child_whose_pythonpath_shadows_the_shim(tmp_path):
    """MUTANT: the census stops reading sitecustomize files ahead of the shim."""
    rc, out = _run_child(tmp_path, _launch(["python3", "-c", PRINT_DATETIME], site="VALUE = 1\n"))
    line = "%s (call): python3 -c %s -- the sitecustomize in" % (NODE, PRINT_DATETIME)
    assert (rc != 0, line in out, "runs instead of the shim" in out) == (True, True, True), out


def test_a_chained_sitecustomize_still_gets_the_shifted_clock(tmp_path):
    """QUIET ON THE CURE. MUTANT: the census flags every sitecustomize ahead of the
    shim, chained or not. This run then fails."""
    rc, out = _run_child(tmp_path, _launch(["python3", "-c", PRINT_DATETIME], site="import clock_shift_shim\n"))
    assert (rc, "1 passed" in out) == (0, True), out


def test_the_census_exemption_is_used_only_by_the_instrument_arms():
    """GUARD. An exemption set anywhere else would hide a real miss from the census.
    MUTANT: any other file naming the variable, or the census renaming it."""
    users = _exempt_users(PLUGIN_ROOT)
    assert users == {"tests/test_clock_shift_instrument.py", "tests/clock_shift/clock_shift_census.py"}, users


def test_tracked_plugin_population_ignores_an_untracked_exempt_user(tmp_path):
    """The needle names the exemption; under the old rglob it joins the users."""
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    known = plugin / "scripts" / "known.py"
    known.parent.mkdir(parents=True)
    known.write_text('EXEMPT_ENV = "x"\n', encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", "pact-plugin"],
        check=True, capture_output=True)

    assert _exempt_users(plugin) == {"scripts/known.py"}

    stray = plugin / "scripts" / "zzprobe.py"
    stray.write_text('EXEMPT_ENV = "y"\n', encoding="utf-8")
    try:
        assert _exempt_users(plugin) == {"scripts/known.py"}
    finally:
        stray.unlink()


def test_a_fresh_file_reads_as_shifted_now_through_every_stat_route(tmp_path):
    """MUTANTS: unwrap os.stat, unwrap os.scandir, or shift one _ns field only.
    Each leaves one route or field on the real clock, ten years below the window."""
    rc, out = _run_child(tmp_path, STAT_ROUTES)
    assert (rc, "9 passed" in out) == (0, True), out


def test_an_explicit_utime_reads_back_what_it_set(tmp_path):
    """MUTANT: os.utime stops subtracting the shift. The explicit times and ns
    then read back ten years late; a bare utime is unaffected."""
    rc, out = _run_child(tmp_path, UTIME_READBACK)
    assert (rc, "3 passed" in out) == (0, True), out


@pytest.mark.parametrize("shift", [None, 0])
def test_an_unshifted_run_installs_nothing(tmp_path, shift):
    """GUARD. Unset and zero both leave os and the clocks as the real functions.
    MUTANT: gate the install on the variable being set rather than non-zero."""
    rc, out = _run_child(tmp_path, IDENTITY, shift=shift)
    assert (rc, "6 passed" in out) == (0, True), out


def test_a_multi_line_argument_stays_on_one_census_line(tmp_path):
    """MUTANT: the census prints argv unflattened, so the problem spans two lines
    and this exact line is absent."""
    rc, out = _run_child(tmp_path, MULTI_LINE_LAUNCH)
    line = "%s (call): python3 -c pass pass -- its env drops PACT_TEST_CLOCK_SHIFT_SECONDS" % NODE
    assert (rc != 0, line in out) == (True, True), out


def test_a_class_attribute_call_is_unbound_and_shifted(tmp_path):
    """MUTANT: install plain functions. A class holding one binds it as a method
    and passes itself as the path, as 3.9's pathlib accessor does."""
    rc, out = _run_child(tmp_path, CLASS_ATTRIBUTE)
    assert (rc, "1 passed" in out) == (0, True), out


def test_supports_membership_matches_the_real_functions(tmp_path):
    """MUTANT: the wrappers do not join the os.supports_* sets."""
    rc, out = _run_child(tmp_path, SUPPORTS_PARITY)
    assert (rc, "5 passed" in out) == (0, True), out


def test_shutil_takes_the_same_rmtree_path_as_unshifted(tmp_path):
    """shutil fixes _use_fd_functions from the os.supports_* sets when it is
    imported. MUTANTS: skip joining the sets, or join them after shutil is imported."""
    import shutil

    rc, out = _run_child(tmp_path, FD_PARITY.replace("@EXPECTED@", repr(shutil._use_fd_functions)))
    assert (rc, "1 passed" in out) == (0, True), out


# UNDER XDIST. A worker's exit status and output do not reach the controller,
# and inside a worker check 2 is satisfied by its siblings' own ledger lines, so
# the census hands each worker's findings to the controller. These arms run a
# `-n 2` child wired the way tests/conftest.py wires the census, in both
# directions; test_the_suite_conftest_collects_the_workers_findings pins that
# wiring in tests/conftest.py itself.
XDIST_CONFTEST = '''\
import pytest

from clock_shift_census import collect, finish, install


def pytest_configure(config):
    install()


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    collect(node)


def pytest_sessionfinish(session):
    finish(session)
'''

XDIST_LAUNCH = '''\
import os
import subprocess
import sys


def test_launch():
    subprocess.run({argv}, env={env}, check=True, timeout=60)


def test_plain():
    pass
'''

DROPS_SHIFT = '{k: v for k, v in os.environ.items() if k != "PACT_TEST_CLOCK_SHIFT_SECONDS"}'


def _run_xdist(tmp_path, argv="[sys.executable, '-c', 'pass']", env="None"):
    (tmp_path / "conftest.py").write_text(XDIST_CONFTEST)
    (tmp_path / "test_synthetic.py").write_text(XDIST_LAUNCH.format(argv=argv, env=env))
    child_env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("PACT_TEST_CLOCK_SHIFT_", "PYTEST_XDIST_"))
        and k not in ("PYTHONPATH", "PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS")
    }
    child_env["PACT_TEST_CLOCK_SHIFT_LEDGER"] = str(tmp_path / "ledger")
    child_env["PACT_TEST_CLOCK_SHIFT_SECONDS"] = str(TEN_YEARS)
    child_env["PYTHONPATH"] = str(SHIM_DIR)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "-n", "2", "-o", "pythonpath=%s" % SHIM_DIR, "test_synthetic.py"],
        cwd=tmp_path, env=child_env, capture_output=True, text=True, timeout=300,
    )
    return result.returncode, result.stdout + result.stderr


def test_under_xdist_a_worker_child_that_drops_the_shift_fails_the_run(tmp_path):
    """MUTANT: the worker stops handing its misses over, or the controller stops
    collecting them. The worker's line is lost and the run passes."""
    rc, out = _run_xdist(tmp_path, env=DROPS_SHIFT)
    line = "] %s (call): " % NODE
    assert ("2 passed" in out, rc, line in out, "its env drops PACT_TEST_CLOCK_SHIFT_SECONDS" in out) == (
        True, 1, True, True), out


def test_under_xdist_a_worker_child_that_keeps_the_shift_passes_the_run(tmp_path):
    """GUARD for the arm above: the same launch with the shift kept passes clean."""
    rc, out = _run_xdist(tmp_path)
    assert ("2 passed" in out, rc, "clock-shift census" in out) == (True, 0, False), out


def test_under_xdist_a_worker_child_that_skips_the_shim_fails_the_run(tmp_path):
    """MUTANT: check 2 counts the controller's or the workers' own ledger lines as
    a child's. `-S` skips sitecustomize, so the child writes no ledger line while
    its PYTHONPATH is right, and only the sibling processes' lines remain."""
    rc, out = _run_xdist(tmp_path, argv="[sys.executable, '-S', '-c', 'pass']")
    assert ("2 passed" in out, rc, "the shim did not load in children: 1 " in out) == (True, 1, True), out


class _Reporter:
    def __init__(self):
        self.lines = []

    def write_line(self, line):
        self.lines.append(line)


class _Session:
    def __init__(self, reporter, workeroutput=None):
        plugins = {"terminalreporter": reporter}
        self.config = type("Config", (), {"pluginmanager": type(
            "PM", (), {"get_plugin": staticmethod(plugins.get)})()})()
        if workeroutput is not None:
            self.config.workeroutput = workeroutput
        self.exitstatus = 0


def _fresh_census(monkeypatch, tmp_path, **state):
    # The same module object tests/conftest.py installs and its hooks call.
    assert clock_shift_census is sys.modules["clock_shift.clock_shift_census"]
    census = clock_shift_census
    fresh = {"installed": True, "misses": [], "python_children": 0, "workers": [], "worker_children": 0}
    fresh.update(state)
    monkeypatch.setattr(census, "_state", fresh)
    monkeypatch.setenv("PACT_TEST_CLOCK_SHIFT_LEDGER", str(tmp_path / "ledger"))
    return census


def test_a_worker_hands_its_findings_to_the_controller(monkeypatch, tmp_path):
    """MUTANT: the worker fails only itself, or collect() drops what it received."""
    census = _fresh_census(monkeypatch, tmp_path, misses=["t::a (call): python3 -c pass -- x"],
                           python_children=1)
    workeroutput = {}
    worker = _Session(_Reporter(), workeroutput=workeroutput)
    census.finish(worker)
    assert worker.exitstatus == 0, "xdist ignores a worker's exit status"
    found = workeroutput[census.WORKEROUTPUT_KEY]
    assert found == {"misses": ["t::a (call): python3 -c pass -- x"], "python_children": 1,
                     "pid": os.getpid()}

    census = _fresh_census(monkeypatch, tmp_path)
    (tmp_path / "ledger").write_text("%d x\n4242424 child\n" % os.getpid())
    node = type("Node", (), {"workeroutput": workeroutput,
                             "gateway": type("Gateway", (), {"id": "gw1"})()})()
    census.collect(node)
    reporter = _Reporter()
    controller = _Session(reporter)
    census.finish(controller)
    assert controller.exitstatus == 1
    assert reporter.lines[1:] == ["  [gw1] t::a (call): python3 -c pass -- x"], reporter.lines


@pytest.mark.parametrize("extra, failed", [("", True), ("4242424 child\n", False)])
def test_the_controller_ignores_its_own_and_its_workers_ledger_lines(monkeypatch, tmp_path, extra, failed):
    """MUTANT: the controller counts a worker's or its own ledger line as a child's."""
    census = _fresh_census(monkeypatch, tmp_path)
    worker_pid = 4141414
    (tmp_path / "ledger").write_text("%d controller\n%d worker\n%s" % (os.getpid(), worker_pid, extra))
    node = type("Node", (), {"workeroutput": {census.WORKEROUTPUT_KEY: {
        "misses": [], "python_children": 2, "pid": worker_pid}}})()
    census.collect(node)
    reporter = _Reporter()
    controller = _Session(reporter)
    census.finish(controller)
    assert controller.exitstatus == (1 if failed else 0), reporter.lines
    assert any("the shim did not load in children: 2 " in line for line in reporter.lines) is failed


def test_the_suite_conftest_collects_the_workers_findings(request, monkeypatch, tmp_path):
    """MUTANT: tests/conftest.py's pytest_testnodedown stops calling the census."""
    suite = Path(__file__).resolve().with_name("conftest.py")
    plugins = [p for p in request.config.pluginmanager.get_plugins()
               if getattr(p, "__file__", None) and Path(p.__file__).resolve() == suite]
    assert len(plugins) == 1, plugins
    census = _fresh_census(monkeypatch, tmp_path)
    node = type("Node", (), {"workeroutput": {census.WORKEROUTPUT_KEY: {
        "misses": ["t::c (call): x"], "python_children": 0, "pid": 4343434}},
        "gateway": type("Gateway", (), {"id": "gw2"})()})()
    plugins[0].pytest_testnodedown(node=node, error=None)
    assert census._state["misses"] == ["[gw2] t::c (call): x"]
    assert census._state["workers"] == [4343434]


def _git_rig(*args, cwd):
    subprocess.run(["git", "-c", "user.name=rig", "-c", "user.email=rig@invalid", *args],
                   cwd=cwd, check=True, capture_output=True, timeout=120)


def test_a_change_to_the_census_selects_this_module_in_the_fast_suite(tmp_path):
    """OUTCOME PIN. A change to the census re-selects this module in the fast
    suite. Four reasons add it back today: its import of the census (as a
    closure module, and as a changed name its test-side closure uses), its
    unproven reach and its scan of the whole plugin tree. MUTANT: the selector
    drops all of them (any one alone still selects it). Runs on a copy of the
    tracked tree in a fresh repository with no remote; the real checkout is only
    read."""
    import importlib.util
    import uuid

    root = PLUGIN_ROOT.parent
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
    # module only through how it depends on the census, not by a shared name.
    probe = "_probe_" + uuid.uuid4().hex
    census_file = rig / "pact-plugin" / "tests" / "clock_shift" / "clock_shift_census.py"
    census_file.write_text(census_file.read_text(encoding="utf-8") + "\n%s = 1\n" % probe,
                           encoding="utf-8")
    _git_rig("commit", "-qam", "change the census", cwd=rig)

    spec = importlib.util.spec_from_file_location(
        "_clock_shift_selection_probe", rig / "scripts" / "fast_suite_selection.py")
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core  # dataclass annotations resolve through sys.modules
    try:
        spec.loader.exec_module(core)
        manifest = core.load_manifest(rig / "scripts" / "fast_suite_exclusions.json")
        selection, changed, status = core.select_for_change(rig, manifest, base)
    finally:
        sys.modules.pop(spec.name, None)
    assert (status, changed) == ("ok", ["pact-plugin/tests/clock_shift/clock_shift_census.py"])
    assert "tests/test_clock_shift_instrument.py" in selection.included, selection
