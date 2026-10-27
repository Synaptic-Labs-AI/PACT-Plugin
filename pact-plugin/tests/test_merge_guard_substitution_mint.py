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
from merge_guard_post import _exact_text_reading, _mint_context_from_bundle, _retire_exact_text_token
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
    sends PostToolUseFailure, whose output shows no success line) keeps the token."""

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
        assert ctx is not None
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
                              {"operation_type": "force-push", "bound_flags": ["remote=origin"], "target_ref": "_"}),
    "force-dollar-paren-target": (ROWS["force-dollar-paren"],
                                  {"operation_type": "force-push", "bound_flags": ["remote=origin"],
                                   "target_ref": "_"}),
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
        assert ctx is not None
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
        assert ctx is not None
        assert ctx["bound_flags"] == []
        assert _runs(ctx, command)
        assert not _runs(ctx, command + " --admin")
        assert not _runs(ctx, command + " --delete-branch")

    def test_a_prose_only_approval_keeps_its_flags(self):
        ctx = _mint_styled(MERGE + "5", lambda c: f"Run {c} --admin now")
        assert ctx is not None
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

    def test_a_target_less_branch_delete_binds_its_exact_text(self):
        command = "git branch -" + "D"
        ctx = _mint_styled(command, APPROVAL_STYLES["inline"])
        assert ctx is not None and ctx.get("command_text") == command
        assert _runs(ctx, command)
        assert not _runs(ctx, command + " feat")


REPO_CHANGES = {
    "branch-rename-force-push": ("git push --" + "force", "git branch -M main && git push --" + "force",
                                 "git branch -M dev && git push --" + "force"),
    "branch-rename-two-args-force-push": ("git push --" + "force", "git branch -m old new && git push --" + "force",
                                          "git branch -m old other && git push --" + "force"),
    "branch-rename-force-push-remote": ("git push --" + "force origin", "git branch --move main && git push --" + "force origin",
                                        "git branch --move dev && git push --" + "force origin"),
    "branch-rename-merge": (MERGE + "--squash", "git branch -m main && " + MERGE + "--squash",
                            "git branch -m dev && " + MERGE + "--squash"),
    "branch-rename-two-args-merge": (MERGE + "--squash", "git branch -M old new && " + MERGE + "--squash",
                                     "git branch -M old other && " + MERGE + "--squash"),
    "env-chdir-merge": (MERGE + "--squash", "env -C /tmp/repoB " + MERGE + "--squash",
                        "env -C /tmp/repoC " + MERGE + "--squash"),
    "env-chdir-force-push": ("git push --" + "force", "env --chdir=/tmp/repoB git push --" + "force",
                             "env --chdir=/tmp/repoC git push --" + "force"),
    "env-chdir-cluster-force-push": ("git push --" + "force", "env -iC /tmp/repoB git push --" + "force",
                                     "env -iC /tmp/repoC git push --" + "force"),
    "env-chdir-cluster-merge": (MERGE + "--squash", "env -iC /tmp/repoB " + MERGE + "--squash",
                                "env -iC /tmp/repoC " + MERGE + "--squash"),
    "env-altpath-chdir-force-push": ("git push --" + "force", "env -P /usr/bin -C /tmp/repoB git push --" + "force",
                                     "env -P /usr/bin -C /tmp/repoC git push --" + "force"),
    "env-altpath-glued-cluster-chdir": ("git push --" + "force", "env -iP/usr/bin -C /tmp/repoB git push --" + "force",
                                        "env -iP/usr/bin -C /tmp/repoC git push --" + "force"),
    "env-checkout-merge": (MERGE + "--squash", "env git checkout feat/y && " + MERGE + "--squash",
                           "env git checkout feat/z && " + MERGE + "--squash"),
    "nice-checkout-force-push": ("git push --" + "force", "nice -n 5 git checkout feat/y && git push --" + "force",
                                 "nice -n 5 git checkout feat/z && git push --" + "force"),
    "pr-checkout-merge": (MERGE + "--squash", "gh pr checkout 6 && " + MERGE + "--squash",
                          "gh pr checkout 7 && " + MERGE + "--squash"),
    "pr-checkout-force-push": ("git push --" + "force", "gh pr checkout 6 && git push --" + "force",
                               "gh pr checkout 7 && git push --" + "force"),
    "dash-c-dir": ("git push --" + "force", "git -C /tmp/repoB push --" + "force", "git -C /tmp/repoC push --" + "force"),
    "git-dir": ("git push --" + "force", "git --git-dir=/tmp/repoB/.git push --" + "force",
                "git --git-dir=/tmp/repoC/.git push --" + "force"),
    "work-tree": ("git push --" + "force", "git --work-tree=/tmp/repoB push --" + "force",
                  "git --work-tree=/tmp/repoC push --" + "force"),
    "git-dir-env": ("git push --" + "force", "GIT_DIR=/tmp/repoB/.git git push --" + "force",
                    "GIT_DIR=/tmp/repoC/.git git push --" + "force"),
    "work-tree-env": ("git push --" + "force", "GIT_WORK_TREE=/tmp/repoB git push --" + "force",
                      "GIT_WORK_TREE=/tmp/repoC git push --" + "force"),
    "subshell-cd": ("git push --" + "force", "(cd /tmp/repoB && git push --" + "force)",
                    "(cd /tmp/repoC && git push --" + "force)"),
    "git-dir-env-command": ("git push --" + "force", "env GIT_DIR=/tmp/repoB/.git git push --" + "force",
                            "env GIT_DIR=/tmp/repoC/.git git push --" + "force"),
    "work-tree-env-options": ("git push --" + "force", "env -i -u HOME GIT_WORK_TREE=/tmp/repoB git push --" + "force",
                              "env -i -u HOME GIT_WORK_TREE=/tmp/repoC git push --" + "force"),
    "git-dir-env-command-merge": (MERGE + "--squash", "env GIT_DIR=/tmp/repoB/.git " + MERGE + "--squash",
                                  "env GIT_DIR=/tmp/repoC/.git " + MERGE + "--squash"),
    "git-dir-wrapper-env": ("git push --" + "force", "nice env GIT_DIR=/tmp/repoB/.git git push --" + "force",
                            "nice env GIT_DIR=/tmp/repoC/.git git push --" + "force"),
    "work-tree-env-split-string": ("git push --" + "force", "env -S 'GIT_WORK_TREE=/tmp/repoB' git push --" + "force",
                                   "env -S 'GIT_WORK_TREE=/tmp/repoC' git push --" + "force"),
    "dash-c-after-wrapper": ("git push --" + "force", "timeout 60 git -C /tmp/repoB push --" + "force",
                             "timeout 60 git -C /tmp/repoC push --" + "force"),
    "set-default": (MERGE + "--squash", "gh repo set-default o/b && " + MERGE + "--squash",
                    "gh repo set-default o/c && " + MERGE + "--squash"),
    "set-default-semicolon": (MERGE + "--squash", "gh repo set-default o/b; " + MERGE + "--squash",
                              "gh repo set-default o/c; " + MERGE + "--squash"),
    "remote-set-url": (MERGE + "--squash", "git remote set-url origin git@github.com:o/b.git && " + MERGE + "--squash",
                       "git remote set-url origin git@github.com:o/c.git && " + MERGE + "--squash"),
    "config-remote-url": (MERGE + "--squash", "git config remote.origin.url git@github.com:o/b.git && " + MERGE + "--squash",
                          "git config remote.origin.url git@github.com:o/c.git && " + MERGE + "--squash"),
}
SAME_REPO = {
    "config-flag": ("git push --" + "force", "git -c http.extraHeader=x push --" + "force"),
    "unrelated-env": ("git push --" + "force", "GIT_TRACE=1 git push --" + "force"),
    "unrelated-env-command": ("git push --" + "force", "env GIT_TRACE=1 git push --" + "force"),
}


class TestAnOpInAnotherRepoNeedsItsOwnApproval:
    """An op on the current branch, run after `gh pr checkout` or `gh repo
    set-default`, inside a subshell cd, or in a repository the git leg names (`-C`,
    `--git-dir`, `--work-tree`, `GIT_DIR`, `GIT_WORK_TREE`, also after `env`), acts
    on another branch or repo: a plain approval does not cover it, its own approval
    runs it, and that approval does not cover the same command pointed elsewhere."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,elsewhere", REPO_CHANGES.values(), ids=REPO_CHANGES.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, moved, elsewhere, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, moved)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,elsewhere", REPO_CHANGES.values(), ids=REPO_CHANGES.keys())
    def test_its_own_approval_runs_it_and_no_other(self, plain, moved, elsewhere, style):
        ctx = _mint_styled(moved, style)
        assert _runs(ctx, moved)
        assert not _runs(ctx, elsewhere)

    @pytest.mark.parametrize("plain,same", SAME_REPO.values(), ids=SAME_REPO.keys())
    def test_the_same_repo_still_runs(self, plain, same):
        assert _runs(_mint_styled(plain, APPROVAL_STYLES["inline"]), same)


_FORCE = "git push --" + "force"
PUSH_SCOPES = {
    "all": (_FORCE, _FORCE + " --all", _FORCE + " --mirror"),
    "mirror": (_FORCE, _FORCE + " --mirror", _FORCE + " --tags"),
    "tags": (_FORCE, _FORCE + " --tags", _FORCE + " --all"),
    "follow-tags": (_FORCE, _FORCE + " --follow-tags", _FORCE + " --tags"),
    "repo-equals": (_FORCE, _FORCE + " --repo=/tmp/repoB", _FORCE + " --repo=/tmp/repoC"),
    "repo-space": (_FORCE, _FORCE + " --repo /tmp/repoB", _FORCE + " --repo /tmp/repoC"),
}


class TestAPushScopeFlagNeedsItsOwnApproval:
    """`--all`, `--mirror`, `--tags` and `--follow-tags` widen what a force push
    rewrites, and `--repo` names where it goes: a plain force-push approval does not
    cover them, and a scoped approval covers its own scope only."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,scoped,other", PUSH_SCOPES.values(), ids=PUSH_SCOPES.keys())
    def test_a_plain_approval_does_not_cover_a_scope(self, plain, scoped, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, scoped)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,scoped,other", PUSH_SCOPES.values(), ids=PUSH_SCOPES.keys())
    def test_a_scoped_approval_runs_its_scope_only(self, plain, scoped, other, style):
        ctx = _mint_styled(scoped, style)
        assert _runs(ctx, scoped)
        assert not _runs(ctx, other)

    def test_the_short_force_flag_still_matches(self):
        assert _runs(_mint_styled("git push -f --tags", APPROVAL_STYLES["inline"]), _FORCE + " --tags")

    def test_both_repo_spellings_name_the_same_destination(self):
        assert _runs(_mint_styled(_FORCE + " --repo=/tmp/repoB", APPROVAL_STYLES["inline"]), _FORCE + " --repo /tmp/repoB")

    def test_a_gh_repo_prefix_on_a_push_binds_nothing(self):
        assert _runs(_mint_styled(_FORCE, APPROVAL_STYLES["inline"]), "GH_REPO=o/b " + _FORCE)


GH_REPO_PREFIXES = {
    "implicit-merge": (MERGE + "--squash", "GH_REPO=o/b " + MERGE + "--squash", "GH_REPO=o/c " + MERGE + "--squash"),
    "numbered-merge": (MERGE + "5 --squash", "GH_REPO=o/b " + MERGE + "5 --squash", "GH_REPO=o/c " + MERGE + "5 --squash"),
    "close": (_CLOSE_PR + "5 -d", "GH_REPO=o/b " + _CLOSE_PR + "5 -d", "GH_REPO=o/c " + _CLOSE_PR + "5 -d"),
    "through-env": (MERGE + "5", "GH_REPO=o/b env " + MERGE + "5", "GH_REPO=o/c env " + MERGE + "5"),
    "env-command": (MERGE + "--squash", "env GH_REPO=o/b " + MERGE + "--squash", "env GH_REPO=o/c " + MERGE + "--squash"),
    "env-options": (MERGE + "5", "env -i -u HOME X=1 GH_REPO=o/b " + MERGE + "5",
                    "env -i -u HOME X=1 GH_REPO=o/c " + MERGE + "5"),
    "wrapper-env": (MERGE + "--squash", "nice env GH_REPO=o/b " + MERGE + "--squash",
                    "nice env GH_REPO=o/c " + MERGE + "--squash"),
    "wrapper-with-options-env": (MERGE + "5", "nice -n 5 env GH_REPO=o/b " + MERGE + "5",
                                 "nice -n 5 env GH_REPO=o/c " + MERGE + "5"),
    "env-split-string": (MERGE + "5", "env -S 'GH_REPO=o/b' " + MERGE + "5", "env -S 'GH_REPO=o/c' " + MERGE + "5"),
    "env-split-whole-command": (MERGE + "5", "env -S 'GH_REPO=o/b " + MERGE + "5'", "env -S 'GH_REPO=o/c " + MERGE + "5'"),
}


class TestAGhRepoPrefixIsARepoRedirect:
    """gh reads its repository from a GH_REPO prefix as from `-R`: a plain approval
    does not cover it, and its own approval covers that repository only."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,redirected,other", GH_REPO_PREFIXES.values(), ids=GH_REPO_PREFIXES.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, redirected, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, redirected)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,redirected,other", GH_REPO_PREFIXES.values(), ids=GH_REPO_PREFIXES.keys())
    def test_its_own_approval_runs_that_repo_only(self, plain, redirected, other, style):
        ctx = _mint_styled(redirected, style)
        assert _runs(ctx, redirected)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("command", [
        "echo GH_REPO=o/b; " + MERGE + "5",
        "GH_REPO=o/b; " + MERGE + "5",
        MERGE + '5 --body "GH_REPO=o/b gh notes"',
        "echo env GH_REPO=o/b; " + MERGE + "5",
        "env -u GH_REPO " + MERGE + "5",
        "GH_REPO=o/b env -u GH_REPO " + MERGE + "5",
        "GH_REPO=o/b env -i " + MERGE + "5",
    ], ids=["echoed", "not-exported", "in-a-body", "env-echoed", "env-unset", "prefix-then-env-unset",
            "prefix-then-env-cleared"])
    def test_a_gh_repo_that_is_not_a_prefix_binds_nothing(self, command):
        ctx = _mint_styled(command, APPROVAL_STYLES["inline"])
        assert _runs(ctx, command)
        assert _runs(_mint_styled(MERGE + "5", APPROVAL_STYLES["inline"]), command)


EXPORTED_GH_REPOS = {
    "numbered-merge": (MERGE + "5 --squash", "export GH_REPO=o/b; " + MERGE + "5 --squash",
                       "export GH_REPO=o/c; " + MERGE + "5 --squash"),
    "implicit-merge": (MERGE + "--squash", "export GH_REPO=o/b && " + MERGE + "--squash",
                       "export GH_REPO=o/c && " + MERGE + "--squash"),
    "close": (_CLOSE_PR + "5 -d", "export GH_REPO=o/b; " + _CLOSE_PR + "5 -d", "export GH_REPO=o/c; " + _CLOSE_PR + "5 -d"),
    "bare-export": (MERGE + "5", "export GH_REPO; " + MERGE + "5", "export GH_REPO GH_HOST; " + MERGE + "5"),
    "declare-x": (MERGE + "5", "declare -x GH_REPO=o/b; " + MERGE + "5", "declare -x GH_REPO=o/c; " + MERGE + "5"),
    "typeset-x": (MERGE + "--squash", "typeset -x GH_REPO=o/b && " + MERGE + "--squash",
                  "typeset -x GH_REPO=o/c && " + MERGE + "--squash"),
    "combined-flags": (_CLOSE_PR + "5 -d", "declare -rx GH_REPO=o/b; " + _CLOSE_PR + "5 -d",
                       "declare -rx GH_REPO=o/c; " + _CLOSE_PR + "5 -d"),
    "allexport": (MERGE + "5", "set -a; GH_REPO=o/b; " + MERGE + "5", "set -a; GH_REPO=o/c; " + MERGE + "5"),
    "allexport-long": (MERGE + "--squash", "set -o allexport; GH_REPO=o/b && " + MERGE + "--squash",
                       "set -o allexport; GH_REPO=o/c && " + MERGE + "--squash"),
    "allexport-bundled": (MERGE + "5", "set -ea; GH_REPO=o/b; " + MERGE + "5", "set -ea; GH_REPO=o/c; " + MERGE + "5"),
    "allexport-among-options": (MERGE + "5", "set -e -a; GH_HOST=ghe.example.com; " + MERGE + "5",
                                "set -e -a; GH_HOST=ghe.other.com; " + MERGE + "5"),
}


class TestAnExportedGhRepoIsARepoRedirect:
    """gh reads GH_REPO from its environment, so `export GH_REPO=…` in an earlier leg
    redirects the merge or close: a plain approval does not cover it, and its own
    approval runs that command only."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,exported,other", EXPORTED_GH_REPOS.values(), ids=EXPORTED_GH_REPOS.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, exported, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, exported)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,exported,other", EXPORTED_GH_REPOS.values(), ids=EXPORTED_GH_REPOS.keys())
    def test_its_own_approval_runs_it_and_no_other(self, plain, exported, other, style):
        ctx = _mint_styled(exported, style)
        assert _runs(ctx, exported)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("command", [
        "GH_REPO=o/b; " + MERGE + "5",
        MERGE + "5; export GH_REPO=o/b",
        "GH_REPO=o/a; " + MERGE + "5; export GH_REPO=o/b",
        "export GH_REPO_X=1; " + MERGE + "5",
        "echo export GH_REPO=o/b; " + MERGE + "5",
        "declare GH_REPO=o/b; " + MERGE + "5",
        "declare -r GH_REPO=o/b; " + MERGE + "5",
        "set -a; set +a; GH_REPO=o/b; " + MERGE + "5",
        "GH_REPO=o/b; set -a; " + MERGE + "5",
        "set -euo pipefail; GH_REPO=o/b; " + MERGE + "5",
        "set +o allexport; GH_REPO=o/b; " + MERGE + "5",
    ], ids=["not-exported", "exported-after", "unexported-before-exported-after", "other-name", "echoed",
            "declared-not-exported", "declared-read-only", "allexport-turned-off", "assigned-before-allexport",
            "other-set-options", "allexport-off"])
    def test_a_plain_approval_still_covers_a_gh_repo_gh_does_not_read(self, command):
        assert _runs(_mint_styled(MERGE + "5", APPROVAL_STYLES["inline"]), command)





GH_HOSTS = {
    "prefix": (MERGE + "--squash", "GH_HOST=ghe.example.com " + MERGE + "--squash",
               "GH_HOST=ghe.other.com " + MERGE + "--squash"),
    "prefix-numbered": (MERGE + "5", "GH_HOST=ghe.example.com " + MERGE + "5", "GH_HOST=ghe.other.com " + MERGE + "5"),
    "env": (_CLOSE_PR + "5 -d", "env GH_HOST=ghe.example.com " + _CLOSE_PR + "5 -d",
            "env GH_HOST=ghe.other.com " + _CLOSE_PR + "5 -d"),
    "export": (MERGE + "5", "export GH_HOST=ghe.example.com; " + MERGE + "5",
               "export GH_HOST=ghe.other.com; " + MERGE + "5"),
    "declare-x": (MERGE + "--squash", "declare -x GH_HOST=ghe.example.com && " + MERGE + "--squash",
                  "declare -x GH_HOST=ghe.other.com && " + MERGE + "--squash"),
    "with-gh-repo": (MERGE + "5", "GH_REPO=o/b GH_HOST=ghe.example.com " + MERGE + "5",
                     "GH_REPO=o/b GH_HOST=ghe.other.com " + MERGE + "5"),
}


class TestAGhHostIsATargetChange:
    """gh reads its host from GH_HOST, as a prefix, after `env` or exported: a plain
    approval does not cover it, and its own approval runs that host only."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", GH_HOSTS.values(), ids=GH_HOSTS.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, moved, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, moved)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", GH_HOSTS.values(), ids=GH_HOSTS.keys())
    def test_its_own_approval_runs_it_and_no_other(self, plain, moved, other, style):
        ctx = _mint_styled(moved, style)
        assert _runs(ctx, moved)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("command", [
        "GH_HOST=ghe.example.com; " + MERGE + "5",
        "echo GH_HOST=ghe.example.com; " + MERGE + "5",
        MERGE + "5; export GH_HOST=ghe.example.com",
    ], ids=["not-exported", "echoed", "exported-after"])
    def test_a_plain_approval_still_covers_a_gh_host_gh_does_not_read(self, command):
        assert _runs(_mint_styled(MERGE + "5", APPROVAL_STYLES["inline"]), command)


GH_CONFIG_DIRS = {
    "prefix": (MERGE + "--squash", "GH_CONFIG_DIR=/tmp/ghb " + MERGE + "--squash",
               "GH_CONFIG_DIR=/tmp/ghc " + MERGE + "--squash"),
    "env": (MERGE + "5", "env GH_CONFIG_DIR=/tmp/ghb " + MERGE + "5", "env GH_CONFIG_DIR=/tmp/ghc " + MERGE + "5"),
    "export": (_CLOSE_PR + "5 -d", "export GH_CONFIG_DIR=/tmp/ghb; " + _CLOSE_PR + "5 -d",
               "export GH_CONFIG_DIR=/tmp/ghc; " + _CLOSE_PR + "5 -d"),
}


class TestAGhConfigDirIsATargetChange:
    """gh reads its hosts and credentials from GH_CONFIG_DIR, so a different one can
    act as another account or host: a plain approval does not cover it, and its own
    approval runs that config only."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", GH_CONFIG_DIRS.values(), ids=GH_CONFIG_DIRS.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, moved, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, moved)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", GH_CONFIG_DIRS.values(), ids=GH_CONFIG_DIRS.keys())
    def test_its_own_approval_runs_it_and_no_other(self, plain, moved, other, style):
        ctx = _mint_styled(moved, style)
        assert _runs(ctx, moved)
        assert not _runs(ctx, other)

    def test_a_gh_config_dir_gh_does_not_read_binds_nothing(self):
        assert _runs(_mint_styled(MERGE + "5", APPROVAL_STYLES["inline"]), "GH_CONFIG_DIR=/tmp/ghb; " + MERGE + "5")

SAME_SHELL_EXPORTS = {
    "subshell": (MERGE + "--squash", "(export GH_REPO=o/b; " + MERGE + "--squash)",
                 "(export GH_REPO=o/c; " + MERGE + "--squash)"),
    "brace-group": (MERGE + "5", "{ export GH_REPO=o/b; " + MERGE + "5; }", "{ export GH_REPO=o/c; " + MERGE + "5; }"),
    "brace-bare-export": (MERGE + "5", "GH_REPO=o/b; { export GH_REPO; " + MERGE + "5; }",
                          "GH_REPO=o/c; { export GH_REPO; " + MERGE + "5; }"),
    "bash-c": (MERGE + "5", "bash -c 'export GH_REPO=o/b; " + MERGE + "5'", "bash -c 'export GH_REPO=o/c; " + MERGE + "5'"),
    "sh-lc-and": (_CLOSE_PR + "5 -d", 'sh -lc "export GH_REPO=o/b && ' + _CLOSE_PR + '5 -d"',
                  'sh -lc "export GH_REPO=o/c && ' + _CLOSE_PR + '5 -d"'),
    "eval": (MERGE + "5", 'eval "export GH_REPO=o/b; ' + MERGE + '5"', 'eval "export GH_REPO=o/c; ' + MERGE + '5"'),
}


class TestAnExportInTheOpsOwnShellIsARepoRedirect:
    """An export in the same subshell, brace group or -c string as the merge or close
    reaches gh as one in the same command line does: a plain approval does not cover
    it, and its own approval runs that command only."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,exported,other", SAME_SHELL_EXPORTS.values(), ids=SAME_SHELL_EXPORTS.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, exported, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, exported)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,exported,other", SAME_SHELL_EXPORTS.values(), ids=SAME_SHELL_EXPORTS.keys())
    def test_its_own_approval_runs_it_and_no_other(self, plain, exported, other, style):
        ctx = _mint_styled(exported, style)
        assert _runs(ctx, exported)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("command", [
        "(GH_REPO=o/b; " + MERGE + "5)",
        "{ GH_REPO=o/b; " + MERGE + "5; }",
        'bash -c "echo export GH_REPO=o/b; ' + MERGE + '5"',
        "(" + MERGE + "5; export GH_REPO=o/b)",
    ], ids=["subshell-not-exported", "brace-not-exported", "bash-c-echoed", "exported-after"])
    def test_a_plain_approval_still_covers_a_gh_repo_gh_does_not_read(self, command):
        assert _runs(_mint_styled(MERGE + "5", APPROVAL_STYLES["inline"]), command)

EXPORTED_GIT_DIRS = {
    "git-dir": (_FORCE, "export GIT_DIR=/tmp/repoB/.git; " + _FORCE, "export GIT_DIR=/tmp/repoC/.git; " + _FORCE),
    "git-dir-and": (_FORCE, "export GIT_DIR=/tmp/repoB/.git && " + _FORCE, "export GIT_DIR=/tmp/repoC/.git && " + _FORCE),
    "bare-export": (_FORCE, "GIT_DIR=/tmp/repoB/.git; export GIT_DIR; " + _FORCE,
                    "GIT_DIR=/tmp/repoC/.git; export GIT_DIR; " + _FORCE),
    "work-tree-declare-x": (_FORCE, "declare -x GIT_WORK_TREE=/tmp/repoB; " + _FORCE,
                            "declare -x GIT_WORK_TREE=/tmp/repoC; " + _FORCE),
    "git-dir-typeset-x-merge": (MERGE + "--squash", "typeset -x GIT_DIR=/tmp/repoB/.git; " + MERGE + "--squash",
                                "typeset -x GIT_DIR=/tmp/repoC/.git; " + MERGE + "--squash"),
    "git-dir-allexport": (_FORCE, "set -a; GIT_DIR=/tmp/repoB/.git; " + _FORCE, "set -a; GIT_DIR=/tmp/repoC/.git; " + _FORCE),
}


class TestAnExportedGitDirIsARepoChange:
    """git reads GIT_DIR and GIT_WORK_TREE from its environment, so exporting either in
    an earlier leg moves an op on the current branch to another repository: a plain
    approval does not cover it, and its own approval runs that command only."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,exported,other", EXPORTED_GIT_DIRS.values(), ids=EXPORTED_GIT_DIRS.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, exported, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, exported)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,exported,other", EXPORTED_GIT_DIRS.values(), ids=EXPORTED_GIT_DIRS.keys())
    def test_its_own_approval_runs_it_and_no_other(self, plain, exported, other, style):
        ctx = _mint_styled(exported, style)
        assert _runs(ctx, exported)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("command", [
        "GIT_DIR=/tmp/repoB/.git; " + _FORCE,
        _FORCE + "; export GIT_DIR=/tmp/repoB/.git",
        "echo export GIT_DIR=/tmp/repoB/.git; " + _FORCE,
        "declare GIT_WORK_TREE=/tmp/repoB; " + _FORCE,
        "export GIT_TRACE=1; " + _FORCE,
    ], ids=["not-exported", "exported-after", "echoed", "declared-not-exported", "other-name"])
    def test_a_plain_approval_still_covers_a_git_dir_git_does_not_read(self, command):
        assert _runs(_mint_styled(_FORCE, APPROVAL_STYLES["inline"]), command)


_NUMBERED = MERGE + "5 --squash"
NUMBERED_REPO_CHANGES = {
    "cd": (_NUMBERED, "cd /tmp/other && " + _NUMBERED, "cd /tmp/third && " + _NUMBERED),
    "subshell-cd": (_NUMBERED, "(cd /tmp/other && " + _NUMBERED + ")", "(cd /tmp/third && " + _NUMBERED + ")"),
    "pushd": (_NUMBERED, "pushd /tmp/other && " + _NUMBERED, "pushd /tmp/third && " + _NUMBERED),
    "set-default": (_NUMBERED, "gh repo set-default o/b && " + _NUMBERED, "gh repo set-default o/c && " + _NUMBERED),
    "remote-set-url": (_NUMBERED, "git remote set-url origin git@github.com:o/b.git && " + _NUMBERED,
                       "git remote set-url origin git@github.com:o/c.git && " + _NUMBERED),
    "remote-add": (_NUMBERED, "git remote add upstream git@github.com:o/b.git && " + _NUMBERED,
                   "git remote add upstream git@github.com:o/c.git && " + _NUMBERED),
    "remote-rename": (_NUMBERED, "git remote rename origin old; " + _NUMBERED, "git remote rename origin older; " + _NUMBERED),
    "env-remote-set-url": (_NUMBERED, "env FOO=1 git remote set-url origin git@github.com:o/b.git && " + _NUMBERED,
                           "env FOO=1 git remote set-url origin git@github.com:o/c.git && " + _NUMBERED),
    "timeout-config-url": (_NUMBERED, "timeout 60 git config remote.origin.url git@github.com:o/b.git && " + _NUMBERED,
                           "timeout 60 git config remote.origin.url git@github.com:o/c.git && " + _NUMBERED),
    "env-split-string-set-url": (_NUMBERED, "env -S 'git remote set-url origin git@github.com:o/b.git' && " + _NUMBERED,
                                 "env -S 'git remote set-url origin git@github.com:o/c.git' && " + _NUMBERED),
    "config-remote-url": (_NUMBERED, "git config remote.origin.url git@github.com:o/b.git && " + _NUMBERED,
                          "git config remote.origin.url git@github.com:o/c.git && " + _NUMBERED),
    "config-unset-pushurl": (_NUMBERED, "git config --unset remote.origin.pushurl; " + _NUMBERED,
                             "git config --unset remote.upstream.pushurl; " + _NUMBERED),
    "config-set": (_NUMBERED, "git config set remote.origin.url git@github.com:o/b.git && " + _NUMBERED,
                   "git config set remote.origin.url git@github.com:o/c.git && " + _NUMBERED),
    "config-unset": (_NUMBERED, "git config unset remote.origin.pushurl && " + _NUMBERED,
                     "git config unset remote.upstream.pushurl && " + _NUMBERED),
    "config-global-unset": (_NUMBERED, "git config --global unset remote.origin.url && " + _NUMBERED,
                            "git config --global unset remote.upstream.url && " + _NUMBERED),
    "config-unset-all": (_NUMBERED, "git config unset --all remote.origin.url && " + _NUMBERED,
                         "git config unset --all remote.upstream.url && " + _NUMBERED),
    "config-rename-section": (_NUMBERED, "git config rename-section remote.origin remote.old && " + _NUMBERED,
                              "git config rename-section remote.origin remote.older && " + _NUMBERED),
    "config-remove-section": (_NUMBERED, "git config remove-section remote.origin && " + _NUMBERED,
                              "git config remove-section remote.upstream && " + _NUMBERED),
    "config-legacy-remove-section": (_NUMBERED, "git config --remove-section remote.origin && " + _NUMBERED,
                                     "git config --remove-section remote.upstream && " + _NUMBERED),
    "config-edit": (_NUMBERED, "git config --edit && " + _NUMBERED, "git config edit && " + _NUMBERED),
    "config-key-case": (_NUMBERED, "git config remote.origin.URL git@github.com:o/b.git && " + _NUMBERED,
                        "git config remote.origin.URL git@github.com:o/c.git && " + _NUMBERED),
    "config-section-case": (_NUMBERED, "git config Remote.origin.PushURL git@github.com:o/b.git && " + _NUMBERED,
                            "git config Remote.origin.PushURL git@github.com:o/c.git && " + _NUMBERED),
    "config-remove-section-case": (_NUMBERED, "git config remove-section REMOTE.origin && " + _NUMBERED,
                                   "git config remove-section REMOTE.upstream && " + _NUMBERED),
    "config-file": (_NUMBERED, "git config -f .git/config remote.origin.url git@github.com:o/b.git && " + _NUMBERED,
                    "git config -f .git/config remote.origin.url git@github.com:o/c.git && " + _NUMBERED),
    "config-value-named-get": (_NUMBERED, "git config remote.origin.url get && " + _NUMBERED,
                               "git config remote.origin.url list && " + _NUMBERED),
    "config-insteadof": (_NUMBERED, "git config url.git@github.com:o/b.git.insteadOf git@github.com:o/a.git && "
                         + _NUMBERED, "git config url.git@github.com:o/c.git.insteadOf git@github.com:o/a.git && "
                         + _NUMBERED),
    "config-set-insteadof": (_NUMBERED, "git config set url.git@github.com:o/b.git.InsteadOf o/a && " + _NUMBERED,
                             "git config set url.git@github.com:o/c.git.InsteadOf o/a && " + _NUMBERED),
    "config-remove-url-section": (_NUMBERED, "git config remove-section url.git@github.com:o/b.git && " + _NUMBERED,
                                  "git config remove-section url.git@github.com:o/c.git && " + _NUMBERED),
    "git-dir-prefix": (_NUMBERED, "GIT_DIR=/tmp/repoB/.git " + _NUMBERED, "GIT_DIR=/tmp/repoC/.git " + _NUMBERED),
    "git-dir-env": (_NUMBERED, "env GIT_DIR=/tmp/repoB/.git " + _NUMBERED, "env GIT_DIR=/tmp/repoC/.git " + _NUMBERED),
    "git-dir-export": (_NUMBERED, "export GIT_DIR=/tmp/repoB/.git; " + _NUMBERED,
                       "export GIT_DIR=/tmp/repoC/.git; " + _NUMBERED),
    "git-dir-function-export": (_NUMBERED, "f() { export GIT_DIR=/tmp/repoB/.git; }; f; " + _NUMBERED,
                                "f() { export GIT_DIR=/tmp/repoC/.git; }; f; " + _NUMBERED),
    "env-chdir": (_NUMBERED, "env -C /tmp/repoB " + _NUMBERED, "env -C /tmp/repoC " + _NUMBERED),
    "env-chdir-attached": (_NUMBERED, "env -C/tmp/repoB " + _NUMBERED, "env -C/tmp/repoC " + _NUMBERED),
    "env-chdir-cluster": (_NUMBERED, "env -iC /tmp/repoB " + _NUMBERED, "env -iC /tmp/repoC " + _NUMBERED),
    "env-chdir-long-cluster": (_NUMBERED, "env -viC /tmp/repoB " + _NUMBERED, "env -viC /tmp/repoC " + _NUMBERED),
    "env-altpath-chdir": (_NUMBERED, "env -P /usr/bin -C /tmp/repoB " + _NUMBERED, "env -P /usr/bin -C /tmp/repoC " + _NUMBERED),
    "env-argv0-chdir": (_NUMBERED, "env -a gh -C /tmp/repoB " + _NUMBERED, "env -a gh -C /tmp/repoC " + _NUMBERED),
    "env-user-class-chdir": (_NUMBERED, "env -U me -C /tmp/repoB " + _NUMBERED, "env -U me -C /tmp/repoC " + _NUMBERED),
    "close": (_CLOSE_PR + "5 -d", "cd /tmp/other && " + _CLOSE_PR + "5 -d", "cd /tmp/third && " + _CLOSE_PR + "5 -d"),
    "bash-c": (_NUMBERED, "bash -c 'cd /tmp/other && " + _NUMBERED + "'", "bash -c 'cd /tmp/third && " + _NUMBERED + "'"),
    "same-repo-subdirectory": (_NUMBERED, "cd sub && " + _NUMBERED, "cd other && " + _NUMBERED),
}


class TestANumberedOpInAnotherRepoNeedsItsOwnApproval:
    """A numbered merge or close names its pull request but not its repository: run
    after a change of directory or of gh's default repository, or with GIT_DIR set,
    the number names a PR of another repository. A plain numbered approval does not
    cover it, and its own approval runs that command only."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", NUMBERED_REPO_CHANGES.values(), ids=NUMBERED_REPO_CHANGES.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, moved, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, moved)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", NUMBERED_REPO_CHANGES.values(), ids=NUMBERED_REPO_CHANGES.keys())
    def test_its_own_approval_runs_it_and_no_other(self, plain, moved, other, style):
        ctx = _mint_styled(moved, style)
        assert _runs(ctx, moved)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("command", [
        "gh pr checkout 6 && " + _NUMBERED,
        "gh co 6 && " + _NUMBERED,
        "git checkout feat/x && " + _NUMBERED,
        "git switch feat/x && " + _NUMBERED,
        _NUMBERED + "; cd /tmp/other",
        "echo cd /tmp/other; " + _NUMBERED,
        "git remote -v && " + _NUMBERED,
        "gh repo set-default --view && " + _NUMBERED,
        "gh repo set-default -v; " + _NUMBERED,
        "git config --get remote.origin.url && " + _NUMBERED,
        "git config --get remote.origin.url github && " + _NUMBERED,
        "git config remote.origin.url && " + _NUMBERED,
        "git config -l; " + _NUMBERED,
        "git config remote.origin.fetch +refs/heads/*:refs/remotes/origin/* && " + _NUMBERED,
        "git config get remote.origin.url && " + _NUMBERED,
        "git config get --all remote.origin.pushurl && " + _NUMBERED,
        "git config list; " + _NUMBERED,
        "git config --get remote.origin.URL && " + _NUMBERED,
        "git config unset user.name && " + _NUMBERED,
        "git config remove-section user && " + _NUMBERED,
        "git -C . config --get remote.origin.url && " + _NUMBERED,
        "git config get url.git@github.com:o/b.git.insteadOf && " + _NUMBERED,
        "env git config --get remote.origin.url && " + _NUMBERED,
        "nice git config list; " + _NUMBERED,
        "env git checkout feat/y && " + _NUMBERED,
        "env echo cd /tmp/other; " + _NUMBERED,
        "env -C /tmp/other true; " + _NUMBERED,
        "env FOO=1 " + _NUMBERED,
        "env -vi " + _NUMBERED,
        "env -iu FOO " + _NUMBERED,
        "env -P /usr/bin " + _NUMBERED,
        "env -iP /usr/bin " + _NUMBERED,
        "env --argv0 gh " + _NUMBERED,
    ], ids=["pr-checkout", "co", "branch-checkout", "switch", "cd-after", "echoed", "remote-list", "set-default-view",
            "set-default-v", "config-get", "config-get-value-pattern", "config-read-key", "config-list", "config-other-key",
            "config-get-subcommand", "config-get-all-subcommand", "config-list-subcommand", "config-get-key-case",
            "config-unset-other-key", "config-remove-other-section", "config-get-dash-c", "config-get-insteadof",
            "env-config-get", "nice-config-list", "env-branch-checkout", "env-echoed",
            "env-chdir-earlier-leg", "env-no-chdir", "env-flag-cluster", "env-unset-cluster",
            "env-altpath", "env-altpath-cluster", "env-argv0"])
    def test_a_plain_numbered_approval_still_covers_a_branch_change(self, command):
        assert _runs(_mint_styled(_NUMBERED, APPROVAL_STYLES["inline"]), command)



_PUSH_X = "git push --" + "force origin feat/x"
_DELETE_X = "git branch -" + "D feat/x"
EXPLICIT_GIT_REPO_CHANGES = {
    "force-push-cd": (_PUSH_X, "cd /tmp/repoB && " + _PUSH_X, "cd /tmp/repoC && " + _PUSH_X),
    "force-push-dash-c": (_PUSH_X, "git -C /tmp/repoB push --" + "force origin feat/x",
                          "git -C /tmp/repoC push --" + "force origin feat/x"),
    "force-push-git-dir": (_PUSH_X, "GIT_DIR=/tmp/repoB/.git " + _PUSH_X, "GIT_DIR=/tmp/repoC/.git " + _PUSH_X),
    "force-push-export": (_PUSH_X, "export GIT_DIR=/tmp/repoB/.git; " + _PUSH_X,
                          "export GIT_DIR=/tmp/repoC/.git; " + _PUSH_X),
    "force-push-function-export": (_PUSH_X, "f() { export GIT_DIR=/tmp/repoB/.git; }; f; " + _PUSH_X,
                                   "f() { export GIT_DIR=/tmp/repoC/.git; }; f; " + _PUSH_X),
    "force-push-env-chdir": (_PUSH_X, "env -C /tmp/repoB " + _PUSH_X, "env -C /tmp/repoC " + _PUSH_X),
    "force-push-env-chdir-long": (_PUSH_X, "env --chdir=/tmp/repoB " + _PUSH_X, "env --chdir=/tmp/repoC " + _PUSH_X),
    "branch-delete-env-chdir": (_DELETE_X, "env -C /tmp/repoB " + _DELETE_X, "env -C /tmp/repoC " + _DELETE_X),
    "force-push-env-chdir-ignore-env": (_PUSH_X, "env -C /tmp/repoB -i " + _PUSH_X, "env -C /tmp/repoC -i " + _PUSH_X),
    "force-push-env-chdir-cluster": (_PUSH_X, "env -iC /tmp/repoB " + _PUSH_X, "env -iC /tmp/repoC " + _PUSH_X),
    "force-push-env-chdir-cluster-glued": (_PUSH_X, "env -iC/tmp/repoB " + _PUSH_X, "env -iC/tmp/repoC " + _PUSH_X),
    "force-push-env-altpath-chdir": (_PUSH_X, "env -P /usr/bin -C /tmp/repoB " + _PUSH_X,
                                     "env -P /usr/bin -C /tmp/repoC " + _PUSH_X),
    "force-push-env-altpath-cluster-chdir": (_PUSH_X, "env -iP /usr/bin -C /tmp/repoB " + _PUSH_X,
                                             "env -iP /usr/bin -C /tmp/repoC " + _PUSH_X),
    "force-push-env-argv0-chdir": (_PUSH_X, "env --argv0 name -C /tmp/repoB " + _PUSH_X,
                                   "env --argv0 name -C /tmp/repoC " + _PUSH_X),
    "branch-delete-env-login-class-chdir": (_DELETE_X, "env -L me -C /tmp/repoB " + _DELETE_X,
                                            "env -L me -C /tmp/repoC " + _DELETE_X),
    "force-push-env": (_PUSH_X, "env GIT_WORK_TREE=/tmp/repoB " + _PUSH_X, "env GIT_WORK_TREE=/tmp/repoC " + _PUSH_X),
    "branch-delete-cd": (_DELETE_X, "cd /tmp/repoB && " + _DELETE_X, "cd /tmp/repoC && " + _DELETE_X),
    "branch-delete-dash-c": (_DELETE_X, "git -C /tmp/repoB branch -" + "D feat/x", "git -C /tmp/repoC branch -" + "D feat/x"),
    "ref-delete-cd": ("git push origin --delete stale", "cd /tmp/repoB && git push origin --delete stale",
                      "cd /tmp/repoC && git push origin --delete stale"),
    "mass-delete-cd": ("git push --mirror origin", "cd /tmp/repoB && git push --mirror origin",
                       "cd /tmp/repoC && git push --mirror origin"),
    "lease-push-cd": ("git push --force-with-lease origin main", "cd /tmp/repoB && git push --force-with-lease origin main",
                      "cd /tmp/repoC && git push --force-with-lease origin main"),
    "force-push-remote-set-url": (_PUSH_X, "git remote set-url origin git@github.com:o/b.git && " + _PUSH_X,
                                  "git remote set-url origin git@github.com:o/c.git && " + _PUSH_X),
    "force-push-config-pushurl": (_PUSH_X, "git config remote.origin.pushurl git@github.com:o/b.git; " + _PUSH_X,
                                  "git config remote.origin.pushurl git@github.com:o/c.git; " + _PUSH_X),
    "force-push-config-unset-pushurl": (_PUSH_X, "git config unset remote.origin.pushurl && " + _PUSH_X,
                                        "git config unset remote.upstream.pushurl && " + _PUSH_X),
    "force-push-config-key-case": (_PUSH_X, "git config remote.origin.PushURL git@github.com:o/b.git; " + _PUSH_X,
                                   "git config remote.origin.PushURL git@github.com:o/c.git; " + _PUSH_X),
    "force-push-config-pushinsteadof": (_PUSH_X, "git config url.git@github.com:o/b.git.pushInsteadOf git@github.com:o/a.git; "
                                        + _PUSH_X, "git config url.git@github.com:o/c.git.pushInsteadOf git@github.com:o/a.git; "
                                        + _PUSH_X),
}


class TestAGitOpOnANamedTargetInAnotherRepoNeedsItsOwnApproval:
    """A git op that names its branch or ref but not its repository, run after a change
    of directory or repository, acts on a same-named target elsewhere: a plain
    approval does not cover it, and its own approval runs that command only. A branch
    change before it does not move it."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", EXPLICIT_GIT_REPO_CHANGES.values(),
                             ids=EXPLICIT_GIT_REPO_CHANGES.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, moved, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, moved)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", EXPLICIT_GIT_REPO_CHANGES.values(),
                             ids=EXPLICIT_GIT_REPO_CHANGES.keys())
    def test_its_own_approval_runs_it_and_no_other(self, plain, moved, other, style):
        ctx = _mint_styled(moved, style)
        assert _runs(ctx, moved)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("plain,command", [
        (_PUSH_X, "git checkout feat/x && " + _PUSH_X),
        (_DELETE_X, "git checkout main && " + _DELETE_X),
        (_DELETE_X, "git switch main && " + _DELETE_X),
        (_PUSH_X, _PUSH_X + "; cd /tmp/repoB"),
    ], ids=["checkout-push", "checkout-delete", "switch-delete", "cd-after"])
    def test_a_plain_approval_still_covers_a_branch_change(self, plain, command):
        assert _runs(_mint_styled(plain, APPROVAL_STYLES["inline"]), command)

_FORCE_F = "git push -" + "f"
_FORCE_ORIGIN = _FORCE + " origin"
PUSH_ROUTING_CHANGES = {
    "branch-u": ("git branch -u upstream/x", "git branch -u fork/x"),
    "set-upstream-to": ("git branch --set-upstream-to=upstream/x", "git branch --set-upstream-to=fork/x"),
    "set-upstream-to-space": ("git branch --set-upstream-to upstream/x", "git branch --set-upstream-to fork/x"),
    "unset-upstream": ("git branch --unset-upstream", "git branch --unset-upstream feat/y"),
    "config-branch-remote": ("git config branch.feat/x.remote upstream", "git config branch.feat/x.remote fork"),
    "config-set-branch-remote": ("git config set branch.feat/x.remote upstream", "git config set branch.feat/x.remote fork"),
    "config-unset-branch-remote": ("git config --unset branch.feat/x.remote", "git config --unset branch.feat/y.remote"),
    "config-push-remote": ("git config branch.feat/x.pushRemote upstream", "git config branch.feat/x.pushRemote fork"),
    "config-push-remote-case": ("git config Branch.feat/x.PUSHREMOTE upstream", "git config Branch.feat/x.PUSHREMOTE fork"),
    "config-push-default-remote": ("git config remote.pushDefault upstream", "git config remote.pushDefault fork"),
    "config-global-push-default-remote": ("git config --global remote.pushdefault upstream",
                                          "git config --global remote.pushdefault fork"),
    "config-remove-branch-section": ("git config remove-section branch.feat/x", "git config remove-section branch.feat/y"),
    "env-branch-u": ("env FOO=1 git branch -u upstream/x", "env FOO=1 git branch -u fork/x"),
    "sudo-config-push-remote": ("sudo -u me git config branch.feat/x.pushRemote upstream",
                                "sudo -u me git config branch.feat/x.pushRemote fork"),
    "push-set-upstream-short": ("git push -u upstream feat/x", "git push -u fork feat/x"),
    "push-set-upstream-long": ("git push --set-upstream upstream feat/x", "git push --set-upstream fork feat/x"),
    "push-set-upstream-bundled": ("git push -qu upstream feat/x", "git push -qu fork feat/x"),
    "config-remote-push": ("git config remote.origin.push refs/heads/feat/x:refs/heads/main",
                           "git config remote.origin.push refs/heads/feat/x:refs/heads/dev"),
    "config-add-remote-push": ("git config --add remote.origin.push refs/heads/feat/x:refs/heads/main",
                               "git config --add remote.origin.push refs/heads/feat/x:refs/heads/dev"),
    "config-replace-all-remote-push": ("git config --replace-all remote.origin.push refs/heads/x:refs/heads/main",
                                       "git config --replace-all remote.origin.push refs/heads/x:refs/heads/dev"),
    "config-unset-remote-push": ("git config unset remote.origin.push", "git config unset remote.upstream.push"),
    "config-remote-push-case": ("git config Remote.origin.PUSH refs/heads/x:refs/heads/main",
                                "git config Remote.origin.PUSH refs/heads/x:refs/heads/dev"),
    "config-push-default": ("git config push.default matching", "git config push.default current"),
    "config-set-push-default-case": ("git config set Push.Default matching", "git config set Push.Default current"),
    "config-branch-merge": ("git config branch.feat/x.merge refs/heads/main", "git config branch.feat/x.merge refs/heads/dev"),
    "config-unset-branch-merge": ("git config unset branch.feat/x.merge", "git config unset branch.feat/y.merge"),
}
IMPLICIT_PUSHES = {"bare": _FORCE, "short": _FORCE_F, "remote": _FORCE_ORIGIN}


def _routing_rows(changes):
    return {
        "%s-%s" % (name, push): (cmd, "%s && %s" % (leg, cmd), "%s && %s" % (other, cmd))
        for name, (leg, other) in changes.items()
        for push, cmd in IMPLICIT_PUSHES.items()
    }


PUSH_ROUTING_ROWS = _routing_rows(PUSH_ROUTING_CHANGES)


class TestAnUpstreamOrPushRoutingChangeMovesAPushNamingNoRef:
    """A force push that names no ref goes where the branch's upstream and push
    settings send it: a leg before it that changes them moves it. A plain approval does
    not cover it, and its own approval runs that command only. A read of the settings,
    a push naming its ref, and gh's own merge are not moved."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", PUSH_ROUTING_ROWS.values(), ids=PUSH_ROUTING_ROWS.keys())
    def test_a_plain_approval_does_not_cover_it(self, plain, moved, other, style):
        ctx = _mint_styled(plain, style)
        assert _runs(ctx, plain)
        assert not _runs(ctx, moved)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("plain,moved,other", PUSH_ROUTING_ROWS.values(), ids=PUSH_ROUTING_ROWS.keys())
    def test_its_own_approval_runs_it_and_no_other(self, plain, moved, other, style):
        ctx = _mint_styled(moved, style)
        assert _runs(ctx, moved)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("plain,command", [
        (_FORCE, "git config --get branch.feat/x.remote && " + _FORCE),
        (_FORCE, "git config get remote.pushDefault && " + _FORCE),
        (_FORCE, "git config branch.feat/x.pushRemote && " + _FORCE),
        (_FORCE, "git config branch.feat/x.description wip && " + _FORCE),
        (_FORCE, "git branch -vv && " + _FORCE),
        (_FORCE, "git branch --track feat/y upstream/y && " + _FORCE),
        (_PUSH_X, "git branch -u upstream/x && " + _PUSH_X),
        (_PUSH_X, "git config branch.feat/x.remote upstream && " + _PUSH_X),
        (MERGE + "--squash", "git branch -u upstream/x && " + MERGE + "--squash"),
        (MERGE + "--squash", "git config remote.pushDefault upstream && " + MERGE + "--squash"),
        (_NUMBERED, "git config branch.feat/x.remote upstream && " + _NUMBERED),
        (_FORCE, "git config --get push.default && " + _FORCE),
        (_FORCE, "git config push.followTags true && " + _FORCE),
        (_PUSH_X, "git config push.default matching && " + _PUSH_X),
        (MERGE + "--squash", "git config branch.feat/x.merge refs/heads/main && " + MERGE + "--squash"),
        (_FORCE, "timeout 5 git branch -vv && " + _FORCE),
        (MERGE + "--squash", "env FOO=1 git checkout -- file.txt && " + MERGE + "--squash"),
        (_FORCE, "git push origin feat/x && " + _FORCE),
        (_PUSH_X, "git push -u upstream feat/x && " + _PUSH_X),
        (MERGE + "--squash", "git push -u upstream feat/x && " + MERGE + "--squash"),
        (_FORCE, "git config --get remote.origin.push && " + _FORCE),
        (_PUSH_X, "git config remote.origin.push refs/heads/feat/x:refs/heads/main && " + _PUSH_X),
        (_FORCE, "git branch --show-current && " + _FORCE),
        (_FORCE, "git rev-parse @{u} && " + _FORCE),
        (_FORCE, "git branch -c copy && " + _FORCE),
        (_PUSH_X, "git branch -M main && " + _PUSH_X),
        (_NUMBERED, "git branch -M main && " + _NUMBERED),
        (_FORCE, "env -iP/opt/Cellar/bin " + _FORCE),
    ], ids=["config-get", "config-get-subcommand", "config-read-key", "config-other-key", "branch-list", "branch-track",
            "named-ref-push-branch-u", "named-ref-push-config", "gh-merge-branch-u", "gh-merge-config", "numbered-merge",
            "config-get-push-default", "config-other-push-key", "named-ref-push-default", "gh-merge-branch-merge",
            "timeout-branch-list", "env-checkout-paths",
            "push-without-upstream", "named-ref-push-set-upstream", "gh-merge-push-set-upstream",
            "config-get-remote-push", "named-ref-push-remote-push",
            "branch-show-current", "rev-parse-upstream", "branch-copy", "named-ref-push-branch-rename",
            "numbered-merge-branch-rename", "env-altpath-cluster-holding-c"])
    def test_a_plain_approval_still_covers_it(self, plain, command):
        assert _runs(_mint_styled(plain, APPROVAL_STYLES["inline"]), command)


class TestANumberedOpInAnotherRepoRetiresOnThatReposState:
    """The approval of a numbered op run after a change of repository retires on the
    state of the PR in the repository it ran in: after one literal absolute `cd`
    that directory's, as a plain numbered approval does in the session's directory.
    After any other change the session directory's state is never read; the run's
    own success retires it."""

    _SUCCESS = {"stdout": "", "stderr": "", "interrupted": False}

    def _retire(self, tmp_path, monkeypatch, command, state):
        import shared.gh_helpers as gh

        read = []
        monkeypatch.setattr(gh, "check_pr_state", lambda pr, cwd=None: read.append(cwd) or state)
        monkeypatch.setattr("merge_guard_post.get_session_id", lambda: "")
        tokens = tmp_path / "tokens"
        tokens.mkdir()
        (tokens / "merge-authorized-1").write_text(json.dumps({"context": {
            "operation_type": "merge", "pr_number": "5", "bound_flags": [],
            "command_text": normalize_command_text(command)}}))
        retired = _retire_exact_text_token(command, str(tmp_path / "session"), token_dir=tokens,
                                           tool_response=self._SUCCESS)
        return retired, read

    @pytest.mark.parametrize("state,retired", [("MERGED", True), ("OPEN", False)])
    @pytest.mark.parametrize("shape", ["cd {d} && {op}", "(cd {d} && {op})"], ids=["cd", "subshell-cd"])
    def test_a_literal_cd_reads_that_directory(self, tmp_path, monkeypatch, shape, state, retired):
        command = shape.format(d=tmp_path, op=MERGE + "5 --squash")
        assert self._retire(tmp_path, monkeypatch, command, state) == (retired, [str(tmp_path)])

    @pytest.mark.parametrize("command", [
        "cd sub && " + MERGE + "5 --squash",
        "gh repo set-default o/b && " + MERGE + "5 --squash",
        "export GIT_DIR=/tmp/repoB/.git; " + MERGE + "5 --squash",
        "export GH_REPO=o/b; " + MERGE + "5 --squash",
    ], ids=["relative-cd", "set-default", "git-dir-export", "gh-repo-export"])
    def test_any_other_change_never_reads_the_session_directory(self, tmp_path, monkeypatch, command):
        assert self._retire(tmp_path, monkeypatch, command, "OPEN") == (True, [])


class TestAGroupedRunRetiresOnItsOwnSuccess:
    """A text-bound approval of a command that is one subshell or brace group, whose
    PR state cannot be read where it ran, retires when the group ran successfully
    with the op as its last command, as the same command without the group does. A
    run that did not succeed, or a group whose last command is not the op, keeps it."""

    _SUCCESS = {"stdout": "", "stderr": "", "interrupted": False}

    def _retire(self, tmp_path, monkeypatch, command, tool_response):
        import shared.gh_helpers as gh

        monkeypatch.setattr(gh, "check_pr_state", lambda pr, cwd=None: "OPEN")
        monkeypatch.setattr("merge_guard_post.get_session_id", lambda: "")
        (tmp_path / "merge-authorized-1").write_text(json.dumps({"context": {
            "operation_type": "merge", "pr_number": "5", "bound_flags": [],
            "command_text": normalize_command_text(command)}}))
        return _retire_exact_text_token(command, None, token_dir=tmp_path, tool_response=tool_response)

    @pytest.mark.parametrize("command", [
        "{ export GH_REPO=o/b; " + MERGE + "5; }",
        "(export GH_REPO=o/b; " + MERGE + "5)",
        "(cd sub && " + MERGE + "5)",
        "{ export GH_REPO=o/b\n" + MERGE + "5\n}",
        "(export GH_REPO=o/b; echo ')'; " + MERGE + "5)",
    ], ids=["brace-group", "subshell", "subshell-cd", "newline-brace-group", "quoted-paren"])
    def test_a_successful_group_run_retires_it(self, tmp_path, monkeypatch, command):
        assert self._retire(tmp_path, monkeypatch, command, self._SUCCESS)

    @pytest.mark.parametrize("command,tool_response", [
        ("{ export GH_REPO=o/b; " + MERGE + "5; }", {"stdout": "", "stderr": "", "interrupted": True}),
        ("{ export GH_REPO=o/b; " + MERGE + "5; }", None),
        ("(export GH_REPO=o/b; " + MERGE + "5 || true)", _SUCCESS),
        ("(export GH_REPO=o/b; " + MERGE + "5; echo done)", _SUCCESS),
        ("(export GH_REPO=o/b; " + MERGE + "5) && echo done", _SUCCESS),
    ], ids=["interrupted", "no-response", "or-true", "op-not-last", "group-not-whole"])
    def test_a_run_that_does_not_show_the_ops_success_keeps_it(self, tmp_path, monkeypatch, command, tool_response):
        assert not self._retire(tmp_path, monkeypatch, command, tool_response)


class TestAGroupBeforeTheOpRetiresOnItsOwnSuccess:
    """A command whose op runs after a subshell, brace group or substitution
    (`(set -a); GH_REPO=o/b gh pr merge 5`, `x=$(…); gh pr merge 5`) retires its
    approval when it runs successfully, as the same command without the group does, whichever token its
    approval minted. A run whose success does not show the op's keeps it."""

    _SUCCESS = {"stdout": "", "stderr": "", "interrupted": False}

    def _run_after_approval(self, tmp_path, monkeypatch, command, tool_response):
        import io

        import merge_guard_post
        import shared.gh_helpers as gh
        from merge_guard_post import write_token

        monkeypatch.setattr(gh, "check_pr_state", lambda pr, cwd=None: "")
        monkeypatch.setattr("merge_guard_post.get_session_id", lambda: "")
        monkeypatch.setattr("merge_guard_post.TOKEN_DIR", tmp_path)
        ctx = _mint_styled(command, APPROVAL_STYLES["inline"])
        assert ctx is not None
        write_token(ctx, token_dir=tmp_path)
        envelope = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                    "tool_input": {"command": command}, "tool_response": tool_response}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(envelope)))
        with pytest.raises(SystemExit):
            merge_guard_post.main()
        return [t.name for t in tmp_path.glob("merge-authorized-*") if not t.name.endswith(".consumed")]

    @pytest.mark.parametrize("command", [
        "(set -a); GH_REPO=o/b " + MERGE + "5",
        "{ set -a; }; GH_REPO=o/b " + MERGE + "5",
        "(set -a) && GH_REPO=o/b " + MERGE + "5",
        "(cd .); GH_REPO=o/b " + MERGE + "5",
        "(true); " + MERGE + "5 --repo o/b",
        "(set -a); GH_REPO=o/b " + _CLOSE_PR + "5 -d",
        "x=$(true); GH_REPO=o/b " + MERGE + "5",
        "x=`true`; GH_REPO=o/b " + MERGE + "5",
        "GH_REPO=$(echo o/b) " + MERGE + "5",
        "x=$(true); " + MERGE + "5 --repo o/b",
        "(x=`true`); GH_REPO=o/b " + MERGE + "5",
        "GH_REPO=o/b " + MERGE + "5",
        "set -a; GH_REPO=o/b; " + MERGE + "5",
        "export GH_REPO=o/b; " + MERGE + "5",
        MERGE + "5 --repo o/b",
        MERGE + "5",
    ], ids=["subshell-set-a", "brace-set-a", "subshell-and", "subshell-cd", "subshell-repo-flag", "close",
            "substitution", "backtick", "substituted-value", "substitution-repo-flag", "backtick-in-group",
            "prefix", "set-a", "export", "repo-flag", "plain"])
    def test_a_successful_run_retires_it(self, tmp_path, monkeypatch, command):
        assert self._run_after_approval(tmp_path, monkeypatch, command, self._SUCCESS) == []

    @pytest.mark.parametrize("command,tool_response", [
        ("(set -a); GH_REPO=o/b " + MERGE + "5", {"stdout": "", "stderr": "", "interrupted": True}),
        ("(set -a); GH_REPO=o/b " + MERGE + "5 || true", _SUCCESS),
        ("(set -a); GH_REPO=o/b " + MERGE + "5; echo done", _SUCCESS),
        ("(set -a); (GH_REPO=o/b " + MERGE + "5; echo done)", _SUCCESS),
        ("x=$(true); GH_REPO=o/b " + MERGE + "5; echo done", _SUCCESS),
        ("x=$(true); GH_REPO=o/b " + MERGE + "5 || true", _SUCCESS),
    ], ids=["interrupted", "or-true", "op-not-last", "op-not-last-in-final-group", "substitution-op-not-last",
            "substitution-or-true"])
    def test_a_run_that_does_not_show_the_ops_success_keeps_it(self, tmp_path, monkeypatch, command, tool_response):
        assert len(self._run_after_approval(tmp_path, monkeypatch, command, tool_response)) == 1

    @pytest.mark.parametrize("command", [
        "(set -a; GH_REPO=o/b " + MERGE + "5",
        "x=$(true; GH_REPO=o/b " + MERGE + "5",
        "x=`true; GH_REPO=o/b " + MERGE + "5",
    ], ids=["open-group", "open-substitution", "open-backtick"])
    def test_an_unclosed_group_shows_no_success(self, command):
        from merge_guard_post import _run_succeeded_as_the_op

        assert not _run_succeeded_as_the_op(command, "merge", self._SUCCESS)


def _quoted_value_rows():
    """One row per bound value-taking flag on each op that has one, in both quotings, plus
    the gh target variables: (approved, the same command naming another value, the
    approved value unquoted)."""
    from shared.merge_guard_common import PRIVILEGED_FLAGS

    base = {"merge": MERGE + "5", "close": _CLOSE_PR + "5 -d", "force-push": "git push --" + "force",
            "push-to-main": "git push origin main", "remote-ref-delete": "git push origin --delete stale",
            "remote-mass-delete": "git push --mirror origin"}
    rows = {}
    for op, flags in PRIVILEGED_FLAGS.items():
        for canonical, (_aliases, takes_value) in flags.items():
            if takes_value:
                assert op in base, "a value-taking flag on %s has no row" % op
                for name, q in (("dq", '"'), ("sq", "'")):
                    rows["%s%s-%s" % (op, canonical, name)] = (
                        "%s %s=%svb%s" % (base[op], canonical, q, q),
                        "%s %s=%svc%s" % (base[op], canonical, q, q),
                        "%s %s=vb" % (base[op], canonical),
                    )
    for variable in ("GH_REPO", "GH_HOST", "GH_CONFIG_DIR"):
        rows[variable] = ('%s="vb" %s5' % (variable, MERGE), '%s="vc" %s5' % (variable, MERGE),
                          "%s=vb %s5" % (variable, MERGE))
    return rows


QUOTED_VALUES = _quoted_value_rows()

# Each push class that takes the remote's program, in both spellings: (approved, the
# same push naming another program, the push with no program).
PUSH_PROGRAMS = {
    "%s-%s-%s" % (op, flag, form): (cmd % (flag + sep + "prog-b"), cmd % (flag + sep + "prog-c"), cmd % "")
    for op, cmd in (
        ("force-push", "git push --" + "force %s origin feat/x"),
        ("push-to-main", "git push %s origin main"),
        ("remote-ref-delete", "git push %s origin --delete stale"),
        ("remote-mass-delete", "git push --mirror %s origin"),
    )
    for flag in ("--receive-pack", "--exec")
    for form, sep in (("eq", "="), ("space", " "))
}


class TestTheRemotesProgramIsPartOfThePush:
    """`--receive-pack`/`--exec` name the program the remote runs to take the push: an
    approval covers the program it names and no other, and a push approved without one
    does not run with one."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,other,plain", PUSH_PROGRAMS.values(), ids=PUSH_PROGRAMS.keys())
    def test_its_own_approval_runs_it_and_no_other_program(self, approved, other, plain, style):
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,other,plain", PUSH_PROGRAMS.values(), ids=PUSH_PROGRAMS.keys())
    def test_a_plain_approval_does_not_run_it(self, approved, other, plain, style):
        ctx = _mint_styled(" ".join(plain.split()), style)
        assert _runs(ctx, " ".join(plain.split()))
        assert not _runs(ctx, approved)


class TestAQuotedFlagValueBindsByText:
    """A quoted `=` value of a bound flag reads as a placeholder every value shares, so
    the approval binds the command's text: it runs the approved value and refuses
    another, and the same value written unquoted still mints and runs on its own."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("quoted,other,unquoted", QUOTED_VALUES.values(), ids=QUOTED_VALUES.keys())
    def test_its_own_approval_runs_it_and_no_other_value(self, quoted, other, unquoted, style):
        ctx = _mint_styled(quoted, style)
        assert _runs(ctx, quoted)
        assert not _runs(ctx, other)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("quoted,other,unquoted", QUOTED_VALUES.values(), ids=QUOTED_VALUES.keys())
    def test_the_unquoted_value_still_mints_on_its_own(self, quoted, other, unquoted, style):
        assert _runs(_mint_styled(unquoted, style), unquoted)

    @pytest.mark.parametrize("quoted,other,unquoted", QUOTED_VALUES.values(), ids=QUOTED_VALUES.keys())
    def test_a_region_token_does_not_run_a_quoted_value(self, quoted, other, unquoted):
        from shared.merge_guard_common import extract_command_context, _single_destructive_leg

        region = extract_command_context(_single_destructive_leg(quoted) or quoted)
        assert not _runs(region, quoted)


_PUSH_FX = "git push --" + "force origin feat/x"
PUSH_DESTINATIONS = {
    "force-push-remote": (_PUSH_FX, "git push --" + "force upstream feat/x"),
    "force-push-ssh-url": (_PUSH_FX, "git push --" + "force git@github.com:o/b.git feat/x"),
    "force-push-https-url": (_PUSH_FX, "git push --" + "force https://github.com/o/b.git feat/x"),
    "force-push-path": (_PUSH_FX, "git push --" + "force ../repoB feat/x"),
    "force-push-set": ("git push --" + "force origin main feature", "git push --" + "force upstream main feature"),
    "push-to-main": ("git push origin main", "git push upstream main"),
    "lease-push-to-main": ("git push --force-with-lease origin main", "git push --force-with-lease upstream main"),
    "ref-delete": ("git push origin --delete stale", "git push upstream --delete stale"),
    "ref-delete-colon": ("git push origin :stale", "git push upstream :stale"),
}
SAME_DESTINATION_SPELLINGS = {
    "short-force": (_PUSH_FX, "git push -f origin feat/x"),
    "force-after-remote": (_PUSH_FX, "git push origin --" + "force feat/x"),
    "force-last": (_PUSH_FX, "git push origin feat/x --" + "force"),
    "push-option-between": (_PUSH_FX, "git push --" + "force -o ci.skip origin feat/x"),
    "quoted-remote": (_PUSH_FX, 'git push --' + 'force "origin" feat/x'),
}


class TestAPushNamingItsRefsBindsItsDestination:
    """A push that names its refs names its destination first: an approval for one
    remote, URL or path does not cover the same refs pushed elsewhere, the other
    destination's own approval runs it, and respellings of the same destination
    still run."""

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,elsewhere", PUSH_DESTINATIONS.values(), ids=PUSH_DESTINATIONS.keys())
    def test_another_destination_needs_its_own_approval(self, approved, elsewhere, style):
        ctx = _mint_styled(approved, style)
        assert _runs(ctx, approved)
        assert not _runs(ctx, elsewhere)
        own = _mint_styled(elsewhere, style)
        assert _runs(own, elsewhere)
        assert not _runs(own, approved)

    @pytest.mark.parametrize("style", APPROVAL_STYLES.values(), ids=APPROVAL_STYLES.keys())
    @pytest.mark.parametrize("approved,respelled", SAME_DESTINATION_SPELLINGS.values(),
                             ids=SAME_DESTINATION_SPELLINGS.keys())
    def test_the_same_destination_respelled_still_runs(self, approved, respelled, style):
        assert _runs(_mint_styled(approved, style), respelled)
        assert _runs(_mint_styled(respelled, style), approved)

    def test_a_push_with_no_refspec_keeps_its_remote_in_its_target(self):
        from shared.merge_guard_common import extract_command_context

        assert "remote=origin" not in extract_command_context("git push --" + "force origin")["bound_flags"]
        assert _runs(_mint_styled("git push --" + "force origin", APPROVAL_STYLES["inline"]), "git push -f origin")
        assert not _runs(_mint_styled("git push --" + "force origin", APPROVAL_STYLES["inline"]),
                         "git push --" + "force upstream")

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
