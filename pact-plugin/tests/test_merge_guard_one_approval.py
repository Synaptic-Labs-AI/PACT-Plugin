"""
Location: pact-plugin/tests/test_merge_guard_one_approval.py
Summary: One approval runs one guarded operation. A command whose target is set
         only when it runs (a variable, a substitution, the implicit current
         branch) retires its token on the first successful run, so the same
         approval cannot run a second, different target. A token never authorizes
         flags the approved command lacks, also when a wrapper (a shell's -c
         string, an interpreter program, a file the command writes and runs)
         carries them.
Used by: pytest. The real merge_guard_post and merge_guard_pre hooks run as
         subprocesses for the run sequences; the token matcher runs in process
         for the wrapper rows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from merge_guard_post import _mint_context_from_bundle
from merge_guard_pre import _token_matches_command

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
MERGE = "gh pr " + "merge "
DELETE = "git branch -" + "D "
FORCE = "git push --" + "force origin "


def _hook(name: str, payload: dict, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOKS / name)], input=json.dumps({"session_id": "s1", **payload}),
        capture_output=True, text=True, cwd=str(root), timeout=60, check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
             "CLAUDE_CONFIG_DIR": str(root / ".claude")})


def _approve(command: str, root: Path) -> None:
    (root / ".claude").mkdir(exist_ok=True)
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": f"Run `{command}` now"},
                      {"label": "No", "description": "Hold."}]}]
    post = _hook("merge_guard_post.py", {
        "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
        "tool_input": {"questions": q}, "tool_response": {"answers": {"Run this?": "Yes, run"}},
    }, root)
    assert "token written" in post.stderr, post.stderr


def _runs(command: str, root: Path) -> bool:
    pre = _hook("merge_guard_pre.py", {
        "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
    }, root)
    return '"deny"' not in pre.stdout


def _succeeded(command: str, root: Path) -> None:
    _hook("merge_guard_post.py", {
        "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": command},
        "tool_response": {"stdout": "", "stderr": "", "interrupted": False},
    }, root)


RUN_TIME_TARGETS = {
    "pr-number-in-a-variable": (MERGE + '"$PR" --squash',
                                "PR=5; " + MERGE + '"$PR" --squash', "PR=6; " + MERGE + '"$PR" --squash'),
    "implicit-current-branch": (MERGE + "--squash",
                                MERGE + "--squash", "git checkout feat/y && " + MERGE + "--squash"),
    "branch-in-a-variable": (DELETE + '"$B"',
                             "B=feat/x; " + DELETE + '"$B"', "B=feat/y; " + DELETE + '"$B"'),
}


class TestATargetSetAtRunTimeRunsOnce:
    @pytest.mark.parametrize("approved,first,second", RUN_TIME_TARGETS.values(), ids=RUN_TIME_TARGETS.keys())
    def test_a_successful_run_retires_the_approval(self, approved, first, second, tmp_path):
        _approve(approved, tmp_path)
        assert _runs(first, tmp_path)
        _succeeded(first, tmp_path)
        assert not _runs(second, tmp_path)

    @pytest.mark.parametrize("approved,first,second", RUN_TIME_TARGETS.values(), ids=RUN_TIME_TARGETS.keys())
    def test_a_failed_run_keeps_the_approval_for_a_retry(self, approved, first, second, tmp_path):
        _approve(approved, tmp_path)
        assert _runs(first, tmp_path)
        assert _runs(first, tmp_path)


def _token(command: str) -> dict:
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": f"Run `{command}` now"},
                      {"label": "No", "description": "Hold."}]}]
    ctx = _mint_context_from_bundle(q, {"Run this?": "Yes, run"})[0]
    assert ctx is not None
    return {"context": ctx}


PLAIN = MERGE + "5 --squash"
ESCALATED_WRAPPERS = {
    "plain-admin": PLAIN + " --admin",
    "bash-c-admin": "bash -c '" + PLAIN + " --admin'",
    "python-c-admin": "python3 -c 'import os; os.system(\"" + PLAIN + " --admin\")'",
    "file-run-admin": "echo '" + PLAIN + " --admin' > /tmp/a.sh && bash /tmp/a.sh",
    "python-c-other-repo": "python3 -c 'import os; os.system(\"" + PLAIN + " --repo evil/other\")'",
}


class TestATokenNeverAddsFlagsThroughAWrapper:
    @pytest.mark.parametrize("command", ESCALATED_WRAPPERS.values(), ids=ESCALATED_WRAPPERS.keys())
    def test_a_wrapped_escalation_is_refused(self, command):
        assert not _token_matches_command(_token(PLAIN), command)

    @pytest.mark.parametrize("command", [PLAIN, "bash -c '" + PLAIN + "'"], ids=["plain", "bash-c-same-flags"])
    def test_the_approved_operation_still_runs(self, command):
        assert _token_matches_command(_token(PLAIN), command)


# A `for` loop over literal words runs its body once per word: two words around a merge
# are two merges, so one approval mints nothing for it; one word is one merge.
LOOPS_OVER_SEVERAL = {
    "merge-two-prs": "for p in 5 6; do " + MERGE + '"$p"; done',
    "merge-on-separate-lines": "for p in 5 6\ndo\n  " + MERGE + '"$p" --squash\ndone',
    "delete-two-branches": "for b in a b; do " + DELETE + '"$b"; done',
    "inside-a-shell-c-string": "bash -c 'for p in 5 6; do " + MERGE + "$p; done'",
    "inner-loop-over-two": "for d in x; do for p in 5 6; do " + MERGE + '"$p"; done; done',
    "merge-after-an-inner-loop": "for p in 5 6; do for f in x; do echo; done; " + MERGE + '"$p"; done',
}
LOOPS_RUN_ONCE = {
    "one-word": "for p in 5; do " + MERGE + '"$p"; done',
    "loop-ends-before-the-merge": 'for f in a b; do echo "$f"; done; ' + MERGE + "5",
}


class TestALoopOverSeveralWordsIsSeveralOperations:
    @pytest.mark.parametrize("command", LOOPS_OVER_SEVERAL.values(), ids=LOOPS_OVER_SEVERAL.keys())
    def test_it_is_compound_and_an_approval_mints_nothing(self, command, tmp_path):
        from shared.merge_guard_common import is_compound_destructive_command

        assert is_compound_destructive_command(command) is True
        (tmp_path / ".claude").mkdir()
        q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
              "options": [{"label": "Yes, run", "description": f"Run `{command}` now"},
                          {"label": "No", "description": "Hold."}]}]
        post = _hook("merge_guard_post.py", {
            "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": q}, "tool_response": {"answers": {"Run this?": "Yes, run"}},
        }, tmp_path)
        assert "token written" not in post.stderr
        assert not _runs(command, tmp_path)

    @pytest.mark.parametrize("command", LOOPS_RUN_ONCE.values(), ids=LOOPS_RUN_ONCE.keys())
    def test_one_pass_is_one_operation_and_runs_once_approved(self, command, tmp_path):
        from shared.merge_guard_common import is_compound_destructive_command

        assert is_compound_destructive_command(command) is False
        _approve(command, tmp_path)
        assert _runs(command, tmp_path)

    @pytest.mark.parametrize("command", [
        "for p in 5 6; do gh pr view \"$p\"; done",
        "for p in $(gh pr list -q .number); do " + MERGE + '"$p"; done',
    ], ids=["view-loop", "list-not-literal"])
    def test_a_view_loop_or_an_unread_list_counts_as_before(self, command):
        from shared.merge_guard_common import is_compound_destructive_command

        assert is_compound_destructive_command(command) is False


# A pass repeats a guarded command only when the command reads the loop variable and
# runs on every pass. A retry loop around one command is one operation, and so is a
# command a pass runs only behind a guard (`then`, `&&`): one approval mints for them.
ONE_OPERATION_LOOPS = {
    "retry-with-if-break": "for i in 1 2 3; do if " + MERGE + "5 --squash; then break; fi; sleep 5; done",
    "retry-with-and-or": "for attempt in 1 2 3; do " + MERGE + "5 --squash && break || sleep 10; done",
    "retry-on-separate-lines": "for i in 1 2 3; do\n  " + MERGE + "5 --squash && break\n  sleep 5\ndone",
    "until-retry": "until " + MERGE + "5 --squash; do sleep 5; done",
    "python-range-retry": "python3 - <<'EOF'\nimport subprocess, time\nfor attempt in range(3):\n"
                          "    if subprocess.run([\"gh\", \"pr\", \"merge\", \"5\", \"--squash\"]).returncode == 0:\n"
                          "        break\n    time.sleep(5)\nEOF",
    "one-quoted-word": 'for b in "a b"; do ' + DELETE + "$b; done",
    "delete-behind-then": 'for b in a b; do if [ "$b" = a ]; then ' + DELETE + '"$b"; fi; done',
    "delete-behind-and": 'for b in a b; do [ "$b" = a ] && ' + DELETE + '"$b"; done',
    "delete-behind-or": 'for b in a b; do [ -z "$b" ] || ' + DELETE + '"$b"; done',
}
REPEATED_LOOPS = {
    "merge-each-word": "for p in 5 6; do " + MERGE + '"$p"; done',
    "merge-in-the-if-condition": "for p in 5 6; do if " + MERGE + '"$p"; then echo ok; fi; done',
    "delete-each-word": "for b in a b; do " + DELETE + '"$b"; done',
    "delete-after-a-closed-if": "for b in a b; do if true; then echo; fi; " + DELETE + '"$b"; done',
}


class TestALoopRepeatsOnlyWhatVariesAndAlwaysRuns:
    @pytest.mark.parametrize("command", ONE_OPERATION_LOOPS.values(), ids=ONE_OPERATION_LOOPS.keys())
    def test_one_operation_mints_and_runs(self, command, tmp_path):
        from shared.merge_guard_common import is_compound_destructive_command

        assert is_compound_destructive_command(command) is False
        assert not _runs(command, tmp_path)
        _approve(command, tmp_path)
        assert _runs(command, tmp_path)

    @pytest.mark.parametrize("command", REPEATED_LOOPS.values(), ids=REPEATED_LOOPS.keys())
    def test_an_operation_every_pass_runs_is_still_counted_per_word(self, command):
        from shared.merge_guard_common import is_compound_destructive_command

        assert is_compound_destructive_command(command) is True
