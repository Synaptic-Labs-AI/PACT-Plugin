"""
Location: pact-plugin/tests/child_pytest_census.py
Summary: Refuses a pytest child the suite starts without blocking pytest-randomly.
Used by: tests/conftest.py, whose pytest_configure installs it, whose
         pytest_testnodedown collects each xdist worker's misses, and whose
         pytest_sessionfinish reports; tests/test_child_pytest_census.py.

pytest-randomly is installed in every environment and blocked by the root
pyproject.toml addopts. A child session rooted outside the repo does not read
that config, so it would shuffle. A pytest child is deterministic when its argv
carries `-p no:randomly` (or `-pno:randomly`), when it opts in visibly with
`-p randomly` (or `-prandomly`), or when its effective environment (the explicit
`env` when one is passed, else this process's) has a non-empty
PYTEST_DISABLE_PLUGIN_AUTOLOAD. The repo config is not consulted: every pytest
child carries the flag itself.

A pytest child is an argv whose program is `pytest` or `py.test`, or one whose
first Python interpreter word (a basename starting with `python` or `pypy`, or
sys.executable) runs `-m pytest`; the interpreter's short and long options before
`-m` are stepped over, including the value of `--check-hash-based-pycs`. The
program is the first word past NAME=VALUE words and the listed wrappers
(`timeout`, `nice`, `nohup`, `stdbuf`, `sudo`, `env`, `xvfb-run`, `uv run`,
`poetry run`) with their options, option values and numeric arguments.
`sh|bash|zsh|dash -c "<command>"` is judged on each part of its command between
the operators `&&`, `||`, `;`, `|` and `&` written as separate words. A word that
only names an interpreter followed by `-m pytest`, as in `echo python -m pytest`,
is refused too.

On a pytest child that is not deterministic the census records a line naming
PYTEST_CURRENT_TEST, the argv and the cwd, then raises ChildPytestCensusError,
which aborts the launch before the child starts. finish() fails the session for
every recorded line, so a test that swallows the error still fails the run.
Under xdist a worker cannot fail the run itself (the controller ignores a
worker's exit status), so a worker's finish() hands its lines to the controller
through workeroutput, collect() gathers them as each worker goes down, and the
controller's finish() reports and fails.

Blind spots:
1. A pytest started by a shell script or another program the census does not
   parse (a `.sh` file, `make`). verify-gate-tree.sh is covered by the args its
   tests pass.
2. A pytest started by a Python intermediary that does not load this census (a
   script run in a rig). verify_full_collection.py carries the flag itself.
3. `python -c "...pytest.main(...)"` and in-process pytest.main.
4. Launches through os.system, os.exec* or os.spawn*, which raise other events.
5. Code no measured run reached: CI-only branches and skipped tests.
6. A nested session inside a child rooted outside the repo, which does not
   load this census.
7. A worker that crashes before its finish() runs sends no lines; xdist reports
   the crash itself.
8. A bare `pytest` program behind a wrapper not in the list (`hatch run pytest`,
   `pdm run pytest`), or behind a listed wrapper's option whose separate value
   the list does not name. The interpreter form (`<wrapper> python -m pytest`) is
   still caught.
"""
import os
import re
import shlex
import sys

REMEDY = 'add "-p", "no:randomly" to the child argv'
WORKEROUTPUT_KEY = "child_pytest_census_misses"
AUTOLOAD_ENV = "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
_PYTEST_PROGRAMS = frozenset({"pytest", "py.test"})
_OPERATORS = frozenset({"&&", "||", ";", "|", "&"})
# Programs that run the command after them, each with its options that take a
# separate value.
_WRAPPERS = {
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "nohup": frozenset(),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "sudo": frozenset({"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir"}),
    "xvfb-run": frozenset({"-n", "-s", "-f", "-e", "-p", "--server-num", "--server-args",
                           "--auth-file", "--error-file", "--xauth-protocol"}),
    "uv run": frozenset({"--with", "--python", "-p", "--project", "--directory", "--package",
                         "--extra", "--group", "--env-file", "--with-requirements"}),
    "poetry run": frozenset({"-C", "--directory", "-P", "--project"}),
}
_TWO_WORD_WRAPPERS = frozenset({("uv", "run"), ("poetry", "run")})
_DURATION = re.compile(r"\d+(\.\d+)?[smhd]?\Z")

_state = {"installed": False, "misses": [], "forwarded": []}


class ChildPytestCensusError(RuntimeError):
    """A pytest child would start with pytest-randomly unblocked."""


def installed():
    return _state["installed"]


def install():
    """Install the launch audit hook once."""
    if _state["installed"]:
        return
    sys.addaudithook(_audit)
    _state["installed"] = True


def _audit(event, args):
    if event == "subprocess.Popen":
        _executable, argv, cwd, env = args
    elif event == "os.posix_spawn":
        (_executable, argv, env), cwd = args, None
    else:
        return
    try:
        line = _miss(argv, cwd, env)
    except Exception as exc:  # a census fault must not break the launch it watches
        _record("child pytest census could not inspect a launch: %r" % (exc,))
        return
    if line:
        _record(line)
        raise ChildPytestCensusError(line)


def _miss(argv, cwd, env):
    """Return the refusal line for a non-deterministic pytest child, else None."""
    tokens = _tokens(argv)
    if not any(_is_pytest(part) and not _deterministic(part, env) for part in _commands(tokens)):
        return None
    current = os.environ.get("PYTEST_CURRENT_TEST") or "<outside a test>"
    shown = " ".join(" ".join(tokens).split())[:200]
    where = os.fsdecode(cwd) if cwd is not None else os.getcwd()
    return "%s: pytest child [%s] (cwd %s) does not block pytest-randomly; %s" % (
        current, shown, where, REMEDY)


def _split(text):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _tokens(argv):
    """The words of the launched command, with a `<shell> -c` command opened."""
    if isinstance(argv, (str, bytes)):
        tokens = _split(os.fsdecode(argv))
    else:
        tokens = [os.fsdecode(a) for a in (argv or [])]
    if len(tokens) >= 3 and os.path.basename(tokens[0]) in _SHELLS and tokens[1] == "-c":
        tokens = _split(tokens[2])
    return tokens


def _commands(tokens):
    """Split a shell command line at its control operators."""
    part = []
    for word in tokens:
        if word in _OPERATORS:
            yield part
            part = []
        else:
            part.append(word)
    yield part


def _is_assignment(word):
    name, eq, _ = word.partition("=")
    return bool(eq) and name.isidentifier()


def _program(tokens):
    """Index of the program word, past NAME=VALUE words and the listed wrappers
    with their options, option values and numeric arguments."""
    i = 0
    while i < len(tokens):
        word = os.path.basename(tokens[i])
        if _is_assignment(tokens[i]):
            i += 1
            continue
        pair = tuple(tokens[i:i + 2])
        if pair in _TWO_WORD_WRAPPERS:
            name, i = " ".join(pair), i + 2
        elif word in _WRAPPERS:
            name, i = word, i + 1
        else:
            return i
        takes_value = _WRAPPERS.get(name, frozenset())
        while i < len(tokens):
            arg = tokens[i]
            if arg in takes_value:
                i += 2
            elif (arg.startswith("-") and arg != "-") or _DURATION.match(arg) or _is_assignment(arg):
                i += 1
            else:
                break
    return i


def _interpreter(tokens):
    """Index of the first Python interpreter word, or None."""
    for i, word in enumerate(tokens):
        base = os.path.basename(word)
        if base.startswith(("python", "pypy")) or word == sys.executable:
            return i
    return None


def _is_pytest(tokens):
    start = _program(tokens)
    if start < len(tokens) and os.path.basename(tokens[start]) in _PYTEST_PROGRAMS:
        return True
    at = _interpreter(tokens)
    if at is None:
        return False
    rest = tokens[at + 1:]
    i = 0
    while i < len(rest):
        word = rest[i]
        if word in ("-W", "-X", "--check-hash-based-pycs"):
            i += 2
            continue
        if word.startswith("--") and word != "--":
            i += 1  # a long option without a value
            continue
        if not word.startswith("-") or word in ("-", "--"):
            return False  # a script, stdin, or the end of options: not `-m pytest`
        letters = word[1:]
        for at_letter, letter in enumerate(letters):
            if letter == "c":
                return False
            if letter == "m":
                module = letters[at_letter + 1:] or (rest[i + 1] if i + 1 < len(rest) else "")
                return module == "pytest"
            if letter in "WX":
                break  # its value is the rest of this word
        i += 1
    return False


def _deterministic(tokens, env):
    for i, word in enumerate(tokens):
        if word in ("-pno:randomly", "-prandomly"):
            return True
        if word == "-p" and i + 1 < len(tokens) and tokens[i + 1] in ("no:randomly", "randomly"):
            return True
    effective = os.environ if env is None else env
    for key, value in effective.items():
        if os.fsdecode(key) == AUTOLOAD_ENV and os.fsdecode(value):
            return True
    return False


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
    """Report every miss and fail the session; a worker hands them on instead."""
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput[WORKEROUTPUT_KEY] = list(_state["misses"])
        return
    problems = list(_state["misses"]) + list(_state["forwarded"])
    if not problems:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    write = reporter.write_line if reporter is not None else (lambda line: print(line, file=sys.stderr))
    write("CHILD PYTEST CENSUS: %d pytest child(ren) started without blocking pytest-randomly"
          % len(problems))
    for line in problems:
        write("  " + line)
    session.exitstatus = 1
