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

A pytest child is an argv whose program is `pytest` or `py.test`, or a Python
interpreter run with `-m pytest`. Leading NAME=VALUE words and a leading `env`
are skipped, and `sh|bash|zsh|dash -c "<command>"` is judged on each part of its
command between the operators `&&`, `||`, `;`, `|` and `&` written as separate
words.

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
   parse (a `.sh` file, `make`, `uv run`). verify-gate-tree.sh is covered by the
   args its tests pass.
2. A pytest started by a Python intermediary that does not load this census (a
   script run in a rig). verify_full_collection.py carries the flag itself.
3. `python -c "...pytest.main(...)"` and in-process pytest.main.
4. Launches through os.system, os.exec* or os.spawn*, which raise other events.
5. Code no measured run reached: CI-only branches and skipped tests.
6. A nested session inside a child rooted outside the repo, which does not
   load this census.
7. A worker that crashes before its finish() runs sends no lines; xdist reports
   the crash itself.
"""
import os
import shlex
import sys

REMEDY = 'add "-p", "no:randomly" to the child argv'
WORKEROUTPUT_KEY = "child_pytest_census_misses"
AUTOLOAD_ENV = "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
_PYTEST_PROGRAMS = frozenset({"pytest", "py.test"})
_OPERATORS = frozenset({"&&", "||", ";", "|", "&"})

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


def _program(tokens):
    """Index of the program word after leading NAME=VALUE words and `env`."""
    i, in_env = 0, False
    while i < len(tokens):
        word = tokens[i]
        if not in_env and os.path.basename(word) == "env":
            in_env = True
        elif not (word.split("=", 1)[0].isidentifier() and "=" in word) and not (
                in_env and word.startswith("-")):
            return i
        i += 1
    return i


def _is_pytest(tokens):
    start = _program(tokens)
    if start >= len(tokens):
        return False
    program = os.path.basename(tokens[start])
    if program in _PYTEST_PROGRAMS:
        return True
    if not (program.startswith("python") or tokens[start] == sys.executable):
        return False
    rest = tokens[start + 1:]
    i = 0
    while i < len(rest):
        word = rest[i]
        if word in ("-W", "-X"):
            i += 2
            continue
        if not word.startswith("-") or word == "-" or word.startswith("--"):
            return False  # a script, stdin, or a long option: not `-m pytest`
        letters = word[1:]
        for at, letter in enumerate(letters):
            if letter == "c":
                return False
            if letter == "m":
                module = letters[at + 1:] or (rest[i + 1] if i + 1 < len(rest) else "")
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
