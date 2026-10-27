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
    detect_command_operation_type,
    extract_command_context,
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


GQL = API + " graphql -f query="
MERGE_BODY = '{"query":"mutation{mergePullRequest(input:{pullRequestId:\\"PR_1\\"}){clientMutationId}}"}'
QUERY_BODY = '{"query":"query{repository(owner:\\"O\\",name:\\"R\\"){pullRequest(number:5){isInMergeQueue}}}"}'
API_MERGE_TYPED_DENY = [
    ("mergePullRequest",
     GQL + "'mutation{mergePullRequest(input:{pullRequestId:\"PR_1\"}){clientMutationId}}'", "merge"),
    ("enablePullRequestAutoMerge",
     GQL + "'mutation { enablePullRequestAutoMerge(input: {pullRequestId: \"PR_1\"}) { clientMutationId } }'",
     "merge"),
    ("mergeBranch",
     GQL + "'mutation{mergeBranch(input:{repositoryId:\"R_1\",base:\"main\",head:\"feat\"}){clientMutationId}}'",
     "branch-merge"),
    ("POST on merges", f"{API} -X POST repos/O/R/merges -f base=main -f head=feat", "branch-merge"),
    ("implicit POST on merges", f"{API} repos/O/R/merges -f base=main -f head=feat", "branch-merge"),
    ("POST on merge-upstream", f"{API} -X POST repos/O/R/merge-upstream -f branch=main", "branch-merge"),
    ("implicit POST on merge-upstream", f"{API} repos/O/R/merge-upstream -f branch=main", "branch-merge"),
    ("gh pr update-branch", "gh pr update-branch 5", "branch-merge"),
    ("gh pr update-branch with rebase", "gh pr update-branch 5 --rebase", "branch-merge"),
    ("PUT on update-branch", f"{API} -X PUT repos/O/R/pulls/5/update-branch", "branch-merge"),
    ("updatePullRequestBranch",
     GQL + "'mutation{updatePullRequestBranch(input:{pullRequestId:\"PR_1\"}){clientMutationId}}'",
     "branch-merge"),
    ("enqueuePullRequest",
     GQL + "'mutation{enqueuePullRequest(input:{pullRequestId:\"PR_1\"}){clientMutationId}}'", "merge"),
]

API_MERGE_INPUT_DENY = [
    ("an --input body from a heredoc", API + " graphql --input - <<'EOF'\n" + MERGE_BODY + "\nEOF"),
    ("an --input body from a here-string", API + " graphql --input - <<< '" + MERGE_BODY + "'"),
    ("an --input body echoed into it", "echo '" + MERGE_BODY + "' | " + API + " graphql --input -"),
    ("an --input body printed into it", "printf '%s' '" + MERGE_BODY + "' | " + API + " graphql --input -"),
    ("an --input process substitution", API + " graphql --input <(echo '" + MERGE_BODY + "')"),
]


@pytest.mark.parametrize("label,cmd", API_MERGE_INPUT_DENY, ids=[r[0] for r in API_MERGE_INPUT_DENY])
def test_a_graphql_merge_in_a_readable_input_body_is_refused_and_typed(label, cmd):
    assert is_dangerous_command(cmd) is True
    ctx = extract_command_context(cmd)
    assert (ctx["operation_type"], ctx["pr_node_id"]) == ("merge", "PR_1")


GRAPHQL_DATA_ALLOW = [
    ("an --input body that queries", "echo '" + QUERY_BODY + "' | " + API + " graphql --input -"),
    ("an --input heredoc that queries", API + " graphql --input - <<'EOF'\n" + QUERY_BODY + "\nEOF"),
    ("an --input file the guard cannot read", API + " graphql --input body.json"),    ("a query", GQL + "'query{repository(owner:\"O\",name:\"R\"){pullRequest(number:5){mergeable}}}'"),
    ("a mutation name inside a comment body",
     GQL + "'mutation{addComment(input:{subjectId:\"X\",body:\"run mergePullRequest(soon)\"}){clientMutationId}}'"),
    ("an echoed mutation", "echo \"" + GQL + "'mutation{mergePullRequest(input:{pullRequestId:1})}'\""),
    ("a path below the merges endpoint", f"{API} repos/O/R/merges/abc"),
    ("a merge-checks environment", f"{API} -X PUT repos/O/R/environments/merge-checks"),
    ("a read of merge-upstream", f"{API} repos/O/R/merge-upstream"),
    ("viewing the merge state", "gh pr view 5 --json mergeStateStatus"),
    ("a read of the pull request", f"{API} repos/O/R/pulls/5"),
    ("a merge queue query",
     GQL + "'query{repository(owner:\"O\",name:\"R\"){mergeQueue{entries{totalCount}}}}'"),
    ("an echoed update-branch", "echo \"gh pr update-branch 5\""),
    ("a read of update-branch without PUT", f"{API} repos/O/R/pulls/5/update-branch"),
]


@pytest.mark.parametrize("label,cmd,op", API_MERGE_TYPED_DENY, ids=[r[0] for r in API_MERGE_TYPED_DENY])
def test_a_graphql_or_branch_merge_is_refused_and_typed(label, cmd, op):
    assert is_dangerous_command(cmd) is True
    assert detect_command_operation_type(cmd) == op


@pytest.mark.parametrize("label,cmd", GRAPHQL_DATA_ALLOW, ids=[r[0] for r in GRAPHQL_DATA_ALLOW])
def test_graphql_text_that_merges_nothing_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_merge_mutation_binds_its_node_id_and_a_branch_merge_its_base():
    pr = extract_command_context(API_MERGE_TYPED_DENY[0][1])
    assert pr["operation_type"] == "merge" and pr["pr_node_id"] == "PR_1" and "pr_number" not in pr
    for _, cmd, _op in API_MERGE_TYPED_DENY:
        if "merges" in cmd or "merge-upstream" in cmd or "mergeBranch" in cmd:
            assert extract_command_context(cmd)["target_ref"] == "main"


def test_a_graphql_merge_names_its_mutation_and_a_rest_merge_does_not():
    assert extract_command_context(GQL + "'mutation{ mergePullRequest(input:{}) { number } }'")[
        "graphql_mutation"] == "mergePullRequest"
    assert extract_command_context(API_MERGE_INPUT_DENY[0][1])["graphql_mutation"] == "mergePullRequest"
    assert "graphql_mutation" not in extract_command_context("gh pr merge 5")
    assert "graphql_mutation" not in extract_command_context(f"{API} -X POST repos/O/R/merges -f base=main")


def test_a_branch_update_binds_its_pull_request():
    for _, cmd, _op in API_MERGE_TYPED_DENY:
        if "update-branch" in cmd:
            assert extract_command_context(cmd)["pr_number"] == "5"
        elif "updatePullRequestBranch" in cmd or "enqueuePullRequest" in cmd:
            assert extract_command_context(cmd)["pr_node_id"] == "PR_1"


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
     f'python3 dbg.py tree "note: {PUSH} \\`git branch --show-current\\`:feat"'),
    ("backtick in single quotes",
     f"python3 dbg.py tree 'note: {PUSH} `git branch --show-current`:feat'"),
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
    ("an unquoted escaped backtick", f'python3 x.py \\`eval\\` "note: {M}"'),
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


def test_a_bare_print_verb_is_left_as_it_is():
    from shared.merge_guard_common import _blank_unquoted_print_words

    assert _blank_unquoted_print_words("echo") == "echo"
    assert _blank_unquoted_print_words("") == ""


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


# ---------------------------------------------------------------------------
# A file written with literal text and then run is judged as the text it runs.
# ---------------------------------------------------------------------------

FILE_RUN_DENY = [
    ("heredoc written then run", f"cat > f <<'EOF'\n{M}\nEOF\nbash f"),
    ("echo then bash", f"echo '{M}' > f; bash f"),
    ("echo then sh", f"echo '{M}' > f; sh f"),
    ("echo then source", f"echo '{M}' > f && source f"),
    ("printf then dot", f"printf '%s\\n' '{M}' > f; . ./f"),
    ("tee then bash", f"echo '{M}' | tee f; bash f"),
    ("append then bash from stdin", f"echo '{M}' >> f; bash < f"),
    ("chmod then run", f"echo '{M}' > f; chmod +x f; ./f"),
    ("an append after a harmless line", f"echo 'echo hi' > f; echo '{M}' >> f; bash f"),
    ("cat into bash", f"echo '{M}' > f; cat f | bash"),
    ("the run's output piped to a shell", f"echo \"echo '{M}'\" > f; bash f | bash"),
    ("a harmless line appended after the merge", f"echo '{M}' > f; echo 'echo hi' >> f; bash f"),
    ("dynamic text beside the merge", f'echo "{M} $(date)" > f; bash f'),
    ("a backtick beside the merge", f'echo "{M} `date`" > f; bash f'),
    ("an expanding heredoc beside the merge", f"cat > f <<EOF\n{M} $(date)\nEOF\nbash f"),
    ("a substitution in an earlier leg", f"ts=$(date); echo '{M}' > f; bash f"),
    ("a backtick holding a cd in an earlier leg", f"ts=`true; cd /tmp`; echo '{M}' > f; bash f"),
    ("an unquoted substitution beside the merge", f"echo {M} $(date) > f; bash f"),
]

FILE_RUN_ALLOW = [
    ("written, never run", f"echo '{M}' > f"),
    ("written then printed", f"echo '{M}' > f; cat f"),
    ("another file run", f"echo '{M}' > f; bash g"),
    ("run before the write", f"bash f; echo '{M}' > f"),
    ("a harmless file", "echo 'echo hi' > f; bash f"),
    ("a read in the file", "echo 'gh pr view 5' > f; bash f"),
    ("a later write replaces the first", f"echo '{M}' > f; echo 'echo hi' > f; bash f"),
    ("a harmless heredoc file", "cat > f <<'EOF'\necho hi\nEOF\nbash f"),
    ("a substitution alone names no command", 'echo "$(cat cmds)" > f; bash f'),
    ("dynamic text in a harmless file", 'echo "echo $(date)" > f; bash f'),
    ("an expanding heredoc that only echoes", "cat > f <<EOF\necho $HOME\nEOF\nbash f"),
    ("a cd between write and run", f"echo '{M}' > f; cd x; bash f"),
    ("a write and run inside a python heredoc",
     f"python3 - <<'PY'\nprint(\"echo '{M}' > f; bash f\")\nPY"),
]


@pytest.mark.parametrize("label,cmd", FILE_RUN_DENY, ids=[r[0] for r in FILE_RUN_DENY])
def test_a_file_written_then_run_is_judged_as_its_text(label, cmd):
    assert is_dangerous_command(cmd) is True
    result = _mint(cmd)
    assert result.context is not None, result.refusal_reason
    assert (result.context["operation_type"], result.context["pr_number"]) == ("merge", "5")
    assert merge_guard_pre._token_matches_command({"context": result.context}, cmd) is True


@pytest.mark.parametrize("label,cmd", FILE_RUN_ALLOW, ids=[r[0] for r in FILE_RUN_ALLOW])
def test_a_file_not_run_or_harmless_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_the_rewritten_command_inlines_the_run_file():
    assert rewritten_executed_command(f"echo '{M}' > f; bash f") == f"echo '{M}' > f; {M}"
    assert rewritten_executed_command(f"echo '{M}' > f; bash g") is None
    dynamic = 'echo "$(cat cmds)" > f; bash f'
    assert rewritten_executed_command(dynamic) == 'echo "$(cat cmds)" > f; $(cat cmds)'


# An interpreter run of a file the command wrote runs that file as its program.
HELPER_MERGE = "import subprocess\nsubprocess.run([\"gh\", \"pr\", \"" + "merge\", \"5\"])"
INTERPRETER_FILE_RUN_DENY = [
    ("python reads a heredoc-written file", f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 helper.py"),
    ("single-quoted argv", "cat > helper.py <<'EOF'\n" + HELPER_MERGE.replace('"', "'") + "\nEOF\npython3 helper.py"),
    ("node reads an echoed file", f"echo 'require(\"child_process\").execSync(\"{M}\")' > run.js; node run.js"),
    ("ruby after &&", f"echo 'system(\"{M}\")' > run.rb && ruby run.rb"),
    ("perl reads a printf-written file",
     "printf '%s\\n' 'system(\"git push --" + "force origin main\");' > run.pl; perl run.pl"),
    ("a command after the run", f"echo 'require(\"child_process\").execSync(\"{M}\")' > run.js; node run.js; echo done"),
    ("an interpreter flag before the file", f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 -u helper.py"),
    ("an argument after the file", f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 helper.py arg1"),
    ("python -W with its value", f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 -W ignore helper.py"),
    ("python -X with its value", f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 -X dev helper.py"),
    ("python -W with its value attached", f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 -Wignore helper.py"),
    ("node --max-old-space-size", f"echo 'require(\"child_process\").execSync(\"{M}\")' > helper.js; node --max-old-space-size 4096 helper.js"),
    ("node --max-old-space-size attached", f"echo 'require(\"child_process\").execSync(\"{M}\")' > helper.js; node --max-old-space-size=4096 helper.js"),
    ("node -r", f"echo 'require(\"child_process\").execSync(\"{M}\")' > helper.js; node -r ./setup.js helper.js"),
    ("ruby -I", f"echo 'system(\"{M}\")' > run.rb; ruby -I lib run.rb"),
    ("ruby -r", f"echo 'system(\"{M}\")' > run.rb; ruby -r json run.rb"),
    ("perl -I", f"echo 'system(\"{M}\");' > run.pl; perl -I lib run.pl"),
    ("perl -M", f"echo 'system(\"{M}\");' > run.pl; perl -M strict run.pl"),
]
SHELL_FILE_RUN_ARGS_DENY = [
    ("a shell flag before the file", f"echo '{M}' > f; bash -e f"),
    ("an argument after the file", f"echo '{M}' > f; bash f arg1"),
    ("both", f"echo '{M}' > f; sh -x f a b"),
    ("a redirect glued to the file", f"echo '{M}' > f; bash <f"),
]
INTERPRETER_FILE_RUN_ALLOW = [
    ("a program that prints the command", f"cat > helper.py <<'EOF'\nprint(\"{M}\")\nEOF\npython3 helper.py", True),
    ("the file is only read", f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\ncat helper.py", False),
    ("a program that runs a read",
     "cat > helper.py <<'EOF'\n" + HELPER_MERGE.replace("merge", "view") + "\nEOF\npython3 helper.py", True),
    ("the run is of another file", f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 other.py", False),
    ("an inline program before the file",
     f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 -c 'print(1)' helper.py", False),
    ("a shell given -c before the file", f"echo '{M}' > f; bash -c 'echo hi' f", False),
    ("a shell reading stdin given the file as an argument", f"echo '{M}' > f; bash -s f < /dev/null", False),
    ("a value-taking flag before another file",
     f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 -W ignore other.py", False),
    ("a flag's value naming the written file",
     f"cat > helper.py <<'EOF'\n{HELPER_MERGE}\nEOF\npython3 -X helper.py other.py", False),
]


@pytest.mark.parametrize("label,cmd", SHELL_FILE_RUN_ARGS_DENY, ids=[r[0] for r in SHELL_FILE_RUN_ARGS_DENY])
def test_a_shell_running_a_written_file_with_flags_or_arguments_is_judged_as_its_text(label, cmd):
    from shared.merge_guard_common import rewritten_executed_command

    assert is_dangerous_command(cmd) is True
    rewritten = rewritten_executed_command(cmd)
    assert rewritten is not None and rewritten.endswith(M)


HEREDOC_WRITER_FORMS_DENY = [
    ("the redirect after the heredoc", f"cat <<'EOF' > /tmp/m.sh\n{M} --squash\nEOF\nsh /tmp/m.sh"),
    ("an append after the heredoc", f"cat <<'EOF' >> /tmp/m.sh\n{M} --squash\nEOF\nbash /tmp/m.sh"),
    ("tee to a file", f"tee /tmp/m.sh >/dev/null <<'EOF'\n{M} --squash\nEOF\nbash /tmp/m.sh"),
    ("tee -a", f"tee -a /tmp/m.sh <<'EOF'\n{M} --squash\nEOF\nbash /tmp/m.sh"),
    ("tee with a redirect before the file", f"tee 2> err.log /tmp/m.sh <<'EOF'\n{M} --squash\nEOF\nbash /tmp/m.sh"),
]
HEREDOC_APPENDS_KEEP_EARLIER_TEXT = [
    ("cat appends", f"echo '{M}' > /tmp/m.sh; cat <<'EOF' >> /tmp/m.sh\necho hi\nEOF\nbash /tmp/m.sh"),
    ("tee -a appends", f"echo '{M}' > /tmp/m.sh; tee -a /tmp/m.sh <<'EOF'\necho hi\nEOF\nbash /tmp/m.sh"),
]
HEREDOC_WRITER_FORMS_ALLOW = [
    ("a written file never run", f"cat <<'EOF' > notes.txt\n{M}\nEOF\ncat notes.txt"),
    ("tee writing a log never run", f"tee log.txt <<'EOF'\n{M}\nEOF"),
    ("another file run", f"tee /tmp/m.sh >/dev/null <<'EOF'\n{M}\nEOF\nbash /tmp/other.sh"),
]


@pytest.mark.parametrize("label,cmd", HEREDOC_WRITER_FORMS_DENY, ids=[r[0] for r in HEREDOC_WRITER_FORMS_DENY])
def test_a_heredoc_written_by_cat_or_tee_then_run_is_judged_as_its_text(label, cmd):
    from shared.merge_guard_common import rewritten_executed_command

    assert is_dangerous_command(cmd) is True
    rewritten = rewritten_executed_command(cmd)
    assert rewritten is not None and rewritten.count(M) == 1


@pytest.mark.parametrize("label,cmd", HEREDOC_APPENDS_KEEP_EARLIER_TEXT,
                         ids=[r[0] for r in HEREDOC_APPENDS_KEEP_EARLIER_TEXT])
def test_a_heredoc_append_keeps_the_text_written_before_it(label, cmd):
    assert is_dangerous_command(cmd) is True


def test_a_heredoc_only_printed_keeps_its_text_in_the_report():
    from shared.merge_guard_common import rewritten_executed_command

    for cat in ("cat", "cat -n"):
        cmd = f"{cat} <<'EOF'\nnote {M}\nEOF\necho 'echo hi' > f; bash f"
        rewritten = rewritten_executed_command(cmd)
        assert rewritten is not None and f"note {M}" in rewritten


@pytest.mark.parametrize("label,cmd", HEREDOC_WRITER_FORMS_ALLOW, ids=[r[0] for r in HEREDOC_WRITER_FORMS_ALLOW])
def test_a_heredoc_written_file_that_is_not_run_is_data(label, cmd):
    assert is_dangerous_command(cmd) is False


HEREDOC_SCRIPT_RUNS = [
    ("a heredoc-written script run by bash", f"cat > /tmp/m.sh <<'EOF'\n{M} --squash\nEOF\nbash /tmp/m.sh", M + " --squash"),
    ("with a set -e header", f"cat > /tmp/m.sh <<'EOF'\n#!/bin/bash\nset -e\n{M} --squash\nEOF\nbash /tmp/m.sh",
     M + " --squash"),
    ("made executable and run", f"cat > /tmp/m.sh <<'EOF'\n{M} --squash\nEOF\nchmod +x /tmp/m.sh && /tmp/m.sh",
     M + " --squash"),
    ("a force push body", "cat > /tmp/m.sh <<'EOF'\ngit push --" + "force origin feat/x\nEOF\nbash /tmp/m.sh",
     "git push --" + "force origin feat/x"),
]


@pytest.mark.parametrize("label,cmd,runs", HEREDOC_SCRIPT_RUNS, ids=[r[0] for r in HEREDOC_SCRIPT_RUNS])
def test_a_heredoc_written_script_is_reported_running_its_command_once(label, cmd, runs):
    from shared.merge_guard_common import is_compound_destructive_command, rewritten_executed_command

    assert is_dangerous_command(cmd) is True
    rewritten = rewritten_executed_command(cmd)
    assert rewritten is not None
    assert rewritten.count(runs) == 1
    assert is_compound_destructive_command(rewritten) is False
    result = _mint(cmd)
    assert result.context is not None, result.refusal_reason


def test_a_heredoc_written_script_running_two_commands_stays_compound():
    from shared.merge_guard_common import is_compound_destructive_command, rewritten_executed_command

    cmd = f"cat > /tmp/m.sh <<'EOF'\n{M}\n{M[:-1]}6\nEOF\nbash /tmp/m.sh"
    assert is_dangerous_command(cmd) is True
    rewritten = rewritten_executed_command(cmd)
    assert rewritten is not None
    assert is_compound_destructive_command(rewritten) is True


@pytest.mark.parametrize("label,cmd", INTERPRETER_FILE_RUN_DENY, ids=[r[0] for r in INTERPRETER_FILE_RUN_DENY])
def test_an_interpreter_running_a_file_the_command_wrote_is_judged_as_its_program(label, cmd):
    from shared.merge_guard_common import interpreter_command_context, rewritten_executed_command

    assert is_dangerous_command(cmd) is True
    rewritten = rewritten_executed_command(cmd)
    assert rewritten is not None and "<<'PACT_FILE_RUN'\n" in rewritten
    ctx = interpreter_command_context(rewritten)
    assert ctx is not None and ctx["operation_type"] in ("merge", "force-push")


@pytest.mark.parametrize("label,cmd,rewritten", INTERPRETER_FILE_RUN_ALLOW,
                         ids=[r[0] for r in INTERPRETER_FILE_RUN_ALLOW])
def test_an_interpreter_file_that_runs_no_guarded_command_is_allowed(label, cmd, rewritten):
    from shared.merge_guard_common import rewritten_executed_command

    assert is_dangerous_command(cmd) is False
    assert (rewritten_executed_command(cmd) is not None) is rewritten


# ---------------------------------------------------------------------------
# A script given a whole guarded command as one argument runs it, like a variable passed there.
# ---------------------------------------------------------------------------

PUSH_MAIN = "git push --" + "force origin main"
SCRIPT_GIVEN_A_COMMAND = [
    ("a retry wrapper", f'./retry.sh "{M}"', {"operation_type": "merge", "pr_number": "5"}),
    ("a python runner", f'python3 retry.py "{M}"', {"operation_type": "merge", "pr_number": "5"}),
    ("a node runner given a force push", f'node run.js "{PUSH_MAIN}"',
     {"operation_type": "force-push", "target_ref": "main"}),
    ("a retry wrapper given a branch delete", './retry.sh "git branch -' + 'D feat"',
     {"operation_type": "branch-delete", "branch": "feat"}),
    ("a script without an extension", f'./retry "{M}"', {"operation_type": "merge", "pr_number": "5"}),
]

SCRIPT_GIVEN_TEXT_ALLOW = [
    ("a flag's value", "python3 tool.py --pattern 'gh pr " + "merge'"),
    ("a command named in prose", f'./notify.sh "merged: {M} done"'),
    ("grep for the command", "/usr/bin/grep -n 'gh pr " + "merge' commands/peer-review.md"),
    ("a read command", 'python3 tools/log.py "gh pr view 5"'),
    ("a bare-word argv", "grep 'gh pr " + "merge 5' notes"),
    ("an awk program, not a script path", "awk '{print $1}' 'gh pr " + "merge 5'"),
]


@pytest.mark.parametrize("label,cmd,ctx", SCRIPT_GIVEN_A_COMMAND, ids=[r[0] for r in SCRIPT_GIVEN_A_COMMAND])
def test_a_script_given_a_whole_guarded_command_is_refused_and_read_as_it(label, cmd, ctx):
    from shared.merge_guard_common import extract_command_context

    assert is_dangerous_command(cmd) is True
    found = extract_command_context(cmd)
    assert {k: found.get(k) for k in ctx} == ctx


@pytest.mark.parametrize("label,cmd", SCRIPT_GIVEN_TEXT_ALLOW, ids=[r[0] for r in SCRIPT_GIVEN_TEXT_ALLOW])
def test_a_script_given_text_that_is_not_a_whole_command_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_an_approved_merge_given_to_a_script_mints_and_runs():
    for _, cmd, _ in SCRIPT_GIVEN_A_COMMAND[:2]:
        result = _mint(cmd)
        assert result.context is not None, result.refusal_reason
        assert merge_guard_pre._token_matches_command({"context": result.context}, cmd) is True


def test_an_inline_program_is_not_a_script_launch():
    from shared.merge_guard_common import _script_argv_guarded_command

    assert _script_argv_guarded_command(f'python3 -c x.py "{M}"') is None
    assert _script_argv_guarded_command(f'python3 -B x.py "{M}"') == M


def test_the_rewritten_command_is_the_command_the_script_is_given():
    from shared.merge_guard_common import rewritten_executed_command

    given = [M, M, PUSH_MAIN, "git branch -" + "D feat", M]
    assert [rewritten_executed_command(cmd) for _, cmd, _ in SCRIPT_GIVEN_A_COMMAND] == given
    assert [rewritten_executed_command(cmd) for _, cmd in SCRIPT_GIVEN_TEXT_ALLOW] == [None] * len(SCRIPT_GIVEN_TEXT_ALLOW)


# ---------------------------------------------------------------------------
# A variable assigned a literal value is judged where the shell expands it, as if its value
# were written there. A site the guard cannot read (an argument of a script or an
# unknown program) keeps the value executing, so the command stays refused.
# ---------------------------------------------------------------------------

VAR = f"CMD='{M}'; "

VARIABLE_SITE_ALLOW = [
    ("an unquoted program path echoed", 'GH=/usr/local/bin/gh; echo "$GH pr ' + 'merge 5"'),
    ("an unquoted escaped value echoed", 'CMD=gh\\ pr\\ ' + 'merge\\ 5; echo "$CMD"'),
    ("unquoted reassignment to a harmless command", f"CMD='{M}'; CMD=true; $CMD"),
    ("reassigned to a harmless command before it runs", f"CMD='{M}'; CMD='echo hi'; $CMD"),
    ("reassigned before it is echoed", f"CMD='{M}'; CMD='echo hi'; echo \"$CMD\""),
    ("an earlier value beside a single-quoted name", f"CMD='{M}'; CMD='echo hi'; echo '$CMD' \"$CMD\""),
    ("echo", VAR + 'echo "$CMD"'),
    ("echo braces", VAR + 'echo "${CMD}"'),
    ("printf", VAR + 'printf "%s\\n" "$CMD"'),
    ("grep", VAR + 'grep -- "$CMD" notes.txt'),
    ("git commit -m", VAR + 'git commit -m "$CMD"'),
    ("tee", VAR + 'echo hi | tee "$CMD.log"'),
    ("double-quoted assignment", f'CMD="{M}"; echo "$CMD"'),
    ("escaped expansion is text", VAR + 'python3 tool.py "\\$CMD"'),
    ("single-quoted expansion is text", VAR + "python3 tool.py '$CMD'"),
]

VARIABLE_SITE_DENY = [
    ("an unquoted program path runs", "GH=/usr/local/bin/gh; $GH pr " + "merge 5"),
    ("an unquoted program name runs", "CMD=gh; $CMD pr " + "merge 5"),
    ("an unquoted escaped value runs", "CMD=gh\\ pr\\ " + "merge\\ 5; $CMD"),
    ("reassigned to a guarded command before it runs", f"CMD='echo hi'; CMD='{M}'; $CMD"),
    ("each run reads the value before it", f"CMD='{M}'; $CMD; CMD='echo hi'; $CMD"),
    ("command position", VAR + "$CMD"),
    ("timeout prefix", VAR + "timeout 60 $CMD"),
    ("env prefix", VAR + "env FOO=1 $CMD"),
    ("sudo prefix", VAR + "sudo $CMD"),
    ("bash -c", VAR + 'bash -c "$CMD"'),
    ("eval", VAR + 'eval "$CMD"'),
    ("ssh", VAR + 'ssh host "$CMD"'),
    ("echo to bash", VAR + 'echo "$CMD" | bash'),
    ("python -c program", VAR + "python3 -c \"import os; os.system('$CMD')\""),
    ("script argument (refused, approvable)", VAR + './retry.sh "$CMD"'),
    ("python script argument (refused, approvable)", VAR + 'python3 tool.py "$CMD"'),
    ("unknown binary argument", VAR + 'mytool "$CMD"'),
    ("script site wins over an echo site", VAR + 'echo "$CMD"; ./retry.sh "$CMD"'),
    ("assigned twice", VAR + "CMD='echo hi'; CMD='" + M + "'; echo \"$CMD\"; $CMD"),
    ("value holding an expansion", f'CMD="{M} $X"; $CMD'),
    ("parameter expansion form", VAR + "${CMD:-x}"),
    ("used before it is assigned", "$CMD; " + VAR + 'echo "$CMD"'),
    ("assigned in a subshell", f"( CMD='{M}' ); $CMD"),
]


def test_a_variable_written_to_a_file_and_run_is_judged_as_its_value():
    cmd = f"P='{M}'; echo \"$P\" > f; bash f"
    assert is_dangerous_command(cmd) is True
    result = _mint(cmd)
    assert result.context is not None, result.refusal_reason
    assert (result.context["operation_type"], result.context["pr_number"]) == ("merge", "5")


def test_the_rewritten_command_expands_literal_variables():
    assert rewritten_executed_command("CMD=gh; $CMD pr " + "merge 5") == "CMD=STRIPPED; gh pr " + "merge 5"
    assert rewritten_executed_command("GH=/usr/local/bin/gh; echo hi") is None


@pytest.mark.parametrize("label,cmd", VARIABLE_SITE_ALLOW, ids=[r[0] for r in VARIABLE_SITE_ALLOW])
def test_a_variable_expanded_only_where_it_is_data_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


@pytest.mark.parametrize("label,cmd", VARIABLE_SITE_DENY, ids=[r[0] for r in VARIABLE_SITE_DENY])
def test_a_variable_expanded_where_it_runs_or_cannot_be_read_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("site", ['echo "{}"', 'grep -- "{}" notes.txt', 'git commit -m "{}"'])
def test_a_data_site_matches_its_literal_twin(site):
    assert is_dangerous_command(VAR + site.format("$CMD")) is is_dangerous_command(site.format(M))


# Each row below pins one of the variable rule's conservative checks: dropping the check changes the verdict.
VARIABLE_GUARD_DENY = [
    ("a prefix assignment reaches a script's environment", f"CMD='{M}' ./retry.sh; echo \"$CMD\""),
    ("a value read from stdin is unknown", VAR + 'read CMD; echo "$CMD"'),
    ("an exported value reaches child processes", f"export CMD='{M}'; echo \"$CMD\""),
    ("a parameter default runs", f"CMD=''; ${{CMD:-{M}}}"),
    # The value at the site is not certain, so today's rule keeps the value whole. These
    # are conservative refusals the rule requires, not executed commands.
    ("assigned in a branch keeps today's rule", f"if true; then CMD='{M}'; fi; echo \"$CMD\""),
    ("an expanding unquoted reassignment keeps today's rule", f"CMD='{M}'; CMD=$X; echo \"$CMD\""),
    ("assigned in a subshell keeps today's rule", f"( CMD='{M}'; echo hi ); echo \"$CMD\""),
    ("used before its assignment keeps today's rule", f"echo \"$CMD\"; CMD='{M}'; echo ok"),
    ("a value that expands keeps today's rule", f"X=a; CMD=\"{M} $X\"; echo \"$CMD\""),
    ("an eval in the command keeps today's rule", VAR + 'echo "$CMD"; eval true'),
]

VARIABLE_LITERAL_ALLOW = [
    ("a value holding quotes stays one argument", 'CMD=\'x" ; ' + M + ' ; echo "\'; echo "$CMD"'),
    ("a value holding a substitution is not re-run", f"CMD='$({M})'; echo \"$CMD\""),
]


@pytest.mark.parametrize("label,cmd", VARIABLE_GUARD_DENY, ids=[r[0] for r in VARIABLE_GUARD_DENY])
def test_a_variable_whose_value_is_uncertain_keeps_todays_rule(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", VARIABLE_LITERAL_ALLOW, ids=[r[0] for r in VARIABLE_LITERAL_ALLOW])
def test_a_variable_value_is_inserted_as_literal_text(label, cmd):
    assert is_dangerous_command(cmd) is False


EVAL_HEREDOC_PARAMETER = [
    ("an escaped parameter", 'eval "$(cat <<EOF\n' + M[:-1] + '\\$N\nEOF\n)"', M[:-1] + "$N"),
    ("a parameter expanded by the heredoc", 'eval "$(cat <<EOF\n' + M[:-1] + '$N\nEOF\n)"', M[:-1] + "$N"),
    ("a braced parameter", 'eval "$(cat <<EOF\n' + M[:-1] + '${N}\nEOF\n)"', M[:-1] + "${N}"),
    ("an escaped backslash", 'eval "$(cat <<EOF\n' + M + ' \\\\\nEOF\n)"', M + " \\"),
]
EVAL_HEREDOC_UNREAD = [
    ("a substitution", 'eval "$(cat <<EOF\n' + M[:-1] + '$(cat n)\nEOF\n)"'),
    ("a backtick", 'eval "$(cat <<EOF\n' + M[:-1] + '`cat n`\nEOF\n)"'),
    ("a parameter with an operator", 'eval "$(cat <<EOF\n' + M[:-1] + '${N:-5}\nEOF\n)"'),
    ("a parameter beside harmless text", 'eval "$(cat <<EOF\necho $X\nEOF\n)"'),
]


@pytest.mark.parametrize("label,cmd,runs", EVAL_HEREDOC_PARAMETER, ids=[r[0] for r in EVAL_HEREDOC_PARAMETER])
def test_an_eval_heredoc_with_escapes_and_parameters_is_read_as_the_shell_expands_it(label, cmd, runs):
    from shared.merge_guard_common import rewritten_executed_command

    assert is_dangerous_command(cmd) is True
    assert rewritten_executed_command(cmd) == runs


@pytest.mark.parametrize("label,cmd", EVAL_HEREDOC_UNREAD, ids=[r[0] for r in EVAL_HEREDOC_UNREAD])
def test_an_eval_heredoc_with_another_expansion_is_left_unread_and_refused(label, cmd):
    from shared.merge_guard_common import rewritten_executed_command

    assert is_dangerous_command(cmd) is True
    assert rewritten_executed_command(cmd) is None


UNREADABLE_EVAL_HEREDOC = [
    ("a parameter with an operator", 'eval "$(cat <<EOF\n' + M[:-1] + '${N:-5}\nEOF\n)"'),
    ("a substitution", 'eval "$(cat <<EOF\n' + M[:-1] + '$(cat n.txt)\nEOF\n)"'),
    ("a parameter beside harmless text", 'eval "$(cat <<EOF\necho $X\nEOF\n)"'),
]
NOT_UNREADABLE_EVAL_HEREDOC = [
    ("an escaped parameter", 'eval "$(cat <<EOF\n' + M[:-1] + '\\$N\nEOF\n)"'),
    ("a parameter read as guarded", 'eval "$(cat <<EOF\n' + M[:-1] + '$N\nEOF\n)"'),
    ("a quoted delimiter", 'eval "$(cat <<\'EOF\'\n' + M[:-1] + '$N\nEOF\n)"'),
    ("a readable literal body", 'eval "$(cat <<EOF\n' + M + '\nEOF\n)"'),
    ("a guarded command without eval", M),
    ("a harmless command", "echo hi"),
    ("an unreadable eval beside a readable one",
     'eval "$(cat <<EOF\necho $X\nEOF\n)"; eval "$(cat <<EOF\n' + M + '\nEOF\n)"'),
]


@pytest.mark.parametrize("label,cmd", UNREADABLE_EVAL_HEREDOC, ids=[r[0] for r in UNREADABLE_EVAL_HEREDOC])
def test_an_unreadable_eval_heredoc_is_refused_and_reported(label, cmd):
    from shared.merge_guard_common import rewritten_executed_command, unreadable_evald_heredoc

    assert is_dangerous_command(cmd) is True
    assert unreadable_evald_heredoc(cmd) is True
    assert rewritten_executed_command(cmd) is None


@pytest.mark.parametrize("label,cmd", NOT_UNREADABLE_EVAL_HEREDOC, ids=[r[0] for r in NOT_UNREADABLE_EVAL_HEREDOC])
def test_a_readable_or_non_eval_command_is_not_reported_as_an_unreadable_eval(label, cmd):
    from shared.merge_guard_common import unreadable_evald_heredoc

    assert unreadable_evald_heredoc(cmd) is False


# A literal variable given to a script is judged as its literal twin.
FORCE_MAIN = "git push --" + "force origin main"
VARIABLE_GIVEN_TO_A_SCRIPT = [
    ("a retry wrapper", f'P="{FORCE_MAIN}"; ./retry.sh "$P"', FORCE_MAIN,
     {"operation_type": "force-push", "target_ref": "main"}),
    ("a python runner", f'P="{FORCE_MAIN}"; python3 x.py "$P"', FORCE_MAIN,
     {"operation_type": "force-push", "target_ref": "main"}),
    ("a single-quoted branch delete", "D='git branch -" + "D victim'; ./retry.sh \"$D\"",
     "git branch -" + "D victim", {"operation_type": "branch-delete", "branch": "victim"}),
]
VARIABLE_IN_A_SCRIPT_DATA_ARGUMENT_ALLOW = [
    ("inside a larger argument", f'P="{FORCE_MAIN}"; python3 x.py "a $P b"'),
    ("its literal twin", f'python3 x.py "a {FORCE_MAIN} b"'),
    ("a here-string written inside the argument", f'P="{FORCE_MAIN}"; python3 x.py "bash -s <<< \'$P\'#"'),
    ("the same without the comment", f'P="{FORCE_MAIN}"; python3 x.py "bash -s <<< \'$P\'"'),
    ("the here-string's literal twin", f'python3 x.py "bash -s <<< \'{FORCE_MAIN}\'#"'),
]


@pytest.mark.parametrize("label,cmd,runs,ctx", VARIABLE_GIVEN_TO_A_SCRIPT,
                         ids=[r[0] for r in VARIABLE_GIVEN_TO_A_SCRIPT])
def test_a_variable_given_whole_to_a_script_is_read_as_the_command_it_holds(label, cmd, runs, ctx):
    from shared.merge_guard_common import extract_command_context, rewritten_executed_command

    assert is_dangerous_command(cmd) is True
    assert rewritten_executed_command(cmd) == runs
    found = extract_command_context(cmd)
    assert {k: found.get(k) for k in ctx} == ctx


@pytest.mark.parametrize("label,cmd", VARIABLE_IN_A_SCRIPT_DATA_ARGUMENT_ALLOW,
                         ids=[r[0] for r in VARIABLE_IN_A_SCRIPT_DATA_ARGUMENT_ALLOW])
def test_a_variable_inside_a_script_data_argument_is_allowed_like_its_literal_twin(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_script_given_a_variable_holding_two_guarded_commands_is_refused_and_binds_nothing():
    from shared.merge_guard_common import extract_command_context

    cmd = "D='git branch -" + "D a; git branch -" + "D b'; ./retry.sh \"$D\""
    assert is_dangerous_command(cmd) is True
    assert extract_command_context(cmd) == {}


def test_a_variable_given_to_an_unknown_program_keeps_its_value_executing_and_is_reported():
    from shared.merge_guard_common import rewritten_executed_command

    cmd = f'P="{FORCE_MAIN}"; mytool "$P"'
    assert is_dangerous_command(cmd) is True
    assert rewritten_executed_command(cmd) == FORCE_MAIN
    two = "D='git branch -" + "D a; git branch -" + "D b'; mytool \"$D\""
    assert rewritten_executed_command(two) == "git branch -" + "D a; git branch -" + "D b"
    both = f'P="{FORCE_MAIN}"; Q="{M}"; mytool "$P" "$Q"'
    assert rewritten_executed_command(both) == f"{FORCE_MAIN}; {M}"


# A heredoc elsewhere in the command does not stop the literal-variable rule; only an
# expansion or assignment inside a heredoc's body is left as it is.
WRITTEN_PROBE = "cat > t.py <<'EOF'\nprint(1)\nEOF\n"
HEREDOC_BESIDE_A_VARIABLE_DENY = [
    ("an env-prefixed script given the variable", WRITTEN_PROBE + f'P="{FORCE_MAIN}"; X=1 python3 t.py "$P"'),
    ("the written script given the variable", WRITTEN_PROBE + f'P="{FORCE_MAIN}"; python3 t.py "$P"'),
    ("the written script given the literal", WRITTEN_PROBE + f'python3 t.py "{FORCE_MAIN}"'),
]
HEREDOC_BESIDE_A_VARIABLE_ALLOW = [
    ("the variable inside a larger argument", WRITTEN_PROBE + f'P="{FORCE_MAIN}"; X=1 python3 t.py "bash -s <<< \'$P\'#"'),
    ("its literal twin", WRITTEN_PROBE + f'X=1 python3 t.py "bash -s <<< \'{FORCE_MAIN}\'#"'),
]


@pytest.mark.parametrize("label,cmd", HEREDOC_BESIDE_A_VARIABLE_DENY, ids=[r[0] for r in HEREDOC_BESIDE_A_VARIABLE_DENY])
def test_a_script_given_a_guarded_command_beside_a_heredoc_is_refused_and_reported(label, cmd):
    from shared.merge_guard_common import rewritten_executed_command

    assert is_dangerous_command(cmd) is True
    assert rewritten_executed_command(cmd) == FORCE_MAIN


@pytest.mark.parametrize("label,cmd", HEREDOC_BESIDE_A_VARIABLE_ALLOW, ids=[r[0] for r in HEREDOC_BESIDE_A_VARIABLE_ALLOW])
def test_a_variable_inside_a_script_argument_beside_a_heredoc_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_a_variable_inside_a_heredoc_body_is_left_as_it_is():
    from shared.merge_guard_common import _expand_literal_variables

    cmd = f'P="{FORCE_MAIN}"; cat > notes <<EOF\n$P\nEOF'
    assert _expand_literal_variables(cmd) == cmd


def test_an_assignment_after_a_heredoc_is_read_at_its_own_offset():
    # The heredoc's body is long, so a surface that dropped it would shift every offset
    # after it; the assignment and its expansion must still be found where they are.
    cmd = "python3 - <<'PY'\n" + "x = 1\n" * 40 + "PY\n" + f'P="{FORCE_MAIN}"; ./retry.sh "$P"'
    assert is_dangerous_command(cmd) is True


# A shell given -s (or only flags) reads its commands from a heredoc or here-string.
SHELL_STDIN_DENY = [
    ("bash -s", f"bash -s <<'EOF'\n{M} --squash\nEOF"),
    ("bash -s with arguments", f"bash -s -- 5 <<'EOF'\n{M[:-1]}\"$1\" --squash\nEOF"),
    ("sh -s", f"sh -s <<'EOF'\n{M}\nEOF"),
    ("bash -e", f"bash -e <<'EOF'\n{M}\nEOF"),
    ("a here-string to bash -s", f"bash -s arg <<< '{M}'"),
]
SHELL_STDIN_ALLOW = [
    ("bash -c given the heredoc as input", f"bash -c 'cat > notes' <<'EOF'\n{M}\nEOF"),
    ("sh -c given a here-string as input", f"sh -c 'cat > notes' <<< '{M}'"),
]


@pytest.mark.parametrize("label,cmd", SHELL_STDIN_DENY, ids=[r[0] for r in SHELL_STDIN_DENY])
def test_a_shell_reading_its_commands_from_stdin_runs_the_heredoc(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", SHELL_STDIN_ALLOW, ids=[r[0] for r in SHELL_STDIN_ALLOW])
def test_a_shell_given_c_reads_the_heredoc_as_data(label, cmd):
    assert is_dangerous_command(cmd) is False


# A `key=value` word that is a command's argument (`gh api -f query=…`) is no assignment.
GRAPHQL_QUERY_WITH_VARIABLES = "mutation($id: ID!) { " + "merge" + "PullRequest(input: {pullRequestId: $id}) { clientMutationId } }"
FIELD_ARGUMENT_DENY = [
    ("the node id from a shell variable", f"gh api graphql -F id=\"$NODE\" -f query='{GRAPHQL_QUERY_WITH_VARIABLES}'"),
    ("the node id as a field", f"gh api graphql -f id=PR_1 -f query='{GRAPHQL_QUERY_WITH_VARIABLES}'"),
]


@pytest.mark.parametrize("label,cmd", FIELD_ARGUMENT_DENY, ids=[r[0] for r in FIELD_ARGUMENT_DENY])
def test_a_graphql_merge_with_query_variables_is_refused_and_typed(label, cmd):
    from shared.merge_guard_common import extract_command_context

    assert is_dangerous_command(cmd) is True
    assert extract_command_context(cmd)["graphql_mutation"] == "merge" + "PullRequest"


def test_a_query_with_variables_that_reads_is_allowed():
    cmd = "gh api graphql -F id=\"$NODE\" -f query='query($id: ID!) { node(id: $id) { id } }'"
    assert is_dangerous_command(cmd) is False
