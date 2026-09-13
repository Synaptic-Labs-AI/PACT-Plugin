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
