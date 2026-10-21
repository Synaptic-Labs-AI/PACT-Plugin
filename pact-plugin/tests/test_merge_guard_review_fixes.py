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

    monkeypatch.setattr(mgc, "_program_from_tokens", boom)
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


# `import os as o` makes `o` an alias of os, of which only some attributes start a
# process; `import subprocess as sp` makes every `sp.*` call one.
OS_ALIAS_ALLOW = [
    ("os alias path.join", f"python3 -c \"import os as o; print(o.path.join('a', 'b'), '{D}')\""),
    ("os alias getcwd", f"python3 -c \"import os as o; print(o.getcwd(), '{D}')\""),
]
MODULE_ALIAS_DENY = [
    ("os alias system", f"python3 -c \"import os as o; o.system('{D}')\""),
    ("subprocess alias run list", "python3 -c \"import subprocess as sp; sp.run(['gh', 'pr', 'merge', '5'])\""),
    ("pty alias spawn", "python3 -c \"import pty as p; p.spawn(['gh', 'pr', 'merge', '5'])\""),
]


@pytest.mark.parametrize("label,cmd", OS_ALIAS_ALLOW, ids=[r[0] for r in OS_ALIAS_ALLOW])
def test_os_alias_non_process_call_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", MODULE_ALIAS_DENY, ids=[r[0] for r in MODULE_ALIAS_DENY])
def test_module_alias_process_call_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


# `from subprocess import *` brings run, call, check_call, check_output and Popen in
# as bare names, so a bare call is the primitive.
STAR_IMPORT_DENY = [
    ("subprocess star run", "python3 -c 'from subprocess import *; run([\"gh\",\"pr\",\"merge\",\"5\"])'"),
    ("subprocess star check_call", "python3 -c 'from subprocess import *; check_call([\"gh\",\"pr\",\"merge\",\"5\"])'"),
    ("os star system", f"python3 -c 'from os import *; system(\"{D}\")'"),
]
STAR_IMPORT_ALLOW = [
    ("subprocess star print", f"python3 -c 'from subprocess import *; print(\"{D}\")'"),
]


@pytest.mark.parametrize("label,cmd", STAR_IMPORT_DENY, ids=[r[0] for r in STAR_IMPORT_DENY])
def test_star_import_process_call_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", STAR_IMPORT_ALLOW, ids=[r[0] for r in STAR_IMPORT_ALLOW])
def test_star_import_without_a_process_call_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# A pipe into a stdin interpreter is fed only by its own pipeline: quoted text in an
# earlier, separate command is not that interpreter's program.
PIPELINE_ALLOW = [
    ("grep then unrelated pipe", f"grep -rn 'os.system(\"{D}\")' tests/ ; git diff | python3 -"),
    ("&& then unrelated pipe", f"echo 'os.system(\"{D}\")' > notes.txt && git log | python3 -"),
]
PIPELINE_DENY = [
    ("echo piped into python3 -", f"echo 'import os; os.system(\"{D}\")' | python3 -"),
    ("cat heredoc piped", f"cat <<'EOF' | python3 -\nimport os\nos.system(\"{D}\")\nEOF"),
    ("piped after an unrelated command", f"git status; echo 'import os; os.system(\"{D}\")' | python3"),
]


@pytest.mark.parametrize("label,cmd", PIPELINE_ALLOW, ids=[r[0] for r in PIPELINE_ALLOW])
def test_text_outside_the_pipeline_is_not_fed_code(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", PIPELINE_DENY, ids=[r[0] for r in PIPELINE_DENY])
def test_text_inside_the_pipeline_is_fed_code(label, cmd):
    assert is_dangerous_command(cmd) is True


# The interpreter direct-call scan reads only real command words: a shell comment and
# the arguments of echo/printf name an interpreter without running it.
NOT_A_COMMAND_ALLOW = [
    ("trailing comment", f"git status  # python3 -c 'import os; os.system(\"{D}\")'"),
    ("comment line", f"# python3 -c 'import os; os.system(\"{D}\")'\ngit log -1"),
    ("echo carries the one-liner", f"echo python3 -c 'import os; os.system(\"{D}\")'"),
    ("printf carries the one-liner", f"printf '%s\\n' python3 -c 'import os; os.system(\"{D}\")'"),
]
REAL_COMMAND_DENY = [
    ("one-liner after a comment line", f"# note\npython3 -c 'import os; os.system(\"{D}\")'"),
    ("one-liner after echo", f"echo start; python3 -c 'import os; os.system(\"{D}\")'"),
    ("hash inside the code", f"python3 -c 'import os  # run it\nos.system(\"{D}\")'"),
]


@pytest.mark.parametrize("label,cmd", NOT_A_COMMAND_ALLOW, ids=[r[0] for r in NOT_A_COMMAND_ALLOW])
def test_interpreter_named_outside_a_command_word_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", REAL_COMMAND_DENY, ids=[r[0] for r in REAL_COMMAND_DENY])
def test_interpreter_in_a_command_word_is_still_judged(label, cmd):
    assert is_dangerous_command(cmd) is True


# A readable program is refused when an exec primitive is GIVEN a guarded command, in
# any string-expression shape; naming merge text elsewhere in the program is not a run.
GIVEN_TO_A_PRIMITIVE_DENY = [
    ("stdin f-string", ("python3 - <<'EOF'\nimport subprocess\nn = 5\n"
                        "subprocess.run(f\"gh pr merge {n} --squash\", shell=True)\nEOF")),
    ("stdin r-string", f"python3 - <<'EOF'\nimport os\nos.system(r\"{D}\")\nEOF"),
    ("-c f-string", "python3 -c 'import subprocess; n = 5; subprocess.run(f\"gh pr merge {n}\", shell=True)'"),
    ("-c concatenation", "python3 -c 'import subprocess; n = \"5\"; subprocess.run(\"gh pr merge \" + n, shell=True)'"),
    ("-c split literal concatenation", "python3 -c 'import os; os.system(\"gh pr \" + \"merge 5\")'"),
    ("stdin concatenation", ("python3 - <<'EOF'\nimport subprocess\nn = '5'\n"
                             "subprocess.run('gh pr merge ' + n, shell=True)\nEOF")),
    ("-c args= keyword", "python3 -c 'import subprocess; subprocess.run(args=[\"gh\", \"pr\", \"merge\", \"5\"])'"),
    ("-c shlex.split", f"python3 -c 'import shlex, subprocess; subprocess.run(shlex.split(\"{D}\"))'"),
    ("-c format", "python3 -c 'import subprocess; subprocess.run(\"gh pr merge {}\".format(5), shell=True)'"),
    ("-c join", "python3 -c 'import subprocess; subprocess.run(\" \".join([\"gh\", \"pr\", \"merge\", \"5\"]), shell=True)'"),
    ("-c percent", "python3 -c 'import subprocess; n = 5; subprocess.run(\"gh pr merge %s\" % n, shell=True)'"),
    ("-c eval of a literal", f"python3 -c 'import os; eval(\"os.system(\\\"{D}\\\")\")'"),
    ("-c popen with mode", f"python3 -c 'import os; os.popen(\"{D}\", \"r\").read()'"),
    ("node execSync template", f"node -e 'require(\"child_process\").execSync(`{D}`)'"),
    ("perl qx", f"perl -e 'print qx({D})'"),
    ("perl open3", f"perl -e 'use IPC::Open3; open3(my $i, my $o, undef, \"{D}\")'"),
    ("ruby %x", f"ruby -e 'puts %x({D})'"),
    ("osascript do shell script", f"osascript -e 'do shell script \"{D}\"'"),
    ("awk print pipe", f"awk 'BEGIN {{ print \"x\" | \"{D}\" }}'"),
]
NAMED_NOT_RUN_ALLOW = [
    ("node runs another command, logs merge text",
     ("node -e 'const {execSync}=require(\"child_process\"); const s=execSync(\"gh pr list --json number\")"
      ".toString(); console.log(\"run: gh pr merge \" + JSON.parse(s)[0].number)'")),
    ("rg searches for merge text", "python3 -c 'import subprocess; subprocess.run([\"rg\",\"gh pr merge\",\"-n\"])'"),
    ("eval arithmetic, print merge text", f"python3 -c 'print(eval(\"1+1\")); print(\"{D}\")'"),
    ("stdin runs git log, prints merge text",
     f"python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['git', 'log', '-1'])\nprint('{D}')\nEOF"),
]


@pytest.mark.parametrize("label,cmd", GIVEN_TO_A_PRIMITIVE_DENY, ids=[r[0] for r in GIVEN_TO_A_PRIMITIVE_DENY])
def test_guarded_command_given_to_a_primitive_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", NAMED_NOT_RUN_ALLOW, ids=[r[0] for r in NAMED_NOT_RUN_ALLOW])
def test_merge_text_beside_an_unrelated_primitive_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


Q3, S3 = '"' * 3, "'" * 3  # triple quotes, kept out of this file's own string syntax

# A triple-quoted literal is read whole, including one that spans lines, rather than as
# the empty literal its first two quotes would be.
TRIPLE_QUOTED_DENY = [
    ("-c single-quote triple", f"python3 -c \"import os; os.system({S3}{D}{S3})\""),
    ("-c triple plus concatenation", f"python3 -c 'import os; os.system({Q3}{D}{Q3} + \" --squash\")'"),
    ("-c multi-line shell block",
     f"python3 -c 'import subprocess\nsubprocess.run({Q3}\ngit fetch origin\n{D} --squash\n{Q3}, shell=True)'"),
    ("stdin multi-line shell block",
     (f"python3 - <<'EOF'\nimport subprocess\nsubprocess.run({Q3}\ngit fetch origin\n{D} --squash\n{Q3}, "
      "shell=True)\nEOF")),
    ("stdin f-string triple", f"python3 - <<'EOF'\nimport os\nn = 5\nos.system(f{Q3}gh pr merge {{n}}{Q3})\nEOF"),
]
TRIPLE_QUOTED_ALLOW = [
    ("-c prints a triple", f"python3 -c 'print({Q3}usage: {D}{Q3})'"),
    ("-c searches for a triple", f"python3 -c 'import subprocess; subprocess.run([\"rg\", {Q3}{D}{Q3}, \"-n\"])'"),
    ("-c runs a view", f"python3 -c 'import os; os.system({Q3}gh pr view 5{Q3})'"),
]


@pytest.mark.parametrize("label,cmd", TRIPLE_QUOTED_DENY, ids=[r[0] for r in TRIPLE_QUOTED_DENY])
def test_triple_quoted_merge_given_to_a_primitive_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", TRIPLE_QUOTED_ALLOW, ids=[r[0] for r in TRIPLE_QUOTED_ALLOW])
def test_triple_quoted_merge_text_not_run_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# A call written inside a multi-line triple-quoted string (a docstring, a usage text) is
# data, not code.
DOCSTRING_ALLOW = [
    ("stdin docstring example",
     f"python3 - <<'EOF'\ndoc = {Q3}\nExample:\n    os.system(\"{D}\")\n{Q3}\nprint(doc)\nEOF"),
    ("-c docstring example", f"python3 -c 'doc = {Q3}\nExample:\n    os.system(\"{D}\")\n{Q3}\nprint(doc)'"),
    ("stdin function docstring",
     (f"python3 - <<'EOF'\ndef helper():\n    {Q3}Call like:\n    subprocess.run([\"gh\", \"pr\", \"merge\", \"5\"])\n"
      f"    {Q3}\n    return 1\nprint(helper())\nEOF")),
    ("stdin single-quote docstring", f"python3 - <<'EOF'\ndoc = {S3}\n    os.system(\"{D}\")\n{S3}\nprint(doc)\nEOF"),
]
DOCSTRING_DENY = [
    ("stdin real call after a docstring",
     f"python3 - <<'EOF'\n{Q3}Merge script.\n{Q3}\nimport os\nos.system(\"{D}\")\nEOF"),
]


@pytest.mark.parametrize("label,cmd", DOCSTRING_ALLOW, ids=[r[0] for r in DOCSTRING_ALLOW])
def test_call_inside_a_multi_line_string_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", DOCSTRING_DENY, ids=[r[0] for r in DOCSTRING_DENY])
def test_call_after_a_multi_line_string_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


# An argv given as a tuple, or holding a non-literal element after a literal head word
# (a loop variable, `str(pr)`), is read with that element as a placeholder.
ARGV_ELEMENT_DENY = [
    ("-c tuple", "python3 -c 'import subprocess; subprocess.run((\"gh\",\"pr\",\"merge\",\"5\"))'"),
    ("-c str() element", "python3 -c 'import subprocess; subprocess.run([\"gh\",\"pr\",\"merge\",str(5)])'"),
    ("stdin loop over PRs",
     ("python3 - <<'EOF'\nimport subprocess\nfor pr in [5, 6]:\n"
      "    subprocess.run([\"gh\", \"pr\", \"merge\", str(pr), \"--squash\"], check=True)\nEOF")),
    ("stdin variable element",
     "python3 - <<'EOF'\nimport subprocess\npr = '5'\nsubprocess.run([\"gh\", \"pr\", \"merge\", pr])\nEOF"),
    ("node variable element",
     "node -e 'const cp = require(\"child_process\"); const n = \"5\"; cp.spawnSync(\"gh\", [\"pr\", \"merge\", n])'"),
]
ARGV_ELEMENT_ALLOW = [
    ("-c tuple view", "python3 -c 'import subprocess; subprocess.run((\"gh\",\"pr\",\"view\",\"5\"))'"),
    ("stdin loop viewing PRs",
     "python3 - <<'EOF'\nimport subprocess\nfor pr in [5, 6]:\n    subprocess.run([\"gh\", \"pr\", \"view\", str(pr)])\nEOF"),
    ("-c variable subcommand",
     "python3 -c 'import subprocess; sub = \"view\"; subprocess.run([\"gh\", \"pr\", sub, \"5\"])'"),
]


@pytest.mark.parametrize("label,cmd", ARGV_ELEMENT_DENY, ids=[r[0] for r in ARGV_ELEMENT_DENY])
def test_merge_argv_with_a_tuple_or_variable_element_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", ARGV_ELEMENT_ALLOW, ids=[r[0] for r in ARGV_ELEMENT_ALLOW])
def test_non_merge_argv_with_a_tuple_or_variable_element_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_list_led_by_a_variable_is_not_read():
    """A placeholder is read only after a literal head word: `[a, b]` spells nothing."""
    import shared.merge_guard_common as mgc

    assert mgc._literal_list_at("[a, \"pr\"]", 0) is None
    assert mgc._literal_list_at("[\"gh\", n]", 0) == (["gh", "X"], 9)
