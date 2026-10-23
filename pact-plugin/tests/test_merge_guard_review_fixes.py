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
    ("argument after the string", "bash -c 'x' arg0", None),
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
