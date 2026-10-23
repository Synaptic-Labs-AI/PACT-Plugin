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


@pytest.mark.parametrize("label,cmd", GH_API_SETTINGS_ALLOW, ids=[r[0] for r in GH_API_SETTINGS_ALLOW])
def test_a_gh_api_field_named_merge_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False

