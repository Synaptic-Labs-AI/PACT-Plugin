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

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
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
    users = set()
    for path in sorted(PLUGIN_ROOT.rglob("*.py")):
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
                users.add(path.relative_to(PLUGIN_ROOT).as_posix())
                break
    assert users == {"tests/test_clock_shift_instrument.py", "tests/clock_shift/clock_shift_census.py"}, users


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
