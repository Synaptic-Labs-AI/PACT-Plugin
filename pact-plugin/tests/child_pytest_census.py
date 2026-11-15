"""
Location: pact-plugin/tests/child_pytest_census.py
Summary: Refuses a pytest child the suite starts without blocking pytest-randomly.
Used by: pact-plugin/conftest.py, whose pytest_configure calls register(),
         which installs it and registers the Plugin whose hooks collect each
         xdist worker's misses and report at session finish, in every session
         under pact-plugin/, a session of skills/ tests alone included;
         tests/test_child_pytest_census.py.

pytest-randomly is installed in CI, where constraints-ci.txt pins it, and may or
may not be installed locally; the root pyproject.toml addopts blocks it, and
`-p no:randomly` is a no-op where it is absent. A child session rooted outside
the repo does not read that config, so where the plugin is installed it would
shuffle. Every pytest child must therefore block the plugin itself; none may
load it. The repo config is not consulted.

A pytest child is deterministic when pytest, loading plugins as pytest 9.2
does, registers pytest-randomly under none of the three names that run its
hooks: its entry point `randomly`, its module `pytest_randomly`, and that module
imported again as `pytest_randomly.__init__`. The census reads the words pytest
reads, in pytest's order: the value of the last `-o addopts=...` (or
`--override-ini addopts=...`) before any bare `--`, then PYTEST_ADDOPTS, then
the argv. Each `-p VALUE` (or `-pVALUE`, the value stripped of spaces) applies
in turn, and a bare `--` does not end them: pytest reads every `-p` before it
parses options. `-p no:NAME` blocks NAME, and `pytest_NAME` unless NAME starts
with `pytest_`, and unloads either if it was loaded; `-p NAME` loads NAME when
it is one of the three, whatever came before. So `-p no:randomly` blocks the
entry point and the module but not `pytest_randomly.__init__`, and
`-p no:pytest_randomly` leaves the entry point to load. Next the entry point
loads unless it is blocked, PYTEST_DISABLE_PLUGIN_AUTOLOAD is non-empty, or
`--disable-plugin-autoload` comes before any bare `--`. Last, each
comma-separated name in PYTEST_PLUGINS loads when it is one of the three and
not blocked, with autoload disabled or not. Other PYTEST_PLUGINS names are not
checked, so a child is refused for one of the three even when another name
there is a module pytest cannot import, which stops pytest with a usage error
before any test runs.

A child's environment starts as the explicit `env` passed to the launch, or this
process's when none is passed, and the NAME=VALUE words before the program apply
to it.

THE CENSUS REFUSES THESE LAUNCH FAMILIES, AND ONLY THESE. A launch in one of
them that does not block pytest-randomly is refused, and a [wrapper] launch is
refused whether or not it does:
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
- [mention] Every word naming an interpreter in a command is read as the
  [module] family, not only the first, up to the word in program position when
  that word names a program that runs none of its words (listed under
  [wrapper]). The word in program position is the program itself, or the
  command a listed wrapper runs (followed along a chain), or the subcommand of
  git, whose subcommand is the program it runs. So `echo python -m pytest`,
  `printf '%s\\n' python -m pytest`, `timeout 5 echo python -m pytest` and
  `git grep python -m pytest` are mentions, while `make test python -m pytest`
  is refused: `test` there is make's argument, not a program. The stop holds
  for one command only: `printf x; python -m pytest` is still judged.
- [script] The program is an interpreter that runs pytest's program or
  `__main__.py` as its script (`python /venv/bin/pytest`). Only the program
  position counts here: in `pip install python-dateutil pytest` or
  `echo python /venv/bin/pytest` no interpreter runs a script.
- [wrapper] Any family above as the command of a program the census does not
  read (`timeout 60 pytest`, `sudo python -m pytest`, `uv run pytest`), refused
  whether or not it blocks pytest-randomly, since the census cannot tell what
  environment and argv that program passes on. The program is any program
  except an interpreter, `coverage`, pytest's program, and the programs that
  print, look up, read, list or ignore their words and run none of them:
  `echo`, `printf`, `which`, `true`, `false`, `:`, `grep`, `egrep`, `fgrep`,
  `rg`, `ag`, `ls`, `cat`, `head`, `tail`, `wc`, `sed`, `awk`, `cut`, `sort`,
  `uniq`, `diff`, `file`, `stat`, `test`, `[`, `chmod`, `chown`, `touch`,
  `mkdir`, `rm`, `cp`, `mv` and `ln`. So `grep -c pytest log`,
  `ls -la pytest` and `chmod 755 /venv/bin/pytest` are mentions, not launches.
  Its command is the first word after it that is not an option (a word starting
  with `-`), a NAME=VALUE word, a number or duration (`60`, `1.5m`, `inf`,
  `0x5`), a hatch matrix selector (`+py=3.12`, `-py=3.11`) or the verb `run`
  or `tool` (`uv tool run pytest`, `hatch -e test run pytest`, and
  `npm run pytest`, whose package script may start pytest). A word right
  after an option may be that option's value, so the word after it is tried
  too (`sudo -u bob pytest`).
  The command is also tried as a shell whose -c text runs a family above, or
  runs such a program (`sudo bash -c 'timeout 5 pytest'`). When the command is
  one of the process wrappers and tool runners `env`, `nice`, `nohup`, `sudo`,
  `timeout`, `time`, `stdbuf`, `xvfb-run`, `exec`, `uv`, `uvx`, `pipx`, `poetry`,
  `pdm`, `pipenv`, `hatch`, `rye`, `pixi`, `conda` or `micromamba`, its own
  command is searched for the same way, so a chain of them is followed to the
  program it starts (`timeout 60 nice -n 5 pytest`, `sudo env uv run pytest`).
  find is in that list, and its commands are the words after `-exec`,
  `-execdir`, `-ok` and `-okdir` (`find . -name x -exec pytest {} ;`).
- [shell] Any family above inside the command text of a shell run with `-c`
  (below).
`-m py.test` and `-m _pytest` start no pytest session and are no family.

EVERY OTHER LAUNCH FORM IS OUTSIDE THE CENSUS, whether or not it starts pytest:
the census does not claim to see it, refuse it, or list it. The blind spots at
the end are examples of that rule, not a complete list.

A HARMLESS COMMAND THE CENSUS STILL REFUSES IS AN ACCEPTED, DOCUMENTED LIMIT,
not a defect to fix. A program the census does not read is refused when a word
that may be its command starts pytest (`make check python -m pytest`,
`python-config -m pytest`), because telling a mention from a launch there would
need that program's own argument grammar. The refusal fails loudly, naming the
program and the remedy.

The program is the first word past NAME=VALUE words, the shell's own
`{ } ! if then elif else fi do done while until`, `function NAME`, `coproc`
(and the NAME it takes before a compound command), `builtin` and `command` (and
its `-p`; `command -v` and `command -V` run nothing). `exec` and `time` are
programs like any other, so a pytest after them is refused as [wrapper].

A shell (`sh`, `bash`, `zsh`, `dash`, `ksh`) run with `-c` (alone, or bundled as
in `-lc` or `-ec`, after other options such as `-e`, `-o pipefail` or `--norc`)
has its command text opened, and so does one behind an assignment.
The text is read as bash reads it:
- Quotes and escapes are resolved, and a backslash-newline joins the lines.
- A `#` that starts a word begins a comment to the end of the line; a `#` inside
  a word or inside quotes is text (`--base-url=http://x/#frag`).
- Each command between `;`, `&`, `&&`, `||`, `|`, `|&`, a newline, `(` and `)`
  is judged on its own, written with or without spaces, and so is a function
  body, where it is defined, whether or not the function is called.
- A redirection (`>`, `>>`, `<`, `2>&1`, `&>`, the here-string `<<<`) and its
  target do not end a command, and neither does a file descriptor number or
  `{name}` written against it (`2>/dev/null pytest`). An operator written
  against another (`true;>log pytest`) is still read as each of them.
- A heredoc's body (`<<WORD` or `<<-WORD`) is data, not commands: it runs from
  the next newline to the line that is the delimiter (its leading tabs stripped
  for `<<-`), and several heredocs on one line take their bodies in turn. In a
  heredoc whose delimiter is unquoted, the body's command substitutions are
  judged.
- The command text of a command substitution (`$( )` or backticks, also inside
  double quotes) and of a process substitution (`<( )` or `>( )`) is judged as
  its own shell text. Arithmetic (`$(( ))` and `(( ))`) runs no command. As in
  bash, `((` and `$((` open arithmetic only when the paren they open closes on
  `))`; `((cmd) )` is two subshells and `$((cmd) )` a command substitution of a
  subshell, both judged.
- A shell command inside a shell command is opened the same way. Shells and
  substitutions together are opened up to four levels deep.
- Every shell's `-c` text (`sh`, `dash`, `zsh`, `ksh`) is read by these bash
  rules, so a construct another shell lacks or reads differently is judged as
  bash reads it: dash rejects `<( )` and `<<<` as syntax errors, so
  `dash -c 'cat <(pytest -q)'` is refused although dash runs nothing.
- Text that bash rejects as a syntax error is still judged by these rules and
  refused on purpose, although it starts no child: `echo \\$(pytest -q)`.

On a pytest child that is not deterministic, or that starts through a program
the census does not read, the census records a line naming PYTEST_CURRENT_TEST,
the argv, the cwd and the remedy, then raises ChildPytestCensusError,
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
8. A chain through a program the [wrapper] family does not name as a wrapper
   (`timeout 60 chrt -f 1 pytest`, `sudo taskset 1 pytest`): the census takes
   that program for the command and judges no further. The list holds names
   only, so a name missing from it can shorten a chain but cannot hide an
   option's value.
9. Shell text the census does not expand or track: variables (`$PYTEST`) and
   what a substitution's output becomes (`$(echo pytest) -q`), `export`,
   aliases, `eval`, `env -S`, a script fed to a shell's stdin (`bash <<EOF`,
   `bash -s`), a command substitution inside arithmetic (`$(( $(pytest) ))`), and
   a `case` pattern's `)` inside a `$( )` (it ends the substitution early). An
   operator written alone inside quotes (`";"`) is read as an operator.
10. pytest run from a file the census cannot name: a renamed or wrapper copy of
    the console script, a zipapp (`python pytest.pyz`), or a module other than
    `pytest` and `pytest.__main__` that starts a session (`python -m runpy`, a
    plugin's own entry point). The census judges a launch by its words, not by
    what the named file does, nor by what an environment file sets.
11. An interpreter not named above: `jython` (which cannot run current
    pytest), a custom-named or embedded interpreter, or one renamed on disk.
12. Plugin loading the census does not read: a config file's `addopts` (the
    child's own pytest.ini, pyproject.toml, tox.ini or setup.cfg; only an
    `-o addopts=` override is read), an `-o addopts=` inside that value,
    `pytest_plugins` in a conftest.py or test module, a plugin that imports
    pytest_randomly itself, and a pytest whose loading order differs from 9.2.
    A lock that pins another pytest or pytest-randomly than REPLAYED_VERSIONS
    fails test_the_lock_pins_the_versions_the_census_replays, and the CI
    environment binding fails a CI run whose installed versions differ from
    the lock, so this drift is left only to a local run on another pytest.
13. A command a program runs from text the census reads as a plain word: sed's
    `e` command and awk's `system()` inside their script, a git alias
    (`git -c alias.t='!pytest' t`), and an option of a read or list program
    that runs one (`sort --compress-program`, `rg --pre`).
"""
import os
import re
import shlex
import sys

import pytest

REMEDY = 'add "-p", "no:randomly" to the child argv'
# The major.minor versions whose plugin loading the census replays.
REPLAYED_VERSIONS = {"pytest": "9.2", "pytest-randomly": "5.1"}
WRAPPER_REMEDY = ("launch pytest directly, as in sys.executable -m pytest -p no:randomly, "
                  "or teach the census this program")
WORKEROUTPUT_KEY = "child_pytest_census_misses"
AUTOLOAD_ENV = "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
ADDOPTS_ENV = "PYTEST_ADDOPTS"
PLUGINS_ENV = "PYTEST_PLUGINS"
NO_AUTOLOAD_FLAG = "--disable-plugin-autoload"
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
# The names under which pytest registers pytest-randomly's hooks: its entry
# point, its module, and the module imported again as `pytest_randomly.__init__`
# (pytest-randomly 5.1.0 has no other module).
_RANDOMLY_NAMES = frozenset({"randomly", "pytest_randomly", "pytest_randomly.__init__"})
# Characters a shell reads as operators; a run of them is one lexer token.
_OPERATOR_CHARS = "();<>|&\n"
# A file descriptor number or `{name}` written against a redirection.
_FD_PREFIX = re.compile(r"(?:\d+|\{[A-Za-z_][A-Za-z0-9_]*\})(?=[<>])")
# The operators a run of operator characters is made of, longest first.
_OPERATOR_PIECE = re.compile(r"&>>?|[<>]&|>>|>\||<<<|<<|<>|[<>]|\|&|&&|\|\||[;&|()\n]")
# Words a shell reads before a command without running them.
_RESERVED = frozenset({"{", "}", "!", "if", "then", "elif", "else", "fi",
                       "do", "done", "while", "until"})
# Programs that run none of the words after them: they print, look up, read,
# list or ignore them. Names only; a missing name refuses a mention loudly.
_RUNS_NOTHING = frozenset({
    "echo", "printf", "which", "true", "false", ":", "grep", "egrep", "fgrep", "rg",
    "ag", "ls", "cat", "head", "tail", "wc", "sed", "awk", "cut", "sort", "uniq",
    "diff", "file", "stat", "test", "[", "chmod", "chown", "touch", "mkdir", "rm", "cp",
    "mv", "ln",
})
# find's actions that run the command after them.
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
# Process wrappers and tool runners whose command may itself be one of them: the
# command search runs again from each, so a chain of them is followed to the
# program it starts. Names only; a missing name shortens a chain.
_CHAINED = frozenset({"env", "nice", "nohup", "sudo", "timeout", "time", "stdbuf",
                      "xvfb-run", "exec", "uv", "uvx", "pipx", "poetry", "pdm", "pipenv",
                      "hatch", "rye", "pixi", "conda", "micromamba", "find"})
# Dispatchers whose first plain word is the program they run (`git grep` runs
# git-grep), so that word is in program position for the [mention] stop.
_DISPATCHERS = frozenset({"git"})
# Subcommand verbs a program names before the command it runs (`uv run`,
# `uv tool run`, `poetry run`, `hatch -e test run`).
_RUN_VERBS = frozenset({"run", "tool"})
# A number or duration a program takes before its command (`timeout 1.5m`,
# `nice -n +5`, `timeout inf`, `timeout 0x5`).
_NUMBER = re.compile(r"[+-]?(?:\.?[0-9][0-9a-z.+-]*|inf(?:inity)?)\Z", re.IGNORECASE)
# A hatch matrix selector before its command: `hatch run +py=3.12 test:pytest`,
# or an exclusion, `-py=3.11`.
_SELECTOR = re.compile(r"[+-][A-Za-z_][A-Za-z0-9_.-]*=")
# hatch names an environment before its command: `hatch run test:pytest`.
_ENVIRONMENT_PREFIX = re.compile(r"[A-Za-z0-9_.-]+:")
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


PLUGIN_NAME = "child-pytest-census"


class Plugin:
    """The census's session hooks: each xdist worker's misses, and the report
    at session finish."""

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        collect(node)

    def pytest_sessionfinish(self, session):
        finish(session)


def register(config):
    """Install the census and register its Plugin, once per session."""
    if config.pluginmanager.has_plugin(PLUGIN_NAME):
        return
    install()
    config.pluginmanager.register(Plugin(), PLUGIN_NAME)


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
    environment = (dict(os.environ) if env is None
                   else {os.fsdecode(k): os.fsdecode(v) for k, v in env.items()})
    if isinstance(argv, (str, bytes)):
        words = _lex(os.fsdecode(argv))
        commands = _shell_commands(os.fsdecode(argv), environment, 0)
    else:
        words = [os.fsdecode(a) for a in (argv or [])]
        commands = _commands(words, environment, False, 0)
    for part, program, part_env in commands:
        wrapper = _wrapper(part, program, 0)
        if wrapper is not None:
            return ("%s: pytest child [%s] (cwd %s) starts through %s, which the census "
                    "does not read; %s" % (_current_test(), _shown(words), _where(cwd),
                                           wrapper, WRAPPER_REMEDY))
        start = _pytest_args(part, program)
        if start is not None and not _deterministic(part[start:], part_env):
            return "%s: pytest child [%s] (cwd %s) does not block pytest-randomly; %s" % (
                _current_test(), _shown(words), _where(cwd), REMEDY)
    return None


def _where(cwd):
    return os.fsdecode(cwd) if cwd is not None else os.getcwd()


def _lex(text):
    """The words of shell text, with each run of operator characters its own word.
    `#` is text here: _scan has already removed the comments a shell would."""
    text = text.replace("\\\n", " ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_OPERATOR_CHARS)
    lexer.commenters = ""
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # an unclosed quote: fall back to a plain split
        return re.findall(r"[();<>|&\n]+|[^\s();<>|&]+", text)


def _closing(text, i, close):
    """Index after the `close` that ends a construct whose body starts at `i`,
    skipping quotes, escapes and nested parentheses; len(text) when unclosed."""
    depth = 1
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char in "'\"" and close == ")":
            end = text.find(char, i + 1) if char == "'" else _double_end(text, i + 1)
            i = len(text) if end < 0 else end + 1
            continue
        if close == ")" and char == "(":
            depth += 1
        elif char == close[0] and text.startswith(close, i):
            depth -= 1
            if depth == 0:
                return i + len(close)
        i += 1
    return len(text)


def _arithmetic_end(text, i):
    """Index after the `))` that ends arithmetic opened by a `((` just before
    `i`, or None when the inner paren closes on anything but `)`, as in
    `((cmd) )`, which bash runs as two subshells. len(text) when unclosed."""
    depth = 2
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char in "'\"":
            end = text.find(char, i + 1) if char == "'" else _double_end(text, i + 1)
            i = len(text) if end < 0 else end + 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 1:
                return i + 2 if text.startswith("))", i) else None
        i += 1
    return len(text)


def _double_end(text, i):
    """Index of the `"` that closes a double-quoted string whose body starts at
    `i`, or -1."""
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == '"':
            return i
        elif text.startswith("$(", i) or text[i] == "`":
            i = _closing(text, i + 2, ")") if text[i] == "$" else _closing(text, i + 1, "`")
        else:
            i += 1
    return -1


def _substitution(text, i, bodies):
    """When a command or process substitution, or arithmetic, starts at `i`,
    record its command text in `bodies` and return (the word that stands for
    it, the index after it); else None."""
    if text.startswith("$((", i) and (end := _arithmetic_end(text, i + 3)) is not None:
        return "0", end  # arithmetic runs no command
    for opener, stands_for in (("$(", "_"), ("<(", "/dev/fd/63"), (">(", "/dev/fd/63")):
        if text.startswith(opener, i):
            end = _closing(text, i + 2, ")")
            bodies.append(text[i + 2:end - 1])
            return stands_for, end
    if text[i] == "`":
        end = _closing(text, i + 1, "`")
        bodies.append(re.sub(r"\\([`$\\])", r"\1", text[i + 1:end - 1]))
        return "_", end
    return None


def _scan(text):
    """Shell text as a shell reads it, before its words are split: (the text with
    comments, heredoc bodies and line continuations removed and each
    substitution replaced by a word, the command texts of its substitutions)."""
    out, bodies, heredocs = [], [], []
    i = 0
    while i < len(text):
        char = text[i]
        at_word_start = not out or out[-1][-1:] in " \t\r" + _OPERATOR_CHARS
        if text.startswith("\\\n", i):
            i += 2
        elif at_word_start and (fd := _FD_PREFIX.match(text, i)):
            i = fd.end()  # `2>x`, `{fd}>x`: the number or name belongs to the redirection
        elif char == "\\":
            out.append(text[i:i + 2])
            i += 2
        elif char == "'":
            end = text.find("'", i + 1)
            end = len(text) if end < 0 else end + 1
            out.append(text[i:end])
            i = end
        elif char == '"':
            i = _scan_double(text, i, out, bodies)
        elif text.startswith("((", i) and at_word_start:
            end = _arithmetic_end(text, i + 2)
            if end is None:  # `((cmd) )`: two subshells, not arithmetic
                out.append("(")
                i += 1
            else:  # an arithmetic command
                out.append("true")
                i = end
        elif (found := _substitution(text, i, bodies)) is not None:
            out.append(found[0])
            i = found[1]
        elif char == "#" and at_word_start:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif text.startswith("<<<", i):
            out.append("<<<")
            i += 3
        elif text.startswith("<<", i):
            i = _heredoc_operator(text, i + 2, heredocs)
            out.append("<< _")
        elif char == "\n" and heredocs:
            out.append("\n")
            i = _heredoc_bodies(text, i + 1, heredocs, bodies)
            heredocs.clear()
        else:
            out.append(char)
            i += 1
    return "".join(out), bodies


def _scan_double(text, i, out, bodies):
    """Copy the double-quoted string at `i` to `out`, replacing its substitutions;
    return the index after it."""
    out.append('"')
    i += 1
    while i < len(text) and text[i] != '"':
        if text.startswith("\\\n", i):
            i += 2
        elif text[i] == "\\":
            out.append(text[i:i + 2])
            i += 2
        elif text[i] in "$`" and (found := _substitution(text, i, bodies)) is not None:
            out.append(found[0])
            i = found[1]
        else:
            out.append(text[i])
            i += 1
    out.append('"')
    return i + 1


def _heredoc_operator(text, i, heredocs):
    """Read a heredoc's `-` and delimiter word from `i`, queue the heredoc and
    return the index after the word."""
    strip_tabs = text.startswith("-", i)
    i += strip_tabs
    while i < len(text) and text[i] in " \t":
        i += 1
    start = i
    while i < len(text) and text[i] not in " \t\r" + _OPERATOR_CHARS:
        if text[i] in "'\"":
            end = text.find(text[i], i + 1)
            i = len(text) if end < 0 else end + 1
        else:
            i += 2 if text[i] == "\\" else 1
    word = text[start:i]
    quoted = any(char in word for char in "'\"\\")
    heredocs.append((re.sub(r"""\\(.)|['"]""", r"\1", word), strip_tabs, quoted))
    return i


def _heredoc_bodies(text, i, heredocs, bodies):
    """Skip the body of each queued heredoc, which starts at `i`, recording the
    substitutions of an unquoted one; return the index after the last."""
    for delimiter, strip_tabs, quoted in heredocs:
        lines = []
        while i < len(text):
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            line, i = text[i:end], end + 1
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                break
            lines.append(line)
        if not quoted:
            _heredoc_substitutions("\n".join(lines), bodies)
    return min(i, len(text))


def _heredoc_substitutions(body, bodies):
    """Record the command texts of the substitutions an unquoted heredoc body
    expands: quotes are text there, and a backslash escapes `$`, a backtick or
    itself."""
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
        elif body.startswith(("<(", ">("), i):
            i += 1
        elif (found := _substitution(body, i, bodies)) is not None:
            i = found[1]
        else:
            i += 1


def _shell_commands(text, env, depth):
    """Yield (command words, program index, environment) for every command in
    shell text and in each of its substitutions."""
    main, bodies = _scan(text)
    yield from _commands(_lex(main), env, True, depth)
    if depth < _MAX_DEPTH:
        for body in bodies:
            yield from _shell_commands(body, env, depth + 1)


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
            for piece in _OPERATOR_PIECE.findall(word):
                if "<" in piece or ">" in piece:
                    skip = True
                else:
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
            yield from _shell_commands(text, part_env, depth + 1)
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
    builtins that run the command after them, and the environment the program
    runs with."""
    env = dict(env)
    i = 0
    while i < len(part):
        word = part[i]
        if _is_assignment(word):
            _assign(env, word)
            i += 1
            continue
        if word in _RESERVED or word == "builtin":
            i += 1
            continue
        if word == "function":
            i += 2  # `function NAME`, then its body
            continue
        if word == "coproc":
            # A NAME is allowed only before a compound command.
            i += 2 if i + 2 < len(part) and part[i + 2] in _RESERVED else 1
            continue
        if word == "command":
            i += 1
            while i < len(part) and part[i].startswith("-") and part[i] != "-":
                if "v" in part[i] or "V" in part[i]:
                    return len(part), env  # `command -v` and `-V` describe, never run
                i += 1
            continue
        return i, env
    return i, env


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


def _launch(part, at):
    """Index of the first pytest argument when the command starting at `at` runs
    pytest by the [program], [coverage], [module] or [script] family, else None."""
    if at >= len(part):
        return None
    word = part[at]
    if _is_pytest_script(word):
        return at + 1
    if _COVERAGE_PROGRAM.match(_command_name(word)):
        return _coverage_run(part, at + 1)
    if not _is_interpreter(word):
        return None
    module, after = _module(part, at + 1)
    if module in _PYTEST_MODULES:
        return after
    if module == "coverage":
        return _coverage_run(part, after)
    if module is None and after < len(part) and _is_pytest_script(part[after]):
        return after + 1  # the interpreter runs pytest's script file
    return None


def _pytest_args(part, program):
    """Index of the first pytest argument when `part` runs pytest, else None."""
    start = _launch(part, program)
    if start is not None:
        return start
    # [mention]: every word naming an interpreter is read for its -m forms, up to
    # a program in program position that runs none of its words.
    stop = _command_position(part, program)
    for at, word in enumerate(part):
        if at == stop and _command_name(word) in _RUNS_NOTHING:
            break
        if not _is_interpreter(word):
            continue
        module, after = _module(part, at + 1)
        if module in _PYTEST_MODULES:
            return after
        if module == "coverage":
            found = _coverage_run(part, after)
            if found is not None:
                return found
    return None


def _command_position(part, program):
    """Index of the word in program position: the command a listed wrapper or
    dispatcher runs, followed along a chain, else the program word itself."""
    if program < len(part) and _command_name(part[program]) in _CHAINED | _DISPATCHERS:
        found = _command_search(part, program, 0)
        if found is not None:
            return found
    return program


def _modelled(word):
    """True for a program the families read themselves."""
    name = _command_name(word)
    return (_is_pytest_script(word) or _is_interpreter(word) or name in _RUNS_NOTHING
            or bool(_COVERAGE_PROGRAM.match(name)))


def _wrapper(part, program, depth):
    """The program word when the program is none the census reads and a word
    that may be the command it runs starts pytest, else None."""
    if program >= len(part) or _modelled(part[program]):
        return None
    return part[program] if _command_starts_pytest(part, program, depth) else None


def _command_starts_pytest(part, program, depth):
    """True when a word that may be the command of the program at `program`
    starts pytest."""
    return _command_search(part, program, depth) is True


def _command_search(part, program, depth):
    """True when a word that may be the command of the program at `program`
    starts pytest, else the index of the word the search took for the command,
    or None when it found none. That word is the first past options,
    NAME=VALUE words, numbers and the verbs in _RUN_VERBS; while it follows an
    option it may be that option's value, so the next one is tried too. A word
    in _CHAINED is searched again as a program of its own. find's commands are
    the words after its _FIND_EXEC actions."""
    if _command_name(part[program]) == "find":
        return (depth < _MAX_DEPTH and any(
            _command_search(part, at, depth + 1) is True
            for at in range(program + 1, len(part)) if part[at] in _FIND_EXEC)) or None
    after_option = False
    for at in range(program + 1, len(part)):
        word = part[at]
        if _is_assignment(word) or _NUMBER.match(word) or _SELECTOR.match(word):
            after_option = False
        elif word.startswith("-"):
            after_option = True
        elif _starts_pytest(part, at, depth):
            return True
        elif _command_name(word) in _CHAINED and depth < _MAX_DEPTH:
            return _command_search(part, at, depth + 1)
        elif after_option or word in _RUN_VERBS:
            after_option = False
        else:
            return at
    return None


def _starts_pytest(part, at, depth):
    """True when the command starting at `at` runs pytest by a family above, or
    is a shell whose -c text runs pytest by one or through a program the census
    does not read."""
    if _launch(part, at) is not None:
        return True
    text = _shell_command(part, at)
    return text is not None and depth < _MAX_DEPTH and any(
        _launch(inner, start) is not None or _wrapper(inner, start, depth + 1) is not None
        for inner, start, _ in _shell_commands(text, {}, depth + 1))


def _split(text):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _plugin_args(words):
    """Each `-p` value in `words`, stripped, in order, as pytest's preparse reads
    them: a bare `--` does not end them."""
    i = 0
    while i < len(words):
        word = words[i]
        if word == "-p":
            if i + 1 < len(words):
                yield words[i + 1].strip()
            i += 2
        else:
            if word.startswith("-p") and len(word) > 2:
                yield word[2:].strip()
            i += 1


def _options(words):
    """The words argparse reads as options: those before the first bare `--`."""
    return words[:words.index("--")] if "--" in words else words


def _override_addopts(words):
    """The words of the last `-o addopts=VALUE` override among the options,
    which pytest places in front of PYTEST_ADDOPTS and the argv."""
    words = _options(words)
    value = None
    i = 0
    while i < len(words):
        word = words[i]
        if word in ("-o", "--override-ini") and i + 1 < len(words):
            item, i = words[i + 1], i + 2
        elif word.startswith("--override-ini="):
            item, i = word.split("=", 1)[1], i + 1
        elif word.startswith("-o") and len(word) > 2:
            item, i = word[2:], i + 1
        else:
            i += 1
            continue
        key, separator, rest = item.partition("=")
        if separator and key == "addopts":
            value = rest
    return _split(value) if value is not None else []


def _plugin_names(name):
    """The names `-p NAME` and `-p no:NAME` act on: NAME, and pytest_NAME unless
    NAME already starts with pytest_."""
    return {name} if name.startswith("pytest_") else {name, "pytest_" + name}


def _deterministic(args, env):
    """True when pytest run with `args` and `env` leaves pytest-randomly unloaded."""
    words = _split(env.get(ADDOPTS_ENV, "")) + list(args)
    words = _override_addopts(words) + words
    blocked, loaded = set(), set()
    for value in _plugin_args(words):
        if value.startswith("no:"):
            names = _plugin_names(value[3:])
            blocked |= names
            loaded -= names
        elif value in _RANDOMLY_NAMES:
            # pytest unblocks the name before loading it; only the load can
            # change the verdict, since a later block blocks and unloads again.
            loaded.add(value)
    if (not env.get(AUTOLOAD_ENV) and NO_AUTOLOAD_FLAG not in _options(words)
            and "randomly" not in blocked):
        loaded.add("randomly")
    for name in (env.get(PLUGINS_ENV) or "").split(","):
        if name in _RANDOMLY_NAMES and name not in blocked:
            loaded.add(name)
    return not loaded


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
