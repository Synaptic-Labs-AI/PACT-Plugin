"""Merge guard: text that only names a command is data, not a command.

Each rule below keeps an executed form refused and lets the same words written as
data through. DENY rows run a guarded command and must be refused; ALLOW rows only
carry its text and must be allowed, because refusing them is the cardinal failure.
"""

import merge_guard_pre
import pytest
from merge_guard_post import _mint_context_from_bundle
from shared.merge_guard_common import (
    _has_eval_with_heredoc,
    _rewrite_readable_evals,
    is_compound_destructive_command,
    is_dangerous_command,
    rewritten_executed_command,
)

M = "gh pr " + "merge 5"  # assembled so this file carries no guarded literal


# ---------------------------------------------------------------------------
# An eval fed by a heredoc is judged where the eval and the heredoc execute.
# ---------------------------------------------------------------------------

EVAL_HEREDOC_DENY = [
    ("eval $(cat <<)", f"eval $(cat <<EOF\n{M}\nEOF\n)"),
    ("eval backtick", f"eval `cat <<EOF\n{M}\nEOF\n`"),
    ('eval "$(cat <<)"', f'eval "$(cat <<EOF\n{M}\nEOF\n)"'),
    ("heredoc on the eval", f'eval "$(cat)" <<EOF\n{M}\nEOF'),
    ("heredoc earlier in the pipeline", f'cat <<EOF | eval "$(cat)"\n{M}\nEOF'),
    ("file then run", f"cat > f <<EOF\n{M}\nEOF\neval $(cat f)"),
    ("append then run", f"cat >> f <<EOF\n{M}\nEOF\neval $(cat f)"),
    ("tee then run", f"tee f <<EOF\n{M}\nEOF\neval $(cat f)"),
    ("shell-fed body", f"bash <<EOF\neval \\$(cat <<X\n{M}\nX\n)\nEOF"),
    ("a reader other than cat", "eval \"$(sed s/x/y/ <<EOF\necho hi\nEOF\n)\""),
    ("a body that expands", "eval \"$(cat <<EOF\necho $X\nEOF\n)\""),
    ("a file written then run", "cat > f <<EOF\necho hi\nEOF\neval \"$(cat f)\""),
    ("after a separator", f"cd x && eval $(cat <<EOF\n{M}\nEOF\n)"),
    ("builtin eval", f"builtin eval $(cat <<EOF\n{M}\nEOF\n)"),
    ("unbalanced substitution", f"eval $(cat <<EOF\n{M}\nEOF\n"),
    ("an unquoted glob the shell may expand", "eval $(cat <<'EOF'\ngh pr view *\nEOF\n)"),
]

EVAL_HEREDOC_ALLOW = [
    ("eval text in a python heredoc", 'python3 - <<EOF\ns = "eval \\$(cat f)"\nprint(s)\nEOF'),
    ("eval text in a notes file", "cat > notes.md <<EOF\nRun eval \\$(cat f) to load it.\nEOF"),
    ("eval inside double quotes", 'echo "eval $(cat f)"; cat <<EOF\nx\nEOF'),
    ("eval in a comment", "# eval $(cat <<EOF\ncat <<EOF\nx\nEOF"),
    ("eval after a separator inside a comment", "echo hi  # then ; eval $(cat <<EOF)"),
    ("eval as an echo argument", "echo eval $(cat <<EOF\nx\nEOF\n)"),
    ("escaped substitution in double quotes", 'echo "\\$(eval \\$(cat <<X))"'),
    ("eval in single quotes", "echo 'eval $(cat <<EOF'; cat <<EOF\nx\nEOF"),
    ("eval of an agent, unrelated heredoc", 'eval $(ssh-agent -s); python3 - <<EOF\nprint("hi")\nEOF'),
    ("eval text in a heredoc written by a script",
     "cat > t.py <<'EOF'\nCMDS = [\"eval $(cat f)\", \"cat <<X\"]\nEOF\npython3 t.py"),
    ("eval word in a quoted python list",
     "python3 - <<'PY'\nCMDS = [\"x; eval $(cat <<X\", \"y\"]\nPY"),
]


@pytest.mark.parametrize("label,cmd", EVAL_HEREDOC_DENY, ids=[r[0] for r in EVAL_HEREDOC_DENY])
def test_an_executed_eval_fed_by_a_heredoc_is_refused(label, cmd):
    assert _has_eval_with_heredoc(cmd) is True
    assert is_dangerous_command(cmd) is True


EVAL_HEREDOC_MINTS = [
    ("unquoted eval $(cat <<)", f"eval $(cat <<EOF\n{M}\nEOF\n)"),
    ("unquoted body joined into one line", "eval $(cat <<EOF\ngh pr\n" + "merge 5\nEOF\n)"),
    ("a quoted delimiter still expands on eval", f"X='{M}'; eval \"$(cat <<'EOF'\n$X\nEOF\n)\""),
    ("the eval's output piped to a shell", f"eval \"$(cat <<'EOF'\necho '{M}'\nEOF\n)\" | bash"),
    ("beside an unrelated python heredoc",
     f"eval \"$(cat <<EOF\n{M}\nEOF\n)\"; python3 - <<'PY'\nprint(\"gh pr " + "merge 6\")\nPY"),
    ('eval "$(cat <<)"', f'eval "$(cat <<EOF\n{M}\nEOF\n)"'),
    ("heredoc on the eval", f'eval "$(cat)" <<EOF\n{M}\nEOF'),
    ("heredoc earlier in the pipeline", f'cat <<EOF | eval "$(cat)"\n{M}\nEOF'),
]


# The text an eval reads from a literal heredoc is the command it runs.
READABLE_EVAL_ALLOW = [
    ("eval $(cat <<) runs echo", "eval $(cat <<EOF\necho hi\nEOF\n)"),
    ("heredoc on the eval runs echo", "eval \"$(cat)\" <<EOF\necho hi\nEOF"),
    ("heredoc piped into the eval runs echo", "cat <<EOF | eval \"$(cat)\"\necho hi\nEOF"),
    ("eval of a backtick cat runs echo", "eval `cat <<EOF\necho hi\nEOF\n`"),
    ("an unassigned variable in a quoted body", "eval \"$(cat <<'EOF'\ncd $HOME && ls\nEOF\n)\""),
    ("a variable the command never assigns", "eval \"$(cat <<'EOF'\n$X\nEOF\n)\""),
    ("a guarded command the body only echoes", f"eval \"$(cat <<'EOF'\necho \"{M}\"\nEOF\n)\""),
    ("a double-quoted star is not expanded", "eval \"$(cat <<'EOF'\necho *\nEOF\n)\""),
]


UNREADABLE_EVALS = [
    ("the terminator line inside the body",
     "eval \"$(cat <<EOF\ngh pr view 5\nEOF\ngh pr " + "merge 5\nEOF\n)\""),
    ("a tab-indented terminator inside a <<- body",
     "eval \"$(cat <<-EOF\n\tgh pr view 5\n\tEOF\ngh pr " + "merge 5\nEOF\n)\""),
    ("an unquoted glob", "eval $(cat <<'EOF'\necho [a]\nEOF\n)"),
    ("an unquoted star", "eval $(cat <<'EOF'\nls *.py\nEOF\n)"),
]


@pytest.mark.parametrize("label,cmd", UNREADABLE_EVALS, ids=[r[0] for r in UNREADABLE_EVALS])
def test_an_eval_body_the_shell_reads_differently_is_not_rewritten(label, cmd):
    assert _rewrite_readable_evals(cmd) == cmd
    assert is_dangerous_command(cmd) is True


def test_the_rewritten_command_is_public_and_none_without_a_rewrite():
    assert rewritten_executed_command("eval $(cat <<EOF\necho hi\nEOF\n)") == "echo hi"
    assert rewritten_executed_command(f'eval "$(cat)" <<EOF\n{M}\nEOF') == M
    assert rewritten_executed_command("echo hi") is None
    assert rewritten_executed_command("eval $(cat <<EOF\necho $X\nEOF\n)") is None
    assert rewritten_executed_command("eval $(cat <<EOF\n\x00'\"\nEOF") is None


@pytest.mark.parametrize("label,cmd", READABLE_EVAL_ALLOW, ids=[r[0] for r in READABLE_EVAL_ALLOW])
def test_an_eval_of_a_readable_harmless_heredoc_is_allowed(label, cmd):
    assert _has_eval_with_heredoc(cmd) is True
    assert is_dangerous_command(cmd) is False


def _mint(cmd):
    question = "Run this?"
    bundle = [{"question": question, "header": "Run", "multiSelect": False, "options": [
        {"label": "Yes, run", "description": f"Run `{cmd}` now"},
        {"label": "No", "description": "Hold."},
    ]}]
    return _mint_context_from_bundle(bundle, {question: "Yes, run"})


@pytest.mark.parametrize("label,cmd", EVAL_HEREDOC_MINTS, ids=[r[0] for r in EVAL_HEREDOC_MINTS])
def test_an_approved_eval_fed_by_a_heredoc_mints_and_runs(label, cmd):
    assert is_dangerous_command(cmd) is True
    result = _mint(cmd)
    assert result.context is not None, result.refusal_reason
    assert (result.context["operation_type"], result.context["pr_number"]) == ("merge", "5")
    assert merge_guard_pre._token_matches_command({"context": result.context}, cmd) is True


TWO = "git push --" + "force origin main"


@pytest.mark.parametrize("cmd", [
    f'eval "$(cat)" <<EOF\n{M}\n{TWO}\nEOF',
    f"eval $(cat <<EOF\n{M}\n{TWO}\nEOF\n)",
])
def test_two_commands_an_eval_runs_from_a_heredoc_are_compound(cmd):
    assert is_compound_destructive_command(cmd) is True


def test_two_commands_in_a_quoted_eval_substitution_are_refused_and_never_mint():
    cmd = f'eval "$(cat <<EOF\n{M}\n{TWO}\nEOF\n)"'
    assert is_dangerous_command(cmd) is True
    assert _mint(cmd).context is None


@pytest.mark.parametrize("label,cmd", EVAL_HEREDOC_ALLOW, ids=[r[0] for r in EVAL_HEREDOC_ALLOW])
def test_eval_heredoc_text_that_does_not_execute_is_allowed(label, cmd):
    assert _has_eval_with_heredoc(cmd) is False
    assert is_dangerous_command(cmd) is False


# Guarded text quoted inside a program's text or a script's argument is data.
PF = "git push --" + "force origin main"
API_SETTING = "gh api -X PATCH repos/O/R -f delete_branch_on_merge=true"
QUOTED_PROGRAM_TEXT_ALLOW = [
    ("a gh api call in a -c string", "python3 -c \"x='gh api -X PATCH repos/O/R -f a=b'\""),
    ("a settings call in a -c string", f"python3 -c \"x='{API_SETTING}'\""),
    ("a settings call as a script argument", f'python3 tool.py "{API_SETTING}"'),
    ("a settings call echoed", f'echo "{API_SETTING}"'),
    ("a merge in a -c string", f"python3 -c \"x='{M}'\""),
    ("a branch delete in a -c string", "python3 -c \"x='git branch -" + "D feat'\""),
    ("a ref delete in a -c string",
     "python3 -c \"x='gh api -X DELETE repos/O/R/git/refs/heads/x'\""),
    ("a settings call in a -c list", f"python3 -c \"\nfor c in ['{API_SETTING}']:\n  print(c)\n\""),
    ("two guarded commands in a -c list",
     f"python3 -c \"\nfor c in ['gh pr merge -t 5','{PF}']:\n  print(c)\n\""),
    ("a guarded command printed with repr", f"python3 -c \"\nfor c in ['{PF}']:\n  print(repr(c),'->',c)\n\""),
    ("a guarded command printed", f"python3 -c \"\nfor c in ['{PF}']:\n  print(c,'->',c)\n\""),
    ("a guarded command as a print argument", f"python3 -c \"print('a','->','{PF}')\""),
    ("a guarded command first in a print", f"python3 -c \"print('{PF}', '->')\""),
    ("a shell line in a -c string",
     f"python3 -c \"\nc=\\\"X='{PF}'; echo \\$X | bash\\\"\nprint(c)\n\""),
    ("an assignment in a -c string", f"python3 -c \"\nc=\\\"X='{PF}'\\\"\nprint(c)\n\""),
    ("merge in a field value of a script argument",
     "python3 tool.py \"gh api -X PATCH repos/O/R -f title='please merge me'\""),
    ("merge in a body of a single-quoted script argument",
     "python3 tool.py 'gh api repos/O/R/issues -f body=\"merge when green\"'"),
    ("a curl delete as a node argument",
     "node tool.js \"curl -X DELETE -d 'x=1' https://api.github.com/repos/O/R/git/refs/heads/x\""),
    ("a gh api list in a -c string",
     "python3 -c \"\ncmds = ['gh api -X POST repos/O/R/issues -f title=merge']\nprint(cmds)\n\""),
    ("a bare assignment in a -c string", f"python3 -c \"\nX='{PF}'\nprint(X)\n\""),
    ("a compound as a ruby argument",
     "ruby tool.rb \"git commit -m 'merge' && gh api -X PATCH repos/O/R -f allow_auto_merge=true\""),
]


@pytest.mark.parametrize("label,cmd", QUOTED_PROGRAM_TEXT_ALLOW,
                         ids=[r[0] for r in QUOTED_PROGRAM_TEXT_ALLOW])
def test_guarded_text_quoted_in_a_program_or_argument_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# ---------------------------------------------------------------------------
# A gh api call is a merge by its endpoint, never by a field name.
# ---------------------------------------------------------------------------

API = "gh " + "api"

GH_API_MERGE_DENY = [
    ("PUT on the merge endpoint", f"{API} -X PUT repos/O/R/pulls/5/merge"),
    ("implicit POST on the merge endpoint", f"{API} repos/O/R/pulls/5/merge -f merge_method=squash"),
    ("POST on merges", f"{API} -X POST repos/O/R/merges -f base=main -f head=feat"),
]

GH_API_SETTINGS_ALLOW = [
    ("delete_branch_on_merge", f"{API} -X PATCH repos/O/R -f delete_branch_on_merge=true"),
    ("allow_auto_merge", f"{API} -X PATCH repos/O/R -F allow_auto_merge=true -F allow_merge_commit=false"),
    ("squash_merge_commit_title", f"{API} repos/O/R -f squash_merge_commit_title=PR_TITLE"),
    ("merge as a field value", f"{API} -X PATCH repos/O/R/pulls/5 -f title=merge"),
    ("a path segment that starts with merge", f"{API} -X PUT repos/O/R/environments/merge-checks"),
]


@pytest.mark.parametrize("label,cmd", GH_API_MERGE_DENY, ids=[r[0] for r in GH_API_MERGE_DENY])
def test_a_gh_api_merge_endpoint_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


IMPLICIT_POST_MERGES = [
    ("a field after the endpoint", "gh api repos/O/R/pulls/5/" + "merge -f merge_method=squash"),
    ("a field before the endpoint", "gh api -f commit_title=t /repos/O/R/pulls/5/" + "merge"),
]


@pytest.mark.parametrize("label,cmd", IMPLICIT_POST_MERGES, ids=[r[0] for r in IMPLICIT_POST_MERGES])
def test_an_implicit_post_api_merge_mints_and_runs(label, cmd):
    assert is_dangerous_command(cmd) is True
    result = _mint(cmd)
    assert result.context is not None, result.refusal_reason
    assert (result.context["operation_type"], result.context["pr_number"]) == ("merge", "5")
    assert merge_guard_pre._token_matches_command({"context": result.context}, cmd) is True


@pytest.mark.parametrize("label,cmd", GH_API_SETTINGS_ALLOW, ids=[r[0] for r in GH_API_SETTINGS_ALLOW])
def test_a_gh_api_field_named_merge_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# ---------------------------------------------------------------------------
# xargs running a shell on here-string or heredoc input runs each item.
# ---------------------------------------------------------------------------

XARGS_SHELL_DENY = [
    ("here-string, -I{}", f'xargs -I{{}} sh -c "{{}}" <<< "{M}"'),
    ("here-string, -0", f"xargs -0 -I{{}} sh -c '{{}}' <<< \"{M}\""),
    ("heredoc", f'xargs -I{{}} sh -c "{{}}" <<EOF\n{M}\nEOF'),
    ("-i default token", f'xargs -i bash -c "{{}}" <<< "{M}"'),
    ("bash -lc", f'xargs -I@ bash -lc "@" <<< "{M}"'),
    ("$@ script", f"xargs -L1 sh -c '\"$@\"' _ <<< \"{M}\""),
    ("second heredoc line", f'xargs -I{{}} sh -c "{{}}" <<EOF\necho hi\n{M}\nEOF'),
]

XARGS_SHELL_ALLOW = [
    ("echo is not a shell", f'xargs -I{{}} echo "{{}}" <<< "{M}"'),
    ("grep -c is not a shell", f'xargs -I{{}} grep -c "{{}}" notes.txt <<< "{M}"'),
    ("shell without the token", f'xargs -I{{}} sh -c "echo done" <<< "{M}"'),
    ("printf with the token", f'xargs -I{{}} printf "%s\\n" "{{}}" <<< "{M}"'),
    ("token in single quotes of a script", "xargs -I{} sh -c 'echo $1' <<< \"x\""),
    ("plain heredoc to xargs", "xargs -n1 echo <<EOF\na b\nEOF"),
]


@pytest.mark.parametrize("label,cmd", XARGS_SHELL_DENY, ids=[r[0] for r in XARGS_SHELL_DENY])
def test_xargs_running_a_shell_on_its_stdin_items_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", XARGS_SHELL_ALLOW, ids=[r[0] for r in XARGS_SHELL_ALLOW])
def test_xargs_that_runs_no_item_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# ---------------------------------------------------------------------------
# A backtick in single quotes, or an escaped one, is literal text.
# ---------------------------------------------------------------------------

PUSH = "git push " + "--force origin"
BRANCH_D = "git branch " + "-D"

LITERAL_BACKTICK_ALLOW = [
    ("escaped backtick in double quotes",
     f'python3 dbg.py tree "{PUSH} \\`git branch --show-current\\`:feat"'),
    ("backtick in single quotes",
     f"python3 dbg.py tree '{PUSH} `git branch --show-current`:feat'"),
    ("escaped backtick in prose",
     f'python3 -B check.py a.txt "prose \\`: x\\` then {BRANCH_D} \\"feat\\""'),
    ("escaped backtick in a module's argument",
     f'python3 -m dbg tree "{PUSH} \\`git branch --show-current\\`:feat"'),
    ("backtick in single quotes in a module's argument",
     f"python3 -m dbg tree '{PUSH} `git branch --show-current`:feat'"),
]

EXECUTED_BACKTICK_DENY = [
    ("unescaped backtick in double quotes runs",
     f'python3 dbg.py tree "x `{BRANCH_D} feat` y"'),
    ("unquoted backtick runs", f"python3 dbg.py tree `{BRANCH_D} feat`"),
    ("double backslash leaves the backtick live",
     f'python3 dbg.py tree "x \\\\`{BRANCH_D} feat\\\\` y"'),
    ("unescaped backtick in a module's argument runs", f'python3 -m dbg tree "x `{BRANCH_D} feat` y"'),
]


@pytest.mark.parametrize("label,cmd", LITERAL_BACKTICK_ALLOW, ids=[r[0] for r in LITERAL_BACKTICK_ALLOW])
def test_a_literal_backtick_in_a_script_argument_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", EXECUTED_BACKTICK_DENY, ids=[r[0] for r in EXECUTED_BACKTICK_DENY])
def test_an_executed_backtick_in_a_script_argument_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


# A script file's arguments are its argv data; a shell substitution in them runs first.
SCRIPT_ARGV_ALLOW = [
    ("prose naming eval and a branch delete",
     f'python3 -B tools/check.py msg.txt "an eval of a printer that prints {BRANCH_D} feat"'),
    ("prose naming system() and a merge", f'python3 tools/check.py "a note: {M} runs via system()"'),
    ("single-quoted prose naming eval", f"python3 x.py 'eval of {M}'"),
    ("a single-quoted substitution", f"python3 x.py '$({M})'"),
    ("an escaped substitution", f'python3 x.py "\\$({M})"'),
    ("an unquoted escaped backtick", f'python3 x.py \\`eval\\` "{M}"'),
]

SCRIPT_ARGV_DENY = [
    ("a substitution in double quotes runs", f'python3 x.py "$({M})"'),
    ("a backtick in double quotes runs", f'python3 x.py "`{M}`"'),
    ("an unquoted substitution runs", f"python3 x.py $({M})"),
    ("an unquoted backtick runs", f"python3 x.py `{M}`"),
    ("a module's argument is searched whole", f'python3 -m tool "eval {M}"'),
    ("a bundled module flag is not a script launch", f'python3 -Bm tool x.py "eval {M}"'),
    ("a live substitution beside the prose", f'python3 x.py "$(which eval) {M}"'),
    ("a program read from stdin is not a script launch", f'python3 - tools/x.py "eval {M}"'),
    ("conservative: a flag value before the script is not placed",
     f'python3 -W ignore tools/x.py "eval {M}"'),
    ("conservative: a live backtick keeps a module's argument judged", f'python3 -m tool "x `ls` {M}"'),
]


@pytest.mark.parametrize("label,cmd", SCRIPT_ARGV_ALLOW, ids=[r[0] for r in SCRIPT_ARGV_ALLOW])
def test_a_script_files_quoted_arguments_are_data(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", SCRIPT_ARGV_DENY, ids=[r[0] for r in SCRIPT_ARGV_DENY])
def test_a_substitution_or_unplaceable_launch_stays_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


# ---------------------------------------------------------------------------
# A heredoc's body runs only when its own command's output reaches a shell.
# ---------------------------------------------------------------------------

HEREDOC_ROUTED_DENY = [
    ("own pipe to bash", f"cat <<EOF | bash\n{M}\nEOF"),
    ("own pipe through sed", f"cat <<EOF | sed s/x/y/ | bash\n{M}\nEOF"),
    ("own procsub", f"cat <<EOF > >(bash)\n{M}\nEOF"),
    ("own pipe to xargs sh", f'cat <<EOF | xargs -I{{}} sh -c "{{}}"\n{M}\nEOF'),
    ("group piped", f"{{ cat <<EOF\n{M}\nEOF\n}} | bash"),
    ("subshell piped", f"( cat <<EOF\n{M}\nEOF\n) | sh"),
    ("loop piped", f"for i in 1; do cat <<EOF\n{M}\nEOF\ndone | bash"),
    ("substitution then piped", f'x=$(cat <<EOF\n{M}\nEOF\n); echo "$x" | bash'),
]

HEREDOC_DATA_ALLOW = [
    ("data heredoc, another pipeline to bash",
     f"cat > rows.json <<'EOF'\n{{\"cmd\": \"{M}\"}}\nEOF\nprintf ok | bash -c 'cat'"),
    ("data heredoc, a later sh read",
     f"cat > f <<'EOF'\n{M}\nEOF\necho x | sh -c 'read y'"),
]


@pytest.mark.parametrize("label,cmd", HEREDOC_ROUTED_DENY, ids=[r[0] for r in HEREDOC_ROUTED_DENY])
def test_a_heredoc_whose_output_reaches_a_shell_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", HEREDOC_DATA_ALLOW, ids=[r[0] for r in HEREDOC_DATA_ALLOW])
def test_a_data_heredoc_beside_another_shell_pipe_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


# ---------------------------------------------------------------------------
# An echo or printf prints its unquoted words; they run only when its output reaches a shell.
# ---------------------------------------------------------------------------

PUSHF = "git push --" + "force origin main"
PRINT_WORDS_ALLOW = [
    ("a plain variable beside the words", f"echo ${{X}} {M}"),
    ("unquoted echo", f"echo {M}"),
    ("unquoted force push echoed", f"echo {PUSHF}"),
    ("printf words", f'printf "%s\\n" {M}'),
    ("echo into a file", f"echo {M} > notes.txt"),
    ("echo with a stderr redirect", f"echo -e {M} 2> err.log"),
    ("echo after an assignment", f"FOO=1 echo {M}"),
    ("echo in a branch", f"if true; then echo {M}; fi"),
    ("a substitution that only prints a word", f'python3 x.py "$(echo eval) {M}"'),
]

PRINT_WORDS_DENY = [
    ("a process substitution among the words", f"echo <({M})"),
    ("an output process substitution", f"printf %s >({PUSHF})"),
    ("an expansion other than a plain name", f"echo ${{X:-{M}}}"),
    ("echo piped to bash", f"echo {M} | bash"),
    ("echo to a process substitution", f"echo {M} > >(bash)"),
    ("a substitution runs", f"echo $({M})"),
    ("a substitution among words", f"echo a $(echo b; {M}) c"),
    ("a backtick inside a word", f"echo x`{M}`y"),
    ("echo as a value, not the command", f"env X=echo {M}"),
    ("a command after the echo", f"echo hi; {M}"),
]


@pytest.mark.parametrize("label,cmd", PRINT_WORDS_ALLOW, ids=[r[0] for r in PRINT_WORDS_ALLOW])
def test_words_an_echo_only_prints_are_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", PRINT_WORDS_DENY, ids=[r[0] for r in PRINT_WORDS_DENY])
def test_printed_words_that_reach_a_shell_or_run_are_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


# ---------------------------------------------------------------------------
# A guarded command run by a substitution is read as that command, so its target binds.
# ---------------------------------------------------------------------------

BRANCH_DEL = "git branch -" + "D feat"
SUBSTITUTED_COMMAND_TARGETS = [
    ("an input process substitution", f"cat <({PUSHF})", ("force-push", "target_ref", "main")),
    ("one beside a file", f"diff <({PUSHF}) x", ("force-push", "target_ref", "main")),
    ("an output process substitution", f"printf %s >({PUSHF})", ("force-push", "target_ref", "main")),
    ("a command substitution", f"echo $({PUSHF})", ("force-push", "target_ref", "main")),
    ("a backtick substitution", f"echo `{PUSHF}`", ("force-push", "target_ref", "main")),
    ("a branch delete", f"cat <({BRANCH_DEL})", ("branch-delete", "branch", "feat")),
    ("a branch delete echoed", f"echo <({BRANCH_DEL})", ("branch-delete", "branch", "feat")),
    ("an assignment from a substitution", f"x=$({PUSHF})", ("force-push", "target_ref", "main")),
    ("a merge", f"echo <({M})", ("merge", "pr_number", "5")),
]


@pytest.mark.parametrize("label,cmd,want", SUBSTITUTED_COMMAND_TARGETS,
                         ids=[r[0] for r in SUBSTITUTED_COMMAND_TARGETS])
def test_a_substituted_command_binds_its_own_target_and_an_approval_runs_it(label, cmd, want):
    from shared.merge_guard_common import extract_command_context

    op, key, value = want
    found = extract_command_context(cmd)
    assert (found.get("operation_type"), found.get(key)) == (op, value)
    result = _mint(cmd)
    assert result.context is not None, result.refusal_reason
    assert merge_guard_pre._token_matches_command({"context": result.context}, cmd) is True


def test_a_substitution_running_two_guarded_commands_binds_nothing_from_its_body():
    from shared.merge_guard_common import _guarded_substitution_body

    assert _guarded_substitution_body(f"echo $({M}; {PUSHF})") is None
    assert _guarded_substitution_body(f"{M} $({PUSHF})") is None
    assert _guarded_substitution_body(f"echo $({M}) $({PUSHF})") is None

