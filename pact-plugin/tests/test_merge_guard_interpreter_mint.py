"""
Location: pact-plugin/tests/test_merge_guard_interpreter_mint.py
Summary: A faithful approval of an interpreter merge mints and runs. The command
         passes its guarded `gh pr merge` as split argv, so the region finder
         sees no contiguous command; the mint reads it with the pre-hook's own
         interpreter reader and binds the token to the command's normalized text,
         and the pre-hook lets exactly that text through.
Used by: pytest. The click arms run the REAL merge_guard_post.py (mint) and then
         the REAL merge_guard_pre.py (gate) as subprocesses with an isolated
         CLAUDE_CONFIG_DIR, the way security-r's click rig does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from merge_guard_post import (
    _exact_text_mint_context,
    _mint_confirmation,
    _retire_exact_text_token,
)
from merge_guard_pre import _token_matches_command
from shared.merge_guard_common import normalize_command_text

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
G = "gh pr " + "merge"
LIST_C = "python3 -c 'import subprocess; subprocess.run([\"gh\", \"pr\", \"merge\", \"5\", \"--squash\"])'"
HEREDOC = (
    "python3 - <<'EOF'\nimport subprocess\n"
    "cmd = [\"gh\", \"pr\", \"merge\", \"5\", \"--squash\"]\nsubprocess.run(cmd)\nEOF"
)
LIST_6 = LIST_C.replace('"5"', '"6"')
ADMIN = "python3 -c 'import os; os.system(\"gh pr merge 5 --admin\")'"


def _env(tmp_path: Path) -> dict:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude")}


def _hook(name: str, payload: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HOOKS / name)], input=json.dumps(payload),
                          capture_output=True, text=True, env=_env(tmp_path),
                          cwd=str(tmp_path), timeout=60, check=False)


def _approve(tmp_path: Path, description: str) -> list:
    """Click 'Yes, run' on an option carrying `description`; return the minted contexts."""
    (tmp_path / ".claude").mkdir(exist_ok=True)
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": description},
                      {"label": "No", "description": "Hold."}]}]
    r = _hook("merge_guard_post.py", {
        "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
        "tool_input": {"questions": q}, "tool_response": {"answers": {"Run this?": "Yes, run"}},
    }, tmp_path)
    assert r.returncode == 0, r.stderr
    return [json.loads(t.read_text())["context"]
            for t in (tmp_path / ".claude").glob("merge-authorized-*")
            if not t.name.endswith(".consumed") and ".use-" not in t.name]


def _runs(tmp_path: Path, command: str) -> bool:
    """Whether the pre-hook lets `command` run."""
    r = _hook("merge_guard_pre.py", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                     "tool_input": {"command": command}}, tmp_path)
    return '"deny"' not in r.stdout


class TestFaithfulClickMintsAndRuns:
    @pytest.mark.parametrize("command", [LIST_C, HEREDOC, ADMIN], ids=["list-c", "heredoc", "os-system-admin"])
    def test_the_approved_interpreter_merge_mints_and_runs(self, tmp_path, command):
        assert not _runs(tmp_path, command)  # refused without an approval
        tokens = _approve(tmp_path, f"Run `{command}` to merge this PR")
        assert len(tokens) == 1
        assert tokens[0]["operation_type"] == "merge"
        assert tokens[0]["pr_number"] == "5"
        assert tokens[0]["command_text"] == normalize_command_text(command)
        assert _runs(tmp_path, command)

    def test_the_admin_flag_is_bound_and_shown(self, tmp_path):
        tokens = _approve(tmp_path, f"Run `{ADMIN}` to merge")
        assert tokens[0]["bound_flags"] == ["--admin"]
        assert "for a merge on PR #5 with --admin." in _mint_confirmation(tokens[0])

    def test_a_fenced_block_is_read_like_a_backtick_span(self, tmp_path):
        tokens = _approve(tmp_path, f"Run this to merge:\n```bash\n{HEREDOC}\n```")
        assert [t["command_text"] for t in tokens] == [normalize_command_text(HEREDOC)]
        assert _runs(tmp_path, HEREDOC)

    @pytest.mark.parametrize("executed", [
        pytest.param(LIST_C + "\n", id="trailing-newline"),
        pytest.param(LIST_C.replace("; subprocess", ";\\\n    subprocess"), id="line-continuation"),
    ])
    def test_a_whitespace_or_continuation_difference_still_runs(self, tmp_path, executed):
        assert not _runs(tmp_path, executed)  # the spelling is gated, so the token is what lets it run
        _approve(tmp_path, f"Run `{LIST_C}` to merge")
        assert _runs(tmp_path, executed)

    def test_a_contiguous_command_still_mints_on_its_target_not_its_text(self, tmp_path):
        tokens = _approve(tmp_path, f"Run `{G} 42 --squash` to merge this PR")
        assert [t.get("pr_number") for t in tokens] == ["42"]
        assert "command_text" not in tokens[0]


class TestNothingElseRuns:
    @pytest.mark.parametrize("other", [
        pytest.param(LIST_6, id="other-pr"),
        pytest.param(LIST_C.replace('"--squash"', '"--squash", "--admin"'), id="extra-flag"),
        pytest.param(f"{G} 5 --squash", id="plain-command-same-merge"),
        pytest.param(ADMIN, id="other-interpreter-merge"),
    ])
    def test_an_exact_command_token_authorizes_only_its_command(self, tmp_path, other):
        _approve(tmp_path, f"Run `{LIST_C}` to merge")
        assert not _runs(tmp_path, other)

    def test_a_compound_span_mints_nothing(self, tmp_path):
        compound = f"{LIST_C} && {LIST_6}"
        assert _approve(tmp_path, f"Run `{compound}` now") == []
        assert not _runs(tmp_path, compound)

    @pytest.mark.parametrize("second", [LIST_6, ADMIN, f"{G} 7"], ids=["list", "os-system", "plain"])
    def test_a_second_command_in_the_option_mints_nothing(self, tmp_path, second):
        assert _approve(tmp_path, f"Run `{LIST_C}` then `{second}`") == []

    def test_an_interpreter_program_that_only_names_a_merge_mints_nothing(self, tmp_path):
        assert _approve(tmp_path, "Run `python3 -c 'print(\"gh pr merge 5\")'` now") == []


class TestMintGates:
    """The interpreter mint's own gates, each isolated from the reader."""

    def test_a_span_the_read_floor_allows_never_mints(self, monkeypatch):
        """Mint only what the pre-hook gates: a span it lets run without a token
        gets none, whatever the reader says."""
        monkeypatch.setattr("merge_guard_post.interpreter_command_context",
                            lambda s: {"operation_type": "merge", "pr_number": "5", "bound_flags": []})
        assert _exact_text_mint_context(["Run `echo hello` now"])[0].context is None

    def test_a_compound_span_is_refused_as_one(self, monkeypatch):
        monkeypatch.setattr("merge_guard_post.is_compound_destructive_command", lambda s: True)
        assert _exact_text_mint_context([f"Run `{LIST_C}` now"])[0] == (None, "compound_command")

    def test_the_notice_names_no_target_it_cannot_read(self):
        notice = _mint_confirmation({"operation_type": "merge", "pr_number": "branch:X",
                                     "bound_flags": [], "command_text": "x"})
        assert "for a merge. It authorizes" in notice


class TestExactTextMatch:
    def test_the_text_is_all_that_matches(self):
        token = {"context": {"operation_type": "merge", "pr_number": "5", "bound_flags": [],
                             "command_text": normalize_command_text(LIST_C)}}
        assert _token_matches_command(token, LIST_C)
        assert not _token_matches_command(token, f"{G} 5")

    @pytest.mark.parametrize("value", [None, 5, ["x"]], ids=["none", "int", "list"])
    def test_a_malformed_command_text_matches_nothing(self, value):
        token = {"context": {"operation_type": "merge", "pr_number": "5", "command_text": value}}
        assert not _token_matches_command(token, f"{G} 5")


class TestRetirement:
    def _token(self, tmp_path: Path, command: str, op: str = "merge") -> Path:
        path = tmp_path / "merge-authorized-1"
        path.write_text(json.dumps({"context": {
            "operation_type": op, "pr_number": "5", "bound_flags": [],
            "command_text": normalize_command_text(command)}}))
        return path

    @pytest.mark.parametrize("state,retired", [("MERGED", True), ("OPEN", False), ("", False)])
    def test_the_token_retires_only_when_github_confirms(self, tmp_path, monkeypatch, state, retired):
        import shared.gh_helpers as gh

        monkeypatch.setattr(gh, "check_pr_state", lambda pr, cwd=None: state)
        monkeypatch.setattr("merge_guard_post.get_session_id", lambda: "")
        path = self._token(tmp_path, LIST_C)
        assert _retire_exact_text_token(LIST_C, None, token_dir=tmp_path) is retired
        assert path.exists() is not retired

    def test_another_command_does_not_retire_it(self, tmp_path, monkeypatch):
        import shared.gh_helpers as gh

        monkeypatch.setattr(gh, "check_pr_state", lambda pr, cwd=None: "MERGED")
        monkeypatch.setattr("merge_guard_post.get_session_id", lambda: "")
        path = self._token(tmp_path, LIST_C)
        assert _retire_exact_text_token(ADMIN, None, token_dir=tmp_path) is False
        assert path.exists()
