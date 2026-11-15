"""
Location: pact-plugin/tests/test_child_pytest_census.py
Summary: Pins the child-pytest census: which launches it refuses and accepts,
         that it is live in this session, that finish() fails the session, and
         that under real xdist a miss a test swallows still fails the run.
Used by: the suite; the census itself is tests/child_pytest_census.py, wired in
         tests/conftest.py.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import child_pytest_census as census

PY = sys.executable
_CONFTEST = Path(__file__).resolve().with_name("conftest.py")
_BLOCK = ["-p", "no:randomly"]


@pytest.fixture
def census_state():
    """Restore the census's recorded lines, so a deliberate miss here does not
    fail the session that runs this module."""
    saved = {key: list(census._state[key]) for key in ("misses", "forwarded")}
    try:
        yield census._state
    finally:
        for key, value in saved.items():
            census._state[key][:] = value


@pytest.mark.parametrize("argv", [
    [PY, "-m", "pytest", "-q"],
    "python -m pytest -q",
    ["/bin/sh", "-c", "python3 -m pytest -q"],
    ["bash", "-c", "cd x && python -m pytest tests"],
    [PY, "-mpytest"],
    [PY, "-Im", "pytest"],
    [PY, "-W", "ignore", "-m", "pytest"],
    ["pytest", "-q"],
    ["/opt/venv/bin/py.test"],
    ["env", "A=1", PY, "-m", "pytest"],
    ["A=1", "python3", "-m", "pytest"],
    [Path(PY), b"-m", "pytest"],
])
def test_an_unblocked_pytest_child_is_refused(argv):
    line = census._miss(argv, "/rig", {})
    assert line is not None, argv
    assert line.endswith(census.REMEDY) and "(cwd /rig)" in line, line


@pytest.mark.parametrize("extra", [
    ["-p", "no:randomly"],
    ["-pno:randomly"],
    # The last randomly flag wins, as in pytest's own -p handling.
    ["-p", "randomly", "-p", "no:randomly"],
    # pytest reads every -p, a bare `--` included, so a block after it counts.
    ["--", "-p", "no:randomly"],
    ["-p", "randomly", "--", "-p", "no:randomly"],
])
def test_each_blocking_flag_is_accepted(extra):
    assert census._miss([PY, "-m", "pytest", "-q", *extra], None, {}) is None
    assert census._miss("pytest -q " + " ".join(extra), None, {}) is None


@pytest.mark.parametrize("extra", [
    ["-p", "randomly"],
    ["-prandomly"],
    # The last randomly flag wins, so a later load undoes an earlier block.
    ["-p", "no:randomly", "-p", "randomly"],
    ["-pno:randomly", "-prandomly"],
    # pytest reads every -p, a bare `--` included, so a load after it counts.
    ["-p", "no:randomly", "--", "-p", "randomly"],
    ["-pno:randomly", "--", "-prandomly"],
])
def test_a_child_that_loads_randomly_or_does_not_block_it_is_refused(extra):
    assert census._miss([PY, "-m", "pytest", "-q", *extra], None, {}) is not None, extra
    assert census._miss("pytest -q " + " ".join(extra), None, {}) is not None, extra


@pytest.mark.parametrize("extra", [
    ["-p", "randomly"],
    ["-prandomly"],
    ["-p", "pytest_randomly"],
    ["-ppytest_randomly"],
])
def test_an_explicit_load_is_refused_even_with_autoload_disabled(extra):
    env = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    assert census._miss([PY, "-m", "pytest", *extra], None, env) is not None, extra


# Every route by which pytest 9.2 loads or blocks pytest-randomly, with the
# verdict pytest itself gives: each row was run against pytest 9.2.0 and
# pytest-randomly 5.1.0, and the child either registered the plugin (False) or
# did not (True).
_D = "PYTEST_DISABLE_PLUGIN_AUTOLOAD"


@pytest.mark.parametrize("args, env, deterministic", [
    ([], {}, False),
    ([], {_D: "1"}, True),
    (["--disable-plugin-autoload"], {}, True),
    ([], {"PYTEST_ADDOPTS": "--disable-plugin-autoload"}, True),
    (["--", "--disable-plugin-autoload"], {}, False),
    (["--disable-plugin-autoload", "-p", "randomly"], {}, False),
    (["-p", "no:randomly", "-p", "pytest_randomly"], {}, False),
    (["-p", "pytest_randomly", "-p", "no:randomly"], {}, True),
    (["-p", "no:randomly", "-p", "pytest_randomly.__init__"], {}, False),
    (["-p", "pytest_randomly.__init__", "-p", "no:randomly"], {}, False),
    (["-p", "pytest_randomly.__init__", "-p", "no:pytest_randomly.__init__",
      "-p", "no:randomly"], {}, True),
    (["-p", "no:pytest_randomly"], {}, False),
    (["-p", "no:pytest_randomly.__init__"], {}, False),
    (["-p", "no:pytest_randomly", "-p", "no:randomly"], {}, True),
    ([], {_D: "1", "PYTEST_PLUGINS": "pytest_randomly"}, False),
    ([], {_D: "1", "PYTEST_PLUGINS": "randomly"}, False),
    ([], {_D: "1", "PYTEST_PLUGINS": "pytest_randomly.__init__"}, False),
    ([], {_D: "1", "PYTEST_PLUGINS": "pytester,pytest_randomly"}, False),
    (["-p", "no:randomly"], {"PYTEST_PLUGINS": "pytest_randomly"}, True),
    (["-p", "no:randomly"], {"PYTEST_PLUGINS": "randomly"}, True),
    (["-p", "no:randomly"], {"PYTEST_PLUGINS": "pytest_randomly.__init__"}, False),
    (["-p", " no:randomly"], {}, True),
    (["-p no:randomly"], {}, True),
    (["-p no:randomly", "-p randomly "], {}, False),
    (["-p", "no:randomly", "-p", "randomly "], {}, False),
    (["-p", "no:randomly"], {"PYTEST_ADDOPTS": "-p randomly"}, True),
    (["-p", "randomly"], {"PYTEST_ADDOPTS": "-p no:randomly"}, False),
    (["-o", "addopts=-p no:randomly"], {}, True),
    (["-oaddopts=-p no:randomly"], {}, True),
    (["--override-ini", "addopts=-p no:randomly"], {}, True),
    (["--override-ini=addopts=-p no:randomly"], {}, True),
    (["-o", "addopts=-p no:randomly", "-o", "addopts=-q"], {}, False),
    (["-p", "no:randomly", "-o", "addopts=-p randomly"], {}, True),
    (["-o", "addopts=-p randomly"], {"PYTEST_ADDOPTS": "-p no:randomly"}, True),
    ([], {"PYTEST_ADDOPTS": "-o addopts=--disable-plugin-autoload"}, True),
    (["--", "-o", "addopts=-p no:randomly"], {}, False),
    (["-o", "other=-p no:randomly"], {}, False),
])
def test_each_route_pytest_loads_or_blocks_the_plugin_by(args, env, deterministic):
    assert census._deterministic(args, env) is deterministic, (args, env)
    verdict = census._miss([PY, "-m", "pytest", *args], None, env)
    assert (verdict is None) is deterministic, (args, env)


def test_an_explicit_env_disabling_autoload_is_accepted():
    argv = [PY, "-m", "pytest"]
    assert census._miss(argv, None, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}) is None
    assert census._miss(argv, None, {b"PYTEST_DISABLE_PLUGIN_AUTOLOAD": b"1"}) is None
    assert census._miss(argv, None, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": ""}) is not None


def test_without_an_explicit_env_the_inherited_env_decides(monkeypatch):
    argv = [PY, "-m", "pytest"]
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert census._miss(argv, None, None) is None
    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
    assert census._miss(argv, None, None) is not None
    # An explicit env replaces the inherited one, even when the parent disables it.
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert census._miss(argv, None, {}) is not None


@pytest.mark.parametrize("argv,env", [
    ([PY, "-m", "pytest"], {"PYTEST_ADDOPTS": "-p no:randomly"}),
    ([PY, "-m", "pytest"], {"PYTEST_ADDOPTS": "-q -pno:randomly"}),
    (["env", "PYTEST_ADDOPTS=-p no:randomly", "pytest"], {}),
    (["env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "pytest"], {}),
    (["PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", PY, "-m", "pytest"], {}),
    (["sh", "-c", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest"], {}),
    (["sh", "-c", "PYTEST_ADDOPTS='-p no:randomly' python3 -m pytest -q"], {}),
    # An assignment before a shell reaches the commands inside it.
    (["env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "bash", "-c", "cd x && pytest"], {}),
    # `env -i` empties the environment, then its own assignment applies.
    (["env", "-i", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "pytest"], {}),
    # A tool runner's --env option sets the variable for the command.
    (["pdm", "run", "--env", "PYTEST_ADDOPTS=-p no:randomly", "pytest"], {}),
    (["pdm", "run", "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "pytest"], {}),
    (["micromamba", "run", "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "pytest"], {}),
    (["micromamba", "run", "--env", "PYTEST_ADDOPTS=-p no:randomly", "pytest"], {}),
    (["micromamba", "run", "--env=PYTEST_ADDOPTS=-p no:randomly", "pytest"], {}),
    # micromamba's clean environment keeps a name `-e` gives, and applies an
    # assignment whichever side of --clean-env it is written on.
    (["micromamba", "run", "--clean-env", "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "pytest"],
     {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}),
    (["micromamba", "run", "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "--clean-env", "pytest"], {}),
])
def test_a_child_blocked_through_its_environment_is_accepted(argv, env):
    assert census._miss(argv, None, env) is None, argv


@pytest.mark.parametrize("command, refused", [
    ("exec -c pytest", True),
    ("exec -cl pytest", True),
    ("exec -lc pytest", True),
    ("exec -ca name pytest", True),
    ("exec -c -a name pytest", True),
    ("exec pytest", False),
    ("exec -l pytest", False),
    ("exec -a name pytest", False),
    ("exec -aname pytest", False),
    ("exec -ac pytest", False),
    ("exec -- pytest", False),
])
def test_exec_clears_the_environment_only_with_its_c_option(command, refused):
    """bash's `exec -c` runs the command in an empty environment, so the
    parent's PYTEST_ADDOPTS block does not reach it; `-a` takes a value, and
    in `-ac` that value is `c`."""
    env = {"PYTEST_ADDOPTS": "-p no:randomly"}
    assert (census._miss(["bash", "-c", command], None, env) is not None) is refused, command


@pytest.mark.parametrize("argv,env", [
    # The command line comes after PYTEST_ADDOPTS, so its load wins.
    ([PY, "-m", "pytest", "-p", "randomly"], {"PYTEST_ADDOPTS": "-p no:randomly"}),
    # An addopts load wins over a disabled autoload.
    ([PY, "-m", "pytest"], {"PYTEST_ADDOPTS": "-p randomly",
                            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}),
    ([PY, "-m", "pytest"], {"PYTEST_ADDOPTS": "-p no:randomly -p randomly",
                            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}),
    # These drop the variable the parent set.
    (["env", "-i", "pytest"], None),
    (["env", "-", "pytest"], None),
    (["env", "-u", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "pytest"], None),
    (["env", "--unset=PYTEST_DISABLE_PLUGIN_AUTOLOAD", "pytest"], None),
    (["env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=", "pytest"], None),
    (["sh", "-c", "PYTEST_DISABLE_PLUGIN_AUTOLOAD= pytest"], None),
    # sudo resets the environment unless asked to keep it.
    (["sudo", "pytest"], None),
    # A tool runner's --env option can empty the variable, and --clean-env
    # drops every name micromamba's `-e` does not keep.
    (["pdm", "run", "--env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=", "pytest"], None),
    (["pdm", "run", "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=", "pytest"], None),
    (["micromamba", "run", "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=", "pytest"], None),
    (["micromamba", "run", "--clean-env", "pytest"], None),
    (["micromamba", "run", "--clean-env", "-e", "HOME", "pytest"], None),
    (["pixi", "run", "--clean-env", "pytest"], None),
])
def test_a_child_whose_environment_does_not_block_is_refused(argv, env, monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert census._miss(argv, None, env) is not None, argv


def test_sudo_preserving_the_environment_keeps_the_parents_block(monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert census._miss(["sudo", "-E", "pytest"], None, None) is None
    assert census._miss(["sudo", "--preserve-env", "pytest"], None, None) is None
    assert census._miss(
        ["sudo", "--preserve-env=PYTEST_DISABLE_PLUGIN_AUTOLOAD", "pytest"], None, None) is None


@pytest.mark.parametrize("argv", [
    ["git", "status"],
    ["git", "commit", "-m", "pytest"],
    [PY, "-c", "print(1)"],
    [PY, "script.py", "-m", "pytest"],
    [PY, "-m", "pip", "install", "pytest"],
    ["bash", "scripts/verify-gate-tree.sh", "repo", "capture", "-q"],
])
def test_a_launch_that_is_not_a_pytest_child_is_ignored(argv):
    assert census._miss(argv, None, {}) is None


@pytest.mark.parametrize("argv", [
    ["timeout", "60", PY, "-m", "pytest"],
    ["timeout", "-s", "KILL", "1.5m", PY, "-m", "pytest"],
    ["nice", "-n", "10", PY, "-m", "pytest"],
    ["nohup", PY, "-m", "pytest"],
    ["sudo", "-u", "bob", PY, "-m", "pytest"],
    ["stdbuf", "-oL", PY, "-m", "pytest"],
    ["xvfb-run", "-a", PY, "-m", "pytest"],
    ["uv", "run", "python", "-m", "pytest"],
    ["poetry", "run", "python", "-m", "pytest"],
    ["pypy3", "-m", "pytest"],
    ["/opt/pypy/bin/pypy", "-I", "-m", "pytest"],
    [PY, "--check-hash-based-pycs", "always", "-m", "pytest"],
    [PY, "-X", "dev", "--check-hash-based-pycs", "never", "-Wd", "-m", "pytest"],
    # A long option CPython may add later is stepped over, not taken as a script.
    [PY, "--a-future-flag", "-m", "pytest"],
    # An unlisted wrapper still exposes the interpreter form.
    ["hatch", "run", "python", "-m", "pytest"],
    ["chrt", "-f", "10", PY, "-m", "pytest"],
    ["uv", "run", "pytest"],
    ["uv", "run", "--with", "x", "pytest", "-q"],
    ["poetry", "run", "pytest"],
    ["timeout", "60", "pytest"],
    ["nice", "py.test"],
    ["env", "-u", "HOME", "pytest"],
    ["timeout", "60", "nice", "-n", "5", "pytest"],
    # -W or -X ending a bundle takes the next word as its value.
    [PY, "-bW", "ignore", "-m", "pytest"],
    [PY, "-sX", "dev", "-m", "pytest"],
    # coverage runs pytest as a module, directly or under an interpreter.
    ["coverage", "run", "-m", "pytest"],
    ["coverage", "run", "--branch", "--source", "hooks", "-m", "pytest", "-q"],
    ["coverage", "run", "--module", "pytest"],
    [PY, "-m", "coverage", "run", "-m", "pytest"],
    ["timeout", "60", "python3", "-m", "coverage", "run", "--parallel-mode", "-m", "pytest"],
])
def test_a_wrapped_or_unusual_interpreter_launch_is_refused(argv):
    assert census._miss(argv, None, {}) is not None, argv


@pytest.mark.parametrize("argv", [
    ["bash", "-lc", "python -m pytest"],
    ["bash", "-ec", "pytest -q"],
    ["sh", "-e", "-c", "pytest"],
    ["bash", "--noprofile", "--norc", "-c", "pytest"],
    ["bash", "-o", "pipefail", "-c", "pytest | tee log"],
    ["zsh", "-fc", "pytest"],
    # A shell behind a wrapper or an assignment.
    ["env", "bash", "-c", "pytest"],
    ["timeout", "60", "bash", "-c", "pytest"],
    ["nohup", "sh", "-c", "python3 -m pytest"],
    ["A=1", "sh", "-c", "pytest"],
    ["/usr/bin/env", "-i", "/bin/bash", "-c", "pytest"],
    # Separators inside the command text.
    ["bash", "-c", "cd x\npytest"],
    ["bash", "-c", "set -e\ncd x\n\npytest tests"],
    ["bash", "-c", "cd x; pytest"],
    ["bash", "-c", "cd x;pytest"],
    ["bash", "-c", "cd x&&pytest"],
    ["bash", "-c", "false||pytest"],
    ["bash", "-c", "pytest|tee log"],
    ["bash", "-c", "true&pytest"],
    ["bash", "-c", "(pytest)"],
    ["bash", "-c", "(cd x&&pytest -q)"],
    ["bash", "-c", "{ pytest; }"],
    ["bash", "-c", "if true; then pytest; fi"],
    ["bash", "-c", "for i in 1 2; do pytest; done"],
    ["bash", "-c", "exec pytest"],
    ["bash", "-c", "time pytest"],
    # A redirection target is not a separator; the `;` after it is.
    ["bash", "-c", "echo start >log;pytest"],
    # A shell inside a shell.
    ["bash", "-c", "sh -c 'pytest'"],
    # A shell command given as text.
    "bash -lc 'cd x; pytest'",
])
def test_a_pytest_child_inside_a_shell_command_is_refused(argv):
    assert census._miss(argv, None, {}) is not None, argv


@pytest.mark.parametrize("argv", [
    ["bash", "-c", "cd x\npytest -p no:randomly"],
    ["bash", "-lc", "cd x; python -m pytest -p no:randomly"],
    ["timeout", "60", "bash", "-c", "(cd x && pytest -pno:randomly) > log 2>&1"],
    # A redirection does not end the command it belongs to.
    ["bash", "-c", "pytest > log -p no:randomly"],
    # A continued line is one command.
    ["bash", "-c", "pytest \\\n  -p no:randomly"],
    # A command that is only assignments, or only a wrapper, has no program word.
    ["bash", "-c", "SJ=/x/y.py; A=1\npytest -p no:randomly"],
    ["bash", "-c", "nohup; uv run -p python3; pytest -pno:randomly"],
])
def test_a_blocked_pytest_child_inside_a_shell_command_is_accepted(argv):
    assert census._miss(argv, None, {}) is None, argv


# A wrapper named by a path is the wrapper: its first word matches by basename.
@pytest.mark.parametrize("argv", [
    ["/usr/local/bin/uv", "run", "pytest", "-q"],
    ["/usr/bin/poetry", "run", "pytest"],
    ["/home/u/.local/bin/uv", "tool", "run", "pytest"],
    ["/opt/conda/bin/conda", "run", "-n", "x", "pytest"],
    ["/home/u/.local/bin/uv", "run", "python", "/venv/bin/pytest"],
    ["/home/u/.local/bin/uv", "run", "coverage", "run", "-m", "pytest"],
])
def test_a_wrapper_named_by_a_path_is_stepped_over(argv):
    assert census._miss(argv, None, {}) is not None, argv
    assert census._miss(argv + _BLOCK, None, {}) is None, argv


# Shell text as bash runs it. Each row was checked against bash 5.2 with a stub
# pytest on PATH: the refused rows start an unblocked pytest, the accepted rows
# do not.
@pytest.mark.parametrize("text", [
    # Process substitution runs its command.
    "cat <(pytest -q)",
    "tee >(pytest -q) < /dev/null",
    "diff <(python -m pytest --co -q) expected.txt",
    "cat < <(pytest -q)",
    # Command substitution runs its command, also inside double quotes.
    "echo `pytest -q`",
    'echo "$(pytest -q)"',
    'echo "`pytest -q`"',
    "echo $(echo $(pytest -q))",
    "echo `echo \\`pytest -q\\``",
    "x=$(pytest -q)",
    "echo ${X:-$(pytest -q)}",
    'cat <<< "$(pytest -q)"',
    # A backslash-newline joins the two lines' words.
    "py\\\ntest -q",
    # A here-string is not a heredoc: the next line is a command.
    "cat <<<x\npytest -q",
    # A # inside a word, after a backslash or inside quotes is text.
    "echo a#b; pytest -q",
    "echo \\#x; pytest -q",
    'echo "a # b"; pytest -q',
    "echo hi #c\npytest -q",
    # A heredoc's body starts at the next newline and ends at its delimiter.
    "cat <<EOF; pytest -q\nbody\nEOF",
    "cat<<EOF\nbody\nEOF\npytest -q",
    "cat <<-EOF\n\tbody\n\tEOF\npytest -q",
    "cat <<A <<B\na\nA\nb\nB\npytest -q",
    # An unquoted heredoc runs its body's substitutions.
    "cat <<EOF\n$(pytest -q)\nEOF",
    "cat <<EOF\n`pytest -q`\nEOF",
    # Arithmetic is not a heredoc.
    "((x=1<<2))\npytest -q",
    # A called function runs its body, in either form.
    "f() { pytest -q; }; f",
    "function f { pytest -q; }; f",
    # An fd number or {name} against a redirection is part of it, not a program.
    "2>/dev/null pytest -q",
    "2>&1 pytest -q",
    "10>log pytest -q",
    "2>>log pytest -q",
    "{fd}>log pytest -q",
    # Only at a word start: here `1` is part of the plugin name.
    "pytest -p no:randomly1>x",
    # An operator written against a redirection still separates.
    "true;>log pytest -q",
    "true;2>log pytest -q",
    "true|>log pytest -q",
    "true&&>log pytest -q",
    # command, builtin and coproc run the command after them.
    "command pytest -q",
    "command -- pytest -q",
    "builtin command pytest -q",
    "coproc pytest -q",
    "coproc P { pytest -q; }",
    # exec -a takes the next word only when the first `a` ends the option.
    "exec -aa pytest -q",
    "exec -a name pytest -q",
    "exec -aname pytest -q",
    # `((` and `$((` whose inner paren closes on anything but `)` are subshells.
    "((pytest -q) )",
    "((pytest -q); )",
    "((pytest -q)|cat)",
    "echo $((pytest -q) )",
])
def test_shell_text_that_runs_an_unblocked_pytest_is_refused(text):
    assert census._miss(["bash", "-c", text], None, {}) is not None, text


def test_shell_text_that_is_a_syntax_error_is_still_judged():
    # bash rejects this text and starts nothing; the census refuses it, as its
    # docstring says.
    assert census._miss(["bash", "-c", "echo \\$(pytest -q)"], None, {}) is not None
    assert census._miss(["bash", "-c", "((pytest -q)\n)"], None, {}) is not None


def test_another_shells_text_is_read_by_bash_rules():
    # dash 0.5.12 rejects `<(` as a syntax error and starts nothing; the census
    # reads every shell's text as bash does and refuses it, as its docstring says.
    assert census._miss(["dash", "-c", "cat <(pytest -q)"], None, {}) is not None


def test_a_function_body_is_judged_where_it_is_defined():
    # bash runs no pytest here, since f is never called; the census judges the
    # body anyway, as its docstring says.
    assert census._miss(["bash", "-c", "f() { pytest -q; }"], None, {}) is not None
    assert census._miss(["bash", "-c", "function f { pytest -q; }"], None, {}) is not None


@pytest.mark.parametrize("text", [
    "pytest --base-url=http://x/#frag -p no:randomly",
    "echo hi #; pytest -q",
    "cat <(pytest -q -p no:randomly)",
    "echo $(pytest -q -p no:randomly)",
    # Single quotes and an escape keep a substitution as text.
    "echo '$(pytest -q)'",
    "echo '`pytest -q`'",
    "echo \\`pytest -q\\`",
    'echo "<(pytest -q)"',
    "cat <<< 'pytest -q'",
    # A heredoc's body is data.
    "cat > run.sh <<'EOF2'\npython -m pytest\nEOF2",
    "cat <<EOF | cat\npytest body\nEOF",
    "cat <<EOF\n\tpytest\n\tEOF\nEOF\npytest -p no:randomly",
    "cat <<-EOF\n\tpytest\n\tEOF\npytest -p no:randomly",
    "cat <<A <<B\npytest a\nA\npytest b\nB\npytest -p no:randomly",
    # A quoted delimiter, however quoted, leaves the body unexpanded.
    "cat <<'EOF'\n$(pytest -q)\nEOF",
    'cat <<"EOF"\n$(pytest -q)\nEOF',
    "cat <<\\EOF\n$(pytest -q)\nEOF",
    'cat <<E"O"F\n$(pytest -q)\nEOF',
    # In an unquoted body a backslash escapes `$`, and `<(` is text.
    "cat <<EOF\n\\$(pytest -q)\nEOF",
    "cat <<EOF\n<(pytest -q)\nEOF",
    # An fd number is not an argument.
    "pytest -p no:randomly 1>x",
    # `&>` redirects both streams; it is not `&` then `>`.
    "true&>log pytest -q",
    # command -v and -V describe a command and run nothing.
    "command -v pytest",
    "command -V pytest",
    "command -pv pytest",
    "exec -ca name pytest -p no:randomly",
    # Arithmetic whose inner parens close early is still arithmetic.
    "((pytest = 1))",
    "(( (pytest) + (1) ))",
    "echo $(( (pytest) + (1) ))",
    "echo $((1+(pytest*3)))",
])
def test_shell_text_that_runs_no_unblocked_pytest_is_accepted(text):
    assert census._miss(["bash", "-c", text], None, {}) is None, text


# Blind spot 9, pinned as listed: bash runs pytest in each, and the census does
# not see it.
@pytest.mark.parametrize("text", [
    "$(echo pytest) -q",
    "echo $(( $(pytest -q) ))",
    "echo $(case x in x) pytest -q;; esac)",
])
def test_a_listed_shell_blind_spot_is_accepted(text):
    assert census._miss(["bash", "-c", text], None, {}) is None, text


@pytest.mark.parametrize("argv", [
    ["git", "grep", "pytest"],
    ["pip", "install", "pytest"],
    ["timeout", "60", "git", "grep", "pytest"],
    ["uv", "run", "python", "script.py", "-m", "pytest"],
    ["bash", "-lc", "echo hi; ls"],
    ["bash", "-c", "cd x\nls\ngit status"],
    ["coverage", "run", "script.py", "-m", "pytest"],
    ["coverage", "report", "-m"],
    [PY, "-m", "coverage", "run", "-m", "pip"],
    [PY, "-m", "coverage", "report"],
    ["bash", "scripts/run.sh", "-c", "pytest"],
])
def test_a_mention_of_pytest_that_launches_no_pytest_is_ignored(argv):
    assert census._miss(argv, None, {}) is None


# THE LAUNCHER FAMILY. Every way the suite could start pytest by a module name, a
# launcher, a console-script file or a tool runner, each refused unblocked and
# accepted with the flag.
_LAUNCHER_FORMS = [
    # pytest's __main__ module, plain and bundled.
    [PY, "-m", "pytest.__main__"],
    [PY, "-mpytest.__main__"],
    [PY, "-I", "-m", "pytest.__main__", "-q"],
    # The Windows launcher and .exe names.
    ["py", "-m", "pytest"],
    ["py", "-3", "-m", "pytest"],
    ["py", "-3.12", "-m", "pytest", "-q"],
    ["py", "-V:3.12", "-m", "pytest"],
    ["py.exe", "-3", "-m", "pytest"],
    ["pyw", "-m", "pytest"],
    ["python.exe", "-m", "pytest"],
    # An interpreter running pytest's console script or its __main__ file.
    [PY, "/venv/bin/pytest"],
    [PY, "/venv/bin/py.test", "-q"],
    [PY, "-I", "/venv/bin/pytest", "-q"],
    [PY, "/venv/lib/python3.12/site-packages/pytest/__main__.py"],
    # coverage's script and __main__ forms.
    ["coverage", "run", "/venv/bin/pytest"],
    ["coverage", "run", "--branch", "/venv/bin/pytest", "-q"],
    ["coverage", "run", "-m", "pytest.__main__"],
    [PY, "-m", "coverage", "run", "-m", "pytest.__main__"],
    [PY, "-m", "coverage", "run", "/venv/bin/pytest"],
    # Distro and Windows program names.
    ["pytest-3"],
    ["py.test-3", "-q"],
    ["pytest3"],
    ["/usr/bin/pytest-3.12"],
    ["pytest.exe", "-q"],
    # Tool runners that start a pytest program by name.
    ["uvx", "pytest"],
    ["uvx", "--with", "pytest-xdist", "pytest", "-q"],
    ["uv", "tool", "run", "pytest"],
    ["uv", "tool", "run", "--from", "pytest==9.2", "pytest"],
    ["pipx", "run", "pytest"],
    ["pipx", "run", "--spec", "pytest==9.2", "pytest"],
    ["pdm", "run", "pytest"],
    ["hatch", "run", "pytest"],
    ["hatch", "run", "test:pytest", "-q"],
    ["conda", "run", "-n", "dev", "pytest"],
    ["micromamba", "run", "-n", "dev", "pytest"],
    ["pipenv", "run", "pytest"],
    ["rye", "run", "pytest"],
    ["pixi", "run", "pytest"],
    # The same forms inside a shell command.
    ["bash", "-c", "py -3 -m pytest"],
    ["bash", "-c", "cd x && uvx pytest"],
    # An interpreter in the program position, after a wrapper or in a shell,
    # running pytest's script.
    ["env", "A=1", "python3", "/venv/bin/pytest"],
    ["bash", "-c", "python3 /venv/bin/pytest"],
    # A tool runner's version pin on pytest's program.
    ["uvx", "pytest@8.3.0"],
    ["uv", "tool", "run", "pytest@8.3.0", "-q"],
    # Interpreters other than CPython and PyPy that run -m pytest.
    ["ipython", "-m", "pytest"],
    ["ipython3", "-m", "pytest", "--", "-q"],
    ["graalpy", "-m", "pytest"],
    ["pyston3", "-m", "pytest"],
    # A wrapper option's value named like an interpreter does not hide the
    # interpreter in the program position.
    ["uv", "run", "-p", "python3", "python", "-m", "pytest"],
    ["uv", "run", "--python", "python3.12", "python", "-m", "pytest"],
    ["conda", "run", "-n", "python3", "python", "-m", "pytest"],
    ["uv", "run", "-p", "python3", "python", "/venv/bin/pytest"],
    ["pipx", "run", "--python", "python3.12", "--spec", "pytest==9", "pytest"],
    # An option the tables do not list does not hide an interpreter after it.
    ["uv", "run", "--made-up-option", "python3", "python", "/venv/bin/pytest"],
    # Any word naming an interpreter is read for -m pytest, not only the first.
    ["echo", "python3", "python", "-m", "pytest"],
    # Each listed wrapper's value-taking options, as read from its interface.
    ["uv", "run", "--index-url", "https://pypi.example/simple", "pytest"],
    ["uv", "run", "-w", "pytest-xdist", "--no-group", "lint", "pytest"],
    ["uv", "run", "-i", "https://pypi.example/simple", "python", "/venv/bin/pytest"],
    ["uv", "tool", "run", "-c", "constraints.txt", "pytest"],
    ["uvx", "--exclude-newer", "2026-01-01", "pytest"],
    ["pipx", "run", "--backend", "uv", "pytest"],
    ["pipx", "run", "--fetch-python", "always", "pytest"],
    ["pdm", "run", "-k", "pre_test", "pytest"],
    ["pdm", "run", "--working-dir", "tests", "pytest"],
    ["hatch", "run", "+py=3.12", "test:pytest"],
    ["conda", "run", "--cwd", "work", "pytest"],
    ["conda", "run", "-p", "/opt/env", "python", "/venv/bin/pytest"],
    ["micromamba", "run", "-a", "none", "pytest"],
    ["micromamba", "run", "--label", "dev", "pytest"],
    ["pixi", "run", "--auth-file", "auth.json", "pytest"],
    ["poetry", "run", "--directory", "work", "pytest"],
    ["sudo", "-R", "/chroot", "pytest"],
    ["sudo", "--user", "bob", "pytest"],
    ["timeout", "--signal", "KILL", "60", "pytest"],
    ["nice", "--adjustment", "5", "pytest"],
    ["stdbuf", "--output", "L", "pytest"],
    ["env", "--chdir", "/tmp", "pytest"],
    ["time", "--output", "t.log", "pytest"],
    ["xvfb-run", "--server-args", "-screen 0 1x1x8", "pytest"],
    ["bash", "-c", "exec -a name pytest"],
]


@pytest.mark.parametrize("argv", _LAUNCHER_FORMS)
def test_each_launcher_form_is_refused_unblocked(argv):
    assert census._miss(argv, None, {}) is not None, argv


@pytest.mark.parametrize("argv", _LAUNCHER_FORMS)
def test_each_launcher_form_is_accepted_with_the_flag(argv):
    if argv[0] == "bash":
        blocked = [argv[0], argv[1], argv[2] + " -p no:randomly"]
    else:
        blocked = [*argv, "-p", "no:randomly"]
    assert census._miss(blocked, None, {}) is None, blocked


@pytest.mark.parametrize("argv", [
    # These name pytest but launch no pytest session.
    [PY, "-m", "py.test"],
    [PY, "-m", "_pytest"],
    [PY, "-m", "pytest_randomly"],
    [PY, "/venv/bin/pip", "install", "pytest"],
    [PY, "tests/pytest_helpers.py"],
    ["py", "-3", "script.py", "-m", "pytest"],
    ["py", "--list"],
    ["uvx", "ruff", "check"],
    ["pipx", "run", "black", "."],
    ["hatch", "run", "lint:ruff"],
    ["conda", "run", "-n", "dev", "python", "script.py"],
    ["coverage", "run", "/venv/bin/pip"],
    ["pytest-cov-report"],
    # A python-named package or file is not an interpreter, so the word after it
    # is not pytest's script.
    ["pip", "install", "python-dateutil", "pytest"],
    ["uv", "pip", "install", "python-dotenv", "pytest"],
    ["bash", "-c", "pip install python-dateutil pytest"],
    ["git", "add", "python_helpers.py", "tests/pytest"],
    ["pip", "install", "pythonnet", "pytest-xdist", "pytest"],
    # An interpreter word in a non-program position starts no script of its own.
    ["echo", "python", "/venv/bin/pytest"],
    ["which", "python3", "pytest"],
    # A word that only starts with an interpreter's name is no interpreter, so
    # its `-m pytest` names no module run: only the exact names count.
    ["python-config", "-m", "pytest"],
    ["bash", "-c", "python-config -m pytest"],
    ["pythonista", "-m", "pytest"],
    ["pypy-helper", "-m", "pytest.__main__"],
])
def test_a_neighbouring_form_that_launches_no_pytest_is_ignored(argv):
    assert census._miss(argv, None, {}) is None, argv


# A WRAPPER OPTION'S VALUE NAMED LIKE AN INTERPRETER. Each refused row starts
# pytest; each accepted row does not.
_OPTION_VALUE_REFUSED = [
    ["uv", "run", "-p", "python3", "python", "-m", "pytest"],
    ["uv", "run", "--python", "python3.12", "python", "-m", "pytest"],
    ["uv", "run", "--python=python3.12", "python", "-m", "pytest"],
    ["uv", "run", "-p", "python3", "python", "/venv/bin/pytest"],
    ["uv", "run", "--python", "python3.12", "python", "/venv/bin/pytest"],
    ["conda", "run", "-n", "python3", "python", "-m", "pytest"],
    ["conda", "run", "-p", "/envs/python3", "python", "-m", "pytest"],
    ["sudo", "-u", "python", "-E", "python", "-m", "pytest"],
    ["bash", "-c", "uv run -p python3 python -m pytest"],
    ["bash", "-c", "conda run -n python3 python -m pytest"],
    ["uvx", "--python", "python3.12", "pytest"],
    ["uv", "tool", "run", "--python", "python3.12", "pytest"],
    ["pipx", "run", "--python", "python3.12", "pytest"],
    ["nice", "-n", "5", "pytest"],
    ["timeout", "-s", "KILL", "60", "pytest"],
    ["stdbuf", "-o", "L", "pytest"],
    ["xvfb-run", "-a", "pytest"],
    ["env", "-u", "X", "pytest"],
    ["env", "-C", "/tmp", "pytest"],
    ["time", "-p", "pytest"],
    ["nohup", "pytest"],
    ["exec", "pytest"],
    ["coverage", "run", "--rcfile", "python.cfg", "-m", "pytest"],
    ["py", "-V:3.12", "-m", "pytest"],
]


@pytest.mark.parametrize("argv", _OPTION_VALUE_REFUSED)
def test_an_option_value_named_like_an_interpreter_hides_nothing(argv):
    assert census._miss(argv, None, {}) is not None, argv
    blocked = ([argv[0], argv[1], argv[2] + " -p no:randomly"] if argv[0] == "bash"
               else [*argv, "-p", "no:randomly"])
    assert census._miss(blocked, None, {}) is None, blocked


@pytest.mark.parametrize("argv", [
    ["uv", "run", "-p", "python3", "python", "-m", "pip", "list"],
    ["uv", "pip", "install", "pytest"],
    ["conda", "install", "-n", "python3", "pytest"],
    ["uv", "run", "-p", "python3", "python", "script.py", "-m", "pytest"],
    ["sudo", "-u", "python", "ls"],
    ["timeout", "60", "git", "status"],
])
def test_an_option_value_named_like_an_interpreter_makes_no_launch_pytest(argv):
    assert census._miss(argv, None, {}) is None, argv


# The script-route candidates step past an interpreter-named word only, and only
# when it follows an option the tables do not know: a known flag (`-v`) has no
# value, and a non-interpreter program (`echo`, `ls`) is the program.
@pytest.mark.parametrize("accepted, refused_twin", [
    (["uv", "run", "-v", "echo", "python", "/venv/bin/pytest"],
     ["uv", "run", "-v", "python", "/venv/bin/pytest"]),
    (["uv", "run", "--frozen", "ls", "-l", "python", "/venv/bin/pytest"],
     ["uv", "run", "--frozen", "python", "/venv/bin/pytest"]),
    (["timeout", "-v", "60", "echo", "python", "/venv/bin/pytest"],
     ["timeout", "-v", "60", "python", "/venv/bin/pytest"]),
    (["uv", "run", "-v", "python3", "python", "/venv/bin/pytest"],
     ["uv", "run", "--made-up-option", "python3", "python", "/venv/bin/pytest"]),
    (["uv", "run", "--made-up-option", "echo", "python", "/venv/bin/pytest"],
     ["uv", "run", "--made-up-option", "py", "python", "/venv/bin/pytest"]),
], ids=["known-flag-then-echo", "known-flag-then-ls", "timeout-flag-then-echo",
        "known-flag-then-interpreter", "unknown-option-then-echo"])
def test_the_script_candidates_step_only_past_an_option_value(accepted, refused_twin):
    assert census._miss(accepted, None, {}) is None, accepted
    assert census._miss(refused_twin, None, {}) is not None, refused_twin


# EACH WRAPPER OPTION THAT TAKES A SEPARATE VALUE. Every value is one the option
# really takes and is neither an option, a number nor a NAME=VALUE word, so an
# option missing from its wrapper's table makes its value the program and the
# row is no longer refused.
_WRAPPER_VALUE_REFUSED = [
    ["uv", "run", "--with-editable", "./plugin", "pytest"],
    ["uv", "run", "--with-requirements", "requirements.txt", "pytest"],
    ["uv", "run", "--env-file", ".env", "pytest"],
    ["uv", "run", "--python-platform", "linux", "pytest"],
    ["uv", "run", "--index", "https://pypi.org/simple", "pytest"],
    ["uv", "run", "--default-index", "https://pypi.org/simple", "pytest"],
    ["uv", "run", "--extra-index-url", "https://example.org/simple", "pytest"],
    ["uv", "run", "-f", "./wheels", "pytest"],
    ["uv", "run", "--find-links", "./wheels", "pytest"],
    ["uv", "run", "--index-strategy", "unsafe-best-match", "pytest"],
    ["uv", "run", "--keyring-provider", "subprocess", "pytest"],
    ["uv", "run", "-P", "requests", "pytest"],
    ["uv", "run", "--upgrade-package", "requests", "pytest"],
    ["uv", "run", "--upgrade-group", "dev", "pytest"],
    ["uv", "run", "--resolution", "lowest", "pytest"],
    ["uv", "run", "--prerelease", "allow", "pytest"],
    ["uv", "run", "--prerelease-package", "requests", "pytest"],
    ["uv", "run", "--fork-strategy", "fewest", "pytest"],
    ["uv", "run", "--exclude-newer-package", "typing-extensions=2024-01-01", "pytest"],
    ["uv", "run", "--no-sources-package", "requests", "pytest"],
    ["uv", "run", "--reinstall-package", "requests", "pytest"],
    ["uv", "run", "--link-mode", "copy", "pytest"],
    ["uv", "run", "-C", "cmake.define.FOO=ON", "pytest"],
    ["uv", "run", "--config-setting", "cmake.define.FOO=ON", "pytest"],
    ["uv", "run", "--config-settings-package", "numpy:setup-args=-Dblas=none", "pytest"],
    ["uv", "run", "--no-build-isolation-package", "requests", "pytest"],
    ["uv", "run", "--no-build-package", "requests", "pytest"],
    ["uv", "run", "--no-binary-package", "requests", "pytest"],
    ["uv", "run", "--cache-dir", ".cache/uv", "pytest"],
    ["uv", "run", "--refresh-package", "requests", "pytest"],
    ["uv", "run", "--color", "always", "pytest"],
    ["uv", "run", "--allow-insecure-host", "localhost", "pytest"],
    ["uv", "run", "--directory", "src", "pytest"],
    ["uv", "run", "--project", "src", "pytest"],
    ["uv", "run", "--config-file", "uv.toml", "pytest"],
    ["uv", "run", "--extra", "test", "pytest"],
    ["uv", "run", "--no-extra", "docs", "pytest"],
    ["uv", "run", "--group", "test", "pytest"],
    ["uv", "run", "--only-group", "test", "pytest"],
    ["uv", "run", "--no-editable-package", "mypkg", "pytest"],
    ["uv", "run", "--package", "mypkg", "pytest"],
    ["uvx", "--from", "pytest>=8", "pytest"],
    ["uvx", "--constraints", "constraints.txt", "pytest"],
    ["uvx", "-b", "build-constraints.txt", "pytest"],
    ["uvx", "--build-constraints", "build-constraints.txt", "pytest"],
    ["uvx", "--overrides", "overrides.txt", "pytest"],
    ["uvx", "--torch-backend", "cpu", "pytest"],
    ["conda", "run", "--name", "dev", "pytest"],
    ["conda", "run", "-p", "/envs/dev", "pytest"],
    ["conda", "run", "--prefix", "/envs/dev", "pytest"],
    ["micromamba", "run", "--name", "dev", "pytest"],
    ["micromamba", "run", "-p", "/envs/dev", "pytest"],
    ["micromamba", "run", "--prefix", "/envs/dev", "pytest"],
    ["micromamba", "run", "-r", "/opt/mamba", "pytest"],
    ["micromamba", "run", "--root-prefix", "/opt/mamba", "pytest"],
    ["micromamba", "run", "--cwd", "src", "pytest"],
    ["micromamba", "run", "--attach", "stdout", "pytest"],
    ["micromamba", "run", "--relocate-prefix", "/opt/envs/dev", "pytest"],
    ["micromamba", "run", "-e", "HOME", "pytest"],
    ["micromamba", "run", "--env", "HOME", "pytest"],
    ["nice", "-n", "+5", "pytest"],
    ["nice", "--adjustment", "+5", "pytest"],
    ["pdm", "run", "-p", "./sub", "pytest"],
    ["pdm", "run", "--project", "./sub", "pytest"],
    ["pdm", "run", "--skip", "pre_test", "pytest"],
    ["pdm", "run", "--venv", "in-project", "pytest"],
    ["pdm", "run", "--env-file", ".env", "pytest"],
    ["pipx", "run", "--spec", "pytest>=8", "pytest"],
    ["pipx", "run", "--with", "requests", "pytest"],
    ["pipx", "run", "-i", "https://pypi.org/simple", "pytest"],
    ["pipx", "run", "--index-url", "https://pypi.org/simple", "pytest"],
    ["pixi", "run", "-e", "test", "pytest"],
    ["pixi", "run", "--environment", "test", "pytest"],
    ["pixi", "run", "-p", "linux-64", "pytest"],
    ["pixi", "run", "--platform", "linux-64", "pytest"],
    ["pixi", "run", "-m", "pixi.toml", "pytest"],
    ["pixi", "run", "--manifest-path", "pixi.toml", "pytest"],
    ["pixi", "run", "-w", "./sub", "pytest"],
    ["pixi", "run", "--workspace", "./sub", "pytest"],
    ["pixi", "run", "-s", "tool.py", "pytest"],
    ["pixi", "run", "--script", "tool.py", "pytest"],
    ["pixi", "run", "--config-file", "config.toml", "pytest"],
    ["pixi", "run", "--pinning-strategy", "semver", "pytest"],
    ["pixi", "run", "--tls-root-certs", "system", "pytest"],
    ["pixi", "run", "--color", "always", "pytest"],
    ["pixi", "run", "--pypi-keyring-provider", "subprocess", "pytest"],
    ["poetry", "run", "-C", "./sub", "pytest"],
    ["poetry", "run", "-P", "./sub", "pytest"],
    ["poetry", "run", "--project", "./sub", "pytest"],
    ["rye", "run", "--pyproject", "./sub/pyproject.toml", "pytest"],
    ["stdbuf", "-i", "1K", "pytest"],
    ["stdbuf", "--input", "1K", "pytest"],
    ["stdbuf", "-e", "L", "pytest"],
    ["stdbuf", "--error", "L", "pytest"],
    ["sudo", "-u", "root", "pytest"],
    ["sudo", "-g", "wheel", "pytest"],
    ["sudo", "--group", "wheel", "pytest"],
    ["sudo", "-h", "buildhost", "pytest"],
    ["sudo", "--host", "buildhost", "pytest"],
    ["sudo", "-p", "Password:", "pytest"],
    ["sudo", "--prompt", "Password:", "pytest"],
    ["sudo", "-D", "/srv", "pytest"],
    ["sudo", "--chdir", "/srv", "pytest"],
    ["sudo", "--chroot", "/srv/root", "pytest"],
    ["sudo", "-r", "sysadm_r", "pytest"],
    ["sudo", "--role", "sysadm_r", "pytest"],
    ["sudo", "-t", "sysadm_t", "pytest"],
    ["sudo", "--type", "sysadm_t", "pytest"],
    ["sudo", "-U", "alice", "pytest"],
    ["sudo", "--other-user", "alice", "pytest"],
    ["sudo", "-T", "1h30m", "pytest"],
    ["sudo", "--command-timeout", "1h30m", "pytest"],
    ["time", "-f", "%e", "pytest"],
    ["time", "--format", "%e", "pytest"],
    ["time", "-o", "time.log", "pytest"],
    ["timeout", "-k", "inf", "60", "pytest"],
    ["timeout", "--kill-after", "inf", "60", "pytest"],
    ["xvfb-run", "-s", "+extension GLX", "pytest"],
    ["xvfb-run", "--server-args", "+extension GLX", "pytest"],
    ["xvfb-run", "-f", ".Xauthority", "pytest"],
    ["xvfb-run", "--auth-file", ".Xauthority", "pytest"],
    ["xvfb-run", "-e", "xvfb.log", "pytest"],
    ["xvfb-run", "--error-file", "xvfb.log", "pytest"],
    ["xvfb-run", "-p", "MIT-MAGIC-COOKIE-1", "pytest"],
    ["xvfb-run", "--xauth-protocol", "MIT-MAGIC-COOKIE-1", "pytest"],
]


@pytest.mark.parametrize("argv", _WRAPPER_VALUE_REFUSED)
def test_each_wrapper_option_value_is_stepped_over(argv):
    assert census._miss(argv, None, {}) is not None, argv
    blocked = [*argv, *_BLOCK]
    assert census._miss(blocked, None, {}) is None, blocked


# EACH KNOWN FLAG, UNDER A WRAPPER THAT HAS IT. A known flag takes no value, so
# in `<wrapper> <flag> python3 python /venv/bin/pytest` the program is python3,
# whose script is a file named `python`, and no pytest starts. A flag missing
# from _KNOWN_FLAGS reads as an option whose value may be `python3`, and the
# row is refused like its twin with an unknown option.
_KNOWN_FLAG_WRAPPERS = {
    "uv run": ("--active", "--all-extras", "--all-groups", "--all-packages",
               "--compile-bytecode", "--exact", "--frozen", "--gui-script", "--help",
               "--isolated", "--locked", "--managed-python", "--module", "--no-binary",
               "--no-build", "--no-build-isolation", "--no-cache", "--no-config",
               "--no-default-groups", "--no-dev", "--no-editable", "--no-env-file",
               "--no-index", "--no-managed-python", "--no-progress", "--no-project",
               "--no-python-downloads", "--no-require-build-hashes", "--no-sources",
               "--no-sync", "--offline", "--only-dev", "--quiet", "--refresh",
               "--reinstall", "--require-build-hashes", "--system-certs",
               "--upgrade", "--verbose", "-q"),
    "uvx": ("--lfs",),
    "timeout": ("--preserve-status", "--foreground"),
    "env": ("--ignore-environment", "-0", "--null", "--debug", "--list-signal-handling",
            "--block-signal", "--default-signal", "--ignore-signal"),
    "xvfb-run": ("--auto-servernum", "-l", "--listen-tcp"),
    "time": ("--append", "--portability"),
    "sudo": ("-A", "-B", "-E", "-H", "-K", "-N", "-S", "-V", "--askpass", "--background",
             "--bell", "--no-update",
             "--preserve-env", "--edit", "--set-home", "--login", "--remove-timestamp",
             "--reset-timestamp", "--list", "--non-interactive", "--preserve-groups",
             "--stdin", "--shell", "--version", "--validate"),
    "pipx run": ("--skip-maintenance", "--global", "--no-path-check", "--path",
                 "--pypackages", "--fetch-missing-python", "--system-site-packages",
                 "--editable"),
    "pdm run": ("-j", "--json", "--site-packages", "--recreate"),
    "poetry run": ("--ansi", "--no-ansi", "--no-interaction", "--no-plugins", "-vv", "-vvv"),
    "pipenv run": ("--system",),
    "conda run": ("--dev", "--debug-wrapper-scripts", "--no-capture-output", "--live-stream",
                  "-vvvv"),
    "micromamba run": ("-d", "--detach", "--clean-env"),
    "pixi run": ("--force-activate", "--revalidate", "--skip-deps", "--dry-run", "-x",
                 "--executable", "--experimental", "--templated", "--run-post-link-scripts",
                 "--no-symbolic-links", "--no-hard-links", "--no-ref-links", "--tls-no-verify",
                 "--use-environment-activation-cache", "--no-completions", "--no-install",
                 "--as-is"),
}


@pytest.mark.parametrize("wrapper, flag", [
    (wrapper, flag) for wrapper, flags in _KNOWN_FLAG_WRAPPERS.items() for flag in flags])
def test_a_known_flag_takes_no_value(wrapper, flag):
    # timeout's DURATION follows its options, so a flag that wrongly took a value
    # would take the duration and make python3 the duration instead.
    duration = ["60"] if wrapper == "timeout" else []
    accepted = [*wrapper.split(), flag, *duration, "python3", "python", "/venv/bin/pytest"]
    assert census._miss(accepted, None, {}) is None, accepted
    refused_twin = [*wrapper.split(), "--made-up-option", "python3", "python", "/venv/bin/pytest"]
    assert census._miss(refused_twin, None, {}) is not None, refused_twin


# THE ENUMERATED FAMILIES. The census docstring lists the launch families it
# refuses, each under a [label]; this map holds at least one refused row per
# label, and the two sets must be equal, so neither can change alone.
_FAMILY_ROWS = {
    "program": [["pytest"], ["/venv/bin/py.test", "-q"], ["pytest-3"]],
    "coverage": [["coverage", "run", "-m", "pytest"], ["coverage", "run", "/venv/bin/pytest"]],
    "module": [[PY, "-m", "pytest"], ["py", "-3", "-m", "pytest.__main__"],
               ["ipython", "-m", "pytest"], [PY, "-m", "coverage", "run", "-m", "pytest"]],
    "mention": [["echo", "python", "-m", "pytest"], ["echo", "python3", "-mpytest.__main__"]],
    "script": [[PY, "/venv/bin/pytest"], [PY, "-I", "/x/pytest/__main__.py"]],
    "wrapper": [["timeout", "60", "pytest"], ["uvx", "pytest@8.3.0"], ["A=1", PY, "-m", "pytest"]],
    "shell": [["bash", "-c", "cd x && pytest"], ["sh", "-ec", "python3 -m pytest"]],
}


def _documented_families():
    return re.findall(r"^- \[([a-z]+)\] ", census.__doc__, re.MULTILINE)


def test_every_documented_family_has_a_refused_row_and_no_other_family_exists():
    documented = _documented_families()
    assert len(documented) == len(set(documented)), documented
    assert sorted(documented) == sorted(_FAMILY_ROWS), (documented, sorted(_FAMILY_ROWS))
    empty = [family for family in documented if not _FAMILY_ROWS[family]]
    assert not empty, f"documented families with no refused row: {empty}"
    unrefused = [(family, argv) for family in documented for argv in _FAMILY_ROWS[family]
                 if census._miss(argv, None, {}) is None]
    assert not unrefused, f"family rows the census accepts: {unrefused}"


@pytest.mark.parametrize("family, argv", [
    (family, argv) for family, rows in sorted(_FAMILY_ROWS.items()) for argv in rows])
def test_each_documented_family_is_refused(family, argv):
    assert census._miss(argv, None, {}) is not None, (family, argv)


def test_the_census_is_live_and_refuses_before_the_child_starts(tmp_path, census_state):
    assert census.installed(), "tests/conftest.py did not install the census"
    before = len(census_state["misses"])
    with pytest.raises(census.ChildPytestCensusError) as caught:
        subprocess.run([PY, "-m", "pytest", "--version"], cwd=tmp_path,
                       capture_output=True, timeout=60)
    message = str(caught.value)
    assert census.REMEDY in message and str(tmp_path) in message, message
    assert "test_the_census_is_live" in message, message
    assert census_state["misses"][before:] == [message]


def test_a_blocked_child_runs(tmp_path, census_state):
    before = list(census_state["misses"])
    proc = subprocess.run([PY, "-m", "pytest", "--version", *_BLOCK], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert census_state["misses"] == before


def test_a_non_pytest_launch_is_untouched(census_state):
    before = list(census_state["misses"])
    proc = subprocess.run([PY, "-c", "print('ok')"], capture_output=True, text=True,
                          timeout=60)
    assert proc.stdout.strip() == "ok"
    assert census_state["misses"] == before


def test_a_malformed_launch_that_names_no_pytest_is_left_to_its_launcher(census_state):
    before = list(census_state["misses"])
    with pytest.raises(TypeError):
        subprocess.run(["true", 1], capture_output=True, timeout=60)
    census._audit("subprocess.Popen", (None, ["true", 1], None, None))
    census._audit("subprocess.Popen", (None, ["true"], None, {"A": 1}))
    assert census_state["misses"] == before


@pytest.mark.parametrize("argv,env", [
    ([PY, "-m", "pytest", 1], None),
    (["bash", "-c", b"pytest", 1], None),
    (["py.test", "-q"], {"A": 1}),
])
def test_a_pytest_launch_the_census_cannot_read_fails_the_run_by_name(
        census_state, argv, env):
    before = len(census_state["misses"])
    census._audit("subprocess.Popen", (None, argv, None, env))
    added = census_state["misses"][before:]
    assert len(added) == 1, added
    assert "could not inspect a pytest launch" in added[0], added
    assert "test_a_pytest_launch_the_census_cannot_read" in added[0], added


def test_a_live_pytest_launch_the_census_cannot_read_is_recorded(census_state):
    before = len(census_state["misses"])
    with pytest.raises(TypeError):
        subprocess.run([PY, "-m", "pytest", 1], capture_output=True, timeout=60)
    added = census_state["misses"][before:]
    assert len(added) == 1 and "test_a_live_pytest_launch" in added[0], added


class _Reporter:
    def __init__(self):
        self.lines = []

    def write_line(self, line):
        self.lines.append(line)


class _PluginManager:
    def __init__(self, reporter):
        self.reporter = reporter

    def get_plugin(self, name):
        return self.reporter if name == "terminalreporter" else None


class _Config:
    def __init__(self, reporter, workeroutput=None):
        self.pluginmanager = _PluginManager(reporter)
        if workeroutput is not None:
            self.workeroutput = workeroutput


class _Session:
    def __init__(self, config):
        self.config = config
        self.exitstatus = 0


def test_finish_fails_the_session_and_reports_each_miss(census_state):
    census_state["misses"][:] = ["t::a: pytest child [x] ...; " + census.REMEDY]
    census_state["forwarded"][:] = []
    reporter = _Reporter()
    session = _Session(_Config(reporter))
    census.finish(session)
    assert session.exitstatus == 1
    assert reporter.lines[0].startswith("CHILD PYTEST CENSUS: 1 ")
    assert reporter.lines[1] == "  " + census_state["misses"][0]


@pytest.mark.parametrize("prior, after", [(0, 1), (1, 1), (2, 2), (3, 3), (4, 4)])
def test_a_miss_raises_only_a_clean_status(census_state, prior, after):
    """MUTANT: finish() overwrites the session's status with 1, so an
    interrupted, internal-error or usage-error session reads as a test failure."""
    census_state["misses"][:] = ["t::a: pytest child [x] ...; " + census.REMEDY]
    census_state["forwarded"][:] = []
    session = _Session(_Config(_Reporter()))
    session.exitstatus = prior
    census.finish(session)
    assert session.exitstatus == after


def test_finish_with_no_miss_leaves_the_session_alone(census_state):
    census_state["misses"][:] = []
    census_state["forwarded"][:] = []
    reporter = _Reporter()
    session = _Session(_Config(reporter))
    census.finish(session)
    assert session.exitstatus == 0 and reporter.lines == []


def test_a_worker_hands_its_misses_to_the_controller(census_state):
    line = "t::b: pytest child [y] ...; " + census.REMEDY
    census_state["misses"][:] = [line]
    census_state["forwarded"][:] = []
    workeroutput = {}
    worker = _Session(_Config(_Reporter(), workeroutput=workeroutput))
    census.finish(worker)
    assert worker.exitstatus == 0, "a worker's exit status is ignored by xdist"
    assert workeroutput[census.WORKEROUTPUT_KEY] == [line]

    census_state["misses"][:] = []  # the controller process recorded nothing itself
    node = type("Node", (), {"workeroutput": workeroutput,
                             "gateway": type("Gateway", (), {"id": "gw3"})()})()
    census.collect(node)
    reporter = _Reporter()
    controller = _Session(_Config(reporter))
    census.finish(controller)
    assert controller.exitstatus == 1
    assert reporter.lines[1] == "  [gw3] " + line


_RIG_CONFTEST = '''
import importlib.util

_spec = importlib.util.spec_from_file_location("pact_suite_conftest", {conftest!r})
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)
pytest_configure = _suite.pytest_configure
pytest_testnodedown = _suite.pytest_testnodedown
pytest_sessionfinish = _suite.pytest_sessionfinish
'''

_RIG_TESTS = '''
import subprocess
import sys


def test_swallows_the_census_error():
    try:
        subprocess.run([sys.executable, "-m", "pytest", "--version"{flag}],
                       capture_output=True, timeout=60)
    except Exception:
        pass


def test_plain():
    pass
'''


def _run_rig(tmp_path, flag):
    (tmp_path / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (tmp_path / "test_swallow.py").write_text(
        _RIG_TESTS.format(flag=flag), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_XDIST_") and k not in ("PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")}
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *_BLOCK, "-n", "2",
         "test_swallow.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


def test_under_xdist_a_swallowed_miss_still_fails_the_run(tmp_path):
    rc, output = _run_rig(tmp_path, "")
    assert "2 passed" in output, output
    assert rc == 1, output
    assert "CHILD PYTEST CENSUS: 1 " in output, output
    assert "test_swallows_the_census_error" in output and census.REMEDY in output, output


def test_under_xdist_a_blocked_child_passes_the_run(tmp_path):
    rc, output = _run_rig(tmp_path, ', "-p", "no:randomly"')
    assert "2 passed" in output, output
    assert rc == 0, output
    assert "CHILD PYTEST CENSUS" not in output, output


# timeout's one DURATION word, in every spelling coreutils 9.4 runs its command
# after, is stepped over.
@pytest.mark.parametrize("duration", [
    "60", ".5", "5.", "inf", "INF", "infinity", "1e1", "1E1", "0x5", "0X5", "0x1p3", "0x.8",
    "2s", "1m", "0.01h", "1e-2d", "+5",
])
def test_each_timeout_duration_is_stepped_over(duration):
    assert census._miss(["timeout", duration, "pytest", "-q"], None, {}) is not None
    assert census._miss(["timeout", duration, "pytest", "-p", "no:randomly"], None, {}) is None


def test_timeout_judges_the_command_after_any_duration_word():
    # timeout rejects `bogus` and runs nothing, but the census judges the command
    # after it anyway, as its docstring says.
    assert census._miss(["timeout", "bogus", "pytest", "-q"], None, {}) is not None
    # With no duration given, `pytest` is the duration and nothing runs.
    assert census._miss(["timeout", "pytest", "-q"], None, {}) is None
