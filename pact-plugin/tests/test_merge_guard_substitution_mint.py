"""
Location: pact-plugin/tests/test_merge_guard_substitution_mint.py
Summary: A faithful approval of a guarded command whose target is a command
         substitution (`git push --force origin $(git branch --show-current)`)
         mints and runs. The region reading cannot resolve such a target, so the
         mint reads the command's one guarded op and binds the token to the
         command's normalized text; the pre-hook then runs exactly that text.
         The same exact-text mint covers every command the classifier judges after
         a rewrite (an eval of readable text, a file written then run, a literal
         variable, a script given a whole command), the GitHub API and GraphQL
         merge and branch-update shapes, and an eval of a heredoc the classifier
         refuses because it cannot read it.
Used by: pytest. The real mint function and the real pre-hook matcher run in
         process; a fenced block carries an approval whose command holds backticks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from merge_guard_post import _exact_text_reading, _mint_context_from_bundle
from merge_guard_pre import _token_matches_command
from shared.merge_guard_common import (
    is_compound_destructive_command,
    is_dangerous_command,
    normalize_command_text,
)

FORCE = "git push --" + "force origin "
MERGE = "gh pr " + "merge "
CURRENT = "$(git branch --show-current)"
ROWS = {
    "force-dollar-paren": FORCE + CURRENT,
    "merge-dollar-paren": MERGE + "$(gh pr view --json number -q .number)",
    "force-backtick": FORCE + "`git branch --show-current`",
    "force-dollar-paren-in-bash-c-dq": 'bash -c "' + FORCE + CURRENT + '"',
    "force-dollar-paren-in-bash-c-sq": "bash -c '" + FORCE + CURRENT + "'",
    "merge-held-in-a-variable-for-a-script": "CMD='" + MERGE + "5'; ./retry.sh \"$CMD\"",
    "evald-heredoc-variable-target": 'eval "$(cat <<EOF\n' + MERGE + '\\$N\nEOF\n)"',
    "evald-heredoc-unescaped-variable": 'eval "$(cat <<EOF\n' + MERGE + '$N\nEOF\n)"',
    "evald-heredoc-quoted-delimiter": "eval \"$(cat <<'EOF'\n" + MERGE + "$N\nEOF\n)\"",
}


def _mint(command: str):
    desc = f"Run this:\n```bash\n{command}\n```" if "`" in command else f"Run `{command}` now"
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": desc},
                      {"label": "No", "description": "Hold."}]}]
    return _mint_context_from_bundle(q, {"Run this?": "Yes, run"})


def _runs(token_ctx, command: str) -> bool:
    if not is_dangerous_command(command):
        return True
    if is_compound_destructive_command(command):
        return False
    return token_ctx is not None and _token_matches_command({"context": token_ctx}, command)


class TestAFaithfulClickRuns:
    @pytest.mark.parametrize("command", ROWS.values(), ids=ROWS.keys())
    def test_the_approved_command_is_refused_then_runs(self, command):
        assert not _runs(None, command)
        ctx, _ = _mint(command)
        assert _runs(ctx, command)

    @pytest.mark.parametrize("key", ["force-dollar-paren", "merge-dollar-paren", "force-backtick"])
    def test_a_substituted_target_is_bound_by_its_text(self, key):
        ctx, _ = _mint(ROWS[key])
        assert ctx is not None
        assert ctx["command_text"] == normalize_command_text(ROWS[key])


API = "gh " + "api "
API_SHAPES = {
    "implicit-post-pulls-merge": API + "repos/O/R/pulls/5/merge -f merge_method=squash",
    "branch-merge": API + "-X POST repos/O/R/merges -f base=main -f head=feat",
    "graphql-merge-pull-request": API + "graphql -f query='mutation{mergePullRequest("
                                  "input:{pullRequestId:\"PR_kwDO123\"}){clientMutationId}}'",
    "graphql-enable-auto-merge": API + "graphql -f query='mutation{enablePullRequestAutoMerge("
                                 "input:{pullRequestId:\"PR_kwDO123\"}){clientMutationId}}'",
    "graphql-merge-branch": API + "graphql -F query='mutation{mergeBranch(input:{repositoryId:"
                            "\"R_1\",base:\"main\",head:\"feat\"}){clientMutationId}}'",
}
UNQUOTED = {
    "assigned-gh-path": "GH=/usr/local/bin/gh; $GH pr " + "merge 5",
    "assigned-gh-word": "CMD=gh; $CMD pr " + "merge 5",
    "escaped-spaces": "CMD=gh\\ pr\\ " + "merge\\ 5; $CMD",
}


class TestGhApiMergeShapesRunOnAClick:
    @pytest.mark.parametrize("command", API_SHAPES.values(), ids=API_SHAPES.keys())
    def test_the_approved_api_merge_is_refused_then_runs(self, command):
        assert not _runs(None, command)
        ctx, _ = _mint(command)
        assert _runs(ctx, command)

    def test_a_graphql_token_runs_only_its_own_node_id(self):
        ctx, _ = _mint(API_SHAPES["graphql-merge-pull-request"])
        other = API_SHAPES["graphql-merge-pull-request"].replace("PR_kwDO123", "PR_kwDO999")
        assert not _runs(ctx, other)
        assert not _runs(ctx, MERGE + "5")

    def test_a_branch_merge_token_runs_only_its_own_head(self):
        ctx, _ = _mint(API_SHAPES["branch-merge"])
        assert ctx is not None and "command_text" in ctx
        assert not _runs(ctx, API_SHAPES["branch-merge"].replace("head=feat", "head=other"))


class TestAnUnquotedVariableMergeRunsOnAClick:
    @pytest.mark.parametrize("command", UNQUOTED.values(), ids=UNQUOTED.keys())
    def test_the_approved_command_is_refused_then_runs(self, command):
        assert not _runs(None, command)
        ctx, _ = _mint(command)
        assert _runs(ctx, command)

    def test_a_plain_command_the_region_reading_misses_is_left_to_it(self, monkeypatch):
        """Only a fact the classifier reports licenses the exact-text path for a
        plain command: the region reading finding nothing must not, on its own."""
        monkeypatch.setattr("merge_guard_post._collect_pairs", lambda texts: {})
        command = MERGE + "5"
        assert _exact_text_reading(command, f"`{command}`") is None


SCRIPT_ARGS = {
    "shell-script-merge": "./retry.sh \"" + MERGE + "5\"",
    "python-script-merge": "python3 retry.py \"" + MERGE + "5\"",
    "node-script-force-push": "node run.js \"" + FORCE + "main\"",
    "shell-script-branch-delete": "./retry.sh \"git branch -" + "D feat\"",
    "variable-for-a-script": "CMD=\"" + MERGE + "5\"; ./retry.sh \"$CMD\"",
    "bash-script-merge": "bash run.sh \"" + MERGE + "5\"",
}
FILE_THEN_RUN = {
    "heredoc-written": "cat > f <<'EOF'\n" + MERGE + "5\nEOF\nbash f",
    "echo-bash": "echo '" + MERGE + "5' > f; bash f",
    "echo-sh": "echo '" + MERGE + "5' > f; sh f",
    "echo-source": "echo '" + MERGE + "5' > f && source f",
    "printf-dot": "printf '%s\\n' '" + MERGE + "5' > f; . ./f",
    "tee-bash": "echo '" + MERGE + "5' | tee f; bash f",
    "append-stdin": "echo '" + MERGE + "5' >> f; bash < f",
    "chmod-run": "echo '" + MERGE + "5' > f; chmod +x f; ./f",
    "second-line-appended": "echo 'echo hi' > f; echo '" + MERGE + "5' >> f; bash f",
    "variable-written": "P='" + MERGE + "5'; echo \"$P\" > f; bash f",
}
BRANCH_UPDATES = {
    "merge-upstream": API + "-X POST repos/O/R/merge-upstream -f branch=main",
    "merge-upstream-implicit-post": API + "repos/O/R/merge-upstream -f branch=main",
    "pr-update-branch": "gh pr update-" + "branch 5",
    "pr-update-branch-rebase": "gh pr update-" + "branch 5 --rebase",
    "rest-update-branch": API + "-X PUT repos/O/R/pulls/5/update-" + "branch",
    "rest-update-branch-expected-head": API + "-X PUT repos/O/R/pulls/5/update-" + "branch -f expected_head_sha=abc",
    "graphql-update-branch": API + "graphql -f query='mutation{updatePullRequest" + "Branch(input:{pullRequestId:\"PR_1\"}){clientMutationId}}'",
    "graphql-enqueue": API + "graphql -f query='mutation{enqueuePullRequest(input:{pullRequestId:\"PR_1\"}){clientMutationId}}'",
    "rest-merges-implicit-post": API + "repos/o/r/merges -f base=main -f head=x",
    "rest-merges-body-file": API + "repos/o/r/merges --input body.json",
    "rest-merges-on-a-host": API + "--hostname ghe.io repos/o/r/merges -f base=main",
    "graphql-merge-empty-input": API + "graphql -f query=\x27mutation{ merge" + "PullRequest(input:{}) { number } }\x27",
}
MERGE_BODY = '{"query":"mutation{merge' + 'PullRequest(input:{pullRequestId:\\"PR_1\\"}){clientMutationId}}"}'
INPUT_BODIES = {
    "heredoc": API + "graphql --input - <<'EOF'\n" + MERGE_BODY + "\nEOF",
    "here-string": API + "graphql --input - <<< '" + MERGE_BODY + "'",
    "echoed": "echo '" + MERGE_BODY + "' | " + API + "graphql --input -",
    "printed": "printf '%s' '" + MERGE_BODY + "' | " + API + "graphql --input -",
    "process-substitution": API + "graphql --input <(echo '" + MERGE_BODY + "')",
}
NOT_GUARDED = {
    "pattern-argument": "python3 tool.py --pattern '" + MERGE.strip() + "'",
    "merge-text-in-a-message": "./notify.sh \"merged: " + MERGE + "5 done\"",
    "grep-for-merge": "/usr/bin/grep -n '" + MERGE.strip() + "' commands/peer-review.md",
    "view-for-a-script": "python3 tools/log.py \"gh pr view 5\"",
    "argument-to-an-inline-program": "python3 -c \"print(1)\" \"" + MERGE + "5\"",
    "pr-merge-state": "gh pr view 5 --json mergeStateStatus",
    "rest-pull-read": API + "repos/O/R/pulls/5",
    "graphql-merge-queue-read": API + "graphql -f query='query{repository(owner:\"O\",name:\"R\"){mergeQueue{entries{totalCount}}}}'",
    "echoed-update-branch": "echo \"gh pr update-" + "branch 5\"",
    "merge-upstream-read": API + "repos/O/R/merge-upstream",
    "environment-named-merge": API + "-X PUT repos/O/R/environments/merge-checks",
    "repo-setting-naming-merge": API + "-X PATCH repos/O/R -f delete_branch_on_merge=true",
    "file-written-not-run": "echo '" + MERGE + "5' > f; cat f",
    "other-file-run": "echo '" + MERGE + "5' > f; bash g",
    "harmless-file-run": "echo 'echo hi' > f; bash f",
}
UNREADABLE_EVALS = {
    "default-expansion": 'eval "$(cat <<EOF\n' + MERGE + '${N:-5}\nEOF\n)"',
    "substitution": 'eval "$(cat <<EOF\n' + MERGE + '$(cat n.txt)\nEOF\n)"',
    "echoed-variable": 'eval "$(cat <<EOF\necho $X\nEOF\n)"',
}


class TestARewrittenOrTextBoundCommandRunsOnAClick:
    @pytest.mark.parametrize("command", [
        *[pytest.param(c, id=f"script-{k}") for k, c in SCRIPT_ARGS.items()],
        *[pytest.param(c, id=f"file-{k}") for k, c in FILE_THEN_RUN.items()],
        *[pytest.param(c, id=f"branch-{k}") for k, c in BRANCH_UPDATES.items()],
        *[pytest.param(c, id=f"input-{k}") for k, c in INPUT_BODIES.items()],
    ])
    def test_the_approved_command_is_refused_then_runs(self, command):
        assert not _runs(None, command)
        ctx, _ = _mint(command)
        assert _runs(ctx, command)

    @pytest.mark.parametrize("command", NOT_GUARDED.values(), ids=NOT_GUARDED.keys())
    def test_a_command_the_guard_allows_mints_nothing(self, command):
        assert _runs(None, command)
        assert _mint(command)[0] is None

    def test_a_branch_update_token_runs_only_its_own_pull_request(self):
        ctx, _ = _mint(BRANCH_UPDATES["pr-update-branch"])
        assert ctx is not None and "command_text" in ctx
        assert not _runs(ctx, "gh pr update-" + "branch 6")


class TestAnUnreadableEvaldHeredocRunsOnAClick:
    """The classifier refuses an eval of a heredoc whose command it cannot read;
    an approval binds the command's exact text, so it runs as shown and nothing
    else does."""

    @pytest.mark.parametrize("command", UNREADABLE_EVALS.values(), ids=UNREADABLE_EVALS.keys())
    def test_the_approved_command_is_refused_then_runs(self, command):
        assert not _runs(None, command)
        ctx, _ = _mint(command)
        assert ctx is not None and ctx["command_text"] == normalize_command_text(command)
        assert _runs(ctx, command)

    @pytest.mark.parametrize("other", [
        *[pytest.param(c, id=k) for k, c in UNREADABLE_EVALS.items() if k != "default-expansion"],
        pytest.param(UNREADABLE_EVALS["default-expansion"].replace(":-5", ":-6"), id="other-default"),
        pytest.param(MERGE + "5", id="plain-merge"),
        pytest.param(FORCE + "main", id="force-push"),
    ])
    def test_an_approval_runs_no_other_command(self, other):
        ctx, _ = _mint(UNREADABLE_EVALS["default-expansion"])
        assert not _runs(ctx, other)


HOOKS = Path(__file__).resolve().parents[1] / "hooks"
ONE_RUN = {
    "unreadable-evald-heredoc": UNREADABLE_EVALS["default-expansion"],
    "branch-merge": BRANCH_UPDATES["merge-upstream"],
    "graphql-node-id-merge": API_SHAPES["graphql-merge-pull-request"],
}


class TestAnApprovalWithNoSuccessSignalRunsOnce:
    """A token whose op has no stdout success line and no PR number to read back
    retires on the successful run itself, through the real hooks: the approved
    command runs once, and a second run is refused. A failed run (the platform
    sends PostToolUseFailure, which this hook does not receive) keeps the token."""

    def _hook(self, name: str, payload: dict, root: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HOOKS / name)], input=json.dumps({"session_id": "s1", **payload}),
            capture_output=True, text=True, cwd=str(root), timeout=60, check=False,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
                 "CLAUDE_CONFIG_DIR": str(root / ".claude")})

    def _approve(self, command: str, root: Path) -> None:
        (root / ".claude").mkdir()
        q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
              "options": [{"label": "Yes, run", "description": f"Run `{command}` now"},
                          {"label": "No", "description": "Hold."}]}]
        post = self._hook("merge_guard_post.py", {
            "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": q}, "tool_response": {"answers": {"Run this?": "Yes, run"}},
        }, root)
        assert "token written" in post.stderr, post.stderr

    def _runs(self, command: str, root: Path) -> bool:
        pre = self._hook("merge_guard_pre.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
        }, root)
        return '"deny"' not in pre.stdout

    def _succeeded(self, command: str, root: Path) -> None:
        self._hook("merge_guard_post.py", {
            "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": {"stdout": "", "stderr": "", "interrupted": False},
        }, root)

    @pytest.mark.parametrize("command", ONE_RUN.values(), ids=ONE_RUN.keys())
    def test_a_successful_run_retires_the_approval(self, command, tmp_path):
        self._approve(command, tmp_path)
        assert self._runs(command, tmp_path)
        self._succeeded(command, tmp_path)
        assert not self._runs(command, tmp_path)

    def test_a_failed_run_keeps_the_approval_for_a_retry(self, tmp_path):
        command = ONE_RUN["unreadable-evald-heredoc"]
        self._approve(command, tmp_path)
        assert self._runs(command, tmp_path)
        assert self._runs(command, tmp_path)


class TestASubstitutionThatRunsCommandsTypesNothing:
    def test_two_commands_in_an_evald_substitution_never_mint(self):
        command = 'eval "$(cat <<EOF\n' + MERGE + "5\n" + FORCE + 'main\nEOF\n)"'
        assert _mint(command)[0] is None



BACKTICK_ROWS = {
    "bare": FORCE + "`git branch --show-current`",
    "in-bash-c": 'bash -c "' + FORCE + '`git branch --show-current`"',
}
INLINE_SUBSTITUTIONS = {
    "evald-heredoc-assignment": 'eval "$(cat <<EOF\nx=`date`; ' + MERGE + '5\nEOF\n)"',
    "evald-heredoc-flag-value": 'eval "$(cat <<EOF\n' + MERGE + '5 --body x`date`\nEOF\n)"',
    "file-run-after-an-assignment": "printf '%s\\n' '" + MERGE + "5' > f; x=`date`; bash f",
    "bash-c-with-a-trailing-argument": 'bash -c "' + FORCE + 'main" x`date`',
    "two-substitutions-apart": 'bash -c "' + FORCE + 'main" `date` `whoami`',
}
SHELL_C = "bash -c '" + FORCE + "feat'"
APPROVAL_STYLES = {
    "inline": lambda c: f"Run `{c}` now",
    "double-backtick": lambda c: f"Run `` {c} `` now",
    "fenced": lambda c: f"Run this:\n```bash\n{c}\n```",
}


def _mint_styled(command: str, style) -> dict | None:
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": style(command)},
                      {"label": "No", "description": "Hold."}]}]
    return _mint_context_from_bundle(q, {"Run this?": "Yes, run"})[0]


class TestABacktickTargetRunsHoweverTheApprovalQuotesIt:
    """An approval may quote a command that holds backticks inline, between double
    backticks, or in a fenced block; each must bind the whole command."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("command", BACKTICK_ROWS.values(), ids=BACKTICK_ROWS.keys())
    def test_the_approved_command_is_refused_then_runs(self, command, style):
        assert not _runs(None, command)
        assert _runs(_mint_styled(command, style), command)

    def test_two_separate_code_spans_still_bind_the_command_span(self):
        command = MERGE + "5"
        ctx = _mint_styled(command, lambda c: f"Run `{c}` (check it with `gh pr view 5` first)")
        assert _runs(ctx, command)

    @pytest.mark.parametrize("style", [
        pytest.param(lambda c: f"Run `{c} ` then check with `gh pr view 5`", id="second-span-after"),
        pytest.param(lambda c: f"Run `{c} ` to merge", id="no-second-span"),
        pytest.param(lambda c: f"Run `{c} `", id="at-end-of-text"),
    ])
    def test_a_trailing_space_inside_the_span_does_not_join_it_to_the_next(self, style):
        command = MERGE + "5"
        assert _runs(_mint_styled(command, style), command)

    def test_a_backtick_span_stops_at_its_own_closer_before_a_second_span(self):
        command = BACKTICK_ROWS["bare"]
        ctx = _mint_styled(command, lambda c: f"Run `{c}` then check `gh pr view 5`")
        assert _runs(ctx, command)

    @pytest.mark.parametrize("command", INLINE_SUBSTITUTIONS.values(), ids=INLINE_SUBSTITUTIONS.keys())
    def test_a_substitution_after_a_word_runs_under_an_inline_approval(self, command):
        assert not _runs(None, command)
        assert _runs(_mint_styled(command, APPROVAL_STYLES["inline"]), command)

    @pytest.mark.parametrize("style", [
        pytest.param(lambda c: f"Use the `gh`s output, then run `{c}` now", id="word-after-a-span"),
        pytest.param(lambda c: f"Run `{c}` (`gh pr view 5` shows it)", id="span-in-parentheses-after"),
    ])
    def test_prose_spans_beside_the_command_stay_apart(self, style):
        assert not _runs(None, SHELL_C)
        assert _runs(_mint_styled(SHELL_C, style), SHELL_C)


class TestNothingElseRuns:
    @pytest.mark.parametrize("other", [
        pytest.param(FORCE + "$(git rev-parse --abbrev-ref HEAD)", id="other-substitution"),
        pytest.param(FORCE + "main", id="literal-target"),
        pytest.param(FORCE + CURRENT + " --no-verify", id="extra-flag"),
    ])
    def test_a_token_runs_only_its_own_substitution_text(self, other):
        ctx, _ = _mint(ROWS["force-dollar-paren"])
        assert not _runs(ctx, other)

    def test_a_compound_with_a_substitution_mints_nothing(self):
        command = f"{FORCE}{CURRENT} && {MERGE}5"
        ctx, reason = _mint(command)
        assert (ctx, reason) == (None, "compound_command")


PY_MERGE = 'import subprocess\nsubprocess.run(["gh", "pr", "' + 'merge", "5"])'
FILE_RUNS = {
    "python-heredoc": "cat > helper.py <<'EOF'\n" + PY_MERGE + "\nEOF\npython3 helper.py",
    "python-heredoc-single-quotes": "cat > helper.py <<'EOF'\n" + PY_MERGE.replace('"', "'") + "\nEOF\npython3 helper.py",
    "node-echo": "echo 'require(\"child_process\").execSync(\"" + MERGE + "5\")' > run.js; node run.js",
    "ruby-echo": "echo 'system(\"" + MERGE + "5\")' > run.rb && ruby run.rb",
    "perl-printf": "printf '%s\\n' 'system(\"" + FORCE + "main\");' > run.pl; perl run.pl",
    "node-echo-then-a-harmless-leg": "echo 'require(\"child_process\").execSync(\"" + MERGE + "5\")' > run.js; node run.js; echo done",
    "python-unbuffered": "cat > helper.py <<'EOF'\n" + PY_MERGE + "\nEOF\npython3 -u helper.py",
    "python-with-an-argument": "cat > helper.py <<'EOF'\n" + PY_MERGE + "\nEOF\npython3 helper.py arg1",
    "bash-errexit": "echo '" + MERGE + "5' > f; bash -e f",
    "bash-with-an-argument": "echo '" + MERGE + "5' > f; bash f arg1",
    "sh-xtrace-with-arguments": "echo '" + MERGE + "5' > f; sh -x f a b",
    "bash-reading-the-file": "echo '" + MERGE + "5' > f; bash <f",
}
FILE_RUN_CONTROLS = {
    "program-prints-the-command": "cat > helper.py <<'EOF'\nprint(\"" + MERGE + "5\")\nEOF\npython3 helper.py",
    "file-only-read": "cat > helper.py <<'EOF'\n" + PY_MERGE + "\nEOF\ncat helper.py",
    "program-runs-a-read": "cat > helper.py <<'EOF'\n" + PY_MERGE.replace("merge", "view") + "\nEOF\npython3 helper.py",
    "another-file-run": "cat > helper.py <<'EOF'\n" + PY_MERGE + "\nEOF\npython3 other.py",
    "inline-program-before-the-file": "cat > helper.py <<'EOF'\n" + PY_MERGE + "\nEOF\npython3 -c 'print(1)' helper.py",
    "shell-c-before-the-file": "echo '" + MERGE + "5' > f; bash -c 'echo hi' f",
    "shell-stdin-given-the-file": "echo '" + MERGE + "5' > f; bash -s f < /dev/null",
}
SCRIPT_VARIABLES = {
    "force-push-to-a-shell-script": 'P="' + FORCE + 'main"; ./retry.sh "$P"',
    "force-push-to-a-python-script": 'P="' + FORCE + 'main"; python3 x.py "$P"',
    "branch-delete-to-a-shell-script": "D='git branch -" + "D victim'; ./retry.sh \"$D\"",
}
SCRIPT_VARIABLE_TARGETS = {
    "force-push-to-a-shell-script": ("force-push", "target_ref", "main"),
    "force-push-to-a-python-script": ("force-push", "target_ref", "main"),
    "branch-delete-to-a-shell-script": ("branch-delete", "branch", "victim"),
}
DATA_VARIABLES = {
    "variable-inside-a-larger-argument": 'P="' + FORCE + 'main"; python3 x.py "a $P b"',
    "its-literal-twin": 'python3 x.py "a ' + FORCE + 'main b"',
    "variable-in-a-here-string-argument": 'P="' + FORCE + 'main"; python3 x.py "bash -s <<< \'$P\'#"',
}


class TestAFileWrittenThenRunMintsAndRuns:
    @pytest.mark.parametrize("command", FILE_RUNS.values(), ids=FILE_RUNS.keys())
    def test_the_approved_command_is_refused_then_runs(self, command):
        assert not _runs(None, command)
        assert _runs(_mint(command)[0], command)

    @pytest.mark.parametrize("command", FILE_RUN_CONTROLS.values(), ids=FILE_RUN_CONTROLS.keys())
    def test_a_control_mints_nothing(self, command):
        assert _runs(None, command)
        assert _mint(command)[0] is None


class TestACommandHeldInAVariableForAScriptRuns:
    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("key", SCRIPT_VARIABLES.keys())
    def test_the_approved_command_runs_and_binds_its_target(self, key, style):
        command = SCRIPT_VARIABLES[key]
        assert not _runs(None, command)
        ctx = _mint_styled(command, style)
        assert _runs(ctx, command)
        op, field, value = SCRIPT_VARIABLE_TARGETS[key]
        assert (ctx["operation_type"], ctx[field]) == (op, value)

    @pytest.mark.parametrize("command", DATA_VARIABLES.values(), ids=DATA_VARIABLES.keys())
    def test_a_variable_in_a_larger_argument_is_data(self, command):
        assert _runs(None, command)
        assert _mint(command)[0] is None

    def test_a_variable_holding_two_guarded_commands_mints_nothing(self):
        command = "D='git branch -" + "D a; git branch -" + "D b'; ./retry.sh \"$D\""
        assert not _runs(None, command)
        assert _mint(command)[0] is None


UNKNOWN_PROGRAM_VARIABLE = 'P="' + FORCE + 'main"; mytool "$P"'


class TestACommandHeldInAVariableForAnUnknownProgramRuns:
    """An unknown program given a variable holding a guarded command stays refused;
    an approval binds its exact text, so it runs and the same program given another
    value does not."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    def test_the_approved_command_is_refused_then_runs(self, style):
        assert not _runs(None, UNKNOWN_PROGRAM_VARIABLE)
        ctx = _mint_styled(UNKNOWN_PROGRAM_VARIABLE, style)
        assert ctx is not None and ctx["command_text"] == normalize_command_text(UNKNOWN_PROGRAM_VARIABLE)
        assert _runs(ctx, UNKNOWN_PROGRAM_VARIABLE)

    @pytest.mark.parametrize("other", [
        pytest.param(UNKNOWN_PROGRAM_VARIABLE.replace("main", "release"), id="same-variable-other-value"),
        pytest.param('Q="' + FORCE + 'release"; mytool "$Q"', id="other-variable"),
    ])
    def test_an_approval_runs_no_other_value(self, other):
        ctx = _mint_styled(UNKNOWN_PROGRAM_VARIABLE, APPROVAL_STYLES["inline"])
        assert not _runs(None, other)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("command", [
        pytest.param("D='git branch -" + "D a; git branch -" + "D b'; mytool \"$D\"", id="unknown-program"),
        pytest.param("D='git branch -" + "D a; git branch -" + "D b'; ./retry.sh \"$D\"", id="script"),
    ])
    def test_a_value_holding_two_guarded_commands_mints_nothing(self, command):
        assert not _runs(None, command)
        assert _mint(command) == (None, "compound_command")


class TestAnUnspacedDoubleBacktickApprovalRuns:
    """An approval between double backticks with no inner spaces ends in one run of
    three backticks when the command itself ends in a substitution's closer."""

    @pytest.mark.parametrize("command", BACKTICK_ROWS.values(), ids=BACKTICK_ROWS.keys())
    def test_the_approved_command_is_refused_then_runs(self, command):
        assert not _runs(None, command)
        assert _runs(_mint_styled(command, lambda c: f"Run ``{c}`` now"), command)


WRITTEN_SCRIPTS = {
    "merge": "cat > /tmp/m.sh <<'EOF'\n" + MERGE + "5 --squash\nEOF\nbash /tmp/m.sh",
    "force-push": "cat > /tmp/m.sh <<'EOF'\n" + FORCE + "feat/x\nEOF\nbash /tmp/m.sh",
}


class TestAScriptWrittenThenRunMintsAndRuns:
    """The region reading finds the guarded command in the written file's text, a
    place the pre-hook does not type it from; the mint reads the command the
    classifier says runs instead, so the approval binds its exact text."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("command", WRITTEN_SCRIPTS.values(), ids=WRITTEN_SCRIPTS.keys())
    def test_the_approved_command_is_refused_then_runs(self, command, style):
        assert not _runs(None, command)
        assert _runs(_mint_styled(command, style), command)


PROBE_WRITE = "cat > probe.py <<'EOF'\nimport sys\nprint(sys.argv)\nEOF\n"
ENV_PREFIXED = {
    "probe-given-commands-as-data": PROBE_WRITE + 'M="' + MERGE + '5 --squash"; P="' + FORCE + 'main"; '
    'PYTHONDONTWRITEBYTECODE=1 python3 probe.py "bash -n < f" "bash --version < f" '
    '"bash -s <<< \'$P\'#" "printf x | env bash -x#"',
}


class TestAnEnvPrefixedLaunchAfterAFileWrite:
    """A file written earlier and an env-assignment prefix on the launch keep a
    variable read where the shell expands it: one inside a larger argument is data,
    and a whole guarded command handed to the script is refused and runs once
    approved."""

    @pytest.mark.parametrize("command", ENV_PREFIXED.values(), ids=ENV_PREFIXED.keys())
    def test_commands_passed_as_data_are_allowed_and_mint_nothing(self, command):
        assert _runs(None, command)
        assert _mint(command)[0] is None

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    def test_a_variable_holding_a_guarded_command_runs_once_approved(self, style):
        command = PROBE_WRITE + 'P="' + FORCE + 'main"; X=1 python3 probe.py "$P"'
        assert not _runs(None, command)
        assert _runs(_mint_styled(command, style), command)


QUOTED_BACKTICK_COMMANDS = {
    "argument-to-a-python-script": 'python3 x.py "`' + MERGE + '5`"',
    "argument-to-a-shell-script": './retry.sh "`' + MERGE + '5`"',
    "argument-to-echo": 'echo "`' + MERGE + '5`"',
    "second-argument": 'python3 x.py "eval" "`' + MERGE + '5`"',
    "force-push-to-a-python-script": 'python3 x.py "`' + FORCE + 'feat/x`"',
    "force-push-to-a-shell-script": './retry.sh "`' + FORCE + 'feat/x`"',
    "branch-delete-to-a-python-script": 'python3 x.py "`git branch -' + 'D feat/x`"',
    "branch-delete-to-echo": 'echo "`git branch -' + 'D feat/x`"',
}
TWO_QUOTED_BACKTICK_COMMANDS = {
    "two-merges": 'python3 x.py "`' + MERGE + '5`" "`' + MERGE + '6`"',
    "merge-and-branch-delete": 'python3 x.py "`' + MERGE + '5`" "`git branch -' + 'D feat/x`"',
}
MASKED_READINGS = {
    "force-backtick-target": (ROWS["force-backtick"],
                              {"operation_type": "force-push", "bound_flags": [], "target_ref": "_"}),
    "force-dollar-paren-target": (ROWS["force-dollar-paren"],
                                  {"operation_type": "force-push", "bound_flags": [], "target_ref": "_"}),
    "merge-dollar-paren-target": (ROWS["merge-dollar-paren"],
                                  {"operation_type": "merge", "bound_flags": [], "pr_number": "branch:_"}),
}
QUOTED_BACKTICK_RUNS = {
    "merge-to-a-python-script": (QUOTED_BACKTICK_COMMANDS["argument-to-a-python-script"],
                                 "✓ Merged pull request #5"),
    "merge-as-second-argument": (QUOTED_BACKTICK_COMMANDS["second-argument"], "✓ Merged pull request #5"),
    "force-push-to-a-python-script": ('python3 x.py "`' + FORCE + 'main`"', ""),
    "branch-delete-to-a-python-script": (QUOTED_BACKTICK_COMMANDS["branch-delete-to-a-python-script"],
                                         "Deleted branch feat/x (was abc1234)."),
}


class TestAGuardedCommandThatIsTheSubstitutionRuns:
    """The guarded command is the backtick substitution itself, so masking the
    substitution leaves nothing to read; the classifier's reading of the command
    binds it instead."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("command", QUOTED_BACKTICK_COMMANDS.values(), ids=QUOTED_BACKTICK_COMMANDS.keys())
    def test_the_approved_command_is_refused_then_runs(self, command, style):
        assert not _runs(None, command)
        assert _runs(_mint_styled(command, style), command)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("command", TWO_QUOTED_BACKTICK_COMMANDS.values(),
                             ids=TWO_QUOTED_BACKTICK_COMMANDS.keys())
    def test_two_guarded_substitutions_mint_nothing(self, command, style):
        assert not _runs(None, command)
        assert _mint_styled(command, style) is None

    @pytest.mark.parametrize("command,reading", MASKED_READINGS.values(), ids=MASKED_READINGS.keys())
    def test_a_command_masking_reads_keeps_its_masked_reading(self, command, reading):
        assert _exact_text_reading(command, f"`{command}`") == reading


class TestAGuardedCommandThatIsTheSubstitutionRunsOnce:
    """Through the real hooks, in each approval style: refused, then minted, run
    once, and retired by the successful run; an unrelated merge stays refused."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("command,stdout", QUOTED_BACKTICK_RUNS.values(), ids=QUOTED_BACKTICK_RUNS.keys())
    def test_the_approval_runs_the_command_once(self, command, stdout, style, tmp_path):
        rig = TestAnApprovalWithNoSuccessSignalRunsOnce()
        (tmp_path / ".claude").mkdir()
        assert not rig._runs(command, tmp_path)
        q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
              "options": [{"label": "Yes, run", "description": style(command)},
                          {"label": "No", "description": "Hold."}]}]
        post = rig._hook("merge_guard_post.py", {
            "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": q}, "tool_response": {"answers": {"Run this?": "Yes, run"}},
        }, tmp_path)
        assert "token written" in post.stderr, post.stderr
        assert rig._runs(command, tmp_path)
        assert not rig._runs(MERGE + "777", tmp_path)
        rig._hook("merge_guard_post.py", {
            "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False},
        }, tmp_path)
        assert not rig._runs(command, tmp_path)


DATA_MENTIONS = {
    "issue-body-heredoc": "gh issue create --title t --body-file - <<'EOF'\nSteps: " + FORCE + "main\nEOF",
    "commit-message-heredoc": "git commit -F - <<'EOF'\ndocs: explain " + FORCE + "main\nEOF",
    "tag-message-heredoc": "git tag -a v1 -F - <<'EOF'\nafter git branch -" + "D feat/x\nEOF",
    "file-written-from-a-heredoc": "cat > notes.md <<'EOF'\n" + FORCE + "main\nEOF",
}


class TestAGuardedCommandAnAllowedCommandOnlyMentionsMintsNothing:
    """An approval binds only the guarded command the approved command runs. A
    guarded command written as data inside a command the pre-hook allows is not
    one, so approving it mints nothing and the real guarded command stays refused."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("command", DATA_MENTIONS.values(), ids=DATA_MENTIONS.keys())
    def test_approving_the_allowed_command_mints_nothing(self, command, style):
        assert _runs(None, command)
        assert _mint_styled(command, style) is None

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    def test_the_mentioned_command_stays_refused_after_the_approval(self, style, tmp_path):
        rig = TestAnApprovalWithNoSuccessSignalRunsOnce()
        (tmp_path / ".claude").mkdir()
        command = DATA_MENTIONS["issue-body-heredoc"]
        q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
              "options": [{"label": "Yes, run", "description": style(command)},
                          {"label": "No", "description": "Hold."}]}]
        rig._hook("merge_guard_post.py", {
            "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": q}, "tool_response": {"answers": {"Run this?": "Yes, run"}},
        }, tmp_path)
        assert rig._runs(command, tmp_path)
        assert not rig._runs(FORCE + "main", tmp_path)

    def test_a_guarded_command_in_its_own_span_beside_one_still_mints(self):
        command = FORCE + "main"
        ctx = _mint_styled(command, lambda c: (
            f"Record it with `{DATA_MENTIONS['commit-message-heredoc']}`, then run `{c}`"))
        assert _runs(ctx, command)

    @pytest.mark.parametrize("command", DATA_MENTIONS.values(), ids=DATA_MENTIONS.keys())
    def test_the_no_mint_notice_says_the_command_needs_no_approval(self, command):
        assert _mint(command) == (None, "not_dangerous")


ALTERNATIVE_BRANCHES = {
    "if-else": "if true; then " + MERGE + "5 --squash; else " + MERGE + "6 --squash; fi",
    "case": "case x in x) " + MERGE + "5 --squash ;; *) " + MERGE + "6 --squash ;; esac",
    "elif-ladder": ("if false; then " + MERGE + "5 --squash; elif true; then " + MERGE
                    + "5 --rebase; else " + MERGE + "5 --merge; fi"),
}


class TestACommandThatPicksOneOfSeveralGuardedCommandsBindsItsText:
    """No one branch's (op, target) stands for a command whose branches run
    different guarded commands, so the approval binds the command's text, and a
    branch runs only as part of it."""

    @pytest.mark.parametrize("command", ALTERNATIVE_BRANCHES.values(), ids=ALTERNATIVE_BRANCHES.keys())
    def test_the_reading_binds_the_text_alone(self, command):
        assert _exact_text_reading(command, f"`{command}`") == {
            "operation_type": "choice between guarded commands"}

    @pytest.mark.parametrize("command", [
        pytest.param("if true; then " + MERGE + "5 --squash; fi", id="one-guarded-branch"),
        pytest.param("if true; then echo hi; else " + MERGE + "5 --squash; fi", id="guarded-else-only"),
    ])
    def test_a_single_guarded_branch_keeps_its_target_binding(self, command):
        assert _exact_text_reading(command, f"`{command}`") is None

    def test_a_successful_run_retires_the_approval(self, tmp_path):
        rig = TestAnApprovalWithNoSuccessSignalRunsOnce()
        (tmp_path / ".claude").mkdir()
        command = ALTERNATIVE_BRANCHES["if-else"]
        from merge_guard_post import write_token
        from shared.merge_guard_common import TOKEN_PREFIX
        assert write_token({"operation_type": "choice between guarded commands",
                            "command_text": normalize_command_text(command)},
                           token_dir=tmp_path / ".claude")

        def live():
            return [p for p in (tmp_path / ".claude").glob(TOKEN_PREFIX + "*")
                    if not p.name.endswith(".consumed")]

        assert live()
        rig._succeeded(command, tmp_path)
        assert not live()


TEXT_BOUND_RUNS = {
    "dry-run-force-push": ("git push --dry-run --" + "force origin feat/x", FORCE + "feat/x"),
    "n-force-push": ("git push -n --" + "force origin feat/x", FORCE + "feat/x"),
    "bundled-fn": ("git push -fn origin feat/x", "git push -f origin feat/x"),
    "dry-run-ref-delete": ("git push --dry-run origin --" + "delete feat/x", "git push origin --" + "delete feat/x"),
    "auto-merge": (MERGE + "5 --squash --auto", MERGE + "5 --squash"),
}


class TestADryRunOrAutoApprovalRunsOnlyItself:
    """A dry run and an --auto merge are other operations than the real push or
    merge an (op, target) token would also run, so their approval binds the
    approved text: it runs as approved, and the real operation stays refused."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,real", TEXT_BOUND_RUNS.values(), ids=TEXT_BOUND_RUNS.keys())
    def test_the_approval_runs_itself_and_not_the_real_operation(self, approved, real, style):
        assert not _runs(None, approved)
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, real)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    def test_an_approval_of_the_real_push_keeps_its_target_binding(self, style):
        ctx = _mint_styled(FORCE + "feat/x", style)
        assert "command_text" not in ctx
        assert _runs(ctx, FORCE + "feat/x")


QUOTED_COMMAND_WITH_PROSE = {
    "prose-names-another-target": (
        lambda c: f"Run `{c}` now (it replaces {FORCE}dev)", FORCE + "main"),
    "prose-names-another-op": (
        lambda c: f"Run `{c}`; this is not {FORCE}main", MERGE + "5 --squash"),
}


class TestProseBesideAQuotedGuardedCommandBindsNothing:
    """An option that quotes its guarded command in a code span names it there:
    a guarded command its prose mentions is neither a second operation nor a
    binding. An option whose command is only in prose still mints, and a flag the
    prose adds to the quoted command still binds."""

    @pytest.mark.parametrize("style,command", QUOTED_COMMAND_WITH_PROSE.values(),
                             ids=QUOTED_COMMAND_WITH_PROSE.keys())
    def test_the_quoted_command_mints_and_runs(self, style, command):
        ctx = _mint_styled(command, style)
        assert _runs(ctx, command)

    def test_a_command_only_in_prose_still_mints(self):
        ctx = _mint_styled(FORCE + "main", lambda c: f"Create it with `gh issue create --title t`; afterwards {c}")
        assert _runs(ctx, FORCE + "main")

    def test_an_empty_code_span_is_not_a_quoted_command(self):
        ctx = _mint_styled(FORCE + "main", lambda c: f"Run `` `` first, then {c}")
        assert _runs(ctx, FORCE + "main")

    @pytest.mark.parametrize("prose", [
        pytest.param(lambda c: f"Run `{c}` with --admin", id="with-admin"),
        pytest.param(lambda c: f"Run `{c}` now, then delete the branch with --delete-branch later",
                     id="delete-branch-later"),
        pytest.param(lambda c: f"Run `{c}` (admin override with --admin if it is blocked)", id="admin-aside"),
    ])
    def test_a_flag_in_prose_binds_nothing(self, prose):
        command = MERGE + "5 --squash"
        ctx = _mint_styled(command, prose)
        assert ctx["bound_flags"] == []
        assert _runs(ctx, command)
        assert not _runs(ctx, command + " --admin")
        assert not _runs(ctx, command + " --delete-branch")

    def test_a_prose_only_approval_keeps_its_flags(self):
        ctx = _mint_styled(MERGE + "5", lambda c: f"Run {c} --admin now")
        assert ctx["bound_flags"] == ["--admin"]


SHELL_C_FLAGGED = {
    "admin": ("bash -c '" + MERGE + "5 --admin'", "bash -c '" + MERGE + "5 --squash --admin'"),
    "delete-branch": ("bash -c '" + MERGE + "5 --squash --delete-branch'",
                      "bash -c '" + MERGE + "5 --squash'"),
    "repo": ("bash -c '" + MERGE + "5 --squash --repo o/b'", "bash -c '" + MERGE + "5 --squash --repo o/c'"),
    "sh-short-repo": ("sh -c '" + MERGE + "5 --squash -R o/b'", "sh -c '" + MERGE + "5 --squash'"),
}


class TestAFlaggedMergeInAShellCStringRuns:
    """The pre-hook holds a token to the flags of both the outer command and the -c
    string it runs, which differ when the flags sit inside the string, so such a
    command is bound by its text: it runs as approved and no other flag set runs."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,other", SHELL_C_FLAGGED.values(), ids=SHELL_C_FLAGGED.keys())
    def test_the_approved_command_runs_and_another_flag_set_does_not(self, approved, other, style):
        assert not _runs(None, approved)
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, other)


RUN_TIME_EVALS = {
    "eval-merge": ('eval "' + MERGE + '$PR --squash"', 'eval "' + MERGE + '$Q --squash"'),
    "assigned-eval-merge": ('c="' + MERGE + '$PR --squash"; eval "$c"', 'c="' + MERGE + '$Q --squash"; eval "$c"'),
    "eval-force-push": ('eval "' + FORCE + '$BR"', 'eval "' + FORCE + '$X"'),
    "assigned-eval-force-push": ('c="' + FORCE + '$BR"; eval "$c"', FORCE + "$BR"),
    "eval-implicit-merge": ('eval "' + MERGE + '--squash"', MERGE + "--squash"),
    "eval-implicit-force-push": ('eval "git push --' + 'force"', "git push --" + "force"),
}


class TestAnEvalWithARunTimeTargetRuns:
    """An eval of a string that names its target by a run-time variable has no
    (op, target) the region reading can see; its approval binds its text, as a
    bare command with a run-time target is bound, so it runs and nothing else does."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,other", RUN_TIME_EVALS.values(), ids=RUN_TIME_EVALS.keys())
    def test_the_approved_command_runs_and_no_other(self, approved, other, style):
        assert not _runs(None, approved)
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, other)

    def test_an_eval_with_no_target_mints_by_its_text_and_runs_once(self):
        command = 'eval "git branch -' + 'D"'
        ctx = _mint_styled(command, APPROVAL_STYLES["inline"])
        assert ctx is not None and ctx.get("command_text") == command
        assert _runs(ctx, command)
        assert not _runs(ctx, 'eval "git branch -' + 'D topic"')


_CLOSE = "gh pr " + "close "
LITERAL_EVALS = {
    "eval-close-delete-branch": ('eval "' + _CLOSE + '5 --delete-branch"', 'eval "' + _CLOSE + '6 --delete-branch"'),
    "eval-force-push": ('eval "' + FORCE + 'feat/x"', 'eval "' + FORCE + 'feat/y"'),
    "single-quoted-eval-force-push": ("eval '" + FORCE + "feat/x'", FORCE + "feat/x"),
}


ASSIGNED_TARGETS = {
    "merge": ("PR=5; " + MERGE + "$PR --squash", "PR=6; " + MERGE + "$PR --squash"),
    "quoted-merge": ("PR=5; " + MERGE + '"$PR" --squash', "PR=6; " + MERGE + '"$PR" --squash'),
    "force-push": ("BR=feat/x; " + FORCE + "$BR", "BR=feat/y; " + FORCE + "$BR"),
    "branch-delete": ("B=topic; git branch -" + "D $B", "B=other; git branch -" + "D $B"),
    "environment-merge": (MERGE + '"$PR" --squash', "PR=6; " + MERGE + '"$PR" --squash'),
    "environment-merge-prefix": (MERGE + "$PR --squash", "PR=6 " + MERGE + "$PR --squash"),
    "environment-force-push": (FORCE + '"$BR"', "BR=main; " + FORCE + '"$BR"'),
    "environment-branch-delete": ('git branch -' + 'D "$B"', 'B=main; git branch -' + 'D "$B"'),
}


class TestAVariableTargetBindsItsText:
    """A target that is a shell variable (`gh pr merge "$PR"`, `PR=5; gh pr merge
    $PR`) is bound by the command's text: the approval runs that command, and the
    same command with another value assigned is refused."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,other", ASSIGNED_TARGETS.values(), ids=ASSIGNED_TARGETS.keys())
    def test_the_approved_command_runs_and_no_other(self, approved, other, style):
        assert not _runs(None, approved)
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, other)


    def test_an_assignment_that_leaves_the_target_keeps_the_target_binding(self):
        ctx = _mint_styled("X=hi; " + MERGE + '5 --squash --body "$X"', APPROVAL_STYLES["inline"])
        assert ctx is not None and ctx.get("pr_number") == "5" and "command_text" not in ctx


TARGET_CHANGING_APPROVALS = {
    "checkout-merge": ("git checkout feat/y && " + MERGE + "--squash", "git checkout feat/z && " + MERGE + "--squash"),
    "switch-force-push": ("git switch feat/y && git push --" + "force", "git switch main && git push --" + "force"),
    "cd-merge": ("cd sub && " + MERGE + "--squash", "cd ../other && " + MERGE + "--squash"),
}


class TestAnApprovedBranchChangeBindsItsText:
    """An approval of an op on the current branch that the command first changes
    (`git checkout feat/y && gh pr merge --squash`) runs that command, and the same op
    after a change to another branch or directory is refused."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,other", TARGET_CHANGING_APPROVALS.values(), ids=TARGET_CHANGING_APPROVALS.keys())
    def test_the_approved_command_runs_and_no_other(self, approved, other, style):
        assert not _runs(None, approved)
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, other)

    def test_an_explicit_target_after_a_checkout_keeps_its_target_binding(self):
        ctx = _mint_styled("git checkout feat/y && " + MERGE + "5 --squash", APPROVAL_STYLES["inline"])
        assert ctx is not None and "command_text" not in ctx and ctx.get("pr_number") == "5"

    def test_an_implicit_op_with_no_branch_change_keeps_its_target_binding(self):
        ctx = _mint_styled("git status && " + MERGE + "--squash", APPROVAL_STYLES["inline"])
        assert ctx is not None and "command_text" not in ctx and ctx.get("merge_implicit")


IMPLICIT_APPROVALS = {
    "merge-after-checkout": (MERGE + "--squash", "git checkout feat/y && " + MERGE + "--squash"),
    "merge-after-new-branch": (MERGE + "--squash", "git checkout -b feat/y && " + MERGE + "--squash"),
    "merge-after-switch": (MERGE + "--squash", "git switch feat/y; " + MERGE + "--squash"),
    "merge-in-another-repo": (MERGE + "--squash", "cd ../other && " + MERGE + "--squash"),
    "force-push-after-checkout": ("git push --" + "force", "git checkout main && git push --" + "force"),
    "force-push-after-pushd": ("git push --" + "force", "pushd ../other && git push --" + "force"),
}
IMPLICIT_SAME_TARGET = {
    "merge-after-file-restore": (MERGE + "--squash", "git checkout -- notes.txt && " + MERGE + "--squash"),
    "merge-then-checkout": (MERGE + "--squash", MERGE + "--squash && git checkout main"),
    "force-push-reordered": ("git push --" + "force origin", "git push origin --" + "force"),
}


class TestAnImplicitTargetIsTheBranchItRunsOn:
    """An approval of an op on the current branch covers that branch: the op run
    after a checkout, a switch or a cd acts on another branch or repo and needs its
    own approval, while the same op on the same branch still runs."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,other", IMPLICIT_APPROVALS.values(), ids=IMPLICIT_APPROVALS.keys())
    def test_a_branch_or_directory_change_first_is_refused(self, approved, other, style):
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("approved,same", IMPLICIT_SAME_TARGET.values(), ids=IMPLICIT_SAME_TARGET.keys())
    def test_the_same_branch_still_runs(self, approved, same):
        ctx = _mint_styled(approved, APPROVAL_STYLES["inline"])
        assert _runs(ctx, same)


_CLOSE_PR = "gh pr " + "close "
IMPLICIT_CLOSES = {
    "delete-branch": (_CLOSE_PR + "--delete-branch", _CLOSE_PR + "6 --delete-branch"),
    "short-flag": (_CLOSE_PR + "-d", "git checkout feat/y && " + _CLOSE_PR + "-d"),
    "with-comment": (_CLOSE_PR + "--comment bye -d", _CLOSE_PR + "--comment bye -d --repo evil/x"),
    "other-repo": (_CLOSE_PR + "--delete-branch --repo o/r", _CLOSE_PR + "--delete-branch --repo o/other"),
}


class TestAnImplicitCloseRuns:
    """A close of the current branch's PR names no number, so no (op, target) binds
    it; its text does: the approved close runs, and no other close does."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,other", IMPLICIT_CLOSES.values(), ids=IMPLICIT_CLOSES.keys())
    def test_the_approved_command_runs_and_no_other(self, approved, other, style):
        assert not _runs(None, approved)
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, other)

    def test_a_numbered_close_keeps_its_target_binding(self):
        ctx = _mint_styled(_CLOSE_PR + "5 --delete-branch", APPROVAL_STYLES["inline"])
        assert _runs(ctx, _CLOSE_PR + "--delete-branch 5")

    def test_a_target_less_branch_delete_stays_unmintable(self):
        assert _mint_styled("git branch -" + "D", APPROVAL_STYLES["inline"]) is None


class TestAnEvalOfALiteralCommandRuns:
    """An eval of a string the region reading locates nothing in (a close or a force
    push inside the quotes) binds its text, so the approved command runs and no
    other does."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,other", LITERAL_EVALS.values(), ids=LITERAL_EVALS.keys())
    def test_the_approved_command_runs_and_no_other(self, approved, other, style):
        assert not _runs(None, approved)
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, other)

    def test_an_eval_of_two_guarded_commands_stays_unmintable(self):
        command = 'eval "' + FORCE + 'feat/x && ' + _CLOSE + '5 --delete-branch"'
        assert _mint_styled(command, APPROVAL_STYLES["inline"]) is None
