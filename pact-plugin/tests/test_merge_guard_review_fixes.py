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
    """An unresolved leading element reads as gh only before `"pr"` and only when its
    text names gh or hub; otherwise the list spells nothing. A later unresolved element
    is a placeholder."""
    import shared.merge_guard_common as mgc

    assert mgc._literal_list_at("[a, \"push\"]", 0) is None
    assert mgc._literal_list_at("[a, \"pr\"]", 0) is None
    assert mgc._literal_list_at("[gh, \"pr\"]", 0) == ([["gh", "pr"]], 10)
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
]
RUNTIME_GH_ALLOW = [
    # A name never assigned raises NameError before anything runs.
    ("-c name never assigned", "python3 -c 'import subprocess; subprocess.run([g, \"pr\", \"merge\", \"5\"])'"),
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


# A comment opener inside a language's own literal (a URL in a JS template, `#` in a
# ruby %w list, `--` in a lua long string, `#` in a perl regex) is not a comment: the
# assignment after it on the line still counts.
_V = "gh pr view 5"
LITERAL_OPENER_ROWS = [
    ("node template URL", 'node -e \'let c="{a}"; const u=`http://x`; c="{b}"; require("child_process").execSync(c)\''),
    ("ruby %w list", 'ruby -e \'c="{a}"; w=%w[a #b]; c="{b}"; system(c)\''),
    ("lua long string", 'lua -e \'local c="{a}"; local s=[[a -- b]]; c="{b}"; os.execute(c)\''),
]
LITERAL_OPENER_DENY = [
    (label, shape.format(a=_V, b=D)) for label, shape in LITERAL_OPENER_ROWS
] + [
    ("perl regex after =~", f'perl -e \'my $c="{_V}"; my $x = "a" =~ / #/; $c="{D}"; system($c)\''),
    ("perl q{} string", f'perl -e \'my $c="{_V}"; my $x = q{{a #b}}; $c="{D}"; system($c)\''),
    ("perl split regex keeps the line", f'perl -e \'my $c="{_V}"; my @p = split / #/, "x"; $c="{D}"; system($c)\''),
]
LITERAL_OPENER_ALLOW = [(label, shape.format(a=D, b=_V)) for label, shape in LITERAL_OPENER_ROWS]


@pytest.mark.parametrize("label,cmd", LITERAL_OPENER_DENY, ids=[r[0] for r in LITERAL_OPENER_DENY])
def test_a_comment_opener_inside_a_literal_hides_nothing(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", LITERAL_OPENER_ALLOW, ids=[r[0] for r in LITERAL_OPENER_ALLOW])
def test_a_reassignment_after_a_literal_opener_is_read(label, cmd):
    assert is_dangerous_command(cmd) is False


# A line continuation before `-c` wraps a long command; the program after it is still
# read, as the shell splices it.
CONTINUED_PROGRAM_DENY = [
    ("continuation then indented -c",
     "python3 \\\n  -c 'import subprocess; subprocess.run([\"gh\",\"pr\",\"merge\",\"5\",\"--squash\"])'"),
    ("continuation then -c", "python3 \\\n-c 'import subprocess; subprocess.run([\"gh\",\"pr\",\"merge\",\"5\"])'"),
    ("continuation before -e", f"node \\\n  -e 'require(\"child_process\").execSync(\"{D}\")'"),
]
CONTINUED_PROGRAM_ALLOW = [
    ("continuation then a view", "python3 \\\n  -c 'import subprocess; subprocess.run([\"gh\",\"pr\",\"view\",\"5\"])'"),
]


@pytest.mark.parametrize("label,cmd", CONTINUED_PROGRAM_DENY, ids=[r[0] for r in CONTINUED_PROGRAM_DENY])
def test_a_program_after_a_continuation_is_read(label, cmd):
    from shared.merge_guard_common import interpreter_command_context

    assert is_dangerous_command(cmd) is True
    context = interpreter_command_context(cmd)
    assert context is not None and context["pr_number"] == "5"


@pytest.mark.parametrize("label,cmd", CONTINUED_PROGRAM_ALLOW, ids=[r[0] for r in CONTINUED_PROGRAM_ALLOW])
def test_a_program_after_a_continuation_that_only_views_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# A guarded command an interpreter program runs is a destructive leg for compound
# detection: two of them, or one beside a shell merge, are refused as compound, and a
# single one beside benign legs is not.
_RUBY_HASH = "ruby -e 'system(\"gh pr \" + \"merge #{5} --squash\")'".replace("\" + \"", "")
COMPOUND_ROWS = [
    ("two interpreter merges", _A + " && " + _B, True),
    ("an interpreter merge and a shell merge", _A + " && gh pr " + "merge 6", True),
    ("a stdin merge and a shell merge", f"python3 - <<'EOF'\nimport os\nos.system(\"{D}\")\nEOF\ngh pr " + "merge 6", True),
    ("one program running two merges", f"python3 -c 'import os; os.system(\"{D}\"); os.system(\"gh pr \" + \"merge 6\")'", True),
    ("one interpreter merge", _A, False),
    ("an interpreter merge then echo", _A + " && echo ok", False),
    ("an interpreter merge then git status", _A + "; git status", False),
    ("an interpreter merge under timeout", "timeout 30 " + _A, False),
    ("an interpreter merge under sh -c", "sh -c \"python3 -c 'import os; os.system(\\\"" + D + "\\\")'\"", False),
    ("the same merge twice in one program", f"python3 -c 'import os; os.system(\"{D}\"); os.system(\"{D}\")'", False),
    ("a ruby merge read by both the shell and the program", _RUBY_HASH, False),
]


@pytest.mark.parametrize("label,cmd,compound", COMPOUND_ROWS, ids=[r[0] for r in COMPOUND_ROWS])
def test_interpreter_legs_count_toward_compound(label, cmd, compound):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_dangerous_command(cmd) is True
    assert is_compound_destructive_command(cmd) is compound


def test_a_parser_defect_on_a_later_call_keeps_what_was_read_before_it(monkeypatch):
    """A merge read through a name (which the coarse fallback cannot see) stays refused,
    and readable for the mint, when a later call in the same program trips the parser."""
    import shared.merge_guard_common as mgc

    real, calls = mgc._argument_commands, []

    def second_call_raises(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise IndexError("parser defect")
        return real(*args, **kwargs)

    monkeypatch.setattr(mgc, "_argument_commands", second_call_raises)
    cmd = ("python3 -c 'import os, subprocess; cmd = \"gh pr \" + \"merge 5\"; "
           "os.system(cmd); subprocess.check_call([\"ls\"])'")
    assert is_dangerous_command(cmd) is True
    calls.clear()
    assert mgc.interpreter_command_context(cmd) == _MERGE_5


# A `#` inside a quoted string is text, not a shell comment: it does not hide the legs
# after it. A `#` that starts a word outside quotes still starts a comment.
QUOTED_HASH_DENY = [
    ("commit message naming an issue", "git commit -m 'fix #12' && gh pr " + "merge 6"),
    ("echo with a step number", "echo 'step #1 done' && gh pr " + "merge 6"),
    ("double-quoted hash", 'git commit -m "fix #12" && gh pr ' + "merge 6"),
    ("escaped quote then hash", 'git commit -m "a \\" #1" && gh pr ' + "merge 6"),
    ("comment with an apostrophe, then a quoted hash", "# don't\ngit commit -m 'fix #12' && gh pr " + "merge 6"),
]
QUOTED_HASH_ALLOW = [
    ("an unquoted comment hides the rest", "echo a #b && gh pr " + "merge 6"),
    ("a commit naming an issue, alone", "git commit -m 'fix #12'"),
    ("a comment with an apostrophe", f"# don't {D}\ngit status"),
]


@pytest.mark.parametrize("label,cmd", QUOTED_HASH_DENY, ids=[r[0] for r in QUOTED_HASH_DENY])
def test_a_hash_inside_quotes_hides_nothing(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", QUOTED_HASH_ALLOW, ids=[r[0] for r in QUOTED_HASH_ALLOW])
def test_a_shell_comment_is_still_stripped(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_quoted_hash_leaves_both_merges_visible_to_compound_detection():
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_compound_destructive_command("ruby -e 'system(\"gh pr " + "merge #{5}\")' && gh pr " + "merge 6") is True
    assert is_compound_destructive_command("git commit -m 'fix #12' && gh pr " + "merge 6") is False


# A `NAME="…"` written inside another quoted string (perl `$c="…"` in `perl -e '…'`) is
# program text, not a shell assignment whose value a later leg expands.
PROGRAM_ASSIGNMENT_ALLOW = [
    ("perl merge held and printed", f'perl -e \'my $c="{D}"; print "$c\\n";\''),
    ("perl merge held, then a view run", f'perl -e \'my $c="{D}"; $c="gh pr view 5"; system($c)\''),
]
SHELL_ASSIGNMENT_DENY = [
    ("perl merge held and run", f'perl -e \'my $c="{D}"; system($c)\''),
    ("shell assignment then expansion", f'FOO="{D}" && $FOO'),
    ("shell export then eval", f'export CMD="{D}" && eval $CMD'),
    ("shell assignment prefixing bash -c", f'FOO="{D}" bash -c "$FOO"'),
]


@pytest.mark.parametrize("label,cmd", PROGRAM_ASSIGNMENT_ALLOW, ids=[r[0] for r in PROGRAM_ASSIGNMENT_ALLOW])
def test_an_assignment_inside_a_program_is_not_a_shell_assignment(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", SHELL_ASSIGNMENT_DENY, ids=[r[0] for r in SHELL_ASSIGNMENT_DENY])
def test_a_shell_assignment_that_runs_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


# Name resolution follows Python scope: the definitions that may reach a use, per scope,
# with branches and loops joined, parameters and loop targets unreadable, and a free name
# read at its def's call sites. Rows: the name-resolution spec's DENY and ALLOW sets.
M = "gh pr " + "merge"
SCOPED_NAME_DENY = [
    ('reuse', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd = ["gh", "pr", "view", "5"]\nsubprocess.run(cmd)\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nsubprocess.run(cmd)\nEOF2'),
    ('if/else', 'python3 - <<\'EOF2\'\nimport subprocess, sys\nif len(sys.argv) > 1:\n    cmd = ["gh", "pr", "merge", "5", "--squash"]\nelse:\n    cmd = ["gh", "pr", "view", "5"]\nsubprocess.run(cmd)\nEOF2'),
    ('free variable, called after', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd = ["gh", "pr", "merge", "5", "--squash"]\ndef go():\n    subprocess.run(cmd)\ngo()\nEOF2'),
    ('def before the assignment', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ndef go():\n    subprocess.run(cmd)\ncmd = ["gh", "pr", "merge", "5", "--squash"]\ngo()\nEOF2'),
    ('loop local', 'python3 - <<\'EOF2\'\nimport subprocess, sys\nfor n in [5]:\n    cmd = ["gh", "pr", "merge", str(n)]\n    subprocess.run(cmd)\nEOF2'),
    ('function-local reuse', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ndef f():\n    cmd = ["gh", "pr", "view", "5"]\n    subprocess.run(cmd)\n    cmd = ["gh", "pr", "merge", "5", "--squash"]\n    subprocess.run(cmd)\nf()\nEOF2'),
    ('try/except', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ntry:\n    cmd = ["gh", "pr", "merge", "5", "--squash"]\nexcept Exception:\n    cmd = ["gh", "pr", "view", "5"]\nsubprocess.run(cmd)\nEOF2'),
    ('closure', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ndef outer():\n    cmd = ["gh", "pr", "merge", "5", "--squash"]\n    def inner():\n        subprocess.run(cmd)\n    inner()\nouter()\nEOF2'),
    ('shell string', 'python3 - <<\'EOF2\'\nimport subprocess, sys\nc = "' + M + ' 5 --squash"\nsubprocess.run(c, shell=True)\nEOF2'),
    ('cmd = cmd + [...]', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd = ["gh", "pr", "merge"]\ncmd = cmd + ["5"]\nsubprocess.run(cmd)\nEOF2'),
    ('annotated', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd: list = ["gh", "pr", "merge", "5", "--squash"]\nsubprocess.run(cmd)\nEOF2'),
    ('while', 'python3 - <<\'EOF2\'\nimport subprocess, sys\nwhile True:\n    cmd = ["gh", "pr", "merge", "5", "--squash"]\n    subprocess.run(cmd)\n    break\nEOF2'),
    ('annotated assignment at module level', 'python3 - <<\'EOF2\'\nimport subprocess\ncmd: list[str] = ["gh", "pr", "merge", "5"]\nsubprocess.run(cmd)\nEOF2'),
    ('global written by a helper called before the run', 'python3 - <<\'EOF2\'\nimport subprocess\ndef setup():\n    global cmd\n    cmd = ["gh", "pr", "merge", "5", "--squash"]\ncmd = ["gh", "pr", "view", "5"]\nsetup()\nsubprocess.run(cmd)\nEOF2'),
    ('merge assigned in an except handler',
     "python3 - <<'EOF'\nimport subprocess\ntry:\n    cmd = ['gh', 'pr', 'view', '5']\nexcept Exception:\n    cmd = ['gh', 'pr', '" + 'merge' + "', '5']\nsubprocess.run(cmd)\nEOF"),
    ('nonlocal written by a helper called before the run', 'python3 - <<\'EOF2\'\nimport subprocess\ndef outer():\n    def pick():\n        nonlocal cmd\n        cmd = ["gh", "pr", "merge", "5"]\n    cmd = ["gh", "pr", "view", "5"]\n    pick()\n    subprocess.run(cmd)\nouter()\nEOF2'),
]
SCOPED_NAME_ALLOW = [
    ('merge printed, then reassigned', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nprint(cmd)\ncmd = ["gh", "pr", "view", "5"]\nsubprocess.run(cmd)\nEOF2'),
    ('free variable whose call site sees view', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nprint(cmd)\ncmd = ["gh", "pr", "view", "5"]\ndef go():\n    subprocess.run(cmd)\ngo()\nEOF2'),
    ('two defs', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ndef a():\n    cmd = ["gh", "pr", "merge", "5", "--squash"]\n    print(cmd)\ndef b():\n    cmd = ["gh", "pr", "view", "5"]\n    subprocess.run(cmd)\nb()\nEOF2'),
    ('lambda parameter', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nprint(cmd)\nf = lambda cmd: subprocess.run(cmd)\nf(["gh", "pr", "view", "5"])\nEOF2'),
    ('comprehension variable', 'python3 - <<\'EOF2\'\nimport subprocess, sys\nc = ["gh", "pr", "merge", "5", "--squash"]\nprint(c)\n[subprocess.run(c) for c in (["gh", "pr", "view", "5"],)]\nEOF2'),
    ('for target', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nprint(cmd)\nfor cmd in (["gh", "pr", "view", "5"],):\n    subprocess.run(cmd)\nEOF2'),
    ('with target', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nprint(cmd)\nwith open(\'/dev/null\') as cmd:\n    pass\ncmd = ["gh", "pr", "view", "5"]\nsubprocess.run(cmd)\nEOF2'),
    ('parameter of a helper', 'python3 - <<\'EOF2\'\nimport subprocess, sys\nimport shutil\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nprint(cmd)\ndef run(cmd):\n    return subprocess.run(cmd)\nrun(["gh", "pr", "view", "5"])\nrun(["gh", "pr", "list"])\nEOF2'),
    ('dead if', 'python3 - <<\'EOF2\'\nimport subprocess, sys\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nif False:\n    pass\nprint(cmd)\ncmd = ["gh", "pr", "view", "5"]\nsubprocess.run(cmd)\nEOF2'),
    ('class attribute', 'python3 - <<\'EOF2\'\nimport subprocess, sys\nclass K:\n    cmd = ["gh", "pr", "merge", "5", "--squash"]\ncmd = ["gh", "pr", "view", "5"]\nsubprocess.run(cmd)\nEOF2'),
    ('class attribute after the module assignment',
     "python3 - <<'EOF'\nimport subprocess\ncmd = ['gh', 'pr', 'view', '5']\nclass K:\n    cmd = ['gh', 'pr', '" + 'merge' + "', '5']\nsubprocess.run(cmd)\nEOF"),
    ('module merge printed, helper parameter run', 'python3 - <<\'EOF2\'\nimport subprocess\ncmd = "' + M + ' 5 --squash"\nprint("suggest:", cmd)\ndef run(cmd):\n    return subprocess.run(cmd, shell=True, capture_output=True)\nrun("gh pr view 5")\nEOF2'),
    ('merge local to another function', 'python3 - <<\'EOF2\'\nimport subprocess\ndef show():\n    cmd = "' + M + ' 5 --squash"\n    print(cmd)\ndef check():\n    subprocess.run(cmd)\ncmd = ["gh", "pr", "view", "5"]\ncheck()\nEOF2'),
    ('shadowing row 1', 'python3 - <<\'EOF2\'\nimport subprocess\ndef show():\n    cmd = ["gh", "pr", "merge", "5", "--squash"]\n    print(" ".join(cmd))\ndef check():\n    subprocess.run(cmd)\ncmd = ["gh", "pr", "view", "5"]\ncheck()\nEOF2'),
    ('shadowing row 2', 'python3 - <<\'EOF2\'\nimport subprocess\nMERGE = ["gh", "pr", "merge", "5", "--squash"]\nprint("next step:", MERGE)\ndef run(cmd):\n    return subprocess.run(cmd, capture_output=True, text=True)\nrun(["gh", "pr", "checks", "5"])\nEOF2'),
    ('shadowing row 3', 'python3 - <<\'EOF2\'\nimport subprocess\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nprint(cmd)\ndef run(cmd):\n    subprocess.run(cmd)\nrun(["gh", "pr", "view", "5"])\nEOF2'),
    ('shadowing row 4', 'python3 - <<\'EOF2\'\nimport subprocess\nfor cmd in (["gh", "pr", "view", "5"], ["gh", "pr", "checks", "5"]):\n    subprocess.run(cmd)\ncmd = ["gh", "pr", "merge", "5"]\nprint(cmd)\nEOF2'),
    ('shadowing row 5', 'python3 -c \'import subprocess; cmd = ["gh","pr","view","5"]; subprocess.run(cmd)  # cmd = ["gh","pr","merge","5"]\''),
]


@pytest.mark.parametrize("label,cmd", SCOPED_NAME_DENY, ids=[r[0] for r in SCOPED_NAME_DENY])
def test_a_merge_reaching_the_use_in_scope_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", SCOPED_NAME_ALLOW, ids=[r[0] for r in SCOPED_NAME_ALLOW])
def test_a_merge_not_reaching_the_use_in_scope_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_program_that_does_not_parse_does_not_raise():
    """A SyntaxError leaves the scope rule unused; the reader still answers."""
    assert is_dangerous_command(f"python3 - <<'EOF'\nimport os\nc = \"{D}\"\nos.system(c)\nnot python (\nEOF") is True


def test_reaching_definitions_use_character_offsets_on_non_ascii_lines():
    import shared.merge_guard_common as mgc

    code = 'n = "é ü"; c = ["gh"]; run(c)'
    use = code.rindex("c)")
    assert mgc._named_values_at("python3", code, use) == ([code.index('["gh"]')], use + 1)


# Without a parser, scope comes from function spans: a parameter is unreadable, an
# assignment in another function does not reach, and the walk back from a use stops at
# the first assignment at the use's depth or shallower. Python that does not parse uses
# def and lambda spans with indentation as depth.
SPAN_SCOPE_DENY = [
    ('node: reuse', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nlet cmd = "gh pr view 5";\ncp.execSync(cmd);\ncmd = "' + M + ' 5 --squash";\ncp.execSync(cmd);\nEOF2'),
    ('node: if/else', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nlet cmd;\nif (process.argv[2]) { cmd = "' + M + ' 5 --squash"; } else { cmd = "gh pr view 5"; }\ncp.execSync(cmd);\nEOF2'),
    ('node: helper-local merge', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction go() { const cmd = "' + M + ' 5 --squash"; cp.execSync(cmd); }\ngo();\nEOF2'),
    ('node: free module variable', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst cmd = "' + M + ' 5 --squash";\nfunction go() { cp.execSync(cmd); }\ngo();\nEOF2'),
    ('php: string in a variable', 'php -r \'$cmd = "' + M + ' 5"; shell_exec($cmd);\''),
    ('node: block-local const run in its block', 'node - <<\'EOF2\'\nconst cp = require("child_process");\n{ const cmd = "' + M + ' 5"; cp.execSync(cmd); }\nEOF2'),
    ('python that does not parse: helper-local merge', 'python3 - <<\'EOF2\'\nimport subprocess\ndef go():\n    cmd = ["gh", "pr", "merge", "5"]\n    subprocess.run(cmd)\ngo()\nthis line is not python (\nEOF2'),
]
SPAN_SCOPE_ALLOW = [
    ('node: merge local to another function', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction show() { const cmd = "' + M + ' 5 --squash"; console.log(cmd); }\nfunction check() { cp.execSync(cmd); }\nconst cmd = "gh pr view 5";\ncheck();\nEOF2'),
    ('node: parameter of a helper', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst cmd = "' + M + ' 5 --squash";\nconsole.log(cmd);\nfunction run(cmd) { cp.execSync(cmd); }\nrun("gh pr view 5");\nEOF2'),
    ('node: arrow parameter', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst cmd = "' + M + ' 5 --squash";\nconsole.log(cmd);\nconst run = (cmd) => { cp.execSync(cmd); };\nrun("gh pr view 5");\nEOF2'),
    ('node: block-local const', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nlet cmd = "gh pr view 5";\n{ const cmd = "' + M + ' 5"; console.log(cmd); }\ncp.execSync(cmd);\nEOF2'),
    ('perl: parameter from @_', 'perl - <<\'EOF2\'\nmy $cmd = "' + M + ' 5 --squash";\nprint "$cmd\\n";\nsub run { my ($cmd) = @_; system($cmd); }\nrun("gh pr view 5");\nEOF2'),
    ('php: parameter', 'php -r \'$cmd = "' + M + ' 5 --squash"; echo $cmd; function run($cmd) { shell_exec($cmd); } run("gh pr view 5");\''),
    ('R: parameter', 'Rscript -e \'cmd = "' + M + ' 5 --squash"; print(cmd); run = function(cmd) { system(cmd) }; run("gh pr view 5")\''),
    ('python that does not parse: parameter of a helper', 'python3 - <<\'EOF2\'\nimport subprocess\ncmd = ["gh", "pr", "merge", "5", "--squash"]\nprint(cmd)\ndef run(cmd):\n    subprocess.run(cmd)\nrun(["gh", "pr", "view", "5"])\nthis line is not python (\nEOF2'),
    ("node: merge printed, then reassigned",
     "node - <<'EOF'\nconst cp = require(\"child_process\");\nlet cmd = \"gh pr " + "merge 5\";\nconsole.log(cmd);\ncmd = \"gh pr view 5\";\ncp.execSync(cmd);\nEOF"),
    ("node: unbalanced braces leave one span", "node -e 'const cmd = \"gh pr view 5\"; function f() { const cmd = \"x\"; require(\"child_process\").execSync(cmd)'"),
]


@pytest.mark.parametrize("label,cmd", SPAN_SCOPE_DENY, ids=[r[0] for r in SPAN_SCOPE_DENY])
def test_a_merge_reaching_the_use_in_its_span_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", SPAN_SCOPE_ALLOW, ids=[r[0] for r in SPAN_SCOPE_ALLOW])
def test_a_merge_not_reaching_the_use_in_its_span_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# Ruby and lua spans come from their block keywords; JS methods are spans too.
KEYWORD_SPAN_DENY = [
    ('ruby: reuse', 'ruby - <<\'EOF2\'\nc = "gh pr view 5"\nsystem(c)\nc = "' + M + ' 5 --squash"\nsystem(c)\nEOF2'),
    ('ruby: if/else', 'ruby - <<\'EOF2\'\nif ARGV[0]\n  c = "' + M + ' 5 --squash"\nelse\n  c = "gh pr view 5"\nend\nsystem(c)\nEOF2'),
    ('ruby: helper-local merge', 'ruby - <<\'EOF2\'\ndef go\n  c = "' + M + ' 5 --squash"\n  system(c) if c\nend\ngo\nEOF2'),
    ('ruby: merge used after an endless def', 'ruby - <<\'EOF2\'\ncmd = "' + M + ' 5 --squash"\ndef show(cmd) =\n  puts(cmd)\nsystem(cmd)\nEOF2'),
    ('lua: helper-local merge', 'lua - <<\'EOF2\'\nlocal function go()\n  local c = "' + M + ' 5 --squash"\n  os.execute(c)\nend\ngo()\nEOF2'),
    ('lua: if/else', 'lua - <<\'EOF2\'\nlocal c\nif arg[1] then c = "' + M + ' 5" else c = "gh pr view 5" end\nos.execute(c)\nEOF2'),
    ('node: method-local merge', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nclass Runner {\n  go() { const c = "' + M + ' 5 --squash"; cp.execSync(c); }\n}\nnew Runner().go();\nEOF2'),
]
KEYWORD_SPAN_ALLOW = [
    ('ruby: helper parameter, modifier if', 'ruby - <<\'EOF2\'\ncmd = "' + M + ' 5 --squash"\nputs cmd\ndef run(cmd)\n  return if cmd.nil?\n  system(cmd)\nend\nrun("gh pr view 5")\nEOF2'),
    ('ruby: while-do, endless def and block parameter', 'ruby - <<\'EOF2\'\ncmd = "' + M + ' 5 --squash"\nputs cmd\nwhile false do\n  break\nend\ndef run(cmd) = system(cmd)\n["gh pr view 5"].each do |cmd|\n  system(cmd)\nend\nEOF2'),
    ('ruby: endless def body on the next line', 'ruby - <<\'EOF2\'\ncmd = "' + M + ' 5 --squash"\nputs cmd\ndef run(cmd) =\n  system(cmd)\nrun("gh pr view 5")\nEOF2'),
    ('ruby: brace block parameter', 'ruby - <<\'EOF2\'\ncmd = "' + M + ' 5 --squash"\nputs cmd\n["gh pr view 5"].each { |cmd| system(cmd) }\nEOF2'),
    ('lua: helper parameter, for-do and if', 'lua - <<\'EOF2\'\nlocal cmd = "' + M + ' 5 --squash"\nprint(cmd)\nlocal function run(cmd)\n  for i = 1, 1 do\n    if cmd then os.execute(cmd) end\n  end\nend\nrun("gh pr view 5")\nEOF2'),
    ('node: class method parameter', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst cmd = "' + M + ' 5 --squash";\nconsole.log(cmd);\nclass Runner {\n  run(cmd) { cp.execSync(cmd); }\n}\nnew Runner().run("gh pr view 5");\nEOF2'),
    ('node: object shorthand method parameter', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst cmd = "' + M + ' 5 --squash";\nconsole.log(cmd);\nconst tools = {\n  async run(cmd) { cp.execSync(cmd); },\n};\ntools.run("gh pr view 5");\nEOF2'),
    ("ruby: unbalanced keywords leave one span",
     "ruby -e 'c = \"gh pr view 5\"; def f(c)\n  x = 1\n  system(c)'"),
]


@pytest.mark.parametrize("label,cmd", KEYWORD_SPAN_DENY, ids=[r[0] for r in KEYWORD_SPAN_DENY])
def test_a_merge_reaching_the_use_in_its_keyword_span_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", KEYWORD_SPAN_ALLOW, ids=[r[0] for r in KEYWORD_SPAN_ALLOW])
def test_a_merge_not_reaching_the_use_in_its_keyword_span_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# An unresolved program read as gh must name gh or hub: in its own text
# (`shutil.which("gh")`, `os.environ.get("GH_BIN", "gh")`), in a bare name (`hub`), or
# in a value assigned to that name. `sys.executable` or an unnamed environment variable
# before `"pr"` runs some other program.
LEADER_DENY = [
    ("which gh assigned", "python3 - <<'EOF'\nimport subprocess, shutil\ngh = shutil.which(\"gh\")\nsubprocess.run([gh, \"pr\", \"merge\", \"5\"])\nEOF"),
    ("which hub assigned to cli", "python3 - <<'EOF'\nimport subprocess, shutil\ncli = shutil.which(\"hub\")\nsubprocess.run([cli, \"pr\", \"merge\", \"5\"])\nEOF"),
    ("environ default gh", "python3 - <<'EOF'\nimport subprocess, os\nbinary = os.environ.get(\"GH_BIN\", \"gh\")\nsubprocess.run([binary, \"pr\", \"merge\", \"5\"])\nEOF"),
    ("inline which gh", "python3 -c 'import subprocess, shutil; subprocess.run([shutil.which(\"gh\"), \"pr\", \"merge\", \"5\"])'"),
    ("parameter named hub", "python3 - <<'EOF'\nimport subprocess\ndef run(hub):\n    subprocess.run([hub, \"pr\", \"merge\", \"5\"])\nrun(None)\nEOF"),
]
LEADER_ALLOW = [
    ("sys.executable script", "python3 - <<'EOF'\nimport subprocess, sys\nsubprocess.run([sys.executable, \"pr\", \"merge.py\"])\nEOF"),
    ("unnamed environment tool", "python3 - <<'EOF'\nimport subprocess, os\ntool = os.environ[\"MY_CLI\"]\nsubprocess.run([tool, \"pr\", \"merge\", \"5\"])\nEOF"),
    ("inline sys.executable", "python3 -c 'import subprocess, sys; subprocess.run([sys.executable, \"pr\", \"merge\", \"5\"])'"),
    ("SIGH is not gh", "python3 -c 'import subprocess, os; subprocess.run([os.environ[\"SIGH\"], \"pr\", \"merge\", \"5\"])'"),
    ("ghost is not gh", "python3 -c 'import subprocess; ghost = \"/usr/bin/ghost\"; subprocess.run([ghost, \"pr\", \"merge\", \"5\"])'"),
]


@pytest.mark.parametrize("label,cmd", LEADER_DENY, ids=[r[0] for r in LEADER_DENY])
def test_a_leader_naming_gh_runs_gh_pr(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", LEADER_ALLOW, ids=[r[0] for r in LEADER_ALLOW])
def test_a_leader_not_naming_gh_is_some_other_program(label, cmd):
    assert is_dangerous_command(cmd) is False


# shell_c_string: the string a POSIX shell leg runs with -c, for minting an approval of
# a guarded command inside it.
SHELL_C_STRING_ROWS = [
    ("plain -c", "bash -c 'git push --force origin feat'", "git push --force origin feat"),
    ("env -i, assignment, path head, redirect",
     "env -i HOME=/x /bin/bash -c 'command -v git; git push --force origin feat' 2>&1",
     "command -v git; git push --force origin feat"),
    ("timeout, double quotes", 'timeout 30 sh -c "git branch -D feat"', "git branch -D feat"),
    ("bundled -lc", "bash -lc 'gh pr merge 5'", "gh pr merge 5"),
    ("--norc", "bash --norc -c 'gh pr merge 5'", "gh pr merge 5"),
    ("bundled -eu", "bash -eu -c 'gh pr merge 5'", "gh pr merge 5"),
    ("separate -e -u", "bash -e -u -c 'gh pr merge 5'", "gh pr merge 5"),
    ("-o value", "bash -o pipefail -c 'gh pr merge 5'", "gh pr merge 5"),
    ("sudo, two redirects", "sudo bash -c 'gh pr merge 5' > out.txt 2>&1", "gh pr merge 5"),
    ("leading assignment", "FOO=1 bash -c 'gh pr merge 5'", "gh pr merge 5"),
    ("single-quoted $ reaches the inner shell", "bash -c 'gh pr merge $N'", "gh pr merge $N"),
    ("script path", "bash script.sh", None),
    ("interpreter", "python3 -c 'print(1)'", None),
    ("no string", "bash -c", None),
    ("a positional word the string does not read", "bash -c 'x' arg0", "x"),
    ("a positional parameter with no word", "bash -c 'gh pr view $2' _ 5", None),
    ("all positional parameters", "bash -c 'echo \"$@\"' _ a b", "echo a b"),
    ("all positional parameters with none given", "bash -c 'echo \"$@\"' _", None),
    ("unbalanced quote", "bash -c 'unbalanced", None),
    ("double-quoted $VAR as written", 'bash -c "gh pr merge $N"', "gh pr merge $N"),
    ("double-quoted ${VAR} as written", 'bash -c "git push --force origin ${B}"', "git push --force origin ${B}"),
    ("outer command substitution", 'bash -c "git push --force origin $(git branch --show-current)"', None),
    ("outer backtick", 'bash -c "gh pr merge `n`"', None),
    ("single-quoted substitution reaches the inner shell", "bash -c 'gh pr merge $(cat n)'", "gh pr merge $(cat n)"),
    ("eval", "eval 'gh pr merge 5'", None),
    ("ssh", "ssh host -c 'x'", None),
    ("unknown long flag", "bash --rcfile x -c 'y'", None),
    ("unknown short flag", "bash -X -c 'y'", None),
    ("empty", "", None),
]


@pytest.mark.parametrize("label,cmd,want", SHELL_C_STRING_ROWS, ids=[r[0] for r in SHELL_C_STRING_ROWS])
def test_shell_c_string_reads_the_string_a_shell_runs(label, cmd, want):
    from shared.merge_guard_common import shell_c_string

    assert shell_c_string(cmd) == want


# A POSIX shell's -c string is judged as the bare command it runs: a push to main, a
# remote branch delete or a mirror push inside `bash -c` is refused as it is bare.
SHELL_C_PUSH_OPS = [
    "git push origin main", "git push origin --delete feat", "git push origin :feat", "git push --mirror origin",
]
SHELL_C_WRAPPERS = [
    "bash -c '{}'", "sh -c '{}'", "bash -lc '{}'", "bash -eu -c '{}'", "bash --norc -c '{}'",
    "env -i HOME=/x /bin/bash -c 'command -v git; {}' 2>&1", "timeout 30 bash -c '{}'",
]
SHELL_C_DENY = [(w.format(op), op) for w in SHELL_C_WRAPPERS for op in SHELL_C_PUSH_OPS] + [
    ('bash -c "cd $DIR && git push origin main"', "outer expansion"),
    ("bash -c 'git push origin main' name", "argument after the string"),
]
SHELL_C_ALLOW = [
    "bash -c 'git push origin feat'",
    "bash -c 'git status'",
    "bash -c \"echo 'git push origin main'\"",
    "sh -c 'git fetch origin main'",
]
SHELL_C_COMPOUND = [
    ("bash -c 'gh pr merge 5 && git branch -D x'", True),
    ("bash -c 'git push origin main; git push origin :feat'", True),
    ("bash -c 'git push origin main' && gh pr merge 5", True),
    ("sh -c \"bash -c 'gh pr merge 5 && git push origin main'\"", True),
    ("bash -c 'gh pr merge 5 && echo ok'", False),
    ("bash -c 'ls # then gh pr merge 5' && gh pr merge 6", False),
    ("bash -c 'git push origin main'", False),
]


@pytest.mark.parametrize("cmd,label", SHELL_C_DENY, ids=[r[0] for r in SHELL_C_DENY])
def test_a_shell_c_string_is_judged_as_the_bare_command(cmd, label):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("cmd", SHELL_C_ALLOW)
def test_a_shell_c_string_running_nothing_guarded_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("cmd,want", SHELL_C_COMPOUND, ids=[r[0] for r in SHELL_C_COMPOUND])
def test_a_shell_c_string_counts_its_destructive_legs(cmd, want):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_compound_destructive_command(cmd) is want


# A name read through an expression: a name operand of a string `+` reads its literal
# values, a list `+` and a spread (`*a`, `...a`) or an R `c(...)` splice take a named
# list's elements, and R `<-`/`<<-` and TypeScript `name: T =` are assignments.
NAME_EXPR_DENY = [
    ('js typed const', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst c: string = "' + M + ' 5";\ncp.execSync(c);\nEOF2'),
    ('bun typed let', 'bun -e \'const cp = require("child_process"); let c: string = "' + M + ' 5"; cp.execSync(c);\''),
    ('js concat names', 'node -e \'const cp = require("child_process"); const base = "gh pr "; cp.execSync(base + "merge 5");\''),
    ('two names', "python3 - <<'EOF2'\nimport os\na = \"gh pr \"\nb = \"merge 5\"\nos.system(a + b)\nEOF2"),
    ('chained name', "python3 - <<'EOF2'\nimport os\nbase = \"gh \"\nsub = base + \"pr \"\nos.system(sub + \"merge 5\")\nEOF2"),
    ('py concat names', "python3 - <<'EOF2'\nimport os\nbase = \"gh pr \"\nos.system(base + \"merge 5\")\nEOF2"),
    ('py list concat', "python3 - <<'EOF2'\nimport subprocess\nA = [\"gh\", \"pr\"]\nB = [\"merge\", \"5\"]\nsubprocess.run(A + B)\nEOF2"),
    ('py spread', "python3 - <<'EOF2'\nimport subprocess\nA = [\"gh\", \"pr\"]\nsubprocess.run([*A, \"merge\", \"5\"])\nEOF2"),
    ('js spread', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst a = ["pr", "merge", "5"];\ncp.execFileSync("gh", [...a]);\nEOF2'),
    ('R arrow', "Rscript -e 'c <- \"" + M + " 5\"; system(c)'"),
    ('R superarrow', "Rscript -e 'f <- function() { c <<- \"" + M + " 5\" }; f(); system(c)'"),
    ('R c splice', "Rscript -e 'a <- c(\"pr\", \"merge\"); system2(\"gh\", c(a, \"5\"))'"),
]
NAME_EXPR_ALLOW = [
    ('bun typed let view', 'bun -e \'const cp = require("child_process"); let c: string = "gh pr view 5"; cp.execSync(c);\''),
    ('js concat names view', 'node -e \'const cp = require("child_process"); const base = "gh pr "; cp.execSync(base + "view 5");\''),
    ('self-referential loop', "python3 - <<'EOF2'\nimport os\nx = \"\"\nfor p in [\"a\", \"b\"]:\n    x = x + p\nos.system(x + \" ls\")\nEOF2"),
    ('R c splice view', "Rscript -e 'a <- c(\"pr\", \"view\"); system2(\"gh\", c(a, \"5\"))'"),
    ('py spread view', "python3 - <<'EOF2'\nimport subprocess\nA = [\"gh\", \"pr\"]\nsubprocess.run([*A, \"view\", \"5\"])\nEOF2"),
    ('py concat names view', "python3 - <<'EOF2'\nimport os\nbase = \"gh pr \"\nos.system(base + \"view 5\")\nEOF2"),
    ('py list concat view', "python3 - <<'EOF2'\nimport subprocess\nA = [\"gh\", \"pr\"]\nB = [\"view\", \"5\"]\nsubprocess.run(A + B)\nEOF2"),
    ('R arrow view', "Rscript -e 'c <- \"gh pr view 5\"; system(c)'"),
]


@pytest.mark.parametrize("label,cmd", NAME_EXPR_DENY, ids=[r[0] for r in NAME_EXPR_DENY])
def test_a_merge_reached_through_a_name_expression_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", NAME_EXPR_ALLOW, ids=[r[0] for r in NAME_EXPR_ALLOW])
def test_a_view_reached_through_a_name_expression_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# A helper function: a parameter reads what its calls pass for it (by position, by
# keyword, or its default), and a call to a function the program defines reads what
# it returns. A helper called with two merges runs two merges: refused, and no single
# approval mints it.
HELPER_DENY = [
    ('param list', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef run(c):\n    subprocess.run(c)\nrun(["gh", "pr", "merge", "5"])\nEOF2'),
    ('param string', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef sh(cmd):\n    os.system(cmd)\nsh("' + M + ' 5")\nEOF2'),
    ('param keyword', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef run(c, check=True):\n    subprocess.run(c, check=check)\nrun(check=False, c=["gh", "pr", "merge", "5"])\nEOF2'),
    ('param via name', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef run(c):\n    subprocess.run(c)\nargs = ["gh", "pr", "merge", "5"]\nrun(args)\nEOF2'),
    ('param two hops', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef run(c):\n    subprocess.run(c)\ndef go(x):\n    run(x)\ngo(["gh", "pr", "merge", "5"])\nEOF2'),
    ('param default', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef run(c=["gh", "pr", "merge", "5"]):\n    subprocess.run(c)\nrun()\nEOF2'),
    ('param prefix concat', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef pr(*rest):\n    pass\ndef run(sub):\n    subprocess.run(["gh", "pr"] + sub)\nrun(["merge", "5"])\nEOF2'),
    ('return list', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef build():\n    return ["gh", "pr", "merge", "5"]\nsubprocess.run(build())\nEOF2'),
    ('return via name', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef build():\n    return ["gh", "pr", "merge", "5"]\nc = build()\nsubprocess.run(c)\nEOF2'),
    ('return local name', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef build():\n    out = ["gh", "pr", "merge", "5"]\n    return out\nsubprocess.run(build())\nEOF2'),
    ('return string', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef cmd():\n    return "' + M + ' 5"\nos.system(cmd())\nEOF2'),
    ('two merges via helper', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef run(c):\n    subprocess.run(c)\nrun(["gh", "pr", "merge", "5"])\nrun(["gh", "pr", "merge", "6"])\nEOF2'),
]
HELPER_ALLOW = [
    ('a nested def returns the merge, the helper returns a view', 'python3 - <<\'EOF2\'\nimport subprocess\ndef build():\n    def inner():\n        return ["gh", "pr", "merge", "5"]\n    return ["gh", "pr", "view", "5"]\nsubprocess.run(build())\nEOF2'),
    ('param list view', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef run(c):\n    subprocess.run(c)\nrun(["gh", "pr", "view", "5"])\nEOF2'),
    ('param string view', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef sh(cmd):\n    os.system(cmd)\nsh("gh pr view 5")\nEOF2'),
    ('param two hops view', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef run(c):\n    subprocess.run(c)\ndef go(x):\n    run(x)\ngo(["gh", "pr", "view", "5"])\nEOF2'),
    ('return list view', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef build():\n    return ["gh", "pr", "view", "5"]\nsubprocess.run(build())\nEOF2'),
    ('return string view', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef cmd():\n    return "gh pr view 5"\nos.system(cmd())\nEOF2'),
    ('helper never passed merge, module merge printed', 'python3 - <<\'EOF2\'\nimport subprocess, os\nc = "' + M + ' 5"\nprint(c)\ndef run(c):\n    subprocess.run(c)\nrun(["gh", "pr", "view", "5"])\nEOF2'),
    ('uncalled helper', 'python3 - <<\'EOF2\'\nimport subprocess, os\ndef run(c):\n    subprocess.run(c)\nprint(["gh", "pr", "merge", "5"])\nEOF2'),
]


@pytest.mark.parametrize("label,cmd", HELPER_DENY, ids=[r[0] for r in HELPER_DENY])
def test_a_merge_through_a_helper_function_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", HELPER_ALLOW, ids=[r[0] for r in HELPER_ALLOW])
def test_a_view_through_a_helper_function_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_helper_called_with_two_merges_is_compound_and_mints_nothing():
    from shared.merge_guard_common import interpreter_command_context, is_compound_destructive_command

    cmd = dict(HELPER_DENY)["two merges via helper"]
    assert is_compound_destructive_command(cmd) is True
    assert interpreter_command_context(cmd) is None


def test_a_helper_called_once_reads_one_merge_context():
    from shared.merge_guard_common import interpreter_command_context

    context = interpreter_command_context(dict(HELPER_DENY)["param list"])
    assert context is not None and context["pr_number"] == "5"


def test_a_parameter_passed_through_an_unpacked_call_reads_nothing_known():
    """`run(*args)` cannot be matched to parameters, so the parameter stays unreadable."""
    from shared.merge_guard_common import _OPAQUE, _python_reaching

    code = "import subprocess\ndef run(c):\n    subprocess.run(c)\nrun(*[['gh', 'pr', 'view']])\n"
    reach = _python_reaching(code)
    assert reach is not None and reach.uses[code.index("run(c)\n") + 4] == frozenset({_OPAQUE})


# A named JS function (`function f(c) {…}`, `const f = (c) => …`): a parameter reads
# what its calls pass in that position, and a call reads what the function returns. A
# method call (`o.f(…)`), a spread argument or a name declared twice reads nothing.
JS_HELPER_DENY = [
    ('function expression param', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst sh = function(c) { cp.execSync(c); };\nsh("' + M + ' 5");\nEOF2'),
    ('fn param', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction run(c) { cp.execFileSync(c[0], c.slice(1)); }\nfunction sh(c) { cp.execSync(c); }\nsh("' + M + ' 5");\nEOF2'),
    ('arrow param', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst sh = (c) => cp.execSync(c);\nsh("' + M + ' 5");\nEOF2'),
    ('arrow block param', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst sh = (c) => { cp.execSync(c); };\nsh("' + M + ' 5");\nEOF2'),
    ('fn return', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction cmd() { return "' + M + ' 5"; }\ncp.execSync(cmd());\nEOF2'),
    ('arrow expr return', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst cmd = () => "' + M + ' 5";\ncp.execSync(cmd());\nEOF2'),
    ('return via name', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction cmd() { return "' + M + ' 5"; }\nconst c = cmd();\ncp.execSync(c);\nEOF2'),
    ('two hops', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\nfunction go(x) { sh(x); }\ngo("' + M + ' 5");\nEOF2'),
    ('second param', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(opts, c) { cp.execSync(c, opts); }\nsh({}, "' + M + ' 5");\nEOF2'),
]
JS_HELPER_ALLOW = [
    ('function expression param view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst sh = function(c) { cp.execSync(c); };\nsh("gh pr view 5");\nEOF2'),
    ('fn param view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\nsh("gh pr view 5");\nEOF2'),
    ('fn return view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction cmd() { return "gh pr view 5"; }\ncp.execSync(cmd());\nEOF2'),
    ('module merge printed, arrow param view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst c = "' + M + ' 5";\nconsole.log(c);\nconst sh = (c) => cp.execSync(c);\nsh("gh pr view 5");\nEOF2'),
    ('fn param reassigned to view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { c = "gh pr view 5"; cp.execSync(c); }\nsh("' + M + ' 5");\nEOF2'),
    ('method call not the helper', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\nconst o = { sh: (x) => console.log(x) };\no.sh("' + M + ' 5");\nsh("gh pr view 5");\nEOF2'),
    ('declared twice', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\nfunction sh(c) { console.log(c); }\nsh("' + M + ' 5");\nEOF2'),
    ("nested return is not the helper's", 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction cmd() { const f = () => { return "' + M + ' 5"; }; return "gh pr view 5"; }\ncp.execSync(cmd());\nEOF2'),
    ('two hops view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\nfunction go(x) { sh(x); }\ngo("gh pr view 5");\nEOF2'),
    ('spread call', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\nsh(...["gh pr view 5"]);\nEOF2'),
]


@pytest.mark.parametrize("label,cmd", JS_HELPER_DENY, ids=[r[0] for r in JS_HELPER_DENY])
def test_a_merge_through_a_js_helper_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", JS_HELPER_ALLOW, ids=[r[0] for r in JS_HELPER_ALLOW])
def test_a_view_through_a_js_helper_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_js_call_with_a_spread_argument_passes_nothing_known():
    """`sh(...a, x)` cannot be matched to parameters by position, so it passes nothing."""
    from shared.merge_guard_common import _calls

    code = 'function sh(c) { console.log(c); }\nsh(...a, "x");\nsh("y");\n'
    assert _calls(code, "node", "sh") == [[], [code.index('"y"')]]


# A named helper in ruby, perl, lua, php or R: a parameter reads what its calls pass
# (parentheses or, in ruby and perl, none), and a call reads what the helper returns,
# including ruby's and R's last expression.
OTHER_HELPER_DENY = [
    ('ruby param parens', 'ruby - <<\'EOF2\'\ndef run(c)\n  system(c)\nend\nrun("' + M + ' 5")\nEOF2'),
    ('ruby param no parens', 'ruby - <<\'EOF2\'\ndef run(c)\n  system(c)\nend\nrun "' + M + ' 5"\nEOF2'),
    ('ruby two hops', 'ruby - <<\'EOF2\'\ndef run(c)\n  system(c)\nend\ndef go(x)\n  run(x)\nend\ngo("' + M + ' 5")\nEOF2'),
    ('ruby implicit return', 'ruby -e \'def cmd; "' + M + ' 5"; end; system(cmd)\''),
    ('ruby implicit return multiline', 'ruby - <<\'EOF2\'\ndef cmd\n  "' + M + ' 5"\nend\nsystem(cmd)\nEOF2'),
    ('ruby explicit return', 'ruby - <<\'EOF2\'\ndef cmd\n  return "' + M + ' 5"\nend\nsystem(cmd())\nEOF2'),
    ('perl list params', 'perl -e \'sub run { my ($c) = @_; system($c); } run("' + M + ' 5");\''),
    ('perl shift param', 'perl -e \'sub run { my $c = shift; system($c); } run("' + M + ' 5");\''),
    ('perl ampersand call', 'perl -e \'sub run { my ($c) = @_; system($c); } &run("' + M + ' 5");\''),
    ('perl return', 'perl -e \'sub cmd { return "' + M + ' 5"; } system(cmd());\''),
    ('lua param', 'lua -e \'local function run(c) os.execute(c) end run("' + M + ' 5")\''),
    ('lua return', 'lua -e \'local function cmd() return "' + M + ' 5" end os.execute(cmd())\''),
    ('php param', 'php -r \'function run($c) { system($c); } run("' + M + ' 5");\''),
    ('php return', 'php -r \'function cmd() { return "' + M + ' 5"; } system(cmd());\''),
    ('R param expression body', 'Rscript -e \'run <- function(c) system(c); run("' + M + ' 5")\''),
    ('R param block body', 'Rscript -e \'run <- function(c) { system(c) }; run("' + M + ' 5")\''),
    ('R implicit return', 'Rscript -e \'cmd <- function() { "' + M + ' 5" }; system(cmd())\''),
]
OTHER_HELPER_ALLOW = [
    ('ruby param parens view', 'ruby - <<\'EOF2\'\ndef run(c)\n  system(c)\nend\nrun("gh pr view 5")\nEOF2'),
    ('ruby param no parens view', 'ruby - <<\'EOF2\'\ndef run(c)\n  system(c)\nend\nrun "gh pr view 5"\nEOF2'),
    ('ruby two hops view', 'ruby - <<\'EOF2\'\ndef run(c)\n  system(c)\nend\ndef go(x)\n  run(x)\nend\ngo("gh pr view 5")\nEOF2'),
    ('ruby implicit return view', 'ruby -e \'def cmd; "gh pr view 5"; end; system(cmd)\''),
    ('ruby implicit return multiline view', 'ruby - <<\'EOF2\'\ndef cmd\n  "gh pr view 5"\nend\nsystem(cmd)\nEOF2'),
    ('ruby explicit return view', 'ruby - <<\'EOF2\'\ndef cmd\n  return "gh pr view 5"\nend\nsystem(cmd())\nEOF2'),
    ('perl list params view', 'perl -e \'sub run { my ($c) = @_; system($c); } run("gh pr view 5");\''),
    ('perl shift param view', 'perl -e \'sub run { my $c = shift; system($c); } run("gh pr view 5");\''),
    ('perl ampersand call view', 'perl -e \'sub run { my ($c) = @_; system($c); } &run("gh pr view 5");\''),
    ('perl return view', 'perl -e \'sub cmd { return "gh pr view 5"; } system(cmd());\''),
    ('lua param view', 'lua -e \'local function run(c) os.execute(c) end run("gh pr view 5")\''),
    ('lua return view', 'lua -e \'local function cmd() return "gh pr view 5" end os.execute(cmd())\''),
    ('php param view', 'php -r \'function run($c) { system($c); } run("gh pr view 5");\''),
    ('php return view', 'php -r \'function cmd() { return "gh pr view 5"; } system(cmd());\''),
    ('R param expression body view', 'Rscript -e \'run <- function(c) system(c); run("gh pr view 5")\''),
    ('R param block body view', 'Rscript -e \'run <- function(c) { system(c) }; run("gh pr view 5")\''),
    ('R implicit return view', 'Rscript -e \'cmd <- function() { "gh pr view 5" }; system(cmd())\''),
    ('ruby param reassigned to view', 'ruby - <<\'EOF2\'\ndef run(c)\n  c = "gh pr view 5"\n  system(c)\nend\nrun("' + M + ' 5")\nEOF2'),
    ('ruby declared twice', 'ruby - <<\'EOF2\'\ndef run(c)\n  system(c)\nend\ndef run(c)\n  puts(c)\nend\nrun("' + M + ' 5")\nEOF2'),
    ('ruby module merge printed, helper view', 'ruby - <<\'EOF2\'\nc = "' + M + ' 5"\nputs c\ndef run(c)\n  system(c)\nend\nrun("gh pr view 5")\nEOF2'),
    ('perl helper never called', 'perl -e \'sub run { my ($c) = @_; system($c); } print("' + M + ' 5");\''),
    ('lua method call not the helper', 'lua -e \'local function run(c) os.execute(c) end obj.run("' + M + ' 5") run("gh pr view 5")\''),
]


@pytest.mark.parametrize("label,cmd", OTHER_HELPER_DENY, ids=[r[0] for r in OTHER_HELPER_DENY])
def test_other_helper_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", OTHER_HELPER_ALLOW, ids=[r[0] for r in OTHER_HELPER_ALLOW])
def test_other_helper_view_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# A literal object or dict passed to a helper and read by a constant key: a JS
# destructured parameter or opts.cmd / opts["cmd"], a Python opts["cmd"] or opts.get("cmd").
# A later entry wins: a later key or a spread of a literal that sets the key replaces
# the value, and a spread that may not set it keeps it as a possible value. A computed
# key, another key or a non-literal argument reads nothing.
OBJECT_HELPER_DENY = [
    ('js destructured arrow', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst sh = ({cmd}) => cp.execSync(cmd);\nsh({cmd: "' + M + ' 5"});\nEOF2'),
    ('js destructured function', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh({cmd}) { cp.execSync(cmd); }\nsh({cmd: "' + M + ' 5"});\nEOF2'),
    ('js destructured alias', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh({c: cmd}) { cp.execSync(cmd); }\nsh({c: "' + M + ' 5"});\nEOF2'),
    ('js spread before the key', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst base = {cwd: "."};\nfunction sh(opts) { cp.execSync(opts.cmd); }\nsh({...base, cmd: "' + M + ' 5"});\nEOF2'),
    ('py dict later key wins', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts["cmd"])\nrun({"cmd": ["ls"], "cmd": ["gh", "pr", "merge", "5"]})\nEOF2'),
    ('js spread after the key', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst opts = {timeout: 5};\nfunction sh(o) { cp.execSync(o.cmd, o); }\nsh({cmd: "' + M + ' 5", ...opts});\nEOF2'),
    ('js spread of an unknown after the key', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(o) { cp.execSync(o.cmd); }\nsh({cmd: "' + M + ' 5", ...load()});\nEOF2'),
    ('py spread after the key', 'python3 - <<\'EOF2\'\nimport subprocess\nopts = {"check": True}\ndef run(o):\n    subprocess.run(o["cmd"])\nrun({"cmd": ["gh", "pr", "merge", "5"], **opts})\nEOF2'),
    ('js spread of a literal setting cmd', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(o) { cp.execSync(o.cmd); }\nsh({cmd: "gh pr view 5", ...{cmd: "' + M + ' 5"}});\nEOF2'),
    ('js destructured second key', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh({cwd, cmd}) { cp.execSync(cmd, {cwd}); }\nsh({cwd: ".", cmd: "' + M + ' 5"});\nEOF2'),
    ('js member dot', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(opts) { cp.execSync(opts.cmd); }\nsh({cmd: "' + M + ' 5"});\nEOF2'),
    ('js member bracket', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(opts) { cp.execSync(opts["cmd"]); }\nsh({"cmd": "' + M + ' 5"});\nEOF2'),
    ('py dict subscript', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts["cmd"])\nrun({"cmd": ["gh", "pr", "merge", "5"]})\nEOF2'),
    ('py dict get', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts.get("cmd"))\nrun({"cmd": ["gh", "pr", "merge", "5"]})\nEOF2'),
    ('py dict string', 'python3 - <<\'EOF2\'\nimport subprocess\nimport os\ndef run(opts):\n    os.system(opts["cmd"])\nrun({"cmd": "' + M + ' 5"})\nEOF2'),
    ('py dict through a name', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts["cmd"])\no = {"cmd": ["gh", "pr", "merge", "5"]}\nrun(o)\nEOF2'),
]
OBJECT_HELPER_ALLOW = [
    ('js destructured arrow view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst sh = ({cmd}) => cp.execSync(cmd);\nsh({cmd: "gh pr view 5"});\nEOF2'),
    ('js destructured function view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh({cmd}) { cp.execSync(cmd); }\nsh({cmd: "gh pr view 5"});\nEOF2'),
    ('js destructured alias view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh({c: cmd}) { cp.execSync(cmd); }\nsh({c: "gh pr view 5"});\nEOF2'),
    ('js spread before the key view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst base = {cwd: "."};\nfunction sh(opts) { cp.execSync(opts.cmd); }\nsh({...base, cmd: "gh pr view 5"});\nEOF2'),
    ('py dict later key wins view', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts["cmd"])\nrun({"cmd": ["ls"], "cmd": ["gh", "pr", "view", "5"]})\nEOF2'),
    ('js spread after the key view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst opts = {timeout: 5};\nfunction sh(o) { cp.execSync(o.cmd, o); }\nsh({cmd: "gh pr view 5", ...opts});\nEOF2'),
    ('js spread of an unknown after the key view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(o) { cp.execSync(o.cmd); }\nsh({cmd: "gh pr view 5", ...load()});\nEOF2'),
    ('py spread after the key view', 'python3 - <<\'EOF2\'\nimport subprocess\nopts = {"check": True}\ndef run(o):\n    subprocess.run(o["cmd"])\nrun({"cmd": ["gh", "pr", "view", "5"], **opts})\nEOF2'),
    ('js spread of a literal setting cmd view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(o) { cp.execSync(o.cmd); }\nsh({cmd: "gh pr view 5", ...{cmd: "gh pr view 5"}});\nEOF2'),
    ('js destructured second key view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh({cwd, cmd}) { cp.execSync(cmd, {cwd}); }\nsh({cwd: ".", cmd: "gh pr view 5"});\nEOF2'),
    ('js member dot view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(opts) { cp.execSync(opts.cmd); }\nsh({cmd: "gh pr view 5"});\nEOF2'),
    ('js member bracket view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(opts) { cp.execSync(opts["cmd"]); }\nsh({"cmd": "gh pr view 5"});\nEOF2'),
    ('py dict subscript view', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts["cmd"])\nrun({"cmd": ["gh", "pr", "view", "5"]})\nEOF2'),
    ('py dict get view', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts.get("cmd"))\nrun({"cmd": ["gh", "pr", "view", "5"]})\nEOF2'),
    ('py dict string view', 'python3 - <<\'EOF2\'\nimport subprocess\nimport os\ndef run(opts):\n    os.system(opts["cmd"])\nrun({"cmd": "gh pr view 5"})\nEOF2'),
    ('py dict through a name view', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts["cmd"])\no = {"cmd": ["gh", "pr", "view", "5"]}\nrun(o)\nEOF2'),
    ('js computed key', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(opts, k) { cp.execSync(opts[k]); }\nsh({cmd: "' + M + ' 5"}, "cmd");\nEOF2'),
    ('js object spread', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst base = {cmd: "gh pr view 5"};\nfunction sh(opts) { cp.execSync(opts.cmd); }\nsh({...base, other: "' + M + ' 5"});\nEOF2'),
    ('js other key holds the merge', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh({cmd}) { cp.execSync(cmd); }\nsh({cmd: "gh pr view 5", note: "' + M + ' 5"});\nEOF2'),
    ('py non-literal argument', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts["cmd"])\nrun(load())\nEOF2'),
    ('py other key holds the merge', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(opts):\n    subprocess.run(opts["cmd"])\nrun({"cmd": ["gh", "pr", "view", "5"], "note": "' + M + ' 5"})\nEOF2'),
    ('js spread after the key sets cmd', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst extra = {cmd: "gh pr view 5"};\nfunction sh(opts) { cp.execSync(opts.cmd); }\nsh({cmd: "' + M + ' 5", ...extra});\nEOF2'),
    ('py spread after the key sets cmd', 'python3 - <<\'EOF2\'\nimport subprocess\nextra = {"cmd": ["gh", "pr", "view", "5"]}\ndef run(opts):\n    subprocess.run(opts["cmd"])\nrun({"cmd": ["gh", "pr", "merge", "5"], **extra})\nEOF2'),
    ('py dict spread', 'python3 - <<\'EOF2\'\nimport subprocess\nbase = {"cmd": ["gh", "pr", "view", "5"]}\ndef run(opts):\n    subprocess.run(opts["cmd"])\nrun({**base, "note": "' + M + ' 5"})\nEOF2'),
]


@pytest.mark.parametrize("label,cmd", OBJECT_HELPER_DENY, ids=[r[0] for r in OBJECT_HELPER_DENY])
def test_object_helper_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", OBJECT_HELPER_ALLOW, ids=[r[0] for r in OBJECT_HELPER_ALLOW])
def test_object_helper_view_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# Each element of a literal list handed to a callback or a loop is one call: a JS
# [...].forEach(f) / .map(f), an inline arrow or function callback, a for...of loop,
# Python map(f, [...]) and for c in [...]. A list with two merges runs two: refused, and
# no single approval mints it. A non-literal or spread list reads nothing.
EACH_HELPER_DENY = [
    ('js forEach named', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\n["' + M + ' 5"].forEach(sh);\nEOF2'),
    ('js map named', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst sh = (c) => cp.execSync(c);\n["' + M + ' 5"].map(sh);\nEOF2'),
    ('js forEach inline arrow', 'node - <<\'EOF2\'\nconst cp = require("child_process");\n["' + M + ' 5"].forEach(c => cp.execSync(c));\nEOF2'),
    ('js forEach inline block', 'node - <<\'EOF2\'\nconst cp = require("child_process");\n["' + M + ' 5"].forEach((c) => { cp.execSync(c); });\nEOF2'),
    ('js forEach inline function', 'node - <<\'EOF2\'\nconst cp = require("child_process");\n["' + M + ' 5"].forEach(function (c) { cp.execSync(c); });\nEOF2'),
    ('js for-of', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfor (const c of ["' + M + ' 5"]) cp.execSync(c);\nEOF2'),
    ('js for-of block helper', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\nfor (const c of ["' + M + ' 5"]) {\n  sh(c);\n}\nEOF2'),
    ('py map named', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(c):\n    subprocess.run(c)\nlist(map(run, [["gh", "pr", "merge", "5"]]))\nEOF2'),
    ('py for-in direct', 'python3 - <<\'EOF2\'\nimport subprocess\nfor c in [["gh", "pr", "merge", "5"]]:\n    subprocess.run(c)\nEOF2'),
    ('py for-in helper', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(c):\n    subprocess.run(c)\nfor c in [["gh", "pr", "merge", "5"]]:\n    run(c)\nEOF2'),
    ('py for-in tuple', 'python3 - <<\'EOF2\'\nimport subprocess\nfor c in ("' + M + ' 5",):\n    subprocess.run(c, shell=True)\nEOF2'),
    ('js forEach two merges', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\n["' + M + ' 5", "' + M + ' 6"].forEach(sh);\nEOF2'),
    ('py for-in two merges', 'python3 - <<\'EOF2\'\nimport subprocess\nfor c in [["gh", "pr", "merge", "5"], ["gh", "pr", "merge", "6"]]:\n    subprocess.run(c)\nEOF2'),
]
EACH_HELPER_ALLOW = [
    ('js forEach named view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\n["gh pr view 5"].forEach(sh);\nEOF2'),
    ('js map named view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nconst sh = (c) => cp.execSync(c);\n["gh pr view 5"].map(sh);\nEOF2'),
    ('js forEach inline arrow view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\n["gh pr view 5"].forEach(c => cp.execSync(c));\nEOF2'),
    ('js forEach inline block view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\n["gh pr view 5"].forEach((c) => { cp.execSync(c); });\nEOF2'),
    ('js forEach inline function view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\n["gh pr view 5"].forEach(function (c) { cp.execSync(c); });\nEOF2'),
    ('js for-of view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfor (const c of ["gh pr view 5"]) cp.execSync(c);\nEOF2'),
    ('js for-of block helper view', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\nfor (const c of ["gh pr view 5"]) {\n  sh(c);\n}\nEOF2'),
    ('py map named view', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(c):\n    subprocess.run(c)\nlist(map(run, [["gh", "pr", "view", "5"]]))\nEOF2'),
    ('py for-in direct view', 'python3 - <<\'EOF2\'\nimport subprocess\nfor c in [["gh", "pr", "view", "5"]]:\n    subprocess.run(c)\nEOF2'),
    ('py for-in helper view', 'python3 - <<\'EOF2\'\nimport subprocess\ndef run(c):\n    subprocess.run(c)\nfor c in [["gh", "pr", "view", "5"]]:\n    run(c)\nEOF2'),
    ('py for-in tuple view', 'python3 - <<\'EOF2\'\nimport subprocess\nfor c in ("gh pr view 5",):\n    subprocess.run(c, shell=True)\nEOF2'),
    ('js forEach non-literal list', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\nlist.forEach(sh);\nEOF2'),
    ('js forEach spread list', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfunction sh(c) { cp.execSync(c); }\n[...more, "gh pr view 5"].forEach(sh);\nEOF2'),
    ('js loop variable reassigned', 'node - <<\'EOF2\'\nconst cp = require("child_process");\nfor (let c of ["' + M + ' 5"]) { c = "gh pr view 5"; cp.execSync(c); }\nEOF2'),
    ('py for-in non-literal', "python3 - <<'EOF2'\nimport subprocess\nfor c in load():\n    subprocess.run(c)\nEOF2"),
    ('py for-in merge only printed', 'python3 - <<\'EOF2\'\nimport subprocess\nfor c in [["gh", "pr", "merge", "5"]]:\n    print(c)\nsubprocess.run(["gh", "pr", "view", "5"])\nEOF2'),
]


@pytest.mark.parametrize("label,cmd", EACH_HELPER_DENY, ids=[r[0] for r in EACH_HELPER_DENY])
def test_each_helper_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", EACH_HELPER_ALLOW, ids=[r[0] for r in EACH_HELPER_ALLOW])
def test_each_helper_view_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label", ["js forEach two merges", "py for-in two merges"])
def test_a_literal_list_with_two_merges_is_compound_and_mints_nothing(label):
    from shared.merge_guard_common import interpreter_command_context, is_compound_destructive_command

    cmd = dict(EACH_HELPER_DENY)[label]
    assert is_compound_destructive_command(cmd) is True
    assert interpreter_command_context(cmd) is None


# A program a shell's -c string hands an interpreter is read as the bare program is,
# heredoc included, and the approval reads the same merge from it. A heredoc the
# outer shell feeds as data is not read.
SHELL_C_PROGRAM_DENY = [
    ('bash -c heredoc python', 'bash -c \'python3 - <<EOF\nimport subprocess\nsubprocess.run(["gh", "pr", "merge", "5"])\nEOF\''),
    ('bash -c python -c', 'bash -c "python3 -c \'import subprocess; subprocess.run([\\"gh\\", \\"pr\\", \\"merge\\", \\"5\\"])\'"'),
    ('sh -c node -e', 'sh -c \'node -e "require(\\"child_process\\").execSync(\\"' + M + ' 5\\")"\''),
    ('bash -lc heredoc helper', 'bash -lc \'python3 - <<EOF\nimport subprocess\ndef run(c):\n    subprocess.run(c)\nrun(["gh", "pr", "merge", "5"])\nEOF\''),
]
SHELL_C_PROGRAM_ALLOW = [
    ('bash -c heredoc python view', 'bash -c \'python3 - <<EOF\nimport subprocess\nsubprocess.run(["gh", "pr", "view", "5"])\nEOF\''),
    ('bash -c python -c view', 'bash -c "python3 -c \'import subprocess; subprocess.run([\\"gh\\", \\"pr\\", \\"view\\", \\"5\\"])\'"'),
    ('sh -c node -e view', 'sh -c \'node -e "require(\\"child_process\\").execSync(\\"gh pr view 5\\")"\''),
    ('bash -lc heredoc helper view', 'bash -lc \'python3 - <<EOF\nimport subprocess\ndef run(c):\n    subprocess.run(c)\nrun(["gh", "pr", "view", "5"])\nEOF\''),
    ('data heredoc quoting it', 'cat <<\'DOC\'\nbash -c \'python3 - <<EOF\nimport subprocess\nsubprocess.run(["gh", "pr", "merge", "5"])\nEOF\'\nDOC'),
    ('echo of it', 'echo "bash -c \'python3 - <<EOF\'"'),
]


@pytest.mark.parametrize("label,cmd", SHELL_C_PROGRAM_DENY, ids=[r[0] for r in SHELL_C_PROGRAM_DENY])
def test_shell_c_program_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", SHELL_C_PROGRAM_ALLOW, ids=[r[0] for r in SHELL_C_PROGRAM_ALLOW])
def test_shell_c_program_view_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", SHELL_C_PROGRAM_DENY, ids=[r[0] for r in SHELL_C_PROGRAM_DENY])
def test_a_program_inside_a_shell_c_string_reads_one_merge_to_approve(label, cmd):
    from shared.merge_guard_common import interpreter_command_context

    context = interpreter_command_context(cmd)
    assert context is not None and context["operation_type"] == "merge" and context["pr_number"] == "5"


# Words after a -c string are its positional parameters: `sh -c 'gh pr merge "$1"' _ 5`
# runs `gh pr merge 5`, so it is judged and approved as that command.
POSITIONAL_ROWS = [
    ("$1 in double quotes", "sh -c 'gh pr " + "merge \"$1\" --squash' _ 5", 'gh pr ' + 'merge 5 --squash'),
    ("a quoted word that needs its quotes", "sh -c 'echo \"$1\"' _ 'a b'", 'echo "a b"'),
    ("$0", "bash -c 'gh pr " + "merge $0 --squash' 5", "gh pr " + "merge 5 --squash"),
    ("${1}", "sh -c 'gh pr " + "merge ${1}' x 7", "gh pr " + "merge 7"),
    ("an unquoted word is split", "bash -c 'git push --force origin $1' _ 'a b'", "git push --force origin a b"),
    ("a quoted word keeps its quotes", "bash -c 'git push --force origin \"$1\"' _ 'a b'", "git push --force origin \"a b\""),
    ("single-quoted $1 is literal", "bash -c 'echo '\"'\"'$1'\"'\"'' _ 5", "echo '$1'"),
    ("an outer substitution in the word", "bash -c 'gh pr " + "merge \"$1\"' _ \"$(pick)\"", None),
    ("\"$@\" keeps each word whole", "bash -c 'gh pr " + "merge \"$@\"' _ 5 --squash", 'gh pr ' + 'merge 5 --squash'),
    ("\"$@\" quotes a word with a space", "bash -c 'echo \"$@\"' _ x 'a b'", 'echo x "a b"'),
    ("${@} reads as $@", "bash -c 'gh \"${@}\"' _ pr " + "merge 5", "gh pr " + "merge 5"),
    ("$* unquoted", "bash -c 'gh pr " + "merge $*' _ 5 --squash", "gh pr " + "merge 5 --squash"),
    ("\"$*\" joins the words", "bash -c 'echo \"$*\"' _ a b", 'echo "a b"'),
    ("$# counts the words", "bash -c 'echo $#' _ a b", "echo 2"),
    ("${10} is not read", "bash -c 'echo ${10}' _ a", None),
]


@pytest.mark.parametrize("label,cmd,want", POSITIONAL_ROWS, ids=[r[0] for r in POSITIONAL_ROWS])
def test_a_shell_c_string_reads_its_positional_words(label, cmd, want):
    from shared.merge_guard_common import shell_c_string

    assert shell_c_string(cmd) == want


@pytest.mark.parametrize("cmd,dangerous", [
    ("sh -c 'gh pr " + "merge \"$1\" --squash' _ 5", True),
    ("sh -c 'gh pr view \"$1\"' _ 5", False),
    ("bash -c 'gh pr " + "merge $0' 5", True),
    ("bash -c 'gh pr view $0' 5", False),
])
def test_a_positional_shell_c_command_is_judged_as_it_runs(cmd, dangerous):
    assert is_dangerous_command(cmd) is dangerous


APPROVAL_STYLES = {
    "inline": lambda c: f"Run `{c}` now",
    "double-backtick": lambda c: f"Run `` {c} `` now",
    "fenced": lambda c: f"Run this:\n```bash\n{c}\n```",
}


def _runs_after_approval(command: str, style) -> bool:
    """Whether `command` runs once its own faithful approval, quoted in `style`, mints."""
    from merge_guard_post import _mint_context_from_bundle
    from merge_guard_pre import _token_matches_command

    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": style(command)},
                      {"label": "No", "description": "Hold."}]}]
    ctx = _mint_context_from_bundle(q, {"Run this?": "Yes, run"})[0]
    return ctx is not None and _token_matches_command({"context": ctx}, command)


POSITIONAL_RUNS = {
    "$1": "sh -c 'gh pr " + "merge \"$1\" --squash' _ 5",
    "$0": "bash -c 'gh pr " + "merge $0 --squash' 5",
    "all-words": "bash -c 'gh pr " + "merge \"$@\"' _ 5 --squash",
    "xargs": "printf '%s\\n' 5 | xargs -I{} sh -c 'gh pr " + "merge \"$1\" --squash' _ {}",
    "force-push-binds-its-ref": "bash -c 'git push --force origin \"$1\"' _ feat/x",
    "unknown-word": "sh -c 'gh pr " + "merge \"$1\" --squash' _ \"$(pick)\"",
}


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("cmd", POSITIONAL_RUNS.values(), ids=POSITIONAL_RUNS.keys())
def test_a_positional_shell_c_merge_is_refused_then_runs_approved(cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


def test_a_positional_approval_binds_its_own_word():
    from merge_guard_post import _mint_context_from_bundle
    from merge_guard_pre import _token_matches_command

    approved = POSITIONAL_RUNS["force-push-binds-its-ref"]
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": f"Run `{approved}` now"},
                      {"label": "No", "description": "Hold."}]}]
    token = {"context": _mint_context_from_bundle(q, {"Run this?": "Yes, run"})[0]}
    assert not _token_matches_command(token, approved.replace("feat/x", "main"))
    assert not _token_matches_command(token, POSITIONAL_RUNS["$1"])


@pytest.mark.parametrize("cmd", [
    "sh -c 'for p in \"$@\"; do gh pr " + "merge \"$p\"; done' _ 5 6",
    "sh -c 'gh pr " + "merge \"$1\" && gh pr " + "merge \"$2\"' _ 5 6",
], ids=["loop-over-two-words", "two-merges"])
def test_positional_words_feeding_two_merges_are_compound(cmd):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_compound_destructive_command(cmd) is True
    assert not _runs_after_approval(cmd, APPROVAL_STYLES["inline"])


# An interpreter given `-` and then the program's own arguments reads its program
# from stdin (`python3 - 5 <<EOF`), so that heredoc is the program; a script path
# before the arguments makes the heredoc data.
STDIN_ARGV_DENY = [
    ('python - args heredoc', 'python3 - 5 <<\'EOF\'\nimport subprocess\nsubprocess.run(["gh", "pr", "merge", "5"])\nEOF'),
    ('argv-built merge', 'python3 - 5 <<\'EOF\'\nimport subprocess, sys\nsubprocess.run(["gh", "pr", "merge", sys.argv[1]])\nEOF'),
    ('python - two args', 'python3 - a b <<\'EOF\'\nimport subprocess\nsubprocess.run(["gh", "pr", "merge", "5"])\nEOF'),
    ('node - args here-string', 'node - 5 <<< \'require("child_process").execSync("' + M + ' 5")\''),
]
STDIN_ARGV_ALLOW = [
    ('view twin', 'python3 - 5 <<\'EOF\'\nimport subprocess\nsubprocess.run(["gh", "pr", "view", "5"])\nEOF'),
    ('python script reading stdin data', 'python3 script.py 5 <<\'EOF\'\nimport subprocess\nsubprocess.run(["gh", "pr", "merge", "5"])\nEOF'),
]


@pytest.mark.parametrize("label,cmd", STDIN_ARGV_DENY, ids=[r[0] for r in STDIN_ARGV_DENY])
def test_stdin_argv_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", STDIN_ARGV_ALLOW, ids=[r[0] for r in STDIN_ARGV_ALLOW])
def test_stdin_argv_view_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", STDIN_ARGV_DENY, ids=[r[0] for r in STDIN_ARGV_DENY])
def test_a_stdin_program_given_argv_runs_once_approved(label, cmd, style):
    assert _runs_after_approval(cmd, style)


def test_a_stdin_program_given_a_dashed_argument_is_still_the_program():
    cmd = "python3 - --dry-run <<'EOF'\nimport subprocess\nsubprocess.run([\"gh\", \"pr\", \"merge\", \"5\"])\nEOF"
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, APPROVAL_STYLES["inline"])


def test_a_stdin_program_given_argv_running_two_merges_is_compound():
    from shared.merge_guard_common import is_compound_destructive_command

    cmd = ("python3 - 5 <<'EOF'\nimport subprocess\nsubprocess.run([\"gh\", \"pr\", \"merge\", \"5\"])\n"
           "subprocess.run([\"gh\", \"pr\", \"merge\", \"6\"])\nEOF")
    assert is_compound_destructive_command(cmd) is True
    assert not _runs_after_approval(cmd, APPROVAL_STYLES["inline"])


def test_a_stdin_program_given_argv_that_only_prints_a_merge_is_allowed():
    assert is_dangerous_command("python3 - 5 <<'EOF'\nprint(\"" + D + "\")\nEOF") is False


# A Python `*args` parameter holds the arguments each call by name passes past the
# named ones, keyword arguments left out: `def gh(*args): subprocess.run(["gh", *args])`
# then `gh("pr", <merge>, "5")` runs that merge, and `list(a)` / `tuple(a)` of a held
# name is the same value. Two calls with two merges are a compound.
STAR_ARGS_DENY = [
    ('star args spread', 'python3 - <<\'EOF\'\nimport subprocess\ndef gh(*args):\n    subprocess.run(["gh", *args])\ngh("pr", "merge", "5")\nEOF'),
    ('star args whole', 'python3 - <<\'EOF\'\nimport subprocess\ndef run(*a):\n    subprocess.run(a)\nrun("gh", "pr", "merge", "5")\nEOF'),
    ('star args list()', 'python3 - <<\'EOF\'\nimport subprocess\ndef run(*a):\n    subprocess.run(list(a))\nrun("gh", "pr", "merge", "5")\nEOF'),
    ('tuple() of a named list', 'python3 - <<\'EOF\'\nimport subprocess\nc = ["gh", "pr", "merge", "5"]\nsubprocess.run(tuple(c))\nEOF'),
    ('star args after a positional', 'python3 - <<\'EOF\'\nimport subprocess\ndef run(cwd, *a):\n    subprocess.run(a, cwd=cwd)\nrun(".", "gh", "pr", "merge", "5")\nEOF'),
    ('star args with a keyword call', 'python3 - <<\'EOF\'\nimport subprocess\ndef gh(*args, **kw):\n    subprocess.run(["gh", *args], **kw)\ngh("pr", "merge", "5", check=True)\nEOF'),
    ('star args two merges', 'python3 - <<\'EOF\'\nimport subprocess\ndef gh(*args):\n    subprocess.run(["gh", *args])\ngh("pr", "merge", "5")\ngh("pr", "merge", "6")\nEOF'),
]
STAR_ARGS_ALLOW = [
    ('star args spread view', 'python3 - <<\'EOF\'\nimport subprocess\ndef gh(*args):\n    subprocess.run(["gh", *args])\ngh("pr", "view", "5")\nEOF'),
    ('star args whole view', 'python3 - <<\'EOF\'\nimport subprocess\ndef run(*a):\n    subprocess.run(a)\nrun("gh", "pr", "view", "5")\nEOF'),
    ('star args list() view', 'python3 - <<\'EOF\'\nimport subprocess\ndef run(*a):\n    subprocess.run(list(a))\nrun("gh", "pr", "view", "5")\nEOF'),
    ('tuple() of a named list view', 'python3 - <<\'EOF\'\nimport subprocess\nc = ["gh", "pr", "view", "5"]\nsubprocess.run(tuple(c))\nEOF'),
    ('list() of a call', "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(list(load()))\nEOF"),
    ('sorted() of a named list is another list', 'python3 - <<\'EOF\'\nimport subprocess\nc = ["gh", "pr", "merge", "5"]\nsubprocess.run(sorted(c))\nEOF'),
    ('star args after a positional view', 'python3 - <<\'EOF\'\nimport subprocess\ndef run(cwd, *a):\n    subprocess.run(a, cwd=cwd)\nrun(".", "gh", "pr", "view", "5")\nEOF'),
    ('star args with a keyword call view', 'python3 - <<\'EOF\'\nimport subprocess\ndef gh(*args, **kw):\n    subprocess.run(["gh", *args], **kw)\ngh("pr", "view", "5", check=True)\nEOF'),
    ('named argument before *args is not in it', 'python3 - <<\'EOF\'\nimport subprocess\ndef pr(mode, *a):\n    print(mode)\n    subprocess.run(["gh", "pr", *a])\npr("merge", "view", "5")\nEOF'),
]


@pytest.mark.parametrize("label,cmd", STAR_ARGS_DENY, ids=[r[0] for r in STAR_ARGS_DENY])
def test_star_args_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", STAR_ARGS_ALLOW, ids=[r[0] for r in STAR_ARGS_ALLOW])
def test_star_args_view_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


STAR_ARGS_RUNS = [row for row in STAR_ARGS_DENY if row[0] != "star args two merges"]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", STAR_ARGS_RUNS, ids=[r[0] for r in STAR_ARGS_RUNS])
def test_a_star_args_merge_runs_once_approved(label, cmd, style):
    assert _runs_after_approval(cmd, style)


def test_star_args_given_two_merges_are_compound_and_mint_nothing():
    from shared.merge_guard_common import interpreter_command_context, is_compound_destructive_command

    cmd = dict(STAR_ARGS_DENY)["star args two merges"]
    assert is_compound_destructive_command(cmd) is True
    assert interpreter_command_context(cmd) is None
    assert not _runs_after_approval(cmd, APPROVAL_STYLES["inline"])


@pytest.mark.parametrize("cmd", [
    "python3 - <<'EOF'\nimport subprocess\ndef gh(*args):\n    subprocess.run([\"gh\", *args])\ngh(*load())\nEOF",
    "python3 - <<'EOF'\nimport subprocess\nc = [\"gh\", \"pr\", \"merge\", \"5\"]\nc = [\"gh\", \"pr\", \"view\", \"5\"]\n"
    "subprocess.run(tuple(c))\nEOF",
    "python3 - <<'EOF'\ndef gh(*args):\n    print(\" \".join([\"gh\", *args]))\ngh(\"pr\", \"merge\", \"5\")\nEOF",
], ids=["unread-star-argument", "reassigned-name", "only-printed"])
def test_star_args_that_run_no_literal_merge_are_allowed(cmd):
    assert is_dangerous_command(cmd) is False


# Parsing a Python program to read its names must not print the program's own
# warnings (an invalid escape in a string) on the hook's stderr.
INVALID_ESCAPE_PROGRAM = (
    "python3 - <<'EOF'\nprint(\"a\\c\")\nimport subprocess\nc = \"ls\"\nsubprocess.run(c)\nEOF"
)


def test_the_pre_hook_prints_nothing_for_a_program_with_an_invalid_escape(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    hooks = Path(__file__).resolve().parents[1] / "hooks"
    (tmp_path / ".claude").mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude")}
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": INVALID_ESCAPE_PROGRAM}}
    r = subprocess.run([sys.executable, str(hooks / "merge_guard_pre.py")], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False)
    assert r.returncode == 0
    assert r.stderr == ""


def test_parsing_a_program_raises_no_warning_in_process():
    import warnings

    from shared.merge_guard_common import _python_reaching

    code = INVALID_ESCAPE_PROGRAM.split("\n", 1)[1].rsplit("\nEOF", 1)[0] + "\n# fresh\n"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _python_reaching(code) is not None
    assert [w for w in caught if issubclass(w.category, SyntaxWarning)] == []


# A rest parameter (ruby `*a`, perl `@_`, JS `...a`) holds the arguments each call by
# name passes past the named parameters; `*a` / `...a` as an argument splices them.
# Two calls with two merges are a compound.
REST_PARAM_DENY = [
    ('ruby splat', 'ruby - <<\'EOF\'\ndef gh(*a)\n  system("gh", *a)\nend\ngh("pr", "merge", "5")\nEOF'),
    ('ruby splat after a named param', 'ruby - <<\'EOF\'\ndef gh(dir, *a)\n  Dir.chdir(dir) { system("gh", *a) }\nend\ngh(".", "pr", "merge", "5")\nEOF'),
    ('perl @_', 'perl -e \'sub gh { system("gh", @_); } gh("pr", "merge", "5");\''),
    ('perl @_ after shift', 'perl -e \'sub gh { my $d = shift; system("gh", @_); } gh(".", "pr", "merge", "5");\''),
    ('js rest', 'node - <<\'EOF\'\nconst cp = require("child_process");\nfunction gh(...a) { cp.execFileSync("gh", a); }\ngh("pr", "merge", "5");\nEOF'),
    ('js rest spread', 'node - <<\'EOF\'\nconst cp = require("child_process");\nconst gh = (...a) => cp.execFileSync("gh", [...a]);\ngh("pr", "merge", "5");\nEOF'),
    ('js rest after a named param', 'node - <<\'EOF\'\nconst cp = require("child_process");\nfunction gh(opts, ...a) { cp.execFileSync("gh", a, opts); }\ngh({}, "pr", "merge", "5");\nEOF'),
    ('ruby splat two merges', 'ruby - <<\'EOF\'\ndef gh(*a)\n  system("gh", *a)\nend\ngh("pr", "merge", "5")\ngh("pr", "merge", "6")\nEOF'),
    ('js rest two merges', 'node - <<\'EOF\'\nconst cp = require("child_process");\nfunction gh(...a) { cp.execFileSync("gh", a); }\ngh("pr", "merge", "5");\ngh("pr", "merge", "6");\nEOF'),
]
REST_PARAM_ALLOW = [
    ('ruby splat view', 'ruby - <<\'EOF\'\ndef gh(*a)\n  system("gh", *a)\nend\ngh("pr", "view", "5")\nEOF'),
    ('ruby splat after a named param view', 'ruby - <<\'EOF\'\ndef gh(dir, *a)\n  Dir.chdir(dir) { system("gh", *a) }\nend\ngh(".", "pr", "view", "5")\nEOF'),
    ('perl @_ view', 'perl -e \'sub gh { system("gh", @_); } gh("pr", "view", "5");\''),
    ('perl @_ after shift view', 'perl -e \'sub gh { my $d = shift; system("gh", @_); } gh(".", "pr", "view", "5");\''),
    ('js rest view', 'node - <<\'EOF\'\nconst cp = require("child_process");\nfunction gh(...a) { cp.execFileSync("gh", a); }\ngh("pr", "view", "5");\nEOF'),
    ('js rest spread view', 'node - <<\'EOF\'\nconst cp = require("child_process");\nconst gh = (...a) => cp.execFileSync("gh", [...a]);\ngh("pr", "view", "5");\nEOF'),
    ('js rest after a named param view', 'node - <<\'EOF\'\nconst cp = require("child_process");\nfunction gh(opts, ...a) { cp.execFileSync("gh", a, opts); }\ngh({}, "pr", "view", "5");\nEOF'),
    ('perl @_ past a shifted mode', 'perl -e \'sub gh { my $m = shift; system("gh", "pr", @_); } gh("merge", "view", "5");\''),
]


@pytest.mark.parametrize("label,cmd", REST_PARAM_DENY, ids=[r[0] for r in REST_PARAM_DENY])
def test_rest_param_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", REST_PARAM_ALLOW, ids=[r[0] for r in REST_PARAM_ALLOW])
def test_rest_param_view_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label", ["ruby splat two merges", "js rest two merges"])
def test_a_rest_parameter_given_two_merges_is_compound_and_mints_nothing(label):
    from shared.merge_guard_common import interpreter_command_context, is_compound_destructive_command

    cmd = dict(REST_PARAM_DENY)[label]
    assert is_compound_destructive_command(cmd) is True
    assert interpreter_command_context(cmd) is None


REST_PARAM_RUNS = [row for row in REST_PARAM_DENY if "two merges" not in row[0]]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", REST_PARAM_RUNS, ids=[r[0] for r in REST_PARAM_RUNS])
def test_a_rest_param_merge_runs_once_approved(label, cmd, style):
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("label", ["ruby splat two merges", "js rest two merges"])
def test_a_rest_parameter_given_two_merges_runs_on_no_approval(label):
    assert not _runs_after_approval(dict(REST_PARAM_DENY)[label], APPROVAL_STYLES["inline"])


@pytest.mark.parametrize("cmd", [
    "node - <<'EOF'\nconst cp = require(\"child_process\");\nfunction gh(...a) { cp.execFileSync(\"gh\", a); }\n"
    "gh(...load());\nEOF",
    "perl -e 'sub gh { print join(\" \", \"gh\", @_); } gh(\"pr\", \"merge\", \"5\");'",
    "ruby - <<'EOF'\ndef gh(*a)\n  puts [\"gh\", *a].join(\" \")\nend\ngh(\"pr\", \"merge\", \"5\")\nEOF",
], ids=["js-unread-spread", "perl-only-printed", "ruby-only-printed"])
def test_a_rest_parameter_running_no_literal_merge_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


# Only the shifts of @_ a perl sub runs at its own level before it first reads @_ take
# arguments away from it: a later shift, a shift of another array or one in a nested
# block does not hide what `system("gh", @_)` runs.
PERL_SHIFT_DENY = [
    ("shift of ARGV after the read", "perl -e 'sub gh { my $d = shift; system(\"gh\", @_); my $x = shift @ARGV; } "
                                     "gh(\".\", \"pr\", \"merge\", \"5\");'"),
    ("shift after the read, none before", "perl -e 'sub gh { system(\"gh\", @_); my $x = shift; } gh(\"pr\", \"merge\", \"5\");'"),
    ("shift in a nested block", "perl -e 'sub gh { my $d = shift; if ($d) { my $y = shift; } system(\"gh\", @_); } "
                                "gh(\".\", \"pr\", \"merge\", \"5\");'"),
    ("shift of ARGV before the read", "perl -e 'sub gh { my $f = shift @ARGV; system(\"gh\", @_); } gh(\"pr\", \"merge\", \"5\");'"),
    ("shift after copying the args", "perl -e 'sub gh { my @a = @_; my $y = shift; system(\"gh\", @a) } gh(\"pr\", \"merge\", \"5\");'"),
    ("shift inside a nested sub", "perl -e 'sub gh { my $c = sub { shift }; system(\"gh\", @_) } gh(\"pr\", \"merge\", \"5\");'"),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", PERL_SHIFT_DENY, ids=[r[0] for r in PERL_SHIFT_DENY])
def test_a_perl_shift_that_leaves_the_merge_in_args_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "perl -e 'sub gh { my $d = shift; system(\"gh\", @_); my $x = shift @ARGV; } gh(\".\", \"pr\", \"view\", \"5\");'",
    "perl -e 'sub gh { my $m = shift; my $n = shift; system(\"gh\", \"pr\", @_); } gh(\"x\", \"merge\", \"view\", \"5\");'",
], ids=["view-twin", "two-shifts-before-the-read"])
def test_perl_shifts_before_the_read_still_take_their_arguments(cmd):
    assert is_dangerous_command(cmd) is False


# A spread that fills a rest helper's named parameters gives them its leading elements,
# not the whole spread, and a spread of a literal list is read as its elements.
SPREAD_SKIP_DENY = [
    ("literal spread after the named argument", "python3 - <<'EOF'\nimport subprocess\ndef gh(a, *args):\n"
     "    subprocess.run([\"gh\", *args])\ngh(\"x\", *[\"pr\", \"merge\", \"5\"])\nEOF"),
    ("literal spread fills the named slot", "python3 - <<'EOF'\nimport subprocess\ndef gh(cwd, *a):\n"
     "    subprocess.run([\"gh\", *a], cwd=cwd)\ngh(*[\".\", \"pr\", \"merge\", \"5\"])\nEOF"),
    ("literal spread with no named slot", "python3 - <<'EOF'\nimport subprocess\ndef gh(*a):\n"
     "    subprocess.run([\"gh\", *a])\ngh(*[\"pr\", \"merge\", \"5\"])\nEOF"),
    ("named spread fills the named slot", "python3 - <<'EOF'\nimport subprocess\ndef gh(cwd, *a):\n"
     "    subprocess.run([\"gh\", *a], cwd=cwd)\ncmd = [\".\", \"pr\", \"merge\", \"5\"]\ngh(*cmd)\nEOF"),
    ("js literal spread fills the named slot", "node - <<'EOF'\nconst cp = require(\"child_process\");\n"
     "function gh(dir, ...a) { cp.execFileSync(\"gh\", a, {cwd: dir}); }\ngh(...[\".\", \"pr\", \"merge\", \"5\"]);\nEOF"),
]
SPREAD_SKIP_ALLOW = [
    ("view twin", "python3 - <<'EOF'\nimport subprocess\ndef gh(cwd, *a):\n"
     "    subprocess.run([\"gh\", *a], cwd=cwd)\ngh(*[\".\", \"pr\", \"view\", \"5\"])\nEOF"),
    ("the merge word is the named slot", "python3 - <<'EOF'\nimport subprocess\ndef pr(mode, *a):\n"
     "    subprocess.run([\"gh\", \"pr\", *a])\npr(*[\"merge\", \"view\", \"5\"])\nEOF"),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", SPREAD_SKIP_DENY, ids=[r[0] for r in SPREAD_SKIP_DENY])
def test_a_spread_into_a_rest_helper_is_read_by_element(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("label,cmd", SPREAD_SKIP_ALLOW, ids=[r[0] for r in SPREAD_SKIP_ALLOW])
def test_a_spread_whose_named_slots_take_the_merge_word_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_spread_shorter_than_the_named_slots_is_read_whole_and_still_mints():
    cmd = ("python3 - <<'EOF'\nimport subprocess\ndef gh(a, b, *rest):\n    subprocess.run([\"gh\", *rest])\n"
           "gh(*[\"pr\"], \"merge\", \"5\")\nEOF")
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, APPROVAL_STYLES["inline"])


# A parameter default holding a comma is one parameter, so the rest after it starts at
# the right argument.
DEFAULT_COMMA_DENY = [
    ("ruby list default", "ruby - <<'EOF'\ndef gh(x = [1, 2], *a)\n  system(\"gh\", *a)\nend\ngh(0, \"pr\", \"merge\", \"5\")\nEOF"),
    ("ruby call default", "ruby - <<'EOF'\ndef gh(x = f(1, 2), *a)\n  system(\"gh\", *a)\nend\ngh(0, \"pr\", \"merge\", \"5\")\nEOF"),
    ("php call default", "php -r 'function gh($x = max(1, 2), $y) { exec($y); } gh(0, \"gh pr " + "merge 5\");'"),
    ("R vector default", "Rscript -e 'gh <- function(x = c(1, 2), cmd) system(cmd); gh(0, \"gh pr " + "merge 5\")'"),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", DEFAULT_COMMA_DENY, ids=[r[0] for r in DEFAULT_COMMA_DENY])
def test_a_default_with_a_comma_keeps_the_rest_in_place(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "ruby - <<'EOF'\ndef gh(x = [1, 2], *a)\n  system(\"gh\", *a)\nend\ngh(0, \"pr\", \"view\", \"5\")\nEOF",
    "php -r 'function gh($x = max(1, 2), $y) { exec($y); } gh(0, \"gh pr view 5\");'",
    "Rscript -e 'gh <- function(x = c(1, 2), cmd) system(cmd); gh(0, \"gh pr view 5\")'",
], ids=["ruby", "php", "R"])
def test_a_default_with_a_comma_view_twin_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


def test_parameter_names_skip_defaults_with_commas():
    from shared.merge_guard_common import _ordered_params

    assert _ordered_params("x = [1, 2], *a", "ruby") == ("x", "a")
    assert _ordered_params("$x = (1, 2), @a", "perl") == ("$x", "@a")
    assert _ordered_params("x = f(1, 2), *a", "ruby") == ("x", "a")


def test_a_bound_name_set_skips_names_inside_a_default():
    from shared.merge_guard_common import _param_names

    assert _param_names("x = foo(bar, baz), *a", "ruby") == frozenset({"x", "a"})


# A ruby endless method (`def gh(*a) = system("gh", *a)`) is a named function whose
# body is its value; a merge it runs is read like any helper's.
ENDLESS_DENY = [
    ("splat helper", "ruby - <<'EOF'\ndef gh(*a) = system(\"gh\", *a)\ngh(\"pr\", \"merge\", \"5\")\nEOF"),
    ("one line with -e", "ruby -e 'def gh(*a) = system(\"gh\", *a); gh(\"pr\", \"merge\", \"5\")'"),
    ("named then splat", "ruby - <<'EOF'\ndef gh(dir, *a) = Dir.chdir(dir) { system(\"gh\", *a) }\n"
                         "gh(\".\", \"pr\", \"merge\", \"5\")\nEOF"),
    ("string parameter", "ruby - <<'EOF'\ndef run(c) = system(c)\nrun(\"gh pr " + "merge 5\")\nEOF"),
    ("body on the next line", "ruby - <<'EOF'\ndef gh(*a) =\n  system(\"gh\", *a)\ngh(\"pr\", \"merge\", \"5\")\nEOF"),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", ENDLESS_DENY, ids=[r[0] for r in ENDLESS_DENY])
def test_a_ruby_endless_method_merge_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "ruby - <<'EOF'\ndef gh(*a) = system(\"gh\", *a)\ngh(\"pr\", \"view\", \"5\")\nEOF",
    "ruby - <<'EOF'\ndef gh(*a) = puts([\"gh\", *a].join(\" \"))\ngh(\"pr\", \"merge\", \"5\")\nEOF",
], ids=["view-twin", "only-printed"])
def test_a_ruby_endless_method_that_runs_no_merge_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


def test_a_ruby_endless_method_given_two_merges_is_compound():
    from shared.merge_guard_common import is_compound_destructive_command

    cmd = "ruby - <<'EOF'\ndef gh(*a) = system(\"gh\", *a)\ngh(\"pr\", \"merge\", \"5\")\ngh(\"pr\", \"merge\", \"6\")\nEOF"
    assert is_compound_destructive_command(cmd) is True
    assert not _runs_after_approval(cmd, APPROVAL_STYLES["inline"])


# A substitution inside a printed value runs only its own body: when that body runs
# nothing guarded (it only prints), the value is printed text, nested spans included.
M5 = "gh pr " + "merge 5"
PRINTED_SUBSTITUTION_ALLOW = [
    ("backtick in quotes", 'echo "`echo ' + M5 + '`"'),
    ("bare backtick", "echo `echo " + M5 + "`"),
    ("dollar paren in quotes", 'echo "$(echo ' + M5 + ')"'),
    ("printf in a substitution", 'echo "$(printf %s ' + M5 + ')"'),
    ("git log grep in a substitution", 'echo "`git log --grep ' + M5 + '`"'),
    ("commit message from a substitution", 'git commit -m "$(echo fix ' + M5 + ' docs)"'),
    ("backtick in a comment body", 'gh pr comment 7 --body "`echo ' + M5 + '`"'),
    ("backtick in an assignment", 'MSG="`echo ' + M5 + '`"'),
    ("backtick in a commit message", 'git commit -m "`echo fix ' + M5 + '`"'),
    ("printf of a printing substitution", "printf '%s\\n' \"$(echo " + M5 + ')"'),
    ("unquoted dollar paren", "echo $(echo " + M5 + ")"),
    ("unquoted dollar paren between words", "echo hi $(printf %s " + M5 + ") there"),
]
RUNNING_SUBSTITUTION_DENY = [
    ("dollar paren", 'echo "$(' + M5 + ')"'),
    ("backtick in quotes", 'echo "`' + M5 + '`"'),
    ("bare backtick", "echo `" + M5 + "`"),
    ("nested dollar paren", 'echo "$(echo "$(' + M5 + ')")"'),
    ("backtick nested in dollar paren", 'echo "$(echo `' + M5 + '`)"'),
    ("commit message running a merge", 'git commit -m "$(' + M5 + ' && echo done)"'),
]


@pytest.mark.parametrize("label,cmd", PRINTED_SUBSTITUTION_ALLOW, ids=[r[0] for r in PRINTED_SUBSTITUTION_ALLOW])
def test_a_substitution_that_only_prints_is_printed_text(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", RUNNING_SUBSTITUTION_DENY, ids=[r[0] for r in RUNNING_SUBSTITUTION_DENY])
def test_a_substitution_that_runs_a_merge_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


def _post_hook_mints(command: str, style, tmp_path) -> bool:
    """Whether the real post hook writes a token for a faithful approval of `command`."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    hooks = Path(__file__).resolve().parents[1] / "hooks"
    (tmp_path / ".claude").mkdir(exist_ok=True)
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": style(command)},
                      {"label": "No", "description": "Hold."}]}]
    payload = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": q}, "tool_response": {"answers": {"Run this?": "Yes, run"}}}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude")}
    r = subprocess.run([sys.executable, str(hooks / "merge_guard_post.py")], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False)
    return "token written" in r.stderr


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", PRINTED_SUBSTITUTION_ALLOW[:4], ids=[r[0] for r in PRINTED_SUBSTITUTION_ALLOW[:4]])
def test_approving_a_printed_substitution_mints_nothing(label, cmd, style, tmp_path):
    assert _post_hook_mints(cmd, style, tmp_path) is False


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
def test_approving_a_real_merge_still_mints(style, tmp_path):
    assert _post_hook_mints("gh pr " + "merge 777", style, tmp_path) is True


# A command substitution runs its body, so a guarded command inside it is read with
# every reader a bare command gets: a -c string, eval, or a pipe or here-string to a
# shell. Two guarded commands inside it are two operations.
SUBSTITUTED_RUNS = [
    ("sh -c in an assignment", "out=$(sh -c 'gh pr " + "merge 5 --squash')"),
    ("bash -c force push with a redirect", "out=$(bash -c 'git push --" + "force origin main' 2>&1)"),
    ("sh -c in a printed value", 'echo "$(sh -c \'gh pr ' + 'merge 5 --squash\')"'),
    ("eval in a printed value", 'echo "$(eval \'gh pr ' + 'merge 5 --squash\')"'),
    ("printf piped to sh", 'echo "$(printf \'gh pr ' + 'merge 5 --squash\' | sh)"'),
    ("here-string to sh", 'echo "$(sh <<< \'gh pr ' + 'merge 5 --squash\')"'),
    ("double-quoted -c in an assignment", 'out=$(sh -c "gh pr ' + 'merge 5 --squash")'),
    ("backtick sh -c", "out=`sh -c 'gh pr " + "merge 5 --squash'`"),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", SUBSTITUTED_RUNS, ids=[r[0] for r in SUBSTITUTED_RUNS])
def test_a_guarded_command_a_substitution_runs_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "out=$(sh -c 'gh pr view 5')",
    "echo '$(sh -c \"gh pr " + "merge 5\")'",
    "cat <<'EOF'\n$(sh -c 'gh pr " + "merge 5')\nEOF",
    "echo hi # $(sh -c 'gh pr " + "merge 5')",
], ids=["view-in-a-substitution", "single-quoted-text", "quoted-heredoc-data", "comment"])
def test_a_substitution_that_runs_nothing_guarded_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("cmd", [
    "out=$(sh -c 'gh pr " + "merge 5 && gh pr " + "merge 6')",
    "out=$(printf 'gh pr " + "merge 5' | sh); gh pr " + "merge 6",
], ids=["two-in-one-c-string", "piped-body-and-a-bare-merge"])
def test_two_guarded_commands_a_substitution_runs_are_compound(cmd):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_compound_destructive_command(cmd) is True
    assert not _runs_after_approval(cmd, APPROVAL_STYLES["inline"])


@pytest.mark.parametrize("cmd", [
    "out=$(gh pr " + "merge 5 --squash)",
    "git push --" + "force origin $(git branch --show-current)",
    "out=$(cat <(gh pr " + "merge 5))",
], ids=["merge-in-a-substitution", "target-from-a-substitution", "process-substitution-nested-in-one"])
def test_one_guarded_command_beside_a_substitution_is_not_compound(cmd):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_compound_destructive_command(cmd) is False


# eval joins its words and runs them as a command line, so the guarded commands it
# chains are counted as a -c string's are: two of them are two operations.
EVAL_TWO = [
    ("two merges", "eval 'gh pr " + "merge 5 && gh pr " + "merge 6'"),
    ("merge and delete", "eval \"gh pr " + "merge 5; git branch -" + "D old\""),
    ("in a printed substitution", 'echo "$(eval \'gh pr ' + 'merge 5 && gh pr ' + 'merge 6\')"'),
]


@pytest.mark.parametrize("label,cmd", EVAL_TWO, ids=[r[0] for r in EVAL_TWO])
def test_an_eval_chaining_two_guarded_commands_is_compound(label, cmd):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_compound_destructive_command(cmd) is True
    assert not _runs_after_approval(cmd, APPROVAL_STYLES["inline"])


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("cmd", [
    "eval 'gh pr " + "merge 5'",
    "eval gh pr " + "merge 5",
    "eval 'gh pr " + "merge 5 && echo done'",
], ids=["quoted", "bare-words", "with-a-benign-tail"])
def test_an_eval_running_one_guarded_command_runs_once_approved(cmd, style):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_compound_destructive_command(cmd) is False
    assert _runs_after_approval(cmd, style)


# A command that opens a compound command's body (`do`, `then`, `{`, `(`, an `if` or
# `while` condition) is a command like any other: its -c string, eval or program is read.
AFTER_KEYWORD_RUNS = [
    ("sh -c in a loop body", "for i in 1 2; do sh -c 'gh pr " + "merge 5'; done"),
    ("sh -c after then", "if true; then sh -c 'gh pr " + "merge 5'; fi"),
    ("bash -c in a while body", 'while true; do bash -c "git push --' + 'force origin main"; break; done'),
    ("sh -c in a group", "{ sh -c 'gh pr " + "merge 5'; }"),
    ("sh -c in a subshell", "( sh -c 'gh pr " + "merge 5' )"),
    ("eval in a loop body", "for i in 1; do eval 'gh pr " + "merge 5'; done"),
    ("sh -c as an if condition", "if sh -c 'gh pr " + "merge 5'; then echo ok; fi"),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", AFTER_KEYWORD_RUNS, ids=[r[0] for r in AFTER_KEYWORD_RUNS])
def test_a_command_after_a_compound_keyword_is_read_and_runs_once_approved(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "if true; then sh -c 'gh pr view 5'; fi",
    "for i in 1 2; do echo 'sh -c \"gh pr " + "merge 5\"'; done",
], ids=["view-after-then", "printed-in-a-loop"])
def test_a_command_after_a_compound_keyword_that_runs_nothing_guarded_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


# A -c string is a command line: one that only prints a guarded command runs nothing
# guarded, as its bare command does; one that runs it is still refused.
PRINTING_C_STRINGS = [
    ("echo in single quotes", "sh -c 'echo gh pr " + "merge 5'"),
    ("echo in double quotes", 'bash -c "echo gh pr ' + 'merge 5"'),
    ("in a substitution", "out=$(sh -c 'echo gh pr " + "merge 5')"),
]
RUNNING_C_STRINGS = [
    ("plain", "sh -c 'gh pr " + "merge 5'"),
    ("after an echo", 'sh -c "echo x; gh pr ' + 'merge 5"'),
    ("printed into a shell", "sh -c 'echo gh pr " + "merge 5 | sh'"),
]


@pytest.mark.parametrize("label,cmd", PRINTING_C_STRINGS, ids=[r[0] for r in PRINTING_C_STRINGS])
def test_a_c_string_that_only_prints_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", RUNNING_C_STRINGS, ids=[r[0] for r in RUNNING_C_STRINGS])
def test_a_c_string_that_runs_the_command_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


# A guarded verb that reaches a -c program through its positional words is read once the
# words are in place: `bash -c 'gh "$@"' _ pr merge 5` runs `gh pr merge 5`.
POSITIONAL_VERB_RUNS = [
    ("gh $@", "bash -c 'gh \"$@\"' _ pr " + "merge 5 --squash"),
    ("git $@", "bash -c 'git \"$@\"' _ push --" + "force origin main"),
    ("after set -x", "bash -c 'set -x; gh \"$@\"' _ pr " + "merge 5 --squash"),
    ("after cd", "bash -c 'cd /tmp && gh \"$@\"' _ pr " + "merge 5 --squash"),
    ("braced $@", "bash -c 'gh \"${@}\"' _ pr " + "merge 5 --squash"),
    ("numbered words", "bash -c 'gh \"$1\" \"$2\" \"$3\"' _ pr " + "merge 5"),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", POSITIONAL_VERB_RUNS, ids=[r[0] for r in POSITIONAL_VERB_RUNS])
def test_a_verb_given_through_positional_words_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


def test_a_view_given_through_positional_words_is_allowed():
    assert is_dangerous_command("bash -c 'gh \"$@\"' _ pr view 5") is False


# A -c program is read the way the bare command is: a guarded command piped or fed by a
# here-string to a shell inside it, or a -c string a substitution prints whole.
C_PROGRAM_RUNS = [
    ("printf piped to sh", "bash -c 'printf \"gh pr " + "merge 5 --squash\" | sh'"),
    ("here-string to sh", "bash -c 'sh <<< \"gh pr " + "merge 5 --squash\"'"),
    ("built by a cat heredoc", "bash -c \"$(cat <<'EOF'\ngh pr " + "merge 5 --squash\nEOF\n)\""),
    ("built by printf", "bash -c \"$(printf 'gh pr " + "merge 5 --squash')\""),
    ("built by echo", "bash -c \"$(echo gh pr " + "merge 5)\""),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", C_PROGRAM_RUNS, ids=[r[0] for r in C_PROGRAM_RUNS])
def test_a_c_program_read_as_its_bare_command_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "sh -c \"$(printf 'gh pr view 5')\"",
    "bash -c 'printf \"gh pr view 5\" | sh'",
], ids=["printed-view", "piped-view"])
def test_a_c_program_that_runs_nothing_guarded_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


# An eval string or a -c program (its positional words in place) that only prints runs
# nothing guarded: its words are data, as an echo's are.
PRINTING_STRING_RUNNERS = [
    ("eval of a quoted echo", "eval \"echo gh pr " + "merge 5\""),
    ("eval of a bare echo", "eval echo gh pr " + "merge 5"),
    ("-c echo of its positional words", "bash -c 'echo \"$@\"' _ gh pr " + "merge 5"),
    ("-c printf of its positional words", "bash -c 'printf \"%s \" \"$@\"' _ gh pr " + "merge 5"),
]


@pytest.mark.parametrize("label,cmd", PRINTING_STRING_RUNNERS, ids=[r[0] for r in PRINTING_STRING_RUNNERS])
def test_a_string_runner_that_only_prints_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


RUNNING_STRING_RUNNERS = [
    ("eval of a quoted merge", "eval \"gh pr " + "merge 5 --squash\""),
    ("eval of a bare merge", "eval gh pr " + "merge 5 --squash"),
    ("-c verb in its positional words", "bash -c 'gh \"$@\"' _ pr " + "merge 5 --squash"),
    ("-c runs its positional words", "bash -c '\"$@\"' _ gh pr " + "merge 5 --squash"),
    ("-c execs its positional words", "sh -c 'exec \"$0\" \"$@\"' gh pr " + "merge 5 --squash"),
    ("eval prints then merges", "eval \"echo x; gh pr " + "merge 5 --squash\""),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", RUNNING_STRING_RUNNERS, ids=[r[0] for r in RUNNING_STRING_RUNNERS])
def test_a_string_runner_that_runs_a_merge_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd,count", [
    ("if true; then gh pr " + "merge 5 --squash; else gh pr " + "merge 6 --squash; fi", 2),
    ("case \"$x\" in a) gh pr " + "merge 5 --squash ;; b) gh pr " + "merge 6 --squash ;; esac", 2),
    ("if [ \"$m\" = s ]; then gh pr " + "merge 5 --squash; elif [ \"$m\" = r ]; then gh pr "
     + "merge 5 --rebase; else gh pr " + "merge 5 --merge; fi", 3),
    ("if a; then if b; then gh pr " + "merge 5 --squash; else gh pr " + "merge 6 --squash; fi; "
     "else gh pr " + "merge 7 --squash; fi", 2),
    ("if a; then gh pr " + "merge 5 --squash; else if b; then gh pr " + "merge 6 --squash; fi; fi", 2),
    ("if true; then gh pr " + "merge 5 --squash; fi", 1),
    ("if x; then echo; else gh pr " + "merge 6 --squash; fi", 1),
    ("gh pr " + "merge 5 --squash", 0),
    ("if true; then gh pr " + "merge 5 --squash; else gh pr " + "merge 6 --squash", 0),
    ("if a; then if b; then gh pr " + "merge 5 --squash; else gh pr " + "merge 6 --squash; fi", 0),
    ("echo hi", 0),
], ids=["if-else", "case", "elif-ladder", "nested-if", "else-if", "one-branch", "else-only",
        "bare", "unclosed", "unclosed-outer", "benign"])
def test_alternative_destructive_branch_count(cmd, count):
    from shared.merge_guard_common import alternative_destructive_branch_count

    assert alternative_destructive_branch_count(cmd) == count


# Only one branch of an if/elif/else, or one arm of a case, runs: alternative branches
# each carrying one merge are one guarded op, and the approval binds the whole text.
ALTERNATIVE_BRANCH_MERGES = {
    "if-else": "if true; then gh pr " + "merge 5 --squash; else gh pr " + "merge 6 --squash; fi",
    "case": "case \"$x\" in a) gh pr " + "merge 5 --squash ;; b) gh pr " + "merge 6 --squash ;; esac",
    "elif-ladder": "if [ \"$m\" = s ]; then gh pr " + "merge 5 --squash; elif [ \"$m\" = r ]; then gh pr "
                   + "merge 5 --rebase; else gh pr " + "merge 5 --merge; fi",
    "nested-if": "if a; then if b; then gh pr " + "merge 5 --squash; else gh pr " + "merge 6 --squash; fi; "
                 "else gh pr " + "merge 7 --squash; fi",
    "else-if": "if a; then gh pr " + "merge 5 --squash; else if b; then gh pr " + "merge 6 --squash; fi; fi",
}


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("cmd", ALTERNATIVE_BRANCH_MERGES.values(), ids=ALTERNATIVE_BRANCH_MERGES.keys())
def test_alternative_branch_merges_are_one_op_that_runs_approved(cmd, style):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_dangerous_command(cmd) is True
    assert is_compound_destructive_command(cmd) is False
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
def test_an_alternative_branch_approval_runs_neither_branch_bare(style):
    from merge_guard_post import _mint_context_from_bundle
    from merge_guard_pre import _token_matches_command

    approved = ALTERNATIVE_BRANCH_MERGES["if-else"]
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": style(approved)},
                      {"label": "No", "description": "Hold."}]}]
    token = {"context": _mint_context_from_bundle(q, {"Run this?": "Yes, run"})[0]}
    assert _token_matches_command(token, approved)
    assert not _token_matches_command(token, "gh pr " + "merge 5 --squash")
    assert not _token_matches_command(token, "gh pr " + "merge 6 --squash")


@pytest.mark.parametrize("cmd", [
    "if true; then gh pr " + "merge 5 --squash; gh pr " + "merge 6 --squash; else echo no; fi",
    "if true; then gh pr " + "merge 5 --squash; fi; gh pr " + "merge 6 --squash",
    "if true; then gh pr " + "merge 5 --squash; else gh pr " + "merge 6 --squash; fi; git branch -D old",
    "if gh pr " + "merge 5 --squash; then echo ok; else gh pr " + "merge 6 --squash; fi",
    "if a; then if b; then gh pr " + "merge 5 --squash; fi; gh pr " + "merge 6 --squash; else echo no; fi",
    "if true; then gh pr " + "merge 5 --squash; else gh pr " + "merge 6 --squash",
], ids=["two-in-one-branch", "branch-then-bare", "branches-then-delete", "condition-and-else",
        "nested-then-bare-in-branch", "unclosed-if"])
def test_merges_that_can_run_together_stay_compound(cmd):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_compound_destructive_command(cmd) is True


# A substitution given to eval or a shell's -c runs what it prints: when its body is a
# shell's -c or an eval that only prints, the printed text is the command that runs.
PRINTING_RUNNER_FEEDS_A_RUNNER = [
    ("eval of a -c echo", "eval \"$(bash -c 'echo gh pr " + "merge 5 --squash')\""),
    ("eval of a -c echo of its words", "eval \"$(sh -c 'echo \"$@\"' _ gh pr " + "merge 5 --squash)\""),
    ("-c of a -c echo", "bash -c \"$(bash -c 'echo gh pr " + "merge 5 --squash')\""),
    ("-c of an eval echo", "sh -c \"$(eval 'echo gh pr " + "merge 5 --squash')\""),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", PRINTING_RUNNER_FEEDS_A_RUNNER,
                         ids=[r[0] for r in PRINTING_RUNNER_FEEDS_A_RUNNER])
def test_a_printing_runner_whose_output_runs_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "eval \"$(bash -c 'echo gh pr view 5')\"",
    "eval \"$(bash -c 'echo hi')\"",
], ids=["prints-a-view", "prints-plain-text"])
def test_a_printing_runner_whose_output_runs_nothing_guarded_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


def test_a_substitution_body_that_pipes_on_is_not_read_as_its_first_print():
    from shared.merge_guard_common import rewritten_executed_command

    cmd = "eval \"$(bash -c 'echo hi' | sh -c 'gh pr " + "merge 5 --squash')\""
    assert rewritten_executed_command(cmd) is None


def test_a_nested_printing_runner_is_read_as_its_printed_text():
    from shared.merge_guard_common import rewritten_executed_command

    cmd = "eval \"$(sh -c 'bash -c \"echo gh pr " + "merge 5 --squash\"')\""
    assert rewritten_executed_command(cmd) == "gh pr " + "merge 5 --squash"


# An echo or printf whose output eval or a shell's -c runs is read as the leg reader reads
# it: quoted words and plain formats included.
QUOTED_PRINT_FEEDS_A_RUNNER = [
    ("single-quoted echo word", "bash -c \"$(echo 'gh pr " + "merge 5 --squash')\""),
    ("double-quoted echo word", "eval \"$(echo \"gh pr " + "merge 5 --squash\")\""),
    ("echo of mixed words", "sh -c \"$(echo 'gh pr' " + "merge 5 --squash)\""),
    ("-c printf in double quotes", "eval \"$(bash -c 'printf \"gh pr " + "merge 5 --squash\"')\""),
    ("printf with a format", "bash -c \"$(printf 'gh pr %s 5 --squash' " + "merge)\""),
    ("echo -n", "eval \"$(echo -n 'gh pr " + "merge 5 --squash')\""),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", QUOTED_PRINT_FEEDS_A_RUNNER, ids=[r[0] for r in QUOTED_PRINT_FEEDS_A_RUNNER])
def test_a_quoted_print_whose_output_runs_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "eval \"$(echo 'gh pr view 5')\"",
    "bash -c \"$(printf '%s\\n' 'gh pr view 5')\"",
    "eval \"$(echo 'echo gh pr " + "merge 5')\"",
], ids=["quoted-view", "printf-view", "prints-an-echo"])
def test_a_quoted_print_whose_output_runs_nothing_guarded_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


def test_a_print_that_runs_a_backtick_substitution_is_not_read():
    from shared.merge_guard_common import rewritten_executed_command

    assert rewritten_executed_command("eval \"$(echo `date`)\"") is None


# Outside double quotes a positional parameter is split into words, as the shell splits it:
# a whole command passed as one word and expanded bare runs that command.
UNQUOTED_POSITIONAL_COMMANDS = [
    ("$1 holds the command", "bash -c '$1' _ \"gh pr " + "merge 5 --squash\""),
    ("$1 holds the start", "sh -c '$1 --squash' _ \"gh pr " + "merge 5\""),
    ("two parameters", "bash -c '$1 $2' _ \"gh pr\" \"" + "merge 5 --squash\""),
    ("$@ of one word", "bash -c '$@' _ \"gh pr " + "merge 5 --squash\""),
    ("$* of one word", "bash -c '$*' _ \"gh pr " + "merge 5 --squash\""),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", UNQUOTED_POSITIONAL_COMMANDS, ids=[r[0] for r in UNQUOTED_POSITIONAL_COMMANDS])
def test_an_unquoted_positional_command_is_refused_then_runs(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "bash -c '$1' _ \"gh pr view 5\"",
    "bash -c 'echo $1' _ \"gh pr " + "merge 5\"",
    "bash -c '\"$1\"' _ \"gh pr " + "merge 5 --squash\"",
], ids=["a-view", "echoed", "quoted-whole-word"])
def test_a_positional_word_that_runs_nothing_guarded_is_allowed(cmd):
    assert is_dangerous_command(cmd) is False


# A literal `set -- WORDS` sets the positional words a later "$1" or "$@" reads.
SET_POSITIONAL_RUNS = [
    ("eval of $1", "set -- \"gh pr " + "merge 5 --squash\"; eval \"$1\""),
    ("$@ as the command", "set -- gh pr " + "merge 5 --squash; \"$@\""),
    ("a target word", "set -- 5; gh pr " + "merge \"$1\" --squash"),
    ("after &&", "set -- pr " + "merge 5 && gh \"$@\" --squash"),
    ("on the next line", "set -- \"gh pr " + "merge 5 --squash\"\neval \"$1\""),
    ("unquoted $1", "set -- \"gh pr " + "merge 5 --squash\"; $1"),
]


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("label,cmd", SET_POSITIONAL_RUNS, ids=[r[0] for r in SET_POSITIONAL_RUNS])
def test_set_positional_words_read_by_a_later_command_are_refused_then_run(label, cmd, style):
    assert is_dangerous_command(cmd) is True
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("cmd", [
    "set -- \"gh pr view 5\"; eval \"$1\"",
    "set -- a b; echo \"$@\"",
    "set -- \"gh pr " + "merge 5\"; echo \"$1\"",
    "set -e; gh pr view 5",
    "echo set -- \"gh pr " + "merge 5 --squash\"; eval \"$1\"",
    "reset -- \"gh pr " + "merge 5 --squash\"; eval \"$1\"",
], ids=["a-view", "echoed-words", "echoed-merge", "set-e", "set-as-an-echo-word", "another-command"])
def test_set_positional_words_that_run_nothing_guarded_are_allowed(cmd):
    assert is_dangerous_command(cmd) is False


# Legs joined only by `||` run one after another only while each fails, so at most one of
# them succeeds: the chain is one op, and its approval binds the whole text.
OR_CHAIN_MERGES = {
    "method-fallback": "gh pr " + "merge 5 --squash || gh pr " + "merge 5 --rebase",
    "three-methods": "gh pr " + "merge 5 --squash || gh pr " + "merge 5 --rebase || gh pr " + "merge 5 --merge",
    "another-pr": "gh pr " + "merge 5 --squash || gh pr " + "merge 6 --squash",
    "admin-on-one-leg": "gh pr " + "merge 5 --squash --admin || gh pr " + "merge 5 --rebase",
    "inside-a-branch": "if true; then gh pr " + "merge 5 --squash || gh pr " + "merge 5 --rebase; fi",
}


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
@pytest.mark.parametrize("cmd", OR_CHAIN_MERGES.values(), ids=OR_CHAIN_MERGES.keys())
def test_an_or_chain_of_merges_is_one_op_that_runs_approved(cmd, style):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_dangerous_command(cmd) is True
    assert is_compound_destructive_command(cmd) is False
    assert _runs_after_approval(cmd, style)


@pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
def test_an_or_chain_approval_runs_no_leg_bare(style):
    from merge_guard_post import _mint_context_from_bundle
    from merge_guard_pre import _token_matches_command

    approved = OR_CHAIN_MERGES["method-fallback"]
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": style(approved)},
                      {"label": "No", "description": "Hold."}]}]
    token = {"context": _mint_context_from_bundle(q, {"Run this?": "Yes, run"})[0]}
    assert _token_matches_command(token, approved)
    for bare in ("gh pr " + "merge 5 --squash", "gh pr " + "merge 5 --rebase", "gh pr " + "merge 777 --squash"):
        assert not _token_matches_command(token, bare)


@pytest.mark.parametrize("cmd", [
    "gh pr checks 5 && gh pr " + "merge 5 --squash || gh pr " + "merge 5 --rebase",
    "gh pr " + "merge 5 --squash || gh pr " + "merge 5 --rebase && gh pr " + "merge 6",
    "gh pr " + "merge 5 --squash && gh pr " + "merge 6 --squash",
    "gh pr " + "merge 5 --squash || gh pr " + "merge 6 --squash | tee log",
], ids=["and-before-the-chain", "and-after-the-chain", "and", "pipe-after-the-chain"])
def test_merges_joined_by_and_or_a_pipe_stay_compound(cmd):
    from shared.merge_guard_common import is_compound_destructive_command

    assert is_compound_destructive_command(cmd) is True


@pytest.mark.parametrize("cmd,count", [
    ("gh pr " + "merge 5 --squash || gh pr " + "merge 5 --rebase", 2),
    ("gh pr " + "merge 5 --squash || gh pr " + "merge 5 --rebase || gh pr " + "merge 5 --merge", 3),
    ("gh pr " + "merge 5 --squash || echo failed", 1),
], ids=["two-legs", "three-legs", "one-destructive-leg"])
def test_alternative_destructive_branch_count_reads_or_chains(cmd, count):
    from shared.merge_guard_common import alternative_destructive_branch_count

    assert alternative_destructive_branch_count(cmd) == count
