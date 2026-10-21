"""Merge-guard classifier fixes from the PR review: each row is an honest command.

DENY rows run a guarded command and must be refused. ALLOW rows only name one, or
run something else, and must be allowed: refusing them is the cardinal failure.
"""

import pytest
from shared.merge_guard_common import is_dangerous_command

D = "gh pr " + "merge 5"  # assembled so this file carries no guarded literal


# A call laid out over several lines, as black formats it.
MULTI_LINE_DENY = [
    ("stdin list", ("python3 - <<'EOF'\nimport subprocess\nsubprocess.run(\n"
                    "    [\"gh\", \"pr\", \"merge\", \"5\", \"--squash\"],\n    check=True,\n)\nEOF")),
    ("-c list", "python3 -c 'import subprocess\nsubprocess.run(\n    [\"gh\", \"pr\", \"merge\", \"5\"],\n)'"),
    ("stdin string", f"python3 - <<'EOF'\nimport os\nos.system(\n    \"{D}\"\n)\nEOF"),
]
MULTI_LINE_ALLOW = [
    ("stdin print", f"python3 - <<'EOF'\nprint(\n    \"{D}\"\n)\nEOF"),
]


@pytest.mark.parametrize("label,cmd", MULTI_LINE_DENY, ids=[r[0] for r in MULTI_LINE_DENY])
def test_multi_line_call_running_a_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", MULTI_LINE_ALLOW, ids=[r[0] for r in MULTI_LINE_ALLOW])
def test_multi_line_call_printing_a_merge_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# An empty import list is a syntax error in Python, but it reaches the classifier in
# comments and quoted text. It must not raise: the pre-hook turns a raise into a deny.
EMPTY_IMPORT_ALLOW = [
    ("from os import ()", "python3 -c 'from os import (); print(1)'"),
    ("from subprocess import ()", "python3 -c 'from subprocess import (); print(1)'"),
    ("in a heredoc comment", f"python3 - <<'EOF'\n# from os import ()\nprint('{D}')\nEOF"),
]


@pytest.mark.parametrize("label,cmd", EMPTY_IMPORT_ALLOW, ids=[r[0] for r in EMPTY_IMPORT_ALLOW])
def test_empty_import_list_does_not_raise(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_parser_error_never_becomes_a_refusal(monkeypatch):
    """A defect in the program parsers degrades to the pre-parser behaviour instead of
    raising into the pre-hook's fail-closed deny."""
    import shared.merge_guard_common as mgc

    def boom(*_args, **_kwargs):
        raise AttributeError("parser defect")

    monkeypatch.setattr(mgc, "_program_names_primitive", boom)
    monkeypatch.setattr(mgc, "_code_runs_guarded_directly", boom)
    assert is_dangerous_command("python3 -c 'print(1)'") is False
    assert is_dangerous_command(f"python3 -c 'import os; os.system(\"{D}\")'") is True


def test_empty_import_list_parses_to_no_aliases():
    from shared.merge_guard_common import _python_aliases

    assert _python_aliases("from os import ()") == (set(), set())


# A gawk extension or include flag, or a deno value flag, takes the NEXT token as its
# value; the program is the token after that.
VALUE_FLAG_DENY = [
    ("gawk -l ext", f"gawk -l ordchr '{{system(\"{D}\")}}' f"),
    ("gawk -i inplace", f"gawk -i inplace '{{system(\"{D}\")}}' f"),
    ("gawk --load ext", f"gawk --load ordchr '{{system(\"{D}\")}}' f"),
    ("gawk --include lib", f"gawk --include lib '{{system(\"{D}\")}}' f"),
    ("deno eval --ext ts", "deno eval --ext ts 'Deno.run({cmd: [\"gh\", \"pr\", \"merge\", \"5\"]})'"),
]
VALUE_FLAG_ALLOW = [
    ("gawk -i inplace print", f"gawk -i inplace '{{print \"{D}\"}}' f"),
    ("deno eval --ext ts log", f"deno eval --ext ts 'console.log(\"{D}\")'"),
]


@pytest.mark.parametrize("label,cmd", VALUE_FLAG_DENY, ids=[r[0] for r in VALUE_FLAG_DENY])
def test_value_flag_does_not_hide_the_program(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", VALUE_FLAG_ALLOW, ids=[r[0] for r in VALUE_FLAG_ALLOW])
def test_value_flag_program_that_only_prints_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False
