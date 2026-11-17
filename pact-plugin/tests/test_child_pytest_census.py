"""
Location: pact-plugin/tests/test_child_pytest_census.py
Summary: Pins the child-pytest census: which launches it refuses and accepts,
         that it is live in this session, that finish() fails the session, and
         that under real xdist a miss a test swallows still fails the run.
Used by: the suite; the census itself is tests/child_pytest_census.py, wired in
         pact-plugin/conftest.py.
"""
import ast
import concurrent.futures
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import child_pytest_census as census

PY = sys.executable
_CONFTEST = Path(__file__).resolve().with_name("conftest.py")
_BLOCK = ["-p", "no:randomly"]


def _child_env():
    """This process's environment without its PYTEST_* names, so a child
    pytest does not take PYTEST_ADDOPTS, PYTEST_PLUGINS or
    PYTEST_DISABLE_PLUGIN_AUTOLOAD from the shell that runs the suite."""
    return {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}


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


_ROUTE_ROWS = [
    ([], {}, False),
    ([], {_D: "1"}, True),
    (["--disable-plugin-autoload"], {}, True),
    ([], {"PYTEST_ADDOPTS": "--disable-plugin-autoload"}, True),
    (["--", "--disable-plugin-autoload"], {}, False),
    (["--disable-plugin-autoload", "-p", "randomly"], {}, False),
    # CI's shuffled shape: the config's block, then the run line's load.
    (["-p", "no:randomly", "-p", "randomly"], {}, False),
    (["-p", "randomly", "-p", "no:randomly"], {}, True),
    (["-p", "no:randomly", "-p", "pytest_randomly"], {}, False),
    (["-p", "pytest_randomly", "-p", "no:randomly"], {}, True),
    (["-p", "no:randomly", "-p", "pytest_randomly.__init__"], {}, False),
    (["-p", "pytest_randomly.__init__", "-p", "no:randomly"], {}, False),
    (["-p", "pytest_randomly.__init__", "-p", "no:pytest_randomly.__init__",
      "-p", "no:randomly"], {}, True),
    (["-p", "no:pytest_randomly"], {}, False),
    (["-p", "no:pytest_randomly.__init__"], {}, False),
    (["-p", "no:pytest_randomly", "-p", "no:randomly"], {}, True),
    # A PYTEST_PLUGINS name is tried as an entry point before a module, so
    # `randomly` loads through the entry point even with autoload disabled.
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
]


@pytest.mark.parametrize("args, env, deterministic", _ROUTE_ROWS)
def test_each_route_pytest_loads_or_blocks_the_plugin_by(args, env, deterministic):
    assert census._deterministic(args, env) is deterministic, (args, env)
    verdict = census._miss([PY, "-m", "pytest", *args], None, env)
    assert (verdict is None) is deterministic, (args, env)


_PROBE_CONFTEST = """\
import os


def pytest_configure(config):
    # The package module, or its __init__ imported again under that name.
    loaded = any(str(getattr(plugin, "__name__", "")).split(".")[0] == "pytest_randomly"
                 for _, plugin in config.pluginmanager.list_name_plugin())
    with open(os.environ["RANDOMLY_PROBE_OUT"], "w") as handle:
        handle.write("loaded" if loaded else "not-loaded")
"""


def _probe(rig, args, env):
    """Whether a real pytest session started in `rig` with `args` and `env` (over
    this process's environment without its PYTEST_* names) registered a
    pytest_randomly module under any name."""
    out = rig / "verdict.txt"
    child_env = _child_env()
    child_env.update(env)
    child_env["RANDOMLY_PROBE_OUT"] = str(out)
    subprocess.run([PY, "-c", "import sys, pytest; sys.exit(pytest.main(sys.argv[1:]))", *args],
                   cwd=rig, env=child_env, capture_output=True, timeout=120)
    return out.read_text(encoding="utf-8") if out.is_file() else "no verdict"


def test_a_real_pytest_agrees_with_every_route_row(tmp_path):
    """The route rows are pytest 9.2's verdicts copied by hand; a pytest patch
    release can change them with nothing else red. Each row runs here as a real
    pytest session, in its own directory holding an empty pytest.ini and a
    conftest that reports whether pytest-randomly registered, and must agree with
    the row. The children start as `python -c "...pytest.main(...)"`, which the
    census's blind spot 3 leaves unjudged on purpose: they are the experiment,
    and some must load the plugin. They run outside the repository, so its
    conftest and seed gate do not load in them. CI installs the lock, so a CI run
    without pytest-randomly fails here instead of skipping. The run also fails
    unless some row really loads the plugin and some row really blocks it, so it
    cannot pass while the plugin is invisible to every child."""
    if importlib.util.find_spec("pytest_randomly") is None:
        reason = "pytest-randomly is not importable, so no real session can load it"
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(f"{reason}; CI installs the lock, so this arm must run there")
        pytest.skip(f"{reason}; CI installs the lock and runs this arm")
    rigs = []
    for index in range(len(_ROUTE_ROWS)):
        rig = tmp_path / f"row-{index}"
        rig.mkdir()
        (rig / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (rig / "conftest.py").write_text(_PROBE_CONFTEST, encoding="utf-8")
        rigs.append(rig)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        verdicts = list(pool.map(lambda job: _probe(job[0], *job[1][:2]),
                                 zip(rigs, _ROUTE_ROWS)))
    mismatches = [(args, env, deterministic, got)
                  for (args, env, deterministic), got in zip(_ROUTE_ROWS, verdicts)
                  if got != ("not-loaded" if deterministic else "loaded")]
    assert not mismatches, (
        f"a real pytest disagrees with these route rows (args, env, row says "
        f"deterministic, real session): {mismatches}. Re-derive the rows and the "
        f"census replay from the installed pytest, then update REPLAYED_VERSIONS.")
    assert "loaded" in verdicts and "not-loaded" in verdicts, verdicts


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
    (["PYTEST_ADDOPTS=-p no:randomly", "pytest"], {}),
    (["PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", PY, "-m", "pytest"], {}),
    (["sh", "-c", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest"], {}),
    (["sh", "-c", "PYTEST_ADDOPTS='-p no:randomly' python3 -m pytest -q"], {}),
    # An assignment before a shell reaches the commands inside it.
    (["PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "bash", "-c", "cd x && pytest"], {}),
])
def test_a_child_blocked_through_its_environment_is_accepted(argv, env):
    assert census._miss(argv, None, env) is None, argv


@pytest.mark.parametrize("argv,env", [
    # The command line comes after PYTEST_ADDOPTS, so its load wins.
    ([PY, "-m", "pytest", "-p", "randomly"], {"PYTEST_ADDOPTS": "-p no:randomly"}),
    # An addopts load wins over a disabled autoload.
    ([PY, "-m", "pytest"], {"PYTEST_ADDOPTS": "-p randomly",
                            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}),
    ([PY, "-m", "pytest"], {"PYTEST_ADDOPTS": "-p no:randomly -p randomly",
                            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}),
    # These drop the variable the parent set.
    (["PYTEST_DISABLE_PLUGIN_AUTOLOAD=", "pytest"], None),
    (["sh", "-c", "PYTEST_DISABLE_PLUGIN_AUTOLOAD= pytest"], None),
])
def test_a_child_whose_environment_does_not_block_is_refused(argv, env, monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert census._miss(argv, None, env) is not None, argv


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
    ["pypy3", "-m", "pytest"],
    ["/opt/pypy/bin/pypy", "-I", "-m", "pytest"],
    [PY, "--check-hash-based-pycs", "always", "-m", "pytest"],
    [PY, "-X", "dev", "--check-hash-based-pycs", "never", "-Wd", "-m", "pytest"],
    # A long option CPython may add later is stepped over, not taken as a script.
    [PY, "--a-future-flag", "-m", "pytest"],
    # -W or -X ending a bundle takes the next word as its value.
    [PY, "-bW", "ignore", "-m", "pytest"],
    [PY, "-sX", "dev", "-m", "pytest"],
    # coverage runs pytest as a module, directly or under an interpreter.
    ["coverage", "run", "-m", "pytest"],
    ["coverage", "run", "--branch", "--source", "hooks", "-m", "pytest", "-q"],
    ["coverage", "run", "--module", "pytest"],
    [PY, "-m", "coverage", "run", "-m", "pytest"],
    ["coverage", "run", "--rcfile", "python.cfg", "-m", "pytest"],
    ["py", "-V:3.12", "-m", "pytest"],
])
def test_an_unusual_interpreter_launch_is_refused(argv):
    assert census._miss(argv, None, {}) is not None, argv


# `--` ends an interpreter's options, and the next word is the script it runs.
@pytest.mark.parametrize("argv", [
    ["python3", "--", "/venv/bin/pytest", "-q"],
    [PY, "-I", "--", "/x/pytest/__main__.py"],
])
def test_a_pytest_script_after_double_dash_is_judged_as_the_script(argv):
    line = census._miss(argv, None, {})
    assert line is not None and line.endswith(census.REMEDY), (argv, line)
    assert census._miss([*argv, *_BLOCK], None, {}) is None, argv


def test_a_script_after_double_dash_that_is_not_pytest_is_accepted():
    assert census._miss(["python3", "--", "script.py", "pytest"], None, {}) is None


# Each o or O in a shell's option bundle takes the next word as its value, so
# the -c text is read past it, by the [shell] family: blocking lifts the
# refusal. zsh and dash are read as bash is; only bash is run for real.
@pytest.mark.parametrize("argv", [
    ["bash", "-euo", "pipefail", "-c", "pytest -q"],
    ["bash", "-oc", "pipefail", "pytest -q"],
    ["bash", "-co", "pipefail", "pytest -q"],
    ["bash", "-eo", "pipefail", "-c", "pytest -q"],
    ["bash", "+o", "history", "-c", "pytest -q"],
    ["bash", "-eO", "extglob", "-c", "pytest"],
    ["bash", "+O", "extglob", "-c", "pytest"],
    ["bash", "-euxo", "pipefail", "-o", "nounset", "-c", "pytest"],
    ["zsh", "-fo", "errexit", "-c", "pytest"],
    ["dash", "-eo", "errexit", "-c", "pytest"],
], ids=lambda argv: " ".join(argv[:4]))
def test_a_bundled_option_value_does_not_hide_the_c_text(argv):
    line = census._miss(argv, None, {})
    assert line is not None and line.endswith(census.REMEDY), (argv, line)
    blocked = [*argv[:-1], argv[-1] + " -p no:randomly"]
    assert census._miss(blocked, None, {}) is None, blocked


@pytest.mark.parametrize("argv", [
    ["bash", "-lc", "python -m pytest"],
    ["bash", "-ec", "pytest -q"],
    ["sh", "-e", "-c", "pytest"],
    ["bash", "--noprofile", "--norc", "-c", "pytest"],
    ["bash", "-o", "pipefail", "-c", "pytest | tee log"],
    ["zsh", "-fc", "pytest"],
    # A shell behind an assignment.
    ["A=1", "sh", "-c", "pytest"],
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
    # A program that runs nothing stops the [mention] read for its own command only.
    ["bash", "-c", "printf x; python -m pytest"],
    ["bash", "-c", "cat f | python -m pytest"],
    ["bash", "-c", "test -x f && python -m pytest"],
    ["bash", "-c", "printf x; make check python -m pytest"],
    ["bash", "-c", "cat f | make check python -m pytest"],
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
    ["bash", "-c", "(cd x && pytest -pno:randomly) > log 2>&1"],
    # A redirection does not end the command it belongs to.
    ["bash", "-c", "pytest > log -p no:randomly"],
    # A continued line is one command.
    ["bash", "-c", "pytest \\\n  -p no:randomly"],
    # A command that is only assignments, or a program with no pytest after it,
    # starts no pytest.
    ["bash", "-c", "SJ=/x/y.py; A=1\npytest -p no:randomly"],
    ["bash", "-c", "nohup; uv run -p python3; pytest -pno:randomly"],
])
def test_a_blocked_pytest_child_inside_a_shell_command_is_accepted(argv):
    assert census._miss(argv, None, {}) is None, argv


# Shell text as bash runs it: the refused rows start an unblocked pytest, the
# accepted rows do not, and test_real_bash_agrees_with_every_shell_text_row runs
# each one under bash to check it.
_SHELL_REFUSED = [
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
    # `((` and `$((` whose inner paren closes on anything but `)` are subshells.
    "((pytest -q) )",
    "((pytest -q); )",
    "((pytest -q)|cat)",
    "echo $((pytest -q) )",
    # Each o or O in a shell's option bundle takes the next word as its value,
    # so the -c text is the word after those values. A nested shell is named by
    # its path, since the real-bash differential's PATH holds only stubs.
    "/bin/bash -euo pipefail -c 'pytest -q'",
    "/bin/bash -oc pipefail 'pytest -q'",
    "/bin/bash -co pipefail 'pytest -q'",
    "/bin/bash -eo pipefail -c 'pytest -q'",
    "/bin/bash +o history -c 'pytest -q'",
    "/bin/bash -eO extglob -c 'pytest -q'",
    "/bin/bash +O extglob -c 'pytest -q'",
    # A heredoc inside $( ) is read as one: a `)` or quote in its body does not
    # end the substitution, and an unquoted body's substitutions run.
    "echo $(cat <<E\n)'\nE\n); pytest -q #'",
    "x=$(cat <<E\n$(pytest -q)\nE\n)",
    # `<<` in arithmetic inside $( ) is a shift, not a heredoc.
    "x=$(echo $((1<<2))); pytest -q",
    "x=$( ((y=1<<2)); echo); pytest -q",
    "x=$(cat <<<')'); pytest -q",
]


@pytest.mark.parametrize("text", _SHELL_REFUSED)
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


_SHELL_ACCEPTED = [
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
    # Arithmetic whose inner parens close early is still arithmetic.
    "((pytest = 1))",
    "(( (pytest) + (1) ))",
    "echo $(( (pytest) + (1) ))",
    "echo $((1+(pytest*3)))",
    # A bundled option's value does not hide a blocked -c text.
    "/bin/bash -euo pipefail -c 'pytest -q -p no:randomly'",
    "/bin/bash -oc pipefail 'pytest -q -p no:randomly'",
    # A heredoc body inside $( ) is data unless it holds a substitution.
    "x=$(cat <<'E'\npytest -q\nE\n)",
    "echo $(cat <<E\n)'\nE\n); pytest -q -p no:randomly #'",
    # A heredoc whose delimiter never comes takes the rest of the text, so the
    # substitution never closes: bash rejects the text and runs nothing, and
    # the census, reading it the same way, finds no command after it.
    "x=$(cat <<E\n); pytest -q",
]


@pytest.mark.parametrize("text", _SHELL_ACCEPTED)
def test_shell_text_that_runs_no_unblocked_pytest_is_accepted(text):
    assert census._miss(["bash", "-c", text], None, {}) is None, text


# Blind spot 9, pinned as listed: bash runs pytest in each, and the census does
# not see it.
_SHELL_BLIND_SPOTS = [
    "$(echo pytest) -q",
    "echo $(( $(pytest -q) ))",
    "echo $(case x in x) pytest -q;; esac)",
    "{pytest,-q}",
    "py{,}test -q",
]
# Also in blind spot 9, but bash runs it only where the path exists, so the
# real-bash differential, whose sandbox has no /venv, does not run it.
_SHELL_BLIND_SPOTS_OUTSIDE_THE_SANDBOX = [
    "/venv/bin/py*test -q",
]


@pytest.mark.parametrize("text", _SHELL_BLIND_SPOTS + _SHELL_BLIND_SPOTS_OUTSIDE_THE_SANDBOX)
def test_a_listed_shell_blind_spot_is_accepted(text):
    assert census._miss(["bash", "-c", text], None, {}) is None, text


# A stub pytest that writes its argv, NUL-separated, to a file of its own, and a
# python that hands `-m pytest` to it and does nothing else.
_STUB_PYTEST = '#!/bin/sh\nprintf \'%s\\0\' "$@" > "$CENSUS_STUB_LOG/call.$$"\n'
_STUB_PYTHON = ('#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "pytest" ]; then\n'
                '  shift 2\n  exec pytest "$@"\nfi\n')


def _bash_runs_an_unblocked_pytest(bash, stubs, rig, text):
    """Whether bash, running `text` in `rig` with only `stubs` on PATH, started a
    pytest whose argv does not block pytest-randomly. This reads the argv as `-p
    no:randomly` or `-pno:randomly` and nothing else; the rows block no other way."""
    log = rig / "calls"
    log.mkdir()
    script = rig / "row.sh"
    script.write_text(text, encoding="utf-8")
    subprocess.run([bash, str(script)], cwd=rig, stdin=subprocess.DEVNULL,
                   env={"PATH": str(stubs), "HOME": str(rig), "CENSUS_STUB_LOG": str(log)},
                   capture_output=True, timeout=30)
    for call in log.iterdir():
        args = call.read_bytes().decode().split("\0")[:-1]
        blocked = "-pno:randomly" in args or any(
            a == "-p" and b == "no:randomly" for a, b in zip(args, args[1:]))
        if not blocked:
            return True
    return False


def test_real_bash_agrees_with_every_shell_text_row(tmp_path):
    """The shell-text rows say what bash does with each text; this runs each under
    the real bash and checks it. It covers the tables whose rows state bash's own
    behaviour: _SHELL_REFUSED (bash starts an unblocked pytest), _SHELL_ACCEPTED
    (it does not) and _SHELL_BLIND_SPOTS (it does, and the census does not see
    it). _SHELL_BLIND_SPOTS_OUTSIDE_THE_SANDBOX is a known divergence it skips:
    its glob matches a pytest only where that path exists, which the sandbox
    does not provide. The other tables with shell text are not covered: their
    rows encode a refusal the census makes on purpose where bash would run
    nothing (`printf x; make check python -m pytest`), or need a program or
    directory that is not there (`zsh`, `uv`, `cd x`).
    Each row runs in its own directory with PATH holding only a stub pytest,
    python and python3, so no real pytest, interpreter, package or network tool
    can start; a command bash cannot find just fails. bash reads each row from a
    script file, which it parses as it parses `-c` text, because the census
    leaves a script file unread (blind spot 1) and would refuse the row itself
    given as `-c` text. Reading the pipes waits for any background or coproc
    child still holding them. The rows describe bash 5.2, so the test skips
    where no bash 5.2 or later is found (macOS ships bash 3.2, which has no
    `coproc`); CI runs bash 5.2 on Linux, so a CI run fails here instead of
    skipping. It also fails unless some row really started an unblocked pytest
    and some row really did not."""
    bash = shutil.which("bash")
    version = subprocess.run(
        [bash, "-c", 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'], capture_output=True,
        text=True, timeout=30).stdout.split() if bash else []
    if [int(part) for part in version] < [5, 2]:
        reason = f"no bash 5.2 or later on PATH (found {bash} {'.'.join(version)})"
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(f"{reason}; CI runs bash 5.2, so this arm must run there")
        pytest.skip(f"{reason}; the shell-text rows describe bash 5.2")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("pytest", _STUB_PYTEST), ("python", _STUB_PYTHON),
                       ("python3", _STUB_PYTHON)):
        (stubs / name).write_text(body, encoding="utf-8")
        (stubs / name).chmod(0o755)
    rows = ([(text, True) for text in _SHELL_REFUSED]
            + [(text, False) for text in _SHELL_ACCEPTED]
            + [(text, True) for text in _SHELL_BLIND_SPOTS])
    results = []
    for index, (text, expected) in enumerate(rows):
        rig = tmp_path / f"row-{index}"
        rig.mkdir()
        results.append((text, expected, _bash_runs_an_unblocked_pytest(bash, stubs, rig, text)))
    mismatches = [(text, expected, ran) for text, expected, ran in results if ran != expected]
    assert not mismatches, (
        f"real bash disagrees with these shell-text rows (text, row says bash starts an "
        f"unblocked pytest, bash did): {mismatches}")
    assert {ran for _, _, ran in results} == {True, False}, results


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
    ["uv", "run", "-p", "python3", "python", "-m", "pip", "list"],
    ["uv", "pip", "install", "pytest"],
    ["conda", "install", "-n", "python3", "pytest"],
    ["uv", "run", "-p", "python3", "python", "script.py", "-m", "pytest"],
    ["sudo", "-u", "python", "ls"],
    ["timeout", "60", "git", "status"],
    ["timeout", "-v", "60", "echo", "python", "/venv/bin/pytest"],
    # A word that does not follow an option ends the search for the command.
    ["timeout", "bogus", "pytest", "-q"],
    # pytest's own arguments are no command, even one naming pytest.
    ["pytest", "-p", "no:randomly", "tests/pytest"],
    # A program that prints, reads or lists its words runs none of them, nor
    # an interpreter named among them.
    ["printf", "%s\\n", "python", "-m", "pytest"],
    ["echo", "python", "-m", "pytest"],
    ["echo", "python3", "-mpytest.__main__"],
    ["git", "grep", "python", "-m", "pytest"],
    ["timeout", "5", "echo", "python", "-m", "pytest"],
    ["sudo", "-u", "bob", "echo", "python", "-m", "pytest"],
    ["git", "-C", "repo", "grep", "python", "-m", "pytest"],
    # git's other options are one word, so the word after one is the subcommand.
    ["git", "--no-pager", "grep", "python", "-m", "pytest"],
    ["git", "-P", "grep", "python", "-m", "pytest"],
    ["git", "--bare", "grep", "python", "-m", "pytest"],
    ["git", "--git-dir=/x/.git", "grep", "python", "-m", "pytest"],
    ["git", "-c", "core.pager=cat", "--no-pager", "grep", "python", "-m", "pytest"],
    # pytest's numbered temp root is a directory, never pytest's program.
    ["git", "-C", "/tmp/pytest-of-ubuntu/pytest-3", "status"],
    ["rsync", "-a", "src/", "/tmp/pytest-of-ubuntu/pytest-12"],
    ["git", "init", "/tmp/pytest-of-ubuntu/pytest-7"],
    # The same, reached through a wrapper chain: git's subcommand, find's first
    # -exec command and the command builtin's command are in program position.
    ["timeout", "5", "git", "grep", "python", "-m", "pytest"],
    ["sudo", "git", "grep", "python", "-m", "pytest"],
    ["timeout", "5", "git", "-C", "repo", "grep", "python", "-m", "pytest"],
    ["find", ".", "-exec", "echo", "python", "-m", "pytest", ";"],
    ["timeout", "5", "find", ".", "-exec", "grep", "python", "-m", "pytest", ";"],
    ["command", "echo", "python", "-m", "pytest"],
    ["timeout", "5", "command", "echo", "python", "-m", "pytest"],
    ["timeout", "5", "command", "-v", "pytest"],
    # A git subcommand that runs a command runs this one, which is no launch.
    ["git", "bisect", "run", "make", "test"],
    ["git", "bisect", "run", "echo", "python", "-m", "pytest"],
    ["git", "submodule", "foreach", "git", "status"],
    ["git", "rebase", "--exec", "make check", "main"],
    # The command bisect run and submodule foreach run is in program position.
    ["git", "submodule", "foreach", "echo", "python", "-m", "pytest"],
    ["git", "submodule", "foreach", "--recursive", "git", "grep", "python", "-m", "pytest"],
    ["git", "submodule", "foreach", "git grep python -m pytest"],
    ["git", "submodule", "foreach", "timeout", "5", "echo", "python", "-m", "pytest"],
    ["timeout", "5", "git", "submodule", "foreach", "echo", "python", "-m", "pytest"],
    ["bash", "-c", "git submodule foreach echo python -m pytest"],
    # A rebase value is one word; this one only mentions pytest.
    ["git", "rebase", "-x", "echo python -m pytest", "main"],
    # A shell keyword or builtin that tests or looks up its words runs none.
    ["bash", "-c", "[[ -x /venv/bin/pytest ]] && echo ok"],
    ["type", "pytest"],
    ["hash", "pytest"],
    # A find with no -exec-family action runs nothing.
    ["find", ".", "-name", "x", "python", "-m", "pytest"],
    ["timeout", "5", "find", ".", "-name", "x", "python", "-m", "pytest"],
    # A file command touches its words and runs none of them.
    ["chmod", "755", "/venv/bin/pytest"],
    ["touch", "pytest"],
    ["mkdir", "-p", "pytest"],
    ["rm", "-rf", "pytest"],
    ["cp", "-r", "/x", "pytest"],
    ["bash", "-c", "printf '%s\\n' python -m pytest"],
    # A program that reads or lists its words runs none of them.
    ["grep", "-rn", "pytest", "."],
    ["rg", "-n", "pytest", "tests"],
    ["grep", "-c", "pytest", "file.txt"],
    ["ls", "-la", "pytest"],
    ["bash", "-c", "grep -q pytest log && echo ok"],
    ["sed", "-n", "/pytest/p", "log"],
    ["test", "-x", "/venv/bin/pytest"],
    ["timeout", "5", "cat", "-n", "pytest"],
    # find runs only the words after its -exec actions.
    ["find", ".", "-name", "pytest"],
    ["find", ".", "-name", "*.py", "-exec", "grep", "-l", "pytest", "{}", ";"],
    # A $'...' word that only mentions pytest.
    ["bash", "-c", "echo $'python -m py\\x74est'"],
    # A substitution holding $'...' ends where bash ends it, so the words after
    # it stay inside the double quotes; a $'...' heredoc delimiter is quoted.
    ["bash", "-c", "echo \"$(echo $'it\\'s') python -m pytest\""],
    ["bash", "-c", "cat <<$'EOF'\n$(pytest -q)\nEOF"],
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
    # A launcher tag or architecture is one option: its m or c is no option letter.
    ["py", "-V:ContinuumAnalytics/3.12", "-m", "pytest"],
    ["py", "-V:Anaconda3/3.12", "-m", "pytest"],
    ["pyw", "-V:PythonCore/3.12", "-m", "pytest"],
    ["py", "-3.12-arm64", "-m", "pytest"],
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
    ["/usr/bin/pytest-3"],
    ["pytest.exe", "-q"],
    # The same forms inside a shell command.
    ["bash", "-c", "py -3 -m pytest"],
    # bash's $'...' quoting builds the word it names, escapes included, and
    # $"..." is a double-quoted string.
    ["bash", "-c", "$'pytest' -q"],
    ["bash", "-c", "$'py\\x74est' -q"],
    ["bash", "-c", "$'py\\164est' -q"],
    ["bash", "-c", "$'py\\u0074est' -q"],
    ["bash", "-c", "python3 -m $'py\\x74est'"],
    ["bash", "-c", '$"pytest" -q'],
    # An escaped quote inside $'...' does not end it.
    ["bash", "-c", "echo $'it\\'s # x'; pytest -q"],
    # So does one inside a substitution body or arithmetic. A backtick body ends
    # at its first unescaped backtick whatever the quotes, as in bash.
    ["bash", "-c", "echo \"a $(echo $'it\\'s') b\"; pytest -q"],
    ["bash", "-c", "echo \"a `echo $'it\\'s'` b\"; pytest -q"],
    ["bash", "-c", "(( $'a\\'' )); pytest -q"],
    # A $'...' or $"..." heredoc delimiter is the word bash builds from it.
    ["bash", "-c", "cat <<$'EOF'\nbody\nEOF\npytest -q"],
    ["bash", "-c", "cat <<$\"EOF\"\nbody\nEOF\npytest -q"],
    ["bash", "-c", "cat <<$'E\\'F'\nbody\nE'F\npytest -q"],
    ["bash", "-c", "cat <<$'E\\x4fF'\nbody\nEOF\npytest -q"],
    # An interpreter in the program position, after an assignment or in a shell,
    # running pytest's script.
    ["A=1", "python3", "/venv/bin/pytest"],
    ["bash", "-c", "python3 /venv/bin/pytest"],
    # A tool runner's version pin on pytest's program.
    ["pytest@8.3.0", "-q"],
    # Interpreters other than CPython and PyPy that run -m pytest.
    ["ipython", "-m", "pytest"],
    ["ipython3", "-m", "pytest", "--", "-q"],
    ["graalpy", "-m", "pytest"],
    ["pyston3", "-m", "pytest"],
    # Any word naming an interpreter is read for -m pytest, not only the first.
    ["make", "check", "python3", "python", "-m", "pytest"],
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
    ["py", "-V:Anaconda3/3.12", "script.py", "-m", "pytest"],
    ["py", "-3.12-arm64", "-c", "print(1)", "-m", "pytest"],
    ["py", "--list"],
    ["uvx", "ruff", "check"],
    ["pipx", "run", "black", "."],
    ["hatch", "run", "lint:ruff"],
    ["hatch", "run", "+py=3.12", "lint:ruff"],
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
    ["echo", "python-config", "-m", "pytest"],
    ["bash", "-c", "echo python-config -m pytest"],
    ["echo", "pythonista", "-m", "pytest"],
    ["pypy-helper", "-m", "pytest.__main__"],
])
def test_a_neighbouring_form_that_launches_no_pytest_is_ignored(argv):
    assert census._miss(argv, None, {}) is None, argv


# A PROGRAM THE CENSUS DOES NOT READ. Each row starts pytest, or may, as the
# command of a program none of the families reads, so it is refused whether or
# not it blocks pytest-randomly: the census cannot tell what environment and
# argv that program passes on.
_WRAPPED = [
    ["timeout", "60", PY, "-m", "pytest"],
    ["timeout", "-s", "KILL", "1.5m", PY, "-m", "pytest"],
    ["timeout", "-k", "inf", "60", "pytest"],
    ["nice", "-n", "10", PY, "-m", "pytest"],
    ["nice", "-n", "-5", "pytest"],
    ["nohup", PY, "-m", "pytest"],
    ["stdbuf", "-oL", PY, "-m", "pytest"],
    ["xvfb-run", "-s", "+extension GLX", "pytest"],
    ["chrt", "-f", "10", PY, "-m", "pytest"],
    ["time", "pytest"],
    ["env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "pytest"],
    ["env", "-i", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "pytest"],
    ["env", "-", "pytest"],
    ["/usr/bin/env", "-u", "HOME", "pytest"],
    ["sudo", "pytest"],
    ["sudo", "--", "pytest"],
    ["sudo", "-E", "python3", "-m", "pytest"],
    # A word after an option may be its value, so the word after it is tried.
    ["sudo", "-u", "bob", "pytest"],
    ["sudo", "-u", "python", "-E", "python", "-m", "pytest"],
    ["uv", "run", "-p", "python3", "python", "-m", "pytest"],
    ["uv", "run", "--made-up-option", "python3", "python", "/venv/bin/pytest"],
    ["conda", "run", "-n", "python3", "python", "-m", "pytest"],
    # Refused although no pytest starts: `-v` takes no value and `-m` is
    # python-config's own option, and the census cannot tell.
    ["uv", "run", "-v", "echo", "python", "/venv/bin/pytest"],
    ["python-config", "-m", "pytest"],
    # A subcommand verb, after the program's options or not.
    ["uv", "run", "pytest"],
    ["/usr/local/bin/uv", "run", "--with", "x", "pytest", "-q"],
    ["uv", "tool", "run", "pytest@8.3.0"],
    ["poetry", "run", "python", "-m", "pytest"],
    ["hatch", "run", "test:pytest"],
    ["hatch", "-e", "test", "run", "pytest"],
    # A matrix selector or exclusion before the command, and the env:script prefix.
    ["hatch", "run", "+py=3.12", "test:pytest"],
    ["hatch", "run", "+py=3.12", "+feature=x", "pytest"],
    ["hatch", "run", "-py=3.11", "test:pytest"],
    ["hatch", "run", "-py=3.11", "+py=3.12", "python", "-m", "pytest"],
    ["conda", "-v", "run", "pytest"],
    ["pipx", "run", "pytest"],
    # A package script may start pytest.
    ["npm", "run", "pytest"],
    ["uvx", "pytest"],
    ["/home/u/.local/bin/uv", "run", "coverage", "run", "-m", "pytest"],
    ["timeout", "60", "python3", "-m", "coverage", "run", "--parallel-mode", "-m", "pytest"],
    # A chain of listed wrappers is followed to the program it starts.
    ["timeout", "60", "nice", "-n", "5", "pytest"],
    ["sudo", "env", "pytest"],
    ["/usr/bin/nohup", "uv", "run", "python", "-m", "pytest"],
    ["timeout", "60", "sudo", "-E", "env", "A=1", "pytest"],
    ["nohup", "timeout", "5", "uv", "run", "python", "-m", "pytest"],
    ["sudo", "-u", "bob", "nice", "pytest"],
    # An interpreter named only by the [module] family's list, behind a wrapper.
    ["sudo", "py", "-3", "-m", "pytest"],
    ["sudo", "graalpy", "-m", "pytest"],
    ["timeout", "5", "pyston3", "-m", "pytest"],
    ["bash", "-c", "timeout 5 nice -n 1 pytest -q"],
    # A dispatcher's subcommand, the command builtin's command and a later find
    # action are judged through a chain as without one.
    ["timeout", "5", "git", "pytest"],
    # git's own options are stepped over, a value with the option it follows.
    ["git", "-C", "repo", "pytest"],
    ["git", "-P", "pytest"],
    ["git", "-c", "x=1", "pytest"],
    ["git", "--git-dir", "/x/.git", "pytest"],
    ["git", "--no-pager", "bisect", "run", "pytest"],
    ["git", "-C", "/tmp/pytest-of-ubuntu/pytest-3", "bisect", "run", "pytest"],
    ["timeout", "5", "command", "pytest"],
    ["timeout", "5", "find", ".", "-exec", "pytest", "{}", ";"],
    ["find", ".", "-exec", "echo", "x", ";", "-exec", "python", "-m", "pytest", "{}", ";"],
    # A git subcommand that runs a command, bare, chained and in shell text.
    ["git", "bisect", "run", "pytest"],
    ["git", "bisect", "run", "timeout", "5", "python", "-m", "pytest"],
    ["timeout", "5", "git", "bisect", "run", "pytest"],
    ["bash", "-c", "git bisect run pytest -q"],
    ["git", "submodule", "foreach", "pytest -q"],
    ["git", "submodule", "--quiet", "foreach", "--recursive", "python", "-m", "pytest"],
    ["git", "rebase", "--exec", "pytest -q", "main"],
    ["git", "rebase", "-x", "python -m pytest", "main"],
    ["git", "rebase", "--exec=pytest", "main"],
    ["git", "rebase", "-xpytest", "main"],
    # foreach's own command that starts pytest, through a chain.
    ["git", "submodule", "foreach", "timeout", "5", "pytest"],
    # Programs that run their arguments stay refused.
    ["xargs", "-n1", "pytest"],
    ["xargs", "-I", "{}", "python3", "-m", "pytest", "{}"],
    ["find", ".", "-name", "x", "-exec", "pytest", "{}", ";"],
    ["timeout", "5", "find", "tests", "-execdir", "python3", "-m", "pytest", "{}", "+"],
    ["bash", "-c", "find . -ok timeout 5 pytest {} \\;"],
    # A shell's -c text behind such a program, and such a program inside one.
    ["timeout", "60", "bash", "-c", "cd x && pytest"],
    ["nohup", "sh", "-c", "cd x && python3 -m pytest"],
    ["sudo", "bash", "-c", "timeout 5 pytest -q"],
    ["bash", "-c", "uv run pytest -q"],
    ["bash", "-c", "exec pytest -q"],
    ["bash", "-c", "time pytest -q"],
    ["bash", "-c", "exec -a name pytest -q"],
]


# Blind spot 8, pinned as listed: a chain through a program the wrapper list
# does not name stops at that program.
@pytest.mark.parametrize("argv", [
    ["timeout", "60", "chrt", "-f", "1", "pytest"],
    ["sudo", "taskset", "1", "pytest"],
])
def test_a_listed_wrapper_blind_spot_is_accepted(argv):
    assert census._miss(argv, None, {}) is None, argv


def test_the_words_after_a_rebase_value_are_read_as_a_mention():
    """`-x` takes one word, so `python -m pytest` after it are arguments of git,
    a program the census does not read: the [mention] family judges them, unblocked
    refused and blocked accepted, as for `make check python -m pytest`."""
    argv = ["git", "rebase", "-x", "echo", "python", "-m", "pytest"]
    line = census._miss(argv, None, {})
    assert line is not None and line.endswith(census.REMEDY), line
    assert census._miss([*argv, *_BLOCK], None, {}) is None


# Rows whose pytest is followed by words of the program that runs it (a rebase
# value then rebase's own arguments, a find action then its `{}` and
# terminator): the block goes where that pytest receives it, not on the end.
_BLOCKED_FORMS = {
    ("git", "rebase", "--exec", "pytest -q", "main"):
        ["git", "rebase", "--exec", "pytest -q -p no:randomly", "main"],
    ("git", "rebase", "-x", "python -m pytest", "main"):
        ["git", "rebase", "-x", "python -m pytest -p no:randomly", "main"],
    ("git", "rebase", "--exec=pytest", "main"):
        ["git", "rebase", "--exec=pytest -p no:randomly", "main"],
    ("git", "rebase", "-xpytest", "main"):
        ["git", "rebase", "-xpytest -p no:randomly", "main"],
    ("timeout", "5", "find", ".", "-exec", "pytest", "{}", ";"):
        ["timeout", "5", "find", ".", "-exec", "pytest", *_BLOCK, "{}", ";"],
    ("find", ".", "-exec", "echo", "x", ";", "-exec", "python", "-m", "pytest", "{}", ";"):
        ["find", ".", "-exec", "echo", "x", ";", "-exec", "python", "-m", "pytest", *_BLOCK,
         "{}", ";"],
    ("find", ".", "-name", "x", "-exec", "pytest", "{}", ";"):
        ["find", ".", "-name", "x", "-exec", "pytest", *_BLOCK, "{}", ";"],
    ("timeout", "5", "find", "tests", "-execdir", "python3", "-m", "pytest", "{}", "+"):
        ["timeout", "5", "find", "tests", "-execdir", "python3", "-m", "pytest", *_BLOCK,
         "{}", "+"],
    ("bash", "-c", "find . -ok timeout 5 pytest {} \\;"):
        ["bash", "-c", "find . -ok timeout 5 pytest -p no:randomly {} \\;"],
}


def _with_block(argv):
    """The row with pytest-randomly blocked where its pytest receives it: the
    written form for a row in _BLOCKED_FORMS, else at the end of the shell text
    when its last word is one, else at the end of the argv."""
    if tuple(argv) in _BLOCKED_FORMS:
        return _BLOCKED_FORMS[tuple(argv)]
    if " " in argv[-1]:
        return [*argv[:-1], argv[-1] + " -p no:randomly"]
    return [*argv, *_BLOCK]


def test_each_find_and_rebase_row_is_blocked_inside_its_command():
    """A block appended after a rebase value or a find action lands in git's or
    find's arguments and blocks nothing, so the blocked half of the refusal
    test would test no block. For every row that runs pytest through find or
    git rebase, the block follows the pytest word, inside that command."""
    rows = [argv for argv in _WRAPPED
            if {"find", "rebase"} & set(" ".join(argv).split())]
    assert len(rows) == len(_BLOCKED_FORMS), rows
    for argv in rows:
        blocked = " ".join(_with_block(argv))
        assert re.search(r"pytest( -q)? -p no:randomly", blocked), (argv, blocked)
        assert not blocked.endswith("-p no:randomly"), (argv, blocked)


@pytest.mark.parametrize("argv", _WRAPPED)
def test_a_launch_through_a_program_the_census_does_not_read_is_refused(argv):
    for launch in (argv, _with_block(argv)):
        line = census._miss(launch, "/rig", {})
        assert line is not None and line.endswith(census.WRAPPER_REMEDY), launch


def test_the_wrapper_refusal_names_the_program_and_the_remedy(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t.py::x (call)")
    line = census._miss(["/usr/bin/timeout", "60", "python3", "-m", "pytest", *_BLOCK],
                        "/rig", {})
    assert line == (
        "t.py::x (call): pytest child [/usr/bin/timeout 60 python3 -m pytest -p no:randomly]"
        " (cwd /rig) starts through /usr/bin/timeout, which the census does not read;"
        " launch pytest directly, as in sys.executable -m pytest -p no:randomly,"
        " or teach the census this program"), line


def test_a_blocked_launch_through_such_a_program_is_refused_before_it_starts(census_state):
    before = len(census_state["misses"])
    with pytest.raises(census.ChildPytestCensusError) as caught:
        subprocess.run(["timeout", "60", PY, "-m", "pytest", "--version", *_BLOCK],
                       capture_output=True, timeout=60)
    message = str(caught.value)
    assert "starts through timeout" in message and census.WRAPPER_REMEDY in message, message
    assert census_state["misses"][before:] == [message]


# THE ENUMERATED FAMILIES. The census docstring lists the launch families it
# refuses, each under a [label]; this map holds at least one refused row per
# label, and the two sets must be equal, so neither can change alone.
_FAMILY_ROWS = {
    "program": [["pytest"], ["/venv/bin/py.test", "-q"], ["pytest-3"]],
    "coverage": [["coverage", "run", "-m", "pytest"], ["coverage", "run", "/venv/bin/pytest"]],
    "module": [[PY, "-m", "pytest"], ["py", "-3", "-m", "pytest.__main__"],
               ["ipython", "-m", "pytest"], [PY, "-m", "coverage", "run", "-m", "pytest"]],
    "mention": [["make", "check", "python", "-m", "pytest"],
                ["make", "test", "python", "-m", "pytest"],
                ["tox", "devenv", "python3", "-mpytest.__main__"]],
    "script": [[PY, "/venv/bin/pytest"], [PY, "-I", "/x/pytest/__main__.py"]],
    "wrapper": [["timeout", "60", "pytest"], ["uvx", "pytest@8.3.0"], ["sudo", "pytest", *_BLOCK]],
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


def test_the_lock_pins_the_versions_the_census_replays():
    """The lock, not this interpreter, is the reference: CI installs it, and the
    CI environment binding fails a CI run whose installed versions differ."""
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_census_verify_ci_environment", root / "scripts" / "verify_ci_environment.py")
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    pins = verify.read_pins(root / "constraints-ci.txt")
    pinned = {name: ".".join(pins[name].split(".")[:2]) for name in census.REPLAYED_VERSIONS}
    assert pinned == census.REPLAYED_VERSIONS, (
        f"constraints-ci.txt pins {pinned}, but the census replays the plugin loading of "
        f"{census.REPLAYED_VERSIONS}. Re-run the rows of "
        "test_each_route_pytest_loads_or_blocks_the_plugin_by against the pinned versions, "
        "re-check _RANDOMLY_NAMES and the census docstring, then update REPLAYED_VERSIONS.")


def test_the_census_is_live_and_refuses_before_the_child_starts(tmp_path, census_state):
    assert census.installed(), "pact-plugin/conftest.py did not install the census"
    before = len(census_state["misses"])
    with pytest.raises(census.ChildPytestCensusError) as caught:
        subprocess.run([PY, "-m", "pytest", "--version"], cwd=tmp_path, env=_child_env(),
                       capture_output=True, timeout=60)
    message = str(caught.value)
    assert census.REMEDY in message and str(tmp_path) in message, message
    assert "test_the_census_is_live" in message, message
    assert census_state["misses"][before:] == [message]


def test_a_blocked_child_runs(tmp_path, census_state):
    before = list(census_state["misses"])
    proc = subprocess.run([PY, "-m", "pytest", "--version", *_BLOCK], cwd=tmp_path,
                          env=_child_env(), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert census_state["misses"] == before


@pytest.mark.parametrize("event, args", [
    # Popen's executable= names the program; argv[0] is only the shown name.
    ("subprocess.Popen", ("/venv/bin/pytest", ["anything", "-q"], None, {})),
    ("subprocess.Popen", (PY, ["foo", "-m", "pytest"], None, {})),
    # With shell=True the executable is the shell, and its -c text is read.
    ("subprocess.Popen", ("/bin/bash", ["/bin/bash", "-c", "pytest -q"], None, {})),
    # posix_spawn's path names the program in the same way.
    ("os.posix_spawn", ("/venv/bin/pytest", ["shown", "-q"], {})),
])
def test_the_program_a_launch_runs_is_judged_not_its_shown_name(census_state, event, args):
    with pytest.raises(census.ChildPytestCensusError):
        census._audit(event, args)


@pytest.mark.parametrize("event, args", [
    ("subprocess.Popen", (PY, ["foo", "-m", "pytest", "-p", "no:randomly"], None, {})),
    ("subprocess.Popen", ("/usr/bin/true", ["pytest", "-q"], None, {})),
    ("subprocess.Popen", ("/bin/bash", ["/bin/bash", "-c", "pytest -q -p no:randomly"],
                          None, {})),
    ("os.posix_spawn", ("/usr/bin/true", ["pytest", "-q"], {})),
])
def test_a_launch_whose_program_blocks_or_is_no_pytest_is_accepted(census_state, event, args):
    before = list(census_state["misses"])
    census._audit(event, args)
    assert census_state["misses"] == before


def test_an_unreadable_launch_whose_executable_is_pytest_fails_by_name(census_state):
    before = len(census_state["misses"])
    census._audit("subprocess.Popen", ("/venv/bin/pytest", ["anything", 1], None, None))
    added = census_state["misses"][before:]
    assert len(added) == 1 and "could not inspect a pytest launch" in added[0], added


def test_a_live_launch_naming_pytest_as_its_executable_is_refused(tmp_path, census_state):
    before = len(census_state["misses"])
    with pytest.raises(census.ChildPytestCensusError) as caught:
        subprocess.run(["anything", "-q"], executable=str(tmp_path / "bin" / "pytest"),
                       cwd=tmp_path, capture_output=True, timeout=60)
    assert census_state["misses"][before:] == [str(caught.value)]


def test_a_live_blocked_launch_through_an_executable_runs(tmp_path, census_state):
    # The shown name sits beside the interpreter, which finds its environment
    # from argv[0]; the census judges the executable, not this name.
    shown = str(Path(PY).with_name("shown-name"))
    before = list(census_state["misses"])
    proc = subprocess.run([shown, "-m", "pytest", "--version", *_BLOCK], executable=PY,
                          cwd=tmp_path, env=_child_env(), capture_output=True, text=True,
                          timeout=60)
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
    ([PY, "-mpytest", 1], None),
    ([PY, "-I", "-m", "pytest.__main__", 1], None),
    # A short-option cluster ending in m is the interpreter's -m.
    ([PY, "-Im", "pytest.__main__", 1], None),
    ([PY, "-Impytest.__main__", 1], None),
    (["coverage", "run", "--module", "pytest", 1], None),
    (["coverage", "run", "-m", "pytest.__main__", 1], None),
    (["bash", "-c", "git status; python3 -Im pytest.__main__", 1], None),
    # The word pytest is pytest's program name wherever it stands, so this
    # fails closed although the -am is git's.
    (["git", "commit", "-am", "pytest", 1], None),
    (["bash", "-c", b"cd x && pytest -q", 1], None),
    # Operators written against pytest's name, as the shell reader splits them.
    (["bash", "-c", "cd x&&pytest -q", 1], None),
    (["bash", "-c", "pytest;echo", 1], None),
    (["bash", "-c", "pytest|tee log", 1], None),
    (["bash", "-c", "(pytest)", 1], None),
    (["bash", "-c", "echo `pytest -q`", 1], None),
    (["bash", "-c", "echo $(pytest -q)", 1], None),
    (["bash", "-c", "cd x;pytest -q 'unclosed", 1], None),
    (["bash", "-c", "$'py\\x74est' -q", 1], None),
    (["py.test", "-q"], {"A": 1}),
    ([PY, "/venv/lib/python3.12/site-packages/pytest/__main__.py"], {"A": 1}),
])
def test_a_pytest_launch_the_census_cannot_read_fails_the_run_by_name(
        census_state, argv, env):
    before = len(census_state["misses"])
    census._audit("subprocess.Popen", (None, argv, None, env))
    added = census_state["misses"][before:]
    assert len(added) == 1, added
    assert "could not inspect a pytest launch" in added[0], added
    assert "test_a_pytest_launch_the_census_cannot_read" in added[0], added


@pytest.mark.parametrize("argv, env", [
    # A tmp_path lives under /tmp/pytest-of-<user>/; the word only contains pytest.
    (["/usr/bin/python3", "/tmp/pytest-of-ubuntu/pytest-7/test_x0/script.py"],
     {"HOME": "/h", "N": 1}),
    (["git", "-C", "/tmp/pytest-of-u/pytest-1/repo", "status", 1], None),
    (["/usr/bin/python3", "/tmp/pytest-of-ubuntu/pytest-3", 1], None),
    (["rsync", "-a", "src/", "/tmp/pytest-of-ubuntu/pytest-12", 1], None),
    (["pip", "install", "pytest-xdist", 1], None),
])
def test_a_malformed_launch_that_only_contains_pytest_is_left_to_its_launcher(
        census_state, argv, env):
    before = list(census_state["misses"])
    census._audit("subprocess.Popen", (None, argv, None, env))
    assert census_state["misses"] == before


@pytest.mark.parametrize("argv", [
    # Only an interpreter or coverage takes -m; here the -m, the cluster
    # ending in m and --module are git's.
    ["git", "commit", "-m", "pytest.__main__", 1],
    ["git", "commit", "-am", "pytest.__main__", 1],
    ["git", "commit", "-ampytest", 1],
    ["git", "commit", "-ampytest.__main__", 1],
    ["git", "commit", "--module", "pytest.__main__", 1],
    # A shell operator ends the interpreter's command.
    ["bash", "-c", "python3 -V; git commit -am pytest.__main__", 1],
])
def test_a_malformed_launch_whose_m_belongs_to_no_interpreter_is_left_to_its_launcher(
        census_state, argv):
    before = list(census_state["misses"])
    census._audit("subprocess.Popen", (None, argv, None, None))
    assert census_state["misses"] == before


def test_a_well_formed_git_commit_am_pytest_is_not_a_pytest_launch(census_state):
    before = list(census_state["misses"])
    census._audit("subprocess.Popen", (None, ["git", "commit", "-am", "pytest"], None, None))
    assert census_state["misses"] == before


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


def test_an_identical_refusal_is_counted_once(census_state):
    """The header counts distinct refusal lines, as it says."""
    line = "t::a: pytest child [x] ...; " + census.REMEDY
    census_state["misses"][:] = []
    census_state["forwarded"][:] = []
    census._record(line)
    census._record(line)
    reporter = _Reporter()
    census.finish(_Session(_Config(reporter)))
    assert reporter.lines == [
        "CHILD PYTEST CENSUS: 1 distinct pytest child(ren) started without blocking "
        "pytest-randomly", "  " + line], reporter.lines


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


# A rig session loads tests/conftest.py and registers the child-pytest census as
# pact-plugin/conftest.py does, and refuses to run unless exactly one census is
# installed, so no rig arm that expects no miss can pass with none. It copies
# the root conftest's census registration rather than loading that conftest,
# so its arms isolate the census from the seed gate, the HOME census and both
# CI plugins. The copy is pinned to the real registration by
# test_the_rig_registers_the_census_as_the_root_conftest_does, and the refusal
# path under xdist through the real root conftest, with those plugins loaded
# and the CI ones inert, is run by
# test_under_xdist_the_real_root_conftest_fails_a_swallowed_miss.
_RIG_CONFTEST = '''
import importlib.util

import pytest

_spec = importlib.util.spec_from_file_location("pact_suite_conftest", {conftest!r})
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)

import child_pytest_census  # noqa: E402  (tests/ is on sys.path once the suite conftest ran)


def pytest_configure(config):
    _suite.pytest_configure(config)
    child_pytest_census.register(config)
    plugins = [p for p in config.pluginmanager.get_plugins()
               if isinstance(p, child_pytest_census.Plugin)]
    if not child_pytest_census.installed() or len(plugins) != 1:
        raise pytest.UsageError("rig: expected one installed child-pytest census, found "
                                "%d plugin(s)" % len(plugins))


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


def _census_registration(source):
    """(whether `source` imports child_pytest_census, the census register calls
    its pytest_configure makes, as source text)."""
    tree = ast.parse(source)
    imports = any(isinstance(node, ast.Import)
                  and any(alias.name == "child_pytest_census" and alias.asname is None
                          for alias in node.names)
                  for node in ast.walk(tree))
    calls = [ast.unparse(node)
             for function in tree.body
             if isinstance(function, ast.FunctionDef) and function.name == "pytest_configure"
             for node in ast.walk(function)
             if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "child_pytest_census.register"]
    return imports, calls


def test_the_rig_registers_the_census_as_the_root_conftest_does():
    """The xdist rig copies the root conftest's census wiring (see _RIG_CONFTEST):
    both import child_pytest_census and call its register(config) exactly once
    from pytest_configure, so the rig exercises the real install path."""
    root = (Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8")
    expected = (True, ["child_pytest_census.register(config)"])
    assert _census_registration(root) == expected, _census_registration(root)
    rig = _RIG_CONFTEST.format(conftest="conftest.py")
    assert _census_registration(rig) == expected, _census_registration(rig)


def _run_rig(tmp_path, flag):
    (tmp_path / "conftest.py").write_text(
        _RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (tmp_path / "test_swallow.py").write_text(
        _RIG_TESTS.format(flag=flag), encoding="utf-8")
    env = _child_env()
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
    # The report starts its own line, not glued onto xdist's progress line.
    assert re.search(r"^CHILD PYTEST CENSUS: 1 distinct pytest child\(ren\) started without blocking "
                     r"pytest-randomly$", output, re.M), output
    assert "test_swallows_the_census_error" in output and census.REMEDY in output, output


def test_under_xdist_a_blocked_child_passes_the_run(tmp_path):
    rc, output = _run_rig(tmp_path, ', "-p", "no:randomly"')
    assert "2 passed" in output, output
    assert rc == 0, output
    assert "CHILD PYTEST CENSUS" not in output, output


# A number or duration before the command, in every spelling coreutils 9.4
# timeout runs its command after, is stepped over.
@pytest.mark.parametrize("duration", [
    "60", ".5", "5.", "inf", "INF", "infinity", "1e1", "1E1", "0x5", "0X5", "0x1p3", "0x.8",
    "2s", "1m", "0.01h", "1e-2d", "+5",
])
def test_each_number_before_the_command_is_stepped_over(duration):
    for launch in (["timeout", duration, "pytest", "-q"], ["timeout", duration, "pytest", *_BLOCK]):
        line = census._miss(launch, None, {})
        assert line is not None and line.endswith(census.WRAPPER_REMEDY), launch


_SKILLS_TEST = "skills/pact-handoff-harvest/test_skill_loading_harvest.py"

# A plugin, not a test file: a test file outside the plugin tree would make the
# child collect from the common ancestor of the two paths. At the end of the
# session it starts one pytest child that does not block pytest-randomly.
_UNBLOCKED_PROBE = """
import os
import subprocess
import sys

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session):
    try:
        subprocess.run([sys.executable, "-m", "pytest", "--version"],
                       capture_output=True, timeout=60)
        outcome = "ran"
    except Exception as exc:
        outcome = "refused: %r" % (exc,)
    with open(os.environ["CHILD_CENSUS_PROBE_MARKER"], "w", encoding="utf-8") as handle:
        handle.write(outcome)
"""


@pytest.mark.parametrize("paths", [(_SKILLS_TEST,),
                                   (_SKILLS_TEST, "tests/test_session_home_sandbox.py")],
                         ids=["skills-alone", "skills-and-tests"])
def test_a_session_with_or_without_the_tests_conftest_refuses_an_unblocked_child(
        tmp_path, paths):
    """A real session over a skills/ test alone loads pact-plugin/conftest.py but
    not tests/conftest.py, and its twin loads both. Each installs one census:
    a pytest child that does not block pytest-randomly is refused before it
    starts, and the session fails with one census report."""
    plugin_root = Path(__file__).resolve().parents[1]
    if not (plugin_root / _SKILLS_TEST).is_file():
        pytest.skip("SKIPPED, NOT PASSED: %s is not in this tree" % _SKILLS_TEST)
    (tmp_path / "child_census_launch_probe.py").write_text(_UNBLOCKED_PROBE, encoding="utf-8")
    marker = tmp_path / "outcome"
    env = _child_env()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tmp_path), env.get("PYTHONPATH")]))
    env["CHILD_CENSUS_PROBE_MARKER"] = str(marker)
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *_BLOCK,
         "-p", "child_census_launch_probe", *paths],
        cwd=plugin_root, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    assert marker.read_text(encoding="utf-8").startswith(
        "refused: ChildPytestCensusError"), output
    assert output.count("CHILD PYTEST CENSUS: 1 ") == 1, output
    assert proc.returncode == 1, output


# The xdist twin of the arm above, through the real pact-plugin/conftest.py and
# tests/conftest.py rather than _RIG_CONFTEST. A plugin, not a test file, for the
# reason given at _UNBLOCKED_PROBE. It launches one pytest child while one named
# test runs, swallows whatever the census raises, and lets the test pass.
_XDIST_TARGET = "tests/test_session_home_sandbox.py::test_the_session_home_is_not_the_real_home"
_XDIST_PROBE = """
import os
import subprocess
import sys

import pytest


def pytest_runtest_call(item):
    # The node id is relative to the rootdir, which may sit above the plugin root.
    target = os.environ["CHILD_CENSUS_PROBE_TARGET"]
    if item.nodeid != target and not item.nodeid.endswith("/" + target):
        return
    argv = [sys.executable, "-m", "pytest", "--version"]
    if os.environ.get("CHILD_CENSUS_PROBE_BLOCK"):
        argv += ["-p", "no:randomly"]
    try:
        subprocess.run(argv, capture_output=True, timeout=60)
        outcome = "ran"
    except Exception as exc:
        outcome = "refused: %r" % (exc,)
    with open(os.environ["CHILD_CENSUS_PROBE_MARKER"], "w", encoding="utf-8") as handle:
        handle.write("%s %s" % (os.environ.get("PYTEST_XDIST_WORKER", "-"), outcome))
"""


@pytest.mark.parametrize("blocked", [False, True], ids=["unblocked", "blocked"])
def test_under_xdist_the_real_root_conftest_fails_a_swallowed_miss(tmp_path, blocked):
    """A real `-n 2` session at the plugin root loads pact-plugin/conftest.py and
    tests/conftest.py as the suite does. One named test launches an unblocked
    pytest child on its worker and swallows the refusal: the worker hands the
    miss to the controller, which fails the session with one census report
    naming that test. Blocked, the same child runs and the session passes.
    GITHUB_* is removed so the CI environment binding and the full-run recorder,
    both gated on GITHUB_ACTIONS, stay inert; the census reads no GITHUB_ name
    and the root conftest registers it unconditionally."""
    pytest.importorskip("xdist")
    plugin_root = Path(__file__).resolve().parents[1]
    (tmp_path / "child_census_xdist_probe.py").write_text(_XDIST_PROBE, encoding="utf-8")
    marker = tmp_path / "outcome"
    env = {key: value for key, value in _child_env().items() if not key.startswith("GITHUB_")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tmp_path), env.get("PYTHONPATH")]))
    env["CHILD_CENSUS_PROBE_MARKER"] = str(marker)
    env["CHILD_CENSUS_PROBE_TARGET"] = _XDIST_TARGET
    if blocked:
        env["CHILD_CENSUS_PROBE_BLOCK"] = "1"
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *_BLOCK, "-n", "2",
         "-p", "child_census_xdist_probe", _XDIST_TARGET.split("::")[0]],
        cwd=plugin_root, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    worker, _, outcome = marker.read_text(encoding="utf-8").partition(" ")
    assert worker.startswith("gw"), (worker, output)  # the launch ran on a worker
    if blocked:
        assert outcome == "ran", output
        assert proc.returncode == 0, output
        assert "CHILD PYTEST CENSUS" not in output, output
    else:
        assert outcome.startswith("refused: ChildPytestCensusError"), output
        assert proc.returncode == 1, output
        assert output.count("CHILD PYTEST CENSUS: 1 ") == 1, output
        lines = [line for line in output.splitlines() if "pytest child [" in line]
        assert len(lines) == 1 and _XDIST_TARGET in lines[0], lines
        assert ("[%s] " % worker) in lines[0], lines
