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
    monkeypatch.setattr(mgc, "_guarded_commands_directly", boom)
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


def test_a_list_led_by_a_variable_is_read_only_as_gh_pr():
    """An unresolved leading element reads as gh only before `"pr"`; otherwise the
    list spells nothing. A later unresolved element is a placeholder."""
    import shared.merge_guard_common as mgc

    assert mgc._literal_list_at("[a, \"push\"]", 0) is None
    assert mgc._literal_list_at("[a, \"pr\"]", 0) == ([["gh", "pr"]], 9)
    assert mgc._literal_list_at("[\"gh\", n]", 0) == ([["gh", "X"]], 9)


# When the direct-call parser raises on a program, that program falls back to a coarse
# check: it names a primitive and a string literal, or a literal argv list joined, reads
# as a guarded command. A merge beside a primitive is still refused (in -c and stdin
# code), and nothing raises.
PARSER_DEFECT_DENY = [
    ("-c os.system", f"python3 -c 'import os; os.system(\"{D}\")'"),
    ("stdin os.system", f"python3 - <<'EOF'\nimport os\nos.system(\"{D}\")\nEOF"),
    ("node execSync", f"node -e 'require(\"child_process\").execSync(\"{D}\")'"),
    ("-c split argv", "python3 -c 'import subprocess; subprocess.run([\"gh\", \"pr\", \"merge\", \"5\"])'"),
    ("stdin split argv tuple",
     "python3 - <<'EOF'\nimport subprocess\nsubprocess.run((\"gh\", \"pr\", \"merge\", \"5\"))\nEOF"),
]
PARSER_DEFECT_ALLOW = [
    ("-c prints merge text", f"python3 -c 'print(\"{D}\")'"),
    ("-c plain program", "python3 -c 'print(1)'"),
]


@pytest.fixture
def direct_parser_raises(monkeypatch):
    import shared.merge_guard_common as mgc

    def boom(*_args, **_kwargs):
        raise IndexError("parser defect")

    monkeypatch.setattr(mgc, "_guarded_commands_directly", boom)


@pytest.mark.parametrize("label,cmd", PARSER_DEFECT_DENY, ids=[r[0] for r in PARSER_DEFECT_DENY])
def test_a_direct_parser_defect_still_refuses_a_merge_beside_a_primitive(direct_parser_raises, label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", PARSER_DEFECT_ALLOW, ids=[r[0] for r in PARSER_DEFECT_ALLOW])
def test_a_direct_parser_defect_allows_a_program_without_a_primitive(direct_parser_raises, label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_defect_in_both_parsers_falls_back_to_primitive_names(direct_parser_raises, monkeypatch):
    import shared.merge_guard_common as mgc

    def boom(*_args, **_kwargs):
        raise KeyError("parser defect")

    monkeypatch.setattr(mgc, "_program_names_primitive", boom)
    assert is_dangerous_command(f"python3 - <<'EOF'\nimport os\nos.system(\"{D}\")\nEOF") is True
    assert is_dangerous_command(f"python3 - <<'EOF'\nprint(\"{D}\")\nEOF") is False


# In a Python program a `#` comment is not code: a triple quote written in it does not
# open a literal that hides the call after it. In JavaScript `#` names a private field.
COMMENT_QUOTE_DENY = [
    ("stdin comment naming triple quotes",
     f"python3 - <<'EOF'\nimport os\n# docstrings use {Q3} quotes\nos.system(\"{D} --squash\")\nHELP = {Q3}done{Q3}\nEOF"),
    ("-c comment naming triple quotes",
     f"python3 -c 'import os\n# uses {Q3} too\nos.system(\"{D}\")\nx = {Q3}y{Q3}'"),
    ("node private field before the call",
     f"node -e 'class A {{ #f = 1 }}; require(\"child_process\").execSync(\"{D}\")'"),
]
COMMENT_QUOTE_ALLOW = [
    ("stdin commented-out call", f"python3 - <<'EOF'\nimport os\n# os.system(\"{D}\")\nprint(1)\nEOF"),
]


@pytest.mark.parametrize("label,cmd", COMMENT_QUOTE_DENY, ids=[r[0] for r in COMMENT_QUOTE_DENY])
def test_quote_in_a_comment_does_not_hide_a_merge(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", COMMENT_QUOTE_ALLOW, ids=[r[0] for r in COMMENT_QUOTE_ALLOW])
def test_python_comment_is_not_code(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_defect_in_the_list_reader_too_keeps_the_literal_judgment(direct_parser_raises, monkeypatch):
    import shared.merge_guard_common as mgc

    def boom(*_args, **_kwargs):
        raise TypeError("parser defect")

    monkeypatch.setattr(mgc, "_literal_list_at", boom)
    assert is_dangerous_command(f"python3 -c 'import os; os.system(\"{D}\")'") is True
    assert is_dangerous_command("python3 -c 'import subprocess; subprocess.run([\"gh\", \"pr\", \"view\"])'") is False


# A subscripted argv element (`sys.argv[1]`, `pr["number"]`) is a placeholder too.
SUBSCRIPT_ELEMENT_DENY = [
    ("-c sys.argv", "python3 -c 'import subprocess, sys; subprocess.run([\"gh\",\"pr\",\"merge\",sys.argv[1]])' 5"),
    ("-c dict key", "python3 -c 'import subprocess; pr = {\"number\": 5}; subprocess.run([\"gh\",\"pr\",\"merge\",pr[\"number\"]])'"),
    ("-c subscript then method",
     "python3 -c 'import subprocess, sys; subprocess.run([\"gh\",\"pr\",\"merge\",sys.argv[-1].strip(),\"--squash\"])' 5"),
    ("node process.argv",
     "node -e 'const cp = require(\"child_process\"); cp.spawnSync(\"gh\", [\"pr\", \"merge\", process.argv[1]])' 5"),
]
SUBSCRIPT_ELEMENT_ALLOW = [
    ("-c view by sys.argv", "python3 -c 'import subprocess, sys; subprocess.run([\"gh\",\"pr\",\"view\",sys.argv[1]])' 5"),
    ("-c subscripted subcommand", "python3 -c 'import subprocess; s = [\"view\"]; subprocess.run([\"gh\",\"pr\",s[0],\"5\"])'"),
]


@pytest.mark.parametrize("label,cmd", SUBSCRIPT_ELEMENT_DENY, ids=[r[0] for r in SUBSCRIPT_ELEMENT_DENY])
def test_merge_argv_with_a_subscripted_element_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", SUBSCRIPT_ELEMENT_ALLOW, ids=[r[0] for r in SUBSCRIPT_ELEMENT_ALLOW])
def test_non_merge_argv_with_a_subscripted_element_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_an_unclosed_subscript_is_not_an_operand():
    import shared.merge_guard_common as mgc

    assert mgc._operand_end("sys.argv[1", 0) == -1
    assert mgc._operand_end("a[0].b(c)[1] + x", 0) == 12


# A command held in a variable and then passed to a primitive reads as the literal value
# the variable was last given before the call. Merge text held in a variable that no
# primitive is given stays data.
NAMED_COMMAND_DENY = [
    ("-c argv in a variable", "python3 -c 'import subprocess; cmd=[\"gh\",\"pr\",\"merge\",\"12\"]; subprocess.run(cmd)'"),
    ("-c line in a variable", "python3 -c 'import os; c=\"gh pr merge 12\"; os.system(c)'"),
    ("stdin variable with keyword args",
     ("python3 - <<'EOF'\nimport subprocess\ncmd = [\"gh\", \"pr\", \"merge\", \"12\", \"--squash\"]\n"
      "subprocess.run(cmd, check=True)\nEOF")),
    ("stdin concatenated line in a variable",
     "python3 - <<'EOF'\nimport os\nn = '12'\nline = 'gh pr merge ' + n\nos.system(line)\nEOF"),
    ("perl scalar", "perl -e 'my $c = \"gh pr merge 12\"; system($c)'"),
    ("node const", "node -e 'const c = \"gh pr merge 12\"; require(\"child_process\").execSync(c)'"),
]
NAMED_COMMAND_ALLOW = [
    ("merge text printed, other command run",
     "python3 -c 'import subprocess; print(\"gh pr merge 12\"); subprocess.run([\"ls\"])'"),
    ("variable reassigned before the call",
     "python3 -c 'import os; c = \"gh pr merge 12\"; c = \"gh pr view 12\"; os.system(c)'"),
    ("merge text in one variable, another run",
     "python3 -c 'import subprocess; msg = \"gh pr merge 12\"; cmd = [\"ls\"]; print(msg); subprocess.run(cmd)'"),
    ("assignment written only inside a string",
     "python3 - <<'EOF'\nimport os\nc = 'ls'\nprint(\"c = 'gh pr merge 12'\")\nos.system(c)\nEOF"),
]


@pytest.mark.parametrize("label,cmd", NAMED_COMMAND_DENY, ids=[r[0] for r in NAMED_COMMAND_DENY])
def test_merge_held_in_a_variable_given_to_a_primitive_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", NAMED_COMMAND_ALLOW, ids=[r[0] for r in NAMED_COMMAND_ALLOW])
def test_merge_text_in_a_variable_no_primitive_runs_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_name_is_resolved_only_to_an_earlier_assignment():
    import shared.merge_guard_common as mgc

    assert mgc._named_values_at("", "run(c); c = 'x'", 4) is None
    assert mgc._named_values_at("", "c == 'x'; run(c)", 14) is None
    assert mgc._named_values_at("", "c = 'x'; run(c)", 13) == ([4], 14)


# A list element that is a bare name reads as the literal its last earlier assignment
# gave it, so an argv led by a variable holding the gh path is read. A name with no
# literal value stays unknown.
NAMED_ELEMENT_DENY = [
    ("-c gh path in a constant",
     "python3 -c 'import subprocess; GH=\"/usr/local/bin/gh\"; subprocess.run([GH,\"pr\",\"merge\",\"5\"])'"),
    ("-c gh in a variable", "python3 -c 'import subprocess; g=\"gh\"; subprocess.run([g,\"pr\",\"merge\",\"5\"])'"),
    ("-c subcommand as the last element",
     "python3 -c 'import subprocess; sub=\"merge\"; subprocess.run([\"gh\",\"pr\",sub])'"),
]
NAMED_ELEMENT_ALLOW = [
    ("-c gh in a variable, view", "python3 -c 'import subprocess; g=\"gh\"; subprocess.run([g,\"pr\",\"view\",\"5\"])'"),
]


@pytest.mark.parametrize("label,cmd", NAMED_ELEMENT_DENY, ids=[r[0] for r in NAMED_ELEMENT_DENY])
def test_merge_argv_with_a_named_element_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", NAMED_ELEMENT_ALLOW, ids=[r[0] for r in NAMED_ELEMENT_ALLOW])
def test_argv_with_a_named_element_not_a_merge_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# gh's path resolved at run time (`shutil.which("gh")`, an environment variable): an
# unresolved leading element followed by `"pr"` reads as gh, and the rest is judged as
# `gh pr ...` typed in a shell is.
RUNTIME_GH_DENY = [
    ("-c shutil.which",
     "python3 -c 'import subprocess, shutil; g = shutil.which(\"gh\"); subprocess.run([g, \"pr\", \"merge\", \"5\"])'"),
    ("-c environment with default",
     "python3 -c 'import subprocess, os; g = os.environ.get(\"GH\", \"gh\"); subprocess.run([g, \"pr\", \"merge\", \"5\"])'"),
    ("-c close with branch delete",
     ("python3 -c 'import subprocess, shutil; g = shutil.which(\"gh\"); "
      "subprocess.run([g, \"pr\", \"close\", \"5\", \"--delete-branch\"])'")),
    ("-c name never assigned", "python3 -c 'import subprocess; subprocess.run([g, \"pr\", \"merge\", \"5\"])'"),
]
RUNTIME_GH_ALLOW = [
    ("-c view", "python3 -c 'import subprocess, shutil; g = shutil.which(\"gh\"); subprocess.run([g, \"pr\", \"view\", \"5\"])'"),
    ("-c list", "python3 -c 'import subprocess, shutil; g = shutil.which(\"gh\"); subprocess.run([g, \"pr\", \"list\"])'"),
    ("-c another program's merge subcommand",
     "python3 -c 'import subprocess, shutil; t = shutil.which(\"tool\"); subprocess.run([t, \"merge\", \"5\"])'"),
]


@pytest.mark.parametrize("label,cmd", RUNTIME_GH_DENY, ids=[r[0] for r in RUNTIME_GH_DENY])
def test_gh_resolved_at_run_time_running_a_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", RUNTIME_GH_ALLOW, ids=[r[0] for r in RUNTIME_GH_ALLOW])
def test_gh_resolved_at_run_time_reading_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# A commented-out assignment is not the name's value, whatever the language's comment
# syntax; the live assignment it replaced decides.
COMMENTED_ASSIGNMENT_ALLOW = [
    ("python list", ("python3 - <<'EOF'\nimport subprocess\ncmd = [\"gh\", \"pr\", \"view\", \"5\"]\n"
                     "# cmd = [\"gh\", \"pr\", \"merge\", \"5\"]\nsubprocess.run(cmd)\nEOF")),
    ("python string", ("python3 - <<'EOF'\nimport subprocess\ncmd = \"gh pr view 5\"\n"
                       f"# old: cmd = \"{D} --squash\"\nsubprocess.run(cmd, shell=True)\nEOF")),
    ("node line comment", ("node - <<'EOF'\nconst cmd = \"gh pr view 5\";\n"
                           f"// const cmd = \"{D}\";\nrequire(\"child_process\").execSync(cmd);\nEOF")),
    ("node block comment", ("node - <<'EOF'\nlet cmd = \"gh pr view 5\";\n"
                            f"/* cmd = \"{D}\"; */\nrequire(\"child_process\").execSync(cmd);\nEOF")),
    ("php hash comment", f"php -r '$c = \"gh pr view 5\";\n# $c = \"{D}\";\nshell_exec($c);'"),
    ("php slash comment", f"php -r '$c = \"gh pr view 5\";\n// $c = \"{D}\";\nshell_exec($c);'"),
    ("perl comment", f"perl -e 'my $c = \"gh pr view 5\";\n# $c = \"{D}\";\nsystem($c);'"),
    ("ruby comment", f"ruby -e 'c = \"gh pr view 5\"\n# c = \"{D}\"\nsystem(c)'"),
    ("lua comment", f"lua -e 'local c = \"gh pr view 5\"\n-- c = \"{D}\"\nos.execute(c)'"),
]
COMMENTED_ASSIGNMENT_DENY = [
    ("python trailing comment on a live merge",
     f"python3 - <<'EOF'\nimport os\nc = \"{D}\"  # was: gh pr view 5\nos.system(c)\nEOF"),
    ("node URL string before a live merge",
     ("node - <<'EOF'\nconst u = \"https://example.com\";\n"
      f"const c = \"{D}\";\nrequire(\"child_process\").execSync(c);\nEOF")),
    ("perl array length is not a comment",
     f"perl -e 'my @a = (1); my $n = $#a; my $c = \"{D}\"; system($c);'"),
]


@pytest.mark.parametrize("label,cmd", COMMENTED_ASSIGNMENT_ALLOW, ids=[r[0] for r in COMMENTED_ASSIGNMENT_ALLOW])
def test_a_commented_out_assignment_is_not_the_value(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", COMMENTED_ASSIGNMENT_DENY, ids=[r[0] for r in COMMENTED_ASSIGNMENT_DENY])
def test_a_live_merge_assignment_beside_comment_syntax_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


# The mint and the pre-hook read an interpreter merge through one public reader: the
# context of the ONE guarded command the programs run, or None.
_A = "python3 -c 'import subprocess; subprocess.run([\"gh\",\"pr\",\"merge\",\"5\",\"--squash\"])'"
_B = "python3 -c 'import subprocess; subprocess.run([\"gh\",\"pr\",\"merge\",\"6\"])'"
_MERGE_5 = {"operation_type": "merge", "bound_flags": [], "pr_number": "5"}
INTERPRETER_CONTEXT_ROWS = [
    ("-c list", _A, _MERGE_5),
    ("-c string with --admin", f"python3 -c 'import os; os.system(\"{D} --admin\")'",
     {"operation_type": "merge", "bound_flags": ["--admin"], "pr_number": "5"}),
    ("-c command held in a variable",
     "python3 -c 'import subprocess; cmd=[\"gh\",\"pr\",\"merge\",\"5\"]; subprocess.run(cmd)'", _MERGE_5),
    ("stdin program", f"python3 - <<'EOF'\nimport os\nos.system(\"{D} --squash\")\nEOF", _MERGE_5),
    ("stdin program running bash -c", f"python3 - <<'EOF'\nimport os\nos.system(\"bash -c '{D}'\")\nEOF", _MERGE_5),
    ("the same merge twice", f"python3 -c 'import os; os.system(\"{D}\"); os.system(\"{D}\")'", _MERGE_5),
    ("two interpreter merges joined", _A + " && " + _B, None),
    ("one program running two merges",
     f"python3 -c 'import os; os.system(\"{D}\"); os.system(\"gh pr \" + \"merge 6\")'", None),
    ("a program that only prints a merge", f"python3 -c 'print(\"{D}\")'", None),
    ("a shell merge, no interpreter", D, None),
]


@pytest.mark.parametrize("label,cmd,context", INTERPRETER_CONTEXT_ROWS, ids=[r[0] for r in INTERPRETER_CONTEXT_ROWS])
def test_interpreter_command_context_reads_one_guarded_command(label, cmd, context):
    from shared.merge_guard_common import interpreter_command_context

    assert interpreter_command_context(cmd) == context


def test_interpreter_command_context_never_raises(monkeypatch):
    import shared.merge_guard_common as mgc

    def boom(*_args, **_kwargs):
        raise ValueError("parser defect")

    monkeypatch.setattr(mgc, "_interpreter_guarded_commands", boom)
    assert mgc.interpreter_command_context(_A) is None


def test_normalize_command_text_splices_and_collapses():
    from shared.merge_guard_common import normalize_command_text

    assert normalize_command_text(f"  {D} \\\n  --squash\n") == f"{D} --squash"
    assert normalize_command_text(f"{D}\t--squash") == f"{D} --squash"
    assert normalize_command_text(f"{D}\\\n5") == f"{D}5"
