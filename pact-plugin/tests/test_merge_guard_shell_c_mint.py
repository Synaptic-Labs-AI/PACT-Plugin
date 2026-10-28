"""
Location: pact-plugin/tests/test_merge_guard_shell_c_mint.py
Summary: A faithful approval of a guarded command run from a shell executor's -c
         string (`bash -c 'git push --force origin feat'`) mints and runs. The
         region finder returns the whole `bash -c '…'` region, where some ops have
         no readable target; the mint reads the -c string instead and binds the
         token to the command's normalized text, as the interpreter mint does.
Used by: pytest. The matrix runs the REAL mint function and the REAL pre-hook
         matcher in process; the click arm runs both hooks as subprocesses with an
         isolated CLAUDE_CONFIG_DIR.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from merge_guard_post import _mint_context_from_bundle, _retire_exact_text_token, _exact_text_reading
from merge_guard_pre import _token_matches_command
from shared.merge_guard_common import (
    is_compound_destructive_command,
    is_dangerous_command,
    normalize_command_text,
)

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
FORCE = "git push --" + "force origin feat"
CLOSE_DELETE = "gh pr " + "close 5 --delete-branch"
GATED_OPS = [
    "gh pr " + "merge 5",
    CLOSE_DELETE,
    "git branch -" + "D feat",
    FORCE,
    "gh api -X DELETE repos/o/r/branches/main/protection",
    "git push origin main",
    "git push origin --delete feat",
    "git push origin :feat",
    "git push --mirror origin",
]
WRAPPERS = {
    "bash-c": "bash -c '{op}'",
    "sh-c-dq": 'sh -c "{op}"',
    "bash-lc": "bash -lc '{op}'",
    "bash-eu-c": "bash -eu -c '{op}'",
    "bash-norc-c": "bash --norc -c '{op}'",
    "env-i-prefix-leg": "env -i HOME=/x /bin/bash -c 'command -v git; {op}' 2>&1",
    "timeout": "timeout 30 bash -c '{op}'",
}
ROW_22 = f"env -i HOME=/tmp/h /bin/bash -c 'command -v git; {FORCE}' 2>&1"
# The corpus row whole: the shell string sits in one leg among benign ones.
CORPUS_22 = (
    "cd /tmp/rg && /usr/bin/git -C runs rev-parse --show-toplevel 2>&1 | head -2; "
    "d=$(mktemp -d runs/probe.XXXX); "
    f"(cd \"$d\" && env -i HOME=\"$PWD\" /bin/bash -c 'command -v git; {FORCE}' 2>&1 | head -3)"
)


def _mint(command: str):
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": f"Run `{command}` now"},
                      {"label": "No", "description": "Hold."}]}]
    return _mint_context_from_bundle(q, {"Run this?": "Yes, run"})


def _runs_with_click(command: str) -> bool:
    """A faithful click on `command`, then the pre-hook's verdict on it."""
    if not is_dangerous_command(command):
        return True
    if is_compound_destructive_command(command):
        return False
    ctx, _ = _mint(command)
    return ctx is not None and _token_matches_command({"context": ctx}, command)


class TestEveryGatedOpRunsFromAShellString:
    @pytest.mark.parametrize("wrapper", WRAPPERS.values(), ids=WRAPPERS.keys())
    @pytest.mark.parametrize("op", GATED_OPS)
    def test_a_faithful_click_mints_and_runs(self, op, wrapper):
        command = wrapper.format(op=op)
        assert is_dangerous_command(command)  # refused without an approval
        assert _runs_with_click(command)

    @pytest.mark.parametrize("op", [FORCE.replace("feat", "$B"), "gh pr " + "merge $N"], ids=["force-var", "merge-var"])
    @pytest.mark.parametrize("quote", ["'", '"'], ids=["single", "double"])
    def test_a_variable_target_runs_as_it_does_bare(self, op, quote):
        """A faithful click on `bash -c "<op with $B>"` has the outcome of the bare op."""
        assert _runs_with_click(op)
        assert _runs_with_click(f"bash -c {quote}{op}{quote}")

    def test_the_corpus_row_with_surrounding_legs_runs(self):
        assert is_dangerous_command(CORPUS_22)
        assert _runs_with_click(CORPUS_22)

    @pytest.mark.parametrize("op", [FORCE, CLOSE_DELETE])
    def test_the_ops_the_region_reading_missed_take_the_exact_text_path(self, op):
        command = f"bash -c '{op}'"
        ctx, _ = _mint(command)
        assert ctx is not None
        assert ctx["command_text"] == normalize_command_text(command)


class TestTheNoticeNamesTheTarget:
    def test_the_destructive_leg_gives_the_target_beside_a_benign_leg(self):
        ctx = _exact_text_reading(ROW_22, f"`{ROW_22}`")
        assert ctx is not None
        assert (ctx["operation_type"], ctx["target_ref"]) == ("force-push", "feat")


class TestNothingElseRuns:
    def test_a_token_does_not_authorize_another_branch(self):
        ctx, _ = _mint(f"bash -c '{FORCE}'")
        assert not _token_matches_command({"context": ctx}, f"bash -c '{FORCE.replace('feat', 'main')}'")
        assert not _token_matches_command({"context": ctx}, f"bash -c '{FORCE} --no-verify'")

    def test_two_destructive_legs_in_one_string_mint_nothing(self):
        command = f"bash -c '{FORCE}; {FORCE.replace('feat', 'other')}'"
        assert _mint(command)[0] is None
        assert not _runs_with_click(command)

    @pytest.mark.parametrize("command", [
        pytest.param("bash script.sh", id="script"),
        pytest.param("python3 -c 'print(1)'", id="interpreter"),
        pytest.param(f"bash -c '{FORCE}; {FORCE.replace('feat', 'b')}'", id="two-legs"),
        pytest.param("bash -c 'git status'", id="no-op"),
    ])
    def test_the_reader_returns_nothing_without_one_guarded_command(self, command):
        assert _exact_text_reading(command, f"`{command}`") is None


class TestTheCorpusRow:
    def _hook(self, name: str, payload: dict, root: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HOOKS / name)], input=json.dumps(payload), capture_output=True,
            text=True, cwd=str(root), timeout=60, check=False,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
                 "CLAUDE_CONFIG_DIR": str(root / ".claude")})

    def test_row_22_mints_through_the_real_hooks_and_runs(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
              "options": [{"label": "Yes, run", "description": f"Run `{ROW_22}` now"},
                          {"label": "No", "description": "Hold."}]}]
        post = self._hook("merge_guard_post.py", {
            "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": q}, "tool_response": {"answers": {"Run this?": "Yes, run"}},
        }, tmp_path)
        assert "token written" in post.stderr, post.stderr
        pre = self._hook("merge_guard_pre.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": ROW_22},
        }, tmp_path)
        assert '"deny"' not in pre.stdout, pre.stdout


class TestRetirement:
    def _token(self, root: Path, command: str, op: str) -> Path:
        path = root / "merge-authorized-1"
        path.write_text(json.dumps({"context": {
            "operation_type": op, "target_ref": "feat", "bound_flags": [],
            "command_text": normalize_command_text(command)}}))
        return path

    def test_a_force_push_token_retires_on_the_successful_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr("merge_guard_post.get_session_id", lambda: "")
        command = f"bash -c '{FORCE}'"
        path = self._token(tmp_path, command, "force-push")
        assert _retire_exact_text_token(command, None, token_dir=tmp_path)
        assert not path.exists()

    @pytest.mark.parametrize("stdout,retired", [("Deleted branch feat (was abc).", True), ("", False)])
    def test_a_branch_delete_token_retires_only_on_its_success_line(self, tmp_path, monkeypatch,
                                                                     stdout, retired):
        monkeypatch.setattr("merge_guard_post.get_session_id", lambda: "")
        command = "bash -c 'git branch -" + "D feat'"
        path = self._token(tmp_path, command, "branch-delete")
        assert _retire_exact_text_token(command, None, token_dir=tmp_path, stdout_text=stdout) is retired
        assert path.exists() is not retired
