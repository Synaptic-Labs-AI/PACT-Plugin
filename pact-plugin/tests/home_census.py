"""
Location: pact-plugin/tests/home_census.py
Summary: Refuses a child process the suite starts without HOME in its environment.
Used by: tests/conftest.py, whose pytest_configure installs it and registers its
         marker, whose pytest_runtest_protocol tells it which test is running,
         whose pytest_testnodedown collects each xdist worker's refusals, and
         whose pytest_sessionfinish reports; tests/test_home_census.py.

A child started without HOME finds its home through the password database: the
real one, outside the sandbox the suite runs in.

THE CENSUS SEES THESE LAUNCHES, AND ONLY THESE, through the audit events that
carry the child's environment:
- `subprocess.Popen` (executable, args, cwd, env): subprocess.Popen, run, call,
  check_call and check_output, os.popen, and asyncio's create_subprocess_exec
  and create_subprocess_shell, which all build a Popen.
- `os.posix_spawn` (path, argv, env): os.posix_spawn and os.posix_spawnp.

TWO RULES REFUSE A LAUNCH:
1. An environment given explicitly (env is not None) with no HOME key, either
   "HOME" or b"HOME". A HOME key with any value, the empty string included,
   is accepted.
2. An inherited environment (env=None) while the suite's own os.environ has no
   HOME key at that moment, as after `monkeypatch.delenv("HOME")`.

A refusal records a line naming PYTEST_CURRENT_TEST and the launch, then raises
HomeCensusError, which aborts the launch before the child starts. finish() fails
the session for every recorded line, so a test that swallows the error still
fails the run. Under xdist a worker cannot fail the run itself (the controller
ignores a worker's exit status), so a worker's finish() hands its lines to the
controller through workeroutput, collect() gathers them as each worker goes
down, and the controller's finish() reports and fails. finish() raises only a
clean exit status (0) to 1. An environment the census cannot inspect (a key
test that raises) is recorded as a fault line and fails the session; that
launch is not aborted.

THE ONE EXEMPTION: `@pytest.mark.child_env_without_home(reason="...")` on a
test, its class or its module, with a non-empty string reason, exempts every
launch made while that test runs: its setup, call and teardown, including a
launch a shared fixture makes then. A marker without such a reason exempts
nothing; the refusal line says the reason is missing.

NOT SEEN, AND OUTSIDE THE CENSUS:
- os.spawn* and os.exec*, and os.fork() followed by an exec: on POSIX the exec
  runs in a forked child, whose refusal never reaches the session.
- launches that raise neither event: os.system, os.fork, os.forkpty and
  pty.fork, multiprocessing's start methods, and launches through ctypes, cffi
  or a C extension.
- a grandchild that a child process starts, since the census is installed only
  in the suite's own processes.
- a test that does not run: skipped, deselected or not collected.
"""
import os
import sys

MARKER = "child_env_without_home"
MARKER_HELP = (MARKER + "(reason): this test starts a child without HOME on purpose; "
               "the HOME census accepts its launches. The reason is required.")
REMEDY = ('pass HOME in the child env (the child_env fixture, or HOME from '
          'os.environ), or mark the test %s(reason=...)' % MARKER)
WORKEROUTPUT_KEY = "home_census_refusals"

_state = {"installed": False, "misses": [], "forwarded": [], "exempt": None,
          "unreasoned": False}


class HomeCensusError(RuntimeError):
    """A child process would start without HOME in its environment."""


def installed():
    return _state["installed"]


def install():
    """Install the launch audit hook once."""
    if _state["installed"]:
        return
    sys.addaudithook(_audit)
    _state["installed"] = True


def enter(item):
    """Note the running test's opt-out, if it carries one with a reason."""
    marker = item.get_closest_marker(MARKER)
    reason = None
    if marker is not None:
        reason = marker.kwargs.get("reason", marker.args[0] if marker.args else None)
    valid = isinstance(reason, str) and reason.strip() != ""
    _state["exempt"] = reason if valid else None
    _state["unreasoned"] = marker is not None and not valid


def leave():
    _state["exempt"] = None
    _state["unreasoned"] = False


def _has_home(env):
    # The keys, as the launch reads them: a dict may hold bytes keys, and
    # os.environ refuses a bytes key test.
    return any(key == "HOME" or key == b"HOME" for key in env)


def _audit(event, args):
    if event == "subprocess.Popen":
        argv, env = args[1], args[3]
    elif event == "os.posix_spawn":
        argv, env = args[1], args[2]
    else:
        return
    if _state["exempt"] is not None:
        return
    try:
        if env is None:
            lacking = "inherits an environment without HOME" if not _has_home(os.environ) \
                else None
        else:
            lacking = "is given an environment without HOME" if not _has_home(env) else None
    except Exception as exc:  # a census fault must not break the launch it watches
        _record("%s: HOME census could not inspect the environment of [%s]: %r" % (
            _current_test(), _shown(argv), exc))
        return
    if lacking is None:
        return
    unreasoned = (" (its %s marker has no reason, so it exempts nothing)" % MARKER
                  if _state["unreasoned"] else "")
    line = "%s: child [%s] %s%s; %s" % (_current_test(), _shown(argv), lacking, unreasoned,
                                         REMEDY)
    _record(line)
    raise HomeCensusError(line)


def _current_test():
    return os.environ.get("PYTEST_CURRENT_TEST") or "<outside a test>"


def _shown(argv):
    try:
        items = [argv] if isinstance(argv, (str, bytes, os.PathLike)) else list(argv)
        words = [os.fsdecode(item) if isinstance(item, (str, bytes, os.PathLike))
                 else repr(item) for item in items]
    except Exception:
        words = [repr(argv)]
    return " ".join(" ".join(words).split())[:200]


def _record(line):
    if line not in _state["misses"]:
        _state["misses"].append(line)


def collect(node):
    """Controller side: take the lines a finished xdist worker handed over."""
    output = getattr(node, "workeroutput", None) or {}
    name = getattr(getattr(node, "gateway", None), "id", "worker")
    for line in output.get(WORKEROUTPUT_KEY) or ():
        _state["forwarded"].append("[%s] %s" % (name, line))


def finish(session):
    """Report every refusal and fail the session; a worker hands them on instead."""
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput[WORKEROUTPUT_KEY] = list(_state["misses"])
        return
    problems = list(_state["misses"]) + list(_state["forwarded"])
    if not problems:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    write = reporter.write_line if reporter is not None else (lambda line: print(line, file=sys.stderr))
    write("HOME CENSUS: %d child(ren) started without HOME" % len(problems))
    for line in problems:
        write("  " + line)
    # Raise a clean status only: an interrupted, internal-error or usage-error
    # session keeps the status that says so.
    if int(session.exitstatus) == 0:
        session.exitstatus = 1
