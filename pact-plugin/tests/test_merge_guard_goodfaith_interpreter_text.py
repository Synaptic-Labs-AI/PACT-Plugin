"""Good-faith interpreter commands that NAME a guarded operation without running it.

Each command a lead or teammate plausibly types: printing, searching or
testing for the text of a merge. None runs one. The danger scan decides on
`is_dangerous_command`, the predicate the merge-guard corpus certification
replays.

ALLOW_FREED pins shapes the guard allows: text that is only printed, searched
or passed as data. ALLOW_WORDS pins that a primitive name is matched as a call
outside string literals, never as a substring: "empty" holds `pty`,
"filesystem" holds `system`, "executable" holds `exec`, and a Python backtick
or `|` is not a process route at all. MUST_DENY bounds both sets: each runs
the guarded command through a real primitive and must stay refused.
"""

from __future__ import annotations

import pytest
from merge_guard_pre import is_dangerous_command

_M = "gh pr " + "merge"
_B = "`"

ALLOW_FREED = [
    pytest.param(f"python3 -c \"print('{_M} 5')\"", id="python-print"),
    pytest.param(f"python3 -c 'import sys; print(sys.argv)' '{_M} 5'", id="python-argv-data"),
    pytest.param(f"node -e \"console.log('{_M} 5')\"", id="node-log"),
    pytest.param(f"ruby -e 'puts \"{_M} 5\"'", id="ruby-puts"),
    pytest.param(f"perl -ne 'print if /{_M}/' session.log", id="perl-grep"),
    pytest.param(f"awk '/{_M}/ {{print FILENAME\": \"$0}}' session.log", id="awk-grep"),
    pytest.param(f"python3 -m pytest -q tests/test_merge_guard.py -k '{_M}'", id="pytest-k"),
    pytest.param(f"python3 tests/probe.py --command '{_M} 5'", id="script-arg"),
    pytest.param(f"python3 - <<'EOF'\nimport subprocess\nsubprocess.run([\"git\", \"log\", \"-1\"])\n"
                 f"print(\"{_M} 5\")\nEOF", id="stdin-primitive-runs-other-command"),
    pytest.param(f"python3 - <<'EOF'\nxs = []\nprint(\"empty\" if not xs else xs, \"{_M} 5\")\nEOF",
                 id="stdin-word-holding-pty"),
]

ALLOW_WORDS = [
    pytest.param(f"python3 -c 'import re,sys; print(bool(re.compile(r\"{_M}\").search(sys.stdin.read())))'"
                 " < session.log", id="re-compile"),
    pytest.param(f"python3 -c 'xs=[]; print(\"empty\" if not xs else xs, \"{_M}\")'", id="empty-holds-pty"),
    pytest.param(f"python3 -c 'print(\"filesystem ok; next {_M} 5\")'", id="filesystem-holds-system"),
    pytest.param(f"python3 -c 'print(\"not executable yet: {_M} 5\")'", id="executable-holds-exec"),
    pytest.param(f"python3 -c 'print(\"env vars set; {_M} 5\")'", id="vars-in-a-string"),
    pytest.param(f"node -e 'console.log(\"no eval here\", \"{_M}\")'", id="eval-in-a-string"),
    pytest.param(f"python3 -c 'print(1 | 2, \"{_M}\")'", id="python-bitwise-or"),
    pytest.param(f"python3 -c 'print(\"{_B}x{_B} {_M}\")'", id="python-backtick-in-string"),
    pytest.param(f"node -e 'console.log({_B}{_M} 5{_B})'", id="node-template-literal"),
    pytest.param(f"node -e 'const n = 5; console.log({_B}{_M} ${{n}}{_B})'", id="node-template-interpolated"),
]

MUST_DENY = [
    pytest.param(f"python3 -c 'import os; os.system(\"{_M} 5\")'", id="python-os-system"),
    pytest.param("python3 -c 'import subprocess; subprocess.run([\"gh\",\"pr\",\"merge\",\"5\"])'",
                 id="python-subprocess-list"),
    pytest.param(f"node -e 'require(\"child_process\").execSync(\"{_M} 5\")'", id="node-execSync"),
    pytest.param(f"node -e 'require(\"child_process\").execSync({_B}{_M} 5{_B})'", id="node-execSync-template"),
    pytest.param(f"ruby -e '{_B}{_M} 5{_B}'", id="ruby-backticks"),
    pytest.param(f"perl -e 'qx({_M} 5)'", id="perl-qx"),
    pytest.param(f"php -r 'shell_exec(\"{_M} 5\");'", id="php-shell-exec"),
    pytest.param(f"bun -e 'await Bun.${_B}{_M} 5{_B}'", id="bun-shell-template"),
    pytest.param(f"awk 'BEGIN {{ system(\"{_M} 5\") }}'", id="awk-system"),
    pytest.param(f"python3 - <<'EOF'\nimport subprocess\nsubprocess.run('{_M} 5', shell=True)\nEOF",
                 id="stdin-subprocess-shell"),
]


# How a good-faith user starts the interpreter that runs a real merge. The
# list form has no guarded text outside the code, so only the code reader sees it.
_LIST_CODE = "import subprocess; subprocess.run([\"gh\",\"pr\",\"merge\",\"5\"])"
_SYSTEM_CODE = f"import os; os.system(\"{_M} 5\")"
LAUNCH_HEADS = [
    "python3", "/usr/bin/python3", "python3.12", "/opt/homebrew/bin/python3.13", ".venv/bin/python3",
    "env python3", "uv run python3", "uv run python", "uvx python", "poetry run python",
    "pipenv run python", "pdm run python", "hatch run python", "rye run python",
    "conda run -n base python", "pyenv exec python3", "sudo python3", "nice -n 5 python3",
    "timeout 30 python3", "command python3", "python3 -I", "python3 -u",
]
LAUNCH_GAPS = [
    pytest.param("python3 -W ignore", id="value-flag-W-before-c"),
    pytest.param("python3 -X dev", id="value-flag-X-before-c"),
    pytest.param("python3.13t", id="free-threaded-313t"),
    pytest.param("python3.14t", id="free-threaded-314t"),
]


@pytest.mark.parametrize("code", [_LIST_CODE, _SYSTEM_CODE], ids=["list", "os-system"])
@pytest.mark.parametrize("head", LAUNCH_HEADS)
def test_every_common_launch_form_refuses_a_real_merge(head, code):
    assert is_dangerous_command(f"{head} -c '{code}'") is True


@pytest.mark.parametrize("code", [_LIST_CODE, _SYSTEM_CODE], ids=["list", "os-system"])
@pytest.mark.parametrize("head", LAUNCH_GAPS)
def test_value_flag_and_free_threaded_launch_forms_are_refused(head, code):
    """A value flag (-W ignore, -X dev) placed before -c does not hide the code
    from the list-form reader, and a free-threaded build name (python3.13t) is
    an interpreter head, so both primitive forms are refused."""
    assert is_dangerous_command(f"{head} -c '{code}'") is True


# Ordinary honest Python that reaches a primitive through an import alias. The
# only declared residual is plain-variable indirection (`c = "..."; run(c)`).
_ARGV = "[\"gh\",\"pr\",\"merge\",\"5\"]"
ALIASED_IMPORTS = [
    pytest.param(f"from subprocess import run; run({_ARGV})", id="from-import-run"),
    pytest.param(f"from subprocess import call; call({_ARGV})", id="from-import-call"),
    pytest.param(f"from subprocess import Popen; Popen({_ARGV}).wait()", id="from-import-Popen"),
    pytest.param(f"from subprocess import check_output; check_output({_ARGV})", id="from-import-check_output"),
    pytest.param(f"from subprocess import check_call; check_call({_ARGV})", id="from-import-check_call"),
    pytest.param(f"from subprocess import run as r; r({_ARGV})", id="from-import-run-as"),
    pytest.param(f"import subprocess as sp; sp.run({_ARGV})", id="import-subprocess-as-sp"),
    pytest.param(f"import subprocess as sp; sp.check_output(\"{_M} 5\", shell=True)", id="import-as-shell-string"),
    pytest.param(f"from os import system; system(\"{_M} 5\")", id="from-os-import-system"),
    pytest.param(f"from os import system as s; s(\"{_M} 5\")", id="from-os-import-system-as"),
    pytest.param(f"import os as o; o.system(\"{_M} 5\")", id="import-os-as"),
]


@pytest.mark.parametrize("code", ALIASED_IMPORTS)
@pytest.mark.parametrize("form", ["c", "stdin"])
def test_an_aliased_import_running_the_command_is_refused(code, form):
    """An import alias resolves to its primitive, so a call that names neither
    `subprocess.` nor `os.system` at the call site is still refused, in -c
    code and in code fed on stdin."""
    command = (f"python3 -c '{code}'" if form == "c"
               else "python3 - <<'EOF'\n" + code.replace("; ", "\n") + "\nEOF")
    assert is_dangerous_command(command) is True


@pytest.mark.parametrize("command", ALLOW_FREED)
def test_freed_text_only_command_is_allowed(command):
    assert is_dangerous_command(command) is False


@pytest.mark.parametrize("command", ALLOW_WORDS)
def test_a_primitive_name_inside_other_text_does_not_refuse(command):
    """The -c/-e scan matches a primitive as a call outside string literals, so
    a primitive name inside a string or an ordinary word does not refuse, and a
    backtick or `|` is a process route only in a language where it runs one."""
    assert is_dangerous_command(command) is False


@pytest.mark.parametrize("command", MUST_DENY)
def test_a_real_primitive_running_the_command_is_refused(command):
    assert is_dangerous_command(command) is True
