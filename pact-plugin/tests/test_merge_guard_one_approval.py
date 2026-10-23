"""
Location: pact-plugin/tests/test_merge_guard_one_approval.py
Summary: One approval runs one guarded operation. A command whose target is set
         only when it runs (a variable, a substitution, the implicit current
         branch) retires its token on the first successful run, so the same
         approval cannot run a second, different target.
Used by: pytest. The real merge_guard_post and merge_guard_pre hooks run as
         subprocesses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
