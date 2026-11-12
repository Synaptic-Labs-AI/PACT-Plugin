"""
Location: pact-plugin/tests/child_pytest_census.py
Summary: Refuses a pytest child the suite starts without blocking pytest-randomly.
Used by: tests/conftest.py, whose pytest_configure installs it, whose
         pytest_testnodedown collects each xdist worker's misses, and whose
         pytest_sessionfinish reports; tests/test_child_pytest_census.py.

pytest-randomly is installed in every environment and blocked by the root
pyproject.toml addopts. A child session rooted outside the repo does not read
that config, so it would shuffle. Every pytest child must therefore block the
plugin itself; none may load it. The repo config is not consulted.

A pytest child is deterministic when the LAST randomly flag it is given is
`-p no:randomly` (or `-pno:randomly`). The flags are read from PYTEST_ADDOPTS
first and the argv after it, as pytest reads them, and the argv stops at a bare
`--`, after which pytest takes words as positional arguments. `-p randomly` and
`-p pytest_randomly` load the plugin, so a child whose last randomly flag is one
of them is refused even with autoload disabled. `-p no:pytest_randomly` blocks
nothing: the plugin's entry point is named `randomly`. With no randomly flag at
all, a child is deterministic when its environment has a non-empty
PYTEST_DISABLE_PLUGIN_AUTOLOAD.

A child's environment starts as the explicit `env` passed to the launch, or this
process's when none is passed. NAME=VALUE words before the program apply to it,
and so do the `env` wrapper's own options: `-i`, `--ignore-environment` and a
bare `-` empty it, `-u NAME`, `-uNAME` and `--unset=NAME` remove a name, and its
NAME=VALUE words set one. `sudo` starts from an empty environment unless it is
given `-E` or `--preserve-env`, or `--preserve-env=NAMES` for those names.

A pytest child is a command whose program is `pytest` or `py.test`, whose program
is `coverage run` with `-m pytest` (or `--module pytest`), or whose first Python
interpreter word (a basename starting with `python` or `pypy`, or
sys.executable) runs `-m pytest` or `-m coverage run ... -m pytest`. The
interpreter's short and long options before `-m` are stepped over, including the
values of `-W`, `-X` and `--check-hash-based-pycs`. A word that only names an
interpreter followed by `-m pytest`, as in `echo python -m pytest`, is refused
too. The program is the first word past NAME=VALUE words, the shell's own
`{ } ! if then elif else fi do done while until`, and the listed wrappers
(`timeout`, `nice`, `nohup`, `stdbuf`, `sudo`, `env`, `exec`, `time`, `xvfb-run`,
`uv run`, `poetry run`) with their options, option values and numeric arguments.

A shell (`sh`, `bash`, `zsh`, `dash`, `ksh`) run with `-c` (alone, or bundled as
in `-lc` or `-ec`, after other options such as `-e`, `-o pipefail` or `--norc`)
has its command text opened, and so does one behind a wrapper or an assignment.
The text is read as a shell reads its words: quotes and escapes are resolved, a
backslash-newline continues the line, and each command between `;`, `&`, `&&`,
`||`, `|`, `|&`, a newline, `(` and `)` is judged on its own, written with or
without spaces. A redirection (`>`, `>>`, `<`, `2>&1`, `&>`) and its target do
not end a command. A shell command inside a shell command is opened the same
way, up to four levels deep.

On a pytest child that is not deterministic the census records a line naming
PYTEST_CURRENT_TEST, the argv and the cwd, then raises ChildPytestCensusError,
which aborts the launch before the child starts. finish() fails the session for
every recorded line, so a test that swallows the error still fails the run.
Under xdist a worker cannot fail the run itself (the controller ignores a
worker's exit status), so a worker's finish() hands its lines to the controller
through workeroutput, collect() gathers them as each worker goes down, and the
controller's finish() reports and fails.

A launch the census cannot read (a word or env value that is not text) is
recorded as a fault line naming PYTEST_CURRENT_TEST and the launch, and fails
the session, when any of its words names pytest (`pytest` or `py.test`). A
malformed launch that names neither is left to the launcher's own error. The
launch itself is never aborted for a fault.

Blind spots:
1. A pytest started by a shell script file or another program the census does
   not parse (`bash run.sh`, `make`). verify-gate-tree.sh is covered by the
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
8. A bare `pytest` program behind a wrapper not in the list (`hatch run pytest`,
   `pdm run pytest`), or behind a listed wrapper's option whose separate value
   the list does not name. The interpreter form (`<wrapper> python -m pytest`) is
   still caught.
9. Shell text the census does not expand or track: `$( )` and backtick
   substitutions, variables (`$PYTEST`), `export`, aliases, functions, `eval`,
   `env -S`, and a script fed to a shell's stdin (`bash <<EOF`, `bash -s`). An
   operator written alone inside quotes (`";"`) is read as an operator.
"""
import os
import re
import shlex
import sys

REMEDY = 'add "-p", "no:randomly" to the child argv'
WORKEROUTPUT_KEY = "child_pytest_census_misses"
AUTOLOAD_ENV = "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
ADDOPTS_ENV = "PYTEST_ADDOPTS"
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_PYTEST_PROGRAMS = frozenset({"pytest", "py.test"})
_PYTEST_MENTION = re.compile(r"py\.?test")
_COVERAGE_PROGRAM = re.compile(r"coverage(-?\d+(\.\d+)*)?\Z")
_BLOCKING = "no:randomly"
_LOADING = frozenset({"randomly", "pytest_randomly"})
# Characters a shell reads as operators; a run of them is one lexer token.
_OPERATOR_CHARS = "();<>|&\n"
# Words a shell reads before a command without running them.
_RESERVED = frozenset({"{", "}", "!", "if", "then", "elif", "else", "fi",
                       "do", "done", "while", "until"})
# Programs that run the command after them, each with its options that take a
# separate value.
_WRAPPERS = {
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "nohup": frozenset(),
    "exec": frozenset({"-a"}),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "sudo": frozenset({"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T"}),
    "env": frozenset({"-C", "--chdir"}),
    "xvfb-run": frozenset({"-n", "-s", "-f", "-e", "-p", "--server-num", "--server-args",
                           "--auth-file", "--error-file", "--xauth-protocol"}),
    "uv run": frozenset({"--with", "--python", "-p", "--project", "--directory", "--package",
                         "--extra", "--group", "--env-file", "--with-requirements"}),
    "poetry run": frozenset({"-C", "--directory", "-P", "--project"}),
}
_TWO_WORD_WRAPPERS = frozenset({("uv", "run"), ("poetry", "run")})
_DURATION = re.compile(r"\d+(\.\d+)?[smhd]?\Z")
# Shell options that take a separate value, and the long ones that take a file.
_SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
# coverage run's options that take a separate value.
_COVERAGE_VALUE_OPTIONS = frozenset({"--rcfile", "--source", "--include", "--omit",
                                     "--context", "--concurrency", "--data-file", "--debug"})
_MAX_DEPTH = 4

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
        # Fail closed only where the launch could be a pytest child; a malformed
        # launch of anything else is the launcher's own error to raise.
        if _names_pytest(argv):
            _record("%s: child pytest census could not inspect a pytest launch [%s]: %r" % (
                _current_test(), _shown(argv), exc))
        return
    if line:
        _record(line)
        raise ChildPytestCensusError(line)


def _current_test():
    return os.environ.get("PYTEST_CURRENT_TEST") or "<outside a test>"


def _text(item):
    try:
        return os.fsdecode(item)
    except Exception:
        return repr(item)


def _items(argv):
    if isinstance(argv, (str, bytes, os.PathLike)):
        return [argv]
    try:
        return list(argv or ())
    except Exception:
        return [argv]


def _shown(argv):
    return " ".join(" ".join(_text(item) for item in _items(argv)).split())[:200]


def _names_pytest(argv):
    """True when any word of a launch the census could not judge names pytest."""
    return any(_PYTEST_MENTION.search(_text(item)) for item in _items(argv))


def _miss(argv, cwd, env):
    """Return the refusal line for a non-deterministic pytest child, else None."""
    if isinstance(argv, (str, bytes)):
        words, shell_text = _lex(os.fsdecode(argv)), True
    else:
        words, shell_text = [os.fsdecode(a) for a in (argv or [])], False
    environment = (dict(os.environ) if env is None
                   else {os.fsdecode(k): os.fsdecode(v) for k, v in env.items()})
    for part, program, part_env in _commands(words, environment, shell_text, 0):
        start = _pytest_args(part, program)
        if start is not None and not _deterministic(part[start:], part_env):
            where = os.fsdecode(cwd) if cwd is not None else os.getcwd()
            return "%s: pytest child [%s] (cwd %s) does not block pytest-randomly; %s" % (
                _current_test(), _shown(words), where, REMEDY)
    return None


def _lex(text):
    """The words of shell text, with each run of operator characters its own word."""
    text = text.replace("\\\n", " ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_OPERATOR_CHARS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # an unclosed quote: fall back to a plain split
        return re.findall(r"[();<>|&\n]+|[^\s();<>|&]+", text)


def _is_operator(word):
    return bool(word) and all(char in _OPERATOR_CHARS for char in word)


def _split_commands(words):
    """Split lexed shell words at their separators, dropping each redirection and
    its target."""
    part, skip = [], False
    for word in words:
        if skip:
            skip = False
            continue
        if _is_operator(word):
            if "<" in word or ">" in word:
                skip = True
                continue
            yield part
            part = []
            continue
        part.append(word)
    yield part


def _commands(words, env, shell_text, depth):
    """Yield (command words, program index, environment) for every command in
    `words`, with each shell -c command text opened."""
    for part in (_split_commands(words) if shell_text else [words]):
        if not part:
            continue
        program, part_env = _program(part, env)
        text = _shell_command(part, program)
        if text is not None and depth < _MAX_DEPTH:
            yield from _commands(_lex(text), part_env, True, depth + 1)
        else:
            yield part, program, part_env


def _is_assignment(word):
    name, eq, _ = word.partition("=")
    return bool(eq) and name.isidentifier()


def _assign(env, word):
    name, _, value = word.partition("=")
    env[name] = value


def _program(part, env):
    """Index of the program word, past NAME=VALUE words, reserved words and the
    listed wrappers with their options, option values and numeric arguments, and
    the environment the program runs with."""
    env = dict(env)
    i = 0
    while i < len(part):
        word = part[i]
        if _is_assignment(word):
            _assign(env, word)
            i += 1
            continue
        if word in _RESERVED:
            i += 1
            continue
        pair = tuple(part[i:i + 2])
        if pair in _TWO_WORD_WRAPPERS:
            name, i = " ".join(pair), i + 2
        elif os.path.basename(word) in _WRAPPERS:
            name, i = os.path.basename(word), i + 1
        else:
            return i, env
        i = _wrapper_options(part, i, name, env)
    return i, env


def _wrapper_options(part, i, name, env):
    """Step over a wrapper's options, applying the ones that change the
    environment, and return the index after them."""
    takes_value = _WRAPPERS[name]
    inherited = dict(env)
    if name == "sudo":
        env.clear()
    while i < len(part):
        arg = part[i]
        if name == "env" and arg in ("-i", "--ignore-environment", "-"):
            env.clear()
            i += 1
        elif name == "env" and arg in ("-u", "--unset") and i + 1 < len(part):
            env.pop(part[i + 1], None)
            i += 2
        elif name == "env" and arg.startswith(("--unset=", "-u")):
            env.pop(arg.split("=", 1)[1] if arg.startswith("--") else arg[2:], None)
            i += 1
        elif name == "sudo" and arg in ("-E", "--preserve-env"):
            env.update(inherited)
            i += 1
        elif name == "sudo" and arg.startswith("--preserve-env="):
            for kept in arg.split("=", 1)[1].split(","):
                if kept in inherited:
                    env[kept] = inherited[kept]
            i += 1
        elif arg in takes_value:
            i += 2
        elif _is_assignment(arg):
            _assign(env, arg)
            i += 1
        elif (arg.startswith("-") and arg != "-") or _DURATION.match(arg):
            i += 1
        else:
            break
    return i


def _shell_command(part, program):
    """The command text of a shell run with -c at `program`, else None."""
    if program >= len(part) or os.path.basename(part[program]) not in _SHELLS:
        return None
    has_c = False
    i = program + 1
    while i < len(part):
        word = part[i]
        if word in _SHELL_VALUE_OPTIONS:
            i += 2
        elif word == "--":
            i += 1
            break
        elif word.startswith("--"):
            i += 1
        elif word[:1] in ("-", "+") and len(word) > 1:
            has_c = has_c or (word[0] == "-" and "c" in word[1:])
            i += 1
        else:
            break
    return part[i] if has_c and i < len(part) else None


def _interpreter(tokens):
    """Index of the first Python interpreter word, or None."""
    for i, word in enumerate(tokens):
        base = os.path.basename(word)
        if base.startswith(("python", "pypy")) or word == sys.executable:
            return i
    return None


def _module(words, i):
    """The module an interpreter runs with -m, reading its options from `i`, and
    the index after the module name; (None, i) when it runs no module."""
    while i < len(words):
        word = words[i]
        if word in ("-W", "-X", "--check-hash-based-pycs"):
            i += 2
            continue
        if word.startswith("--") and word != "--":
            i += 1  # a long option without a value
            continue
        if not word.startswith("-") or word in ("-", "--"):
            return None, i  # a script, stdin, or the end of options
        letters = word[1:]
        for at, letter in enumerate(letters):
            if letter == "c":
                return None, i
            if letter == "m":
                rest = letters[at + 1:]
                if rest:
                    return rest, i + 1
                return (words[i + 1] if i + 1 < len(words) else None), i + 2
            if letter in "WX":
                if at == len(letters) - 1:
                    i += 1  # its value is the next word
                break  # otherwise its value is the rest of this word
        i += 1
    return None, i


def _coverage_run(words, i):
    """Index of pytest's first argument when `words[i:]` is `run ... -m pytest`."""
    if i >= len(words) or words[i] != "run":
        return None
    i += 1
    while i < len(words):
        word = words[i]
        if word in ("-m", "--module"):
            return i + 2 if i + 1 < len(words) and words[i + 1] == "pytest" else None
        if word in _COVERAGE_VALUE_OPTIONS:
            i += 2
        elif word.startswith("-") and word != "-":
            i += 1
        else:
            return None  # a script
    return None


def _pytest_args(part, program):
    """Index of the first pytest argument when `part` runs pytest, else None."""
    if program < len(part):
        base = os.path.basename(part[program])
        if base in _PYTEST_PROGRAMS:
            return program + 1
        if _COVERAGE_PROGRAM.match(base):
            return _coverage_run(part, program + 1)
    at = _interpreter(part)
    if at is None:
        return None
    module, after = _module(part, at + 1)
    if module == "pytest":
        return after
    if module == "coverage":
        return _coverage_run(part, after)
    return None


def _split(text):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _randomly_flag(words):
    """The last randomly flag in `words` before a bare `--`: "blocked", "loaded"
    or None."""
    state = None
    i = 0
    while i < len(words):
        word = words[i]
        if word == "--":
            break
        if word == "-p":
            value = words[i + 1] if i + 1 < len(words) else None
            i += 2
        elif word.startswith("-p") and len(word) > 2:
            value = word[2:]
            i += 1
        else:
            i += 1
            continue
        if value == _BLOCKING:
            state = "blocked"
        elif value in _LOADING:
            state = "loaded"
    return state


def _deterministic(args, env):
    """True when pytest run with `args` and `env` leaves pytest-randomly unloaded."""
    flag = _randomly_flag(args) or _randomly_flag(_split(env.get(ADDOPTS_ENV, "")))
    if flag is not None:
        return flag == "blocked"
    return bool(env.get(AUTOLOAD_ENV))


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
