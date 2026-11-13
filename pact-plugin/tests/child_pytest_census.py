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
first and the argv after it, as pytest reads them. A bare `--` does not end
them: pytest reads every `-p` in its argv before it parses options, so a `-p`
after `--` loads or blocks the plugin like any other. `-p randomly` and
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
`pdm run --env NAME=VALUE` and micromamba's `-e NAME=VALUE` and `--env
NAME=VALUE` (either tool also as `--env=NAME=VALUE`) set one. `micromamba run
--clean-env` and `pixi run --clean-env` empty it, except that micromamba keeps
each name a bare `-e NAME` or `--env NAME` gives.

THE CENSUS REFUSES THESE LAUNCH FAMILIES, AND ONLY THESE. A launch in one of
them that does not block pytest-randomly is refused:
- [program] The program is pytest's own: `pytest` or `py.test`, by path or not,
  with a distro version suffix (`pytest-3`, `pytest3`, `py.test-3`), `.exe`, or a
  tool runner's `@version` pin (`pytest@8.3.0`); or the package's
  `pytest/__main__.py`.
- [coverage] The program is `coverage run` (`coverage`, `coverage3`,
  `coverage-3.12`) given `-m pytest`, `-m pytest.__main__`, `--module pytest`, or
  pytest's program or `__main__.py` as its script.
- [module] An interpreter runs `-m pytest`, `-m pytest.__main__`, or the same
  `-m coverage run ...` forms. An interpreter is a word named `python`, `pypy`,
  `ipython`, `graalpy` or `pyston`, each optionally with a version and one letter
  (`python3`, `python3.12`, `python3.12t`, `pythonw`, `pypy3.10`, `ipython3`),
  the Windows launcher `py` or `pyw`, any of these with `.exe`, or
  sys.executable. Its short and long options before `-m` are stepped over,
  including the values of `-W`, `-X` and `--check-hash-based-pycs`, and the
  launcher's `-3`, `-3.12` and `-V:3.12`.
- [mention] Every word naming an interpreter anywhere in a command is read as
  the [module] family, not only the first, so `echo python -m pytest` is
  refused, and a wrapper option's value named like an interpreter
  (`uv run -p python3 python -m pytest`) does not hide the interpreter after it.
- [script] The program is an interpreter that runs pytest's program or
  `__main__.py` as its script (`python /venv/bin/pytest`). Only the program
  position counts here: in `pip install python-dateutil pytest` or
  `echo python /venv/bin/pytest` no interpreter runs a script. When the program
  word is named like an interpreter and follows an option the wrapper tables
  below do not know (neither a value option nor a flag), it may be that option's
  value, so the next word that would then be the program is tried too
  (`uv run --made-up-option python3 python /venv/bin/pytest`). A known flag
  (`uv run -v python3 ...`) or a program not named like an interpreter
  (`uv run -v echo python ...`) is never stepped past.
- [wrapper] Any family above behind NAME=VALUE words, the shell's reserved words
  or the listed wrappers and tool runners (below).
- [shell] Any family above inside the command text of a shell run with `-c`
  (below).
`-m py.test` and `-m _pytest` start no pytest session and are no family.

EVERY OTHER LAUNCH FORM IS OUTSIDE THE CENSUS, whether or not it starts pytest:
the census does not claim to see it, refuse it, or list it. The blind spots at
the end are examples of that rule, not a complete list.

The program is the first word past NAME=VALUE words, the shell's own
`{ } ! if then elif else fi do done while until`, and the listed wrappers with
their options, option values and numeric arguments: `timeout`, `nice`, `nohup`,
`stdbuf`, `sudo`, `env`, `exec`, `time`, `xvfb-run`, and the tool runners
`uv run`, `uv tool run`, `uvx`, `poetry run`, `pipx run`, `pdm run`, `hatch run`
(and its `ENV:` prefix, as in `hatch run test:pytest`), `conda run`,
`micromamba run`, `pipenv run`, `rye run` and `pixi run` (and hatch's `+`/`-`
matrix selectors). Each wrapper's options that take a separate value are in
_WRAPPERS, read from the tool's own `--help` where it is installed (coreutils,
xvfb-run, uv 0.13.6, pipx, pdm, hatch, pipenv, poetry) and from its
documentation otherwise (sudo, GNU time, conda, micromamba, rye, pixi).

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

Blind spots, examples of launches outside the census:
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
8. A bare `pytest` program behind a wrapper not in the list: a test runner or
   task runner (`tox`, `nox`, `make`, `just`), a tool runner whose options come
   before its run verb (`hatch -e test run pytest`, `conda -v run pytest`), and
   process wrappers such as `chrt`, `taskset`, `ionice` or `setsid`; or behind a
   listed wrapper's option whose separate value _WRAPPERS does not name, which
   hides a bare `pytest` program, `coverage run` or a shell after it, and hides
   an interpreter running pytest's script unless the hidden value is itself
   named like an interpreter. [module] and [mention] are still caught there.
9. Shell text the census does not expand or track: `$( )` and backtick
   substitutions, variables (`$PYTEST`), `export`, aliases, functions, `eval`,
   `env -S`, and a script fed to a shell's stdin (`bash <<EOF`, `bash -s`). An
   operator written alone inside quotes (`";"`) is read as an operator.
10. pytest run from a file the census cannot name: a renamed or wrapper copy of
    the console script, a zipapp (`python pytest.pyz`), or a module other than
    `pytest` and `pytest.__main__` that starts a session (`python -m runpy`, a
    plugin's own entry point). The census judges a launch by its words, not by
    what the named file does, nor by what an environment file sets (`uv run
    --env-file`, `pdm run --env-file`).
11. An interpreter not named above: `jython` (which cannot run current
    pytest), a custom-named or embedded interpreter, or one renamed on disk.
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
# pytest's program names: pytest and py.test, with a distro version suffix
# (pytest-3, pytest3, py.test-3) and, after _command_name, without `.exe`.
_PYTEST_PROGRAM = re.compile(r"py\.?test(-?[0-9]+(\.[0-9]+)*)?\Z")
# The modules `-m` can run pytest as.
_PYTEST_MODULES = frozenset({"pytest", "pytest.__main__"})
# The Windows launcher's names; any basename starting with python or pypy is an
# interpreter too.
_LAUNCHERS = frozenset({"py", "pyw"})
# An interpreter's name, after _command_name: one of these, optionally with a
# version and one letter (python3, python3.12t, pythonw, pypy3.10, graalpy-24.1).
_INTERPRETER = re.compile(r"(?:python|pypy|ipython|graalpy|pyston)(?:-?[0-9]+(?:\.[0-9]+)*)?[a-z]?\Z")
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
# The option tables come from each wrapper's own `--help`: coreutils timeout,
# nice, stdbuf and env, xvfb-run, uv 0.13.6 (`uv run`, `uv tool run`), pipx,
# pdm, hatch, pipenv and poetry. sudo, GNU time, conda, micromamba, rye and pixi
# come from their documentation.
_UV_COMMON_VALUE_OPTIONS = frozenset({
    "-w", "--with", "--with-editable", "--with-requirements", "--env-file",
    "--python-platform", "--index", "--default-index", "-i", "--index-url",
    "--extra-index-url", "-f", "--find-links", "--index-strategy", "--keyring-provider",
    "-P", "--upgrade-package", "--upgrade-group", "--resolution", "--prerelease",
    "--prerelease-package", "--fork-strategy", "--exclude-newer", "--exclude-newer-package",
    "--no-sources-package", "--reinstall-package", "--link-mode", "-C", "--config-setting",
    "--config-settings-package", "--no-build-isolation-package", "--no-build-package",
    "--no-binary-package", "--cache-dir", "--refresh-package", "-p", "--python", "--color",
    "--allow-insecure-host", "--directory", "--project", "--config-file",
})
_UV_TOOL_RUN_VALUE_OPTIONS = _UV_COMMON_VALUE_OPTIONS | {
    "--from", "-c", "--constraints", "-b", "--build-constraints", "--overrides",
    "--torch-backend",
}
_WRAPPERS = {
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "nohup": frozenset(),
    "exec": frozenset({"-a"}),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "stdbuf": frozenset({"-i", "--input", "-o", "--output", "-e", "--error"}),
    "sudo": frozenset({"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
                       "-D", "--chdir", "-R", "--chroot", "-r", "--role",
                       "-t", "--type", "-U", "--other-user", "-T", "--command-timeout"}),
    "env": frozenset({"-C", "--chdir"}),
    "xvfb-run": frozenset({"-s", "-f", "-e", "-p", "--server-args",
                           "--auth-file", "--error-file", "--xauth-protocol"}),
    "uv run": _UV_COMMON_VALUE_OPTIONS | {
        "--extra", "--no-extra", "--group", "--no-group", "--only-group",
        "--no-editable-package", "--package",
    },
    "poetry run": frozenset({"-C", "--directory", "-P", "--project"}),
    "uv tool run": _UV_TOOL_RUN_VALUE_OPTIONS,
    "uvx": _UV_TOOL_RUN_VALUE_OPTIONS,
    "pipx run": frozenset({"--spec", "--python", "--with", "-i", "--index-url", "--backend",
                           "--fetch-python"}),
    "pdm run": frozenset({"-p", "--project", "-k", "--skip", "--venv", "--env-file",
                          "--working-dir"}),
    "hatch run": frozenset(),
    "conda run": frozenset({"-n", "--name", "-p", "--prefix", "--cwd"}),
    "micromamba run": frozenset({"-n", "--name", "-p", "--prefix", "-r", "--root-prefix",
                                 "--cwd", "-a", "--attach", "--label"}),
    "pipenv run": frozenset(),
    "rye run": frozenset({"--pyproject"}),
    "pixi run": frozenset({"-e", "--environment", "--manifest-path", "--auth-file", "--color",
                           "--pypi-keyring-provider"}),
}
# micromamba's options that set one environment variable for the command
# (`-e NAME=VALUE`), or keep one through `--clean-env` (`-e NAME`). pdm's
# `--env NAME=VALUE` needs no entry: its value is a NAME=VALUE word.
_ENV_OPTIONS = {
    "micromamba run": frozenset({"-e", "--env"}),
}
# The listed wrappers' options that take no value, from the same sources. The
# script-route candidates never step past a word after one of these; a letter
# that takes a value under another wrapper is known from that table.
_KNOWN_FLAGS = frozenset({
    # uv run / uv tool run / uvx (uv 0.13.6 --help)
    "--active", "--all-extras", "--all-groups", "--all-packages", "--compile-bytecode", "--exact",
    "--frozen", "--gui-script", "--help", "--isolated", "--locked", "--managed-python", "--module",
    "--no-binary", "--no-build", "--no-build-isolation", "--no-cache", "--no-config",
    "--no-default-groups", "--no-dev", "--no-editable", "--no-env-file", "--no-index",
    "--no-managed-python", "--no-progress", "--no-project", "--no-python-downloads",
    "--no-require-build-hashes", "--no-sources", "--no-sync", "--offline", "--only-dev", "--quiet",
    "--refresh", "--reinstall", "--require-build-hashes", "--script", "--system-certs", "--upgrade",
    "--verbose", "--lfs", "-m", "-q", "-v",
    # timeout, env, xvfb-run, time, exec, sudo
    "--preserve-status", "--foreground", "--ignore-environment", "-0", "--null", "--debug",
    "--list-signal-handling", "--auto-servernum", "-l", "--listen-tcp", "--append",
    "--portability", "-A", "-E", "-H", "-K", "-S", "-V",
    "--askpass", "--background", "--preserve-env", "--edit", "--set-home", "--login",
    "--remove-timestamp", "--reset-timestamp", "--list", "--non-interactive",
    "--preserve-groups", "--stdin", "--shell", "--version", "--validate",
    # pipx run, pdm run, poetry run, pipenv run
    "--skip-maintenance", "--global", "--no-path-check", "--path", "--pypackages",
    "--fetch-missing-python", "--system-site-packages", "--editable", "-j", "--json",
    "--site-packages", "--recreate", "--ansi", "--no-ansi", "--no-interaction", "--no-plugins",
    "-vv", "-vvv", "--system",
    # conda run, micromamba run, rye run, pixi run (documentation)
    "--dev", "--debug-wrapper-scripts", "--no-capture-output", "--live-stream", "-d", "--detach",
    "--clean-env", "--force-activate", "--revalidate", "--skip-deps", "--dry-run",
})
# Wrappers named by more than one word, longest first.
_MULTI_WORD_WRAPPERS = tuple(sorted(
    (tuple(name.split()) for name in _WRAPPERS if " " in name), key=len, reverse=True))
# hatch names an environment before its command: `hatch run test:pytest`.
_ENVIRONMENT_PREFIX = re.compile(r"[A-Za-z0-9_.-]+:")
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
        words = next((words for words in _MULTI_WORD_WRAPPERS
                      if tuple(part[i:i + len(words)]) == words), None)
        if words is not None:
            name, i = " ".join(words), i + len(words)
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
    clean = False
    env_args = []  # pdm's and micromamba's --env values, applied after the loop
    while i < len(part):
        arg = part[i]
        if arg in _ENV_OPTIONS.get(name, ()) and i + 1 < len(part):
            env_args.append(part[i + 1])
            i += 2
        elif name in ("pdm run", "micromamba run") and arg.startswith("--env="):
            env_args.append(arg.split("=", 1)[1])
            i += 1
        elif arg == "--clean-env" and name in ("micromamba run", "pixi run"):
            clean = True
            i += 1
        elif name == "env" and arg in ("-i", "--ignore-environment", "-"):
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
        elif name == "hatch run" and arg.startswith("+"):
            i += 1  # a matrix selector, as in `hatch run +py=3.12 test:pytest`
        else:
            break
    if clean:
        env.clear()
    for value in env_args:
        if _is_assignment(value):
            _assign(env, value)
        elif clean and value in inherited:
            env[value] = inherited[value]  # micromamba's `-e NAME` keeps NAME
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


def _command_name(word):
    """A program word's name: its basename, lower-cased, without a tool runner's
    `@version` pin (`pytest@8.3.0`), without `.exe` and without a hatch
    environment prefix (`test:pytest`)."""
    name = os.path.basename(word).lower().split("@", 1)[0]
    if name.endswith(".exe"):
        name = name[:-4]
    prefix = _ENVIRONMENT_PREFIX.match(name)
    return name[prefix.end():] if prefix else name


def _is_pytest_script(word):
    """True for a word naming pytest's program or console-script file, or its
    package's __main__.py."""
    parts = word.replace("\\", "/").split("/")
    return bool(_PYTEST_PROGRAM.match(_command_name(word))) or parts[-2:] == ["pytest", "__main__.py"]


def _is_interpreter(word):
    name = _command_name(word)
    return bool(_INTERPRETER.match(name)) or name in _LAUNCHERS or word == sys.executable


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
    """Index of pytest's first argument when `words[i:]` is `run ... -m pytest`
    or `run ... <pytest's script>`."""
    if i >= len(words) or words[i] != "run":
        return None
    i += 1
    while i < len(words):
        word = words[i]
        if word in ("-m", "--module"):
            return i + 2 if i + 1 < len(words) and words[i + 1] in _PYTEST_MODULES else None
        if word in _COVERAGE_VALUE_OPTIONS:
            i += 2
        elif word.startswith("-") and word != "-":
            i += 1
        else:
            return i + 1 if _is_pytest_script(word) else None
    return None


def _pytest_args(part, program):
    """Index of the first pytest argument when `part` runs pytest, else None."""
    if program < len(part):
        name = _command_name(part[program])
        if _is_pytest_script(part[program]):
            return program + 1
        if _COVERAGE_PROGRAM.match(name):
            return _coverage_run(part, program + 1)
    # [module] and [mention]: every word naming an interpreter is read for its -m
    # forms, so a wrapper option's value named like one (`uv run -p python3`)
    # cannot hide the interpreter after it.
    for at, word in enumerate(part):
        if not _is_interpreter(word):
            continue
        module, after = _module(part, at + 1)
        if module in _PYTEST_MODULES:
            return after
        if module == "coverage":
            found = _coverage_run(part, after)
            if found is not None:
                return found
    # [script]: only an interpreter in the program position runs a script; a
    # word after an interpreter mentioned elsewhere (`pip install python-x
    # pytest`) is no script of it.
    for at in _program_candidates(part, program):
        if _is_interpreter(part[at]):
            module, after = _module(part, at + 1)
            if module is None and after < len(part) and _is_pytest_script(part[after]):
                return after + 1  # the interpreter runs pytest's script file
    return None


def _program_candidates(part, program):
    """The program index, then each later word that is the program if the word
    before it was the value of an option the wrapper tables do not list
    (`uv run --made-up-option python3 python ...`). A step is taken only past an
    interpreter-named word that follows an option the tables do not know: a
    known flag (`-v`) takes no value, and any other word (`echo`, `ls`) is the
    program itself. A part with no program word (only assignments or a bare
    wrapper) has none."""
    if program >= len(part):
        return []
    candidates = [program]
    i = program
    while 0 < i < len(part) and _is_interpreter(part[i]) and _unknown_option(part[i - 1]):
        i += 1
        while i < len(part) and part[i].startswith("-") and part[i] not in ("-", "--"):
            i += 1
        if i >= len(part):
            break
        candidates.append(i)
    return candidates


def _unknown_option(word):
    """True for an option word that may take the next word as its value: an
    option no wrapper table knows, written without `=`."""
    return word.startswith("-") and "=" not in word and word not in ("-", "--") \
        and word not in _KNOWN_FLAGS and not any(word in values for values in _WRAPPERS.values())


def _split(text):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _randomly_flag(words):
    """The last randomly flag anywhere in `words`, a bare `--` included, as
    pytest's -p preparse reads them: "blocked", "loaded" or None."""
    state = None
    i = 0
    while i < len(words):
        word = words[i]
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
    # Raise a clean status only: an interrupted, internal-error or usage-error
    # session keeps the status that says so.
    if int(session.exitstatus) == 0:
        session.exitstatus = 1
