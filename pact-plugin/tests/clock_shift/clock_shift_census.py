"""
Location: pact-plugin/tests/clock_shift/clock_shift_census.py
Summary: Fails a clock-shift sweep in which a child process ran on the real clock.
Used by: tests/conftest.py, whose pytest_configure installs it and whose
         pytest_sessionfinish reports; tests/test_clock_shift_instrument.py
         loads the same module in its child runs.

Does nothing unless PACT_TEST_CLOCK_SHIFT_SECONDS is set. Two checks:

1. A child launched as a Python interpreter or a shell (bash, sh, zsh) is a miss
   when the environment it receives drops PACT_TEST_CLOCK_SHIFT_SECONDS, when
   its PYTHONPATH does not carry this directory, or when an entry ahead of this
   directory holds a sitecustomize that does not import clock_shift_shim. The
   environment is the explicit `env` when one is passed, else this process's.
2. When a Python child that writes THIS run's ledger ran, and no child process
   wrote to it, the shim did not load in children. A child whose env points the
   ledger elsewhere still gets check 1 but is not counted here.

A launch whose explicit env carries PACT_TEST_CLOCK_SHIFT_CENSUS_EXEMPT is not
checked. Only tests/test_clock_shift_instrument.py sets it, on the launches that
deliberately run unshifted or without the shim, and a guard arm there pins that.

Launches are seen through the `subprocess.Popen` and `os.posix_spawn` audit
events. The suite launches nothing through os.system, os.exec* or os.spawn*,
which raise other events, so a launch added through one of those is not seen.
"""
import os
import sys
from pathlib import Path

SHIFT_ENV = "PACT_TEST_CLOCK_SHIFT_SECONDS"
LEDGER_ENV = "PACT_TEST_CLOCK_SHIFT_LEDGER"
SHIM_DIR = Path(__file__).resolve().parent
CHAIN_LINE = "import clock_shift_shim"
EXEMPT_ENV = "PACT_TEST_CLOCK_SHIFT_CENSUS_EXEMPT"
_SHELLS = frozenset({"bash", "sh", "zsh"})

_state = {"installed": False, "misses": [], "python_children": 0}


def installed():
    return _state["installed"]


def install():
    """Install the launch audit hook once, and only while the shift is on."""
    if _state["installed"] or not os.environ.get(SHIFT_ENV):
        return
    sys.addaudithook(_audit)
    _state["installed"] = True


def _audit(event, args):
    if event == "subprocess.Popen":
        executable, argv, cwd, env = args
    elif event == "os.posix_spawn":
        (executable, argv, env), cwd = args, None
    else:
        return
    try:
        _check(executable, argv, cwd, env)
    except Exception as exc:  # a raising audit hook would break the launch it watches
        _record("the census could not inspect a launch: %r" % (exc,))


def _check(executable, argv, cwd, env):
    current = os.environ.get("PYTEST_CURRENT_TEST", "")
    argv = [os.fsdecode(a) for a in ([argv] if isinstance(argv, (str, bytes)) else (argv or []))]
    names = [os.fsdecode(executable)] if executable else []
    names += argv[:1]
    is_python = any(os.path.basename(n).startswith("python") or n == sys.executable for n in names)
    if not (is_python or any(os.path.basename(n) in _SHELLS for n in names)):
        return
    explicit = env is not None and env is not os.environ
    env = {os.fsdecode(k): os.fsdecode(v) for k, v in env.items()} if explicit else os.environ
    if explicit and env.get(EXEMPT_ENV):
        return
    if is_python and (not explicit or env.get(LEDGER_ENV) == os.environ.get(LEDGER_ENV)):
        _state["python_children"] += 1
    if explicit and not env.get(SHIFT_ENV):
        problem = "its env drops %s" % SHIFT_ENV
    else:
        problem = _pythonpath_problem(env.get("PYTHONPATH", ""), os.fsdecode(cwd) if cwd else None)
    if problem:
        _record("%s: %s -- %s" % (current or "<outside a test>", " ".join(argv)[:160], problem))


def _pythonpath_problem(pythonpath, cwd):
    base = Path(cwd) if cwd else Path.cwd()
    shadowed = None
    first_site_seen = False
    for entry in pythonpath.split(os.pathsep):
        if not entry:
            continue
        path = (base / entry).resolve()
        if path == SHIM_DIR:
            return shadowed
        if first_site_seen:
            continue
        source = _sitecustomize_source(path)
        if source is not None:
            # Only the first sitecustomize on the path is imported.
            first_site_seen = True
            if CHAIN_LINE not in source:
                shadowed = "the sitecustomize in %s runs instead of the shim and does not import it" % path
    return shadowed or "its PYTHONPATH does not carry %s" % SHIM_DIR


def _sitecustomize_source(directory):
    module, package = directory / "sitecustomize.py", directory / "sitecustomize"
    if module.is_file():
        target = module
    elif package.is_dir():
        target = package / "__init__.py"
    else:
        return None
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _record(line):
    if line not in _state["misses"]:
        _state["misses"].append(line)


def _children_wrote_ledger():
    ledger = os.environ.get(LEDGER_ENV)
    if not ledger:
        return False
    try:
        lines = Path(ledger).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    own = str(os.getpid())
    return any(line.split(" ", 1)[0] != own for line in lines if line.strip())


def finish(session):
    """Report every problem and fail the session when there is one."""
    if not _state["installed"]:
        return
    problems = list(_state["misses"])
    if _state["python_children"] and not _children_wrote_ledger():
        problems.append(
            "the shim did not load in children: %d Python launch(es) writing this run's "
            "ledger seen, and no child process wrote to %s (%s)"
            % (_state["python_children"], LEDGER_ENV, os.environ.get(LEDGER_ENV, "unset"))
        )
    if not problems:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    write = reporter.write_line if reporter is not None else (lambda line: print(line, file=sys.stderr))
    write("clock-shift census: %d problem(s); this run did not shift every clock" % len(problems))
    for line in problems:
        write("  " + line)
    session.exitstatus = 1
