"""Token retirement arms the PR-state check's own file does not carry.

Same rig shape as test_merge_guard_pr_state_retirement.py: the REAL
merge_guard_post.py as a subprocess, an isolated CLAUDE_CONFIG_DIR holding the
token, and a fake `gh` first on an explicitly built PATH that logs each call.
This fake can also exit non-zero while printing a state.

What these arms add:
- Block 2 still runs before the state check: an interrupted call neither
  asks gh nor retires. A non-dict response is normalised to {} upstream and is
  decided by the state check, like an empty stdout.
- The token stays bound to its PR: a confirmed merge of another PR does not
  retire it.
- A retired token no longer authorizes the same command.
- gh's exit status is read, not only its output: a failing gh that prints
  MERGED keeps the token, as does a state GitHub never returns.
- Only merge and close ask gh. Force-push, branch-delete and push-to-main never do.
- merge_guard_post is not registered on PostToolUseFailure, so a failed bare
  merge never reaches retirement at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from merge_guard_post import write_token
from merge_guard_pre import check_merge_authorization
from shared.merge_guard_common import _single_destructive_leg, extract_command_context

_PLUGIN = Path(__file__).resolve().parent.parent
_HOOK = _PLUGIN / "hooks" / "merge_guard_post.py"
_MERGE = "gh pr " + "merge"


@pytest.fixture
def rig(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$FAKE_GH_LOG"\n'
        'echo "$FAKE_GH_STATE"\n'
        'exit "${FAKE_GH_RC:-0}"\n'
    )
    gh.chmod(0o755)
    return {"tmp": tmp_path, "config": config, "bin": bin_dir, "log": tmp_path / "gh.log"}


def _post(rig, command, response, state="MERGED", rc=0):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": response,
    }
    env = {
        "PATH": os.pathsep.join([str(rig["bin"]), "/usr/bin", "/bin"]),
        "HOME": str(rig["tmp"]),
        "CLAUDE_CONFIG_DIR": str(rig["config"]),
        "FAKE_GH_LOG": str(rig["log"]),
        "FAKE_GH_STATE": state,
        "FAKE_GH_RC": str(rc),
    }
    proc = subprocess.run(
        [sys.executable, str(_HOOK)], input=json.dumps(payload), capture_output=True,
        text=True, env=env, cwd=str(rig["tmp"]), timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"suppressOutput": True}, proc.stdout


def _mint(rig, command):
    ctx = extract_command_context(_single_destructive_leg(command) or command)
    assert ctx.get("operation_type"), ctx
    assert write_token(ctx, token_dir=rig["config"])


def _gh_calls(rig):
    return rig["log"].read_text().splitlines() if rig["log"].exists() else []


def _retired(rig):
    return len(list(rig["config"].glob("merge-authorized-*.consumed"))) == 1


_EMPTY = {"stdout": "", "stderr": "", "interrupted": False}


class TestBlockTwoRunsFirst:
    def test_an_interrupted_call_never_asks_gh_or_retires(self, rig):
        cmd = f"{_MERGE} 5 --squash"
        _mint(rig, cmd)
        _post(rig, cmd, {"stdout": "", "stderr": "", "interrupted": True})
        assert _gh_calls(rig) == []
        assert not _retired(rig)

    @pytest.mark.parametrize("response", [
        pytest.param("merged", id="non-dict"),
        pytest.param(None, id="null"),
    ])
    @pytest.mark.parametrize("state,retired", [("MERGED", True), ("OPEN", False)])
    def test_a_non_dict_response_is_an_empty_one(self, rig, response, state, retired):
        """extract_tool_response maps a non-dict or absent response to {}, so
        Block 2's isinstance test never sees one: the frame reaches the state
        check like an empty stdout, and GitHub's state alone decides. Pinned
        so a change to that mapping is seen here."""
        cmd = f"{_MERGE} 5 --squash"
        _mint(rig, cmd)
        _post(rig, cmd, response, state=state)
        assert _gh_calls(rig) == ["pr view 5 --json state --jq .state"]
        assert _retired(rig) is retired


class TestTokenStaysBoundToItsPr:
    def test_a_confirmed_merge_of_another_pr_keeps_the_token(self, rig):
        _mint(rig, f"{_MERGE} 5 --squash")
        _post(rig, f"{_MERGE} 6 --squash", _EMPTY)
        assert not _retired(rig)
        assert check_merge_authorization(f"{_MERGE} 5 --squash", token_dir=rig["config"]) is None

    def test_a_retired_token_no_longer_authorizes_the_command(self, rig):
        """The point of retirement: after the confirmed merge, the same
        command needs a new approval."""
        cmd = f"{_MERGE} 5 --squash"
        _mint(rig, cmd)
        assert check_merge_authorization(cmd, token_dir=rig["config"]) is None
        _post(rig, cmd, _EMPTY)
        assert _retired(rig)
        assert check_merge_authorization(cmd, token_dir=rig["config"]) is not None


class TestGhAnswerIsReadStrictly:
    @pytest.mark.parametrize("state,rc", [
        pytest.param("MERGED", 1, id="gh-fails-but-prints-merged"),
        pytest.param("DRAFT", 0, id="state-github-never-returns"),
        pytest.param("", 0, id="empty-answer"),
        pytest.param("MERGED\nCLOSED", 0, id="two-lines"),
    ])
    def test_anything_but_a_clean_merged_keeps_the_token(self, rig, state, rc):
        """Piped, so the run's exit status is tail's and only GitHub's answer can
        confirm the merge."""
        cmd = f"{_MERGE} 5 --squash 2>&1 | tail -3"
        _mint(rig, f"{_MERGE} 5 --squash")
        _post(rig, cmd, _EMPTY, state=state, rc=rc)
        assert _gh_calls(rig) == ["pr view 5 --json state --jq .state"]
        assert not _retired(rig)

    def test_lowercase_merged_from_gh_still_confirms(self, rig):
        """check_pr_state upper-cases the answer, so the comparison is on
        GitHub's state, not on its spelling."""
        cmd = f"{_MERGE} 5 --squash"
        _mint(rig, cmd)
        _post(rig, cmd, _EMPTY, state="merged")
        assert _retired(rig)


class TestOnlyMergeAndCloseAskGh:
    @pytest.mark.parametrize("cmd", [
        "git push --force origin feature",
        "git push origin --delete feature",
        "git push origin main",
    ])
    def test_other_guarded_operations_never_call_gh(self, rig, cmd):
        _mint(rig, cmd)
        _post(rig, cmd, _EMPTY)
        assert _gh_calls(rig) == []


class TestRegistration:
    def test_merge_guard_post_runs_on_post_tool_use_only(self):
        """A failed bare merge goes to PostToolUseFailure. Registered there, the
        hook would reach retirement on a merge that never happened."""
        hooks = json.loads((_PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        events = {
            event
            for event, groups in hooks.items()
            for group in groups
            for hook in group.get("hooks", [])
            if "merge_guard_post.py" in hook.get("command", "")
        }
        assert events == {"PostToolUse"}, events


class TestAnUnconfirmableTargetRetiresOnItsOwnSuccess:
    """This hook sees only successful runs. When the guarded command is the
    command's last leg, nothing pipes it, runs it in the background or could skip
    it, and it is not a dry run, that success is the operation's, so one approval
    covers one run whatever its target."""

    @pytest.mark.parametrize("cmd", [
        "git -C {repo} push origin --delete feat/x",
        "git -C {repo} push --force origin main",
        "echo y | git push origin --delete feat/x",
    ])
    def test_the_run_retires_the_token(self, rig, cmd):
        repo = rig["tmp"] / "repo"
        repo.mkdir()
        cmd = cmd.format(repo=repo)
        _mint(rig, cmd)
        _post(rig, cmd, _EMPTY)
        assert _retired(rig)

    def test_an_exact_command_token_retires_on_its_run(self, rig):
        """A branch delete prints its success line only on a terminal; the run's
        own success retires the token without it."""
        from shared.merge_guard_common import normalize_command_text

        cmd = "bash -c 'git branch -" + "D feat/x'"
        assert write_token({"operation_type": "branch-delete",
                            "command_text": normalize_command_text(cmd)}, token_dir=rig["config"])
        _post(rig, cmd, _EMPTY)
        assert _retired(rig)

    @pytest.mark.parametrize("cmd", [
        "git push origin --delete feat/x 2>&1 | tail -3",
        "git push origin --delete feat/x || true",
        "true || git push origin --delete feat/x",
        "! git push origin --delete feat/x",
        "git push origin --delete feat/x &",
        "git push origin --delete feat/x; echo done",
        "git push origin --delete feat/x & wait",
        "git push --dry-run origin --delete feat/x",
        "git push -n origin --delete feat/x",
    ])
    def test_a_run_whose_status_is_not_the_operations_keeps_the_token(self, rig, cmd):
        _mint(rig, "git push origin --delete feat/x")
        _post(rig, cmd, _EMPTY)
        assert not _retired(rig)

    def test_a_response_not_in_the_success_shape_keeps_the_token(self, rig):
        cmd = "git push origin --delete feat/x"
        _mint(rig, cmd)
        _post(rig, cmd, "Error: Exit code 1")
        assert not _retired(rig)


class TestARunThatDidNotDoTheOperationKeepsTheToken:
    """A dry run and a backgrounded leg exit 0 without doing the operation, so
    their success retires nothing; the operation run in the foreground still
    retires on its success, piped or not."""

    @pytest.mark.parametrize("cmd", [
        "git push --dry-run --force origin feature",
        "git push -n --force origin feature",
        "git push -fn origin feature",
        "git push --force -n origin feature",
        "git push --force origin feature &",
        "git push --force origin feature & sleep 1",
    ])
    def test_the_token_is_kept(self, rig, cmd):
        _mint(rig, "git push --force origin feature")
        _post(rig, cmd, _EMPTY)
        assert not _retired(rig)

    @pytest.mark.parametrize("cmd", [
        "bash -c 'git push --force origin feature &'",
        "bash -c 'git push -fn origin feature'",
    ])
    def test_an_exact_command_token_is_kept(self, rig, cmd):
        from shared.merge_guard_common import normalize_command_text

        assert write_token({"operation_type": "force-push",
                            "command_text": normalize_command_text(cmd)}, token_dir=rig["config"])
        _post(rig, cmd, _EMPTY)
        assert not _retired(rig)

    @pytest.mark.parametrize("cmd", [
        "git push --force origin feature",
        "git push --force origin feature 2>&1 | tail -3",
        "sleep 1 & git push --force origin feature",
        "git push --force origin feature & wait",
        "git push --force origin feature & wait $!",
        "git log -n 1 && git push --force origin feature",
    ])
    def test_the_operation_itself_still_retires(self, rig, cmd):
        _mint(rig, "git push --force origin feature")
        _post(rig, cmd, _EMPTY)
        assert _retired(rig)

    def test_a_flag_holding_an_n_is_not_a_dry_run(self):
        from merge_guard_post import _op_did_not_run

        assert not _op_did_not_run("find . -name x -exec sh -c 'gh pr " + "merge 5' \\;")
        assert _op_did_not_run("git push -qn --force origin feature")


class TestAHeredocBodyIsPartOfItsLeg:
    """A heredoc's body belongs to the leg that opens it, so a guarded command
    followed only by its own heredoc is the command's last leg."""

    @pytest.mark.parametrize("cmd", [
        "gh pr " + "merge 5 --squash -R o/r --body-file - <<'EOF'\nMerged; see notes || later\nEOF",
        "git push origin --" + "delete feat/x <<'EOF'\nunused\nEOF",
    ])
    def test_the_run_retires_the_token(self, rig, cmd):
        _mint(rig, cmd)
        _post(rig, cmd, _EMPTY)
        assert _retired(rig)

    def test_a_leg_after_the_heredoc_keeps_the_token(self, rig):
        cmd = "gh pr " + "merge 5 --squash -R o/r --body-file - <<'EOF'\nbody\nEOF\necho done"
        _mint(rig, cmd)
        _post(rig, cmd, _EMPTY)
        assert not _retired(rig)


class TestGitsDeletedLineProvesAPipedRefDelete:
    """A piped remote ref delete exits with the last stage's status, but git's own
    ` - [deleted]  <ref>` line, passed through to stdout or stderr, proves it
    for exactly the ref the command deletes."""

    @pytest.mark.parametrize("key", ["stdout", "stderr"])
    def test_the_line_for_the_target_retires_the_token(self, rig, key):
        cmd = "git push origin --" + "delete feat/x 2>&1 | tail -3"
        _mint(rig, cmd)
        _post(rig, cmd, {**_EMPTY, key: "To ../remote.git\n - [deleted]         feat/x\n"})
        assert _retired(rig)

    @pytest.mark.parametrize("out", [
        " - [deleted]         feat/xyz\n",
        " - [deleted]         other/feat/x\n",
        "error: unable to delete 'feat/x': remote ref does not exist\n",
        "",
    ])
    def test_any_other_output_keeps_the_token(self, rig, out):
        cmd = "git push origin --" + "delete feat/x 2>&1 | tail -3"
        _mint(rig, cmd)
        _post(rig, cmd, {**_EMPTY, "stdout": out})
        assert not _retired(rig)


class TestAnExactMergeInAnotherRepoIsNotReadHere:
    """An exact-command merge token whose PR is named in another repo (-R/--repo,
    GH_REPO) cannot be confirmed by this directory's PR state, so the state of a
    same-numbered PR here never decides it."""

    @pytest.mark.parametrize("cmd,flags", [
        ("python3 -c 'import subprocess; subprocess.run([\"gh\", \"pr\", \"" + "merge\", \"5\", \"-R\", \"o/r\"])'",
         ["--repo=o/r"]),
        ("GH_REPO=o/r python3 -c 'import subprocess; subprocess.run([\"gh\", \"pr\", \"" + "merge\", \"5\"])'", []),
    ])
    def test_the_token_is_kept_without_asking_gh(self, rig, cmd, flags):
        from shared.merge_guard_common import normalize_command_text

        assert write_token({"operation_type": "merge", "pr_number": "5", "bound_flags": flags,
                            "command_text": normalize_command_text(cmd)}, token_dir=rig["config"])
        _post(rig, cmd, _EMPTY, state="MERGED")
        assert _gh_calls(rig) == []
        assert not _retired(rig)

    def test_an_exact_merge_in_this_repo_still_reads_the_state(self, rig):
        from shared.merge_guard_common import normalize_command_text

        cmd = "python3 -c 'import subprocess; subprocess.run([\"gh\", \"pr\", \"" + "merge\", \"5\"])'"
        assert write_token({"operation_type": "merge", "pr_number": "5", "bound_flags": [],
                            "command_text": normalize_command_text(cmd)}, token_dir=rig["config"])
        _post(rig, cmd, _EMPTY, state="MERGED")
        assert _retired(rig)


class TestAGitAliasRetiresAsTheCommandItRuns:
    """A git alias given with -c runs the command its value names, and the
    classifier reads it that way; retirement reads the same expansion, so the
    aliased push retires on success and an aliased dry run keeps its token."""

    def test_a_shell_alias_push_retires(self, rig):
        cmd = "git -c alias.x='!git push --" + "force' x origin main"
        assert write_token({"operation_type": "force-push", "bound_flags": ["remote=origin"], "target_ref": "main"},
                           token_dir=rig["config"])
        _post(rig, cmd, _EMPTY)
        assert _retired(rig)

    @pytest.mark.parametrize("cmd", [
        "git -c alias.pn='push -n --" + "force' pn origin main",
        "git -c alias.x='!git push -n --" + "force' x origin main",
    ])
    def test_an_aliased_dry_run_keeps_its_token(self, rig, cmd):
        from shared.merge_guard_common import normalize_command_text

        assert write_token({"operation_type": "force-push", "bound_flags": [], "target_ref": "main",
                            "command_text": normalize_command_text(cmd)}, token_dir=rig["config"])
        _post(rig, cmd, _EMPTY)
        assert not _retired(rig)

    @pytest.mark.parametrize("cmd,retires", [
        ("git px origin main", True),
        ("git pn origin main", False),
        ("git pxn origin main", False),
    ])
    def test_a_config_alias_retires_as_the_command_it_runs(self, rig, cmd, retires):
        from shared.merge_guard_common import normalize_command_text

        (rig["tmp"] / ".gitconfig").write_text(
            "[alias]\n\tpn = push -n --" + "force\n"
            "\tpx = \"!git push --" + "force\"\n\tpxn = \"!git push -n --" + "force\"\n")
        assert write_token({"operation_type": "force-push", "bound_flags": [], "target_ref": "main",
                            "command_text": normalize_command_text(cmd)}, token_dir=rig["config"])
        _post(rig, cmd, _EMPTY)
        assert _retired(rig) is retires


class TestAShellCMergeInAnotherRepoRetiresOnItsRun:
    """An exact-command merge in another repo cannot be read here; inside a shell's
    -c string the merge is the command's last leg, so its success retires the token."""

    def test_the_run_retires_the_token(self, rig):
        from shared.merge_guard_common import normalize_command_text

        cmd = "bash -c 'gh pr " + "merge 5 --squash --repo o/b'"
        assert write_token({"operation_type": "merge", "pr_number": "5", "bound_flags": ["--repo=o/b"],
                            "command_text": normalize_command_text(cmd)}, token_dir=rig["config"])
        _post(rig, cmd, _EMPTY, state="OPEN")
        assert _gh_calls(rig) == []
        assert _retired(rig)


class TestAVariableEvalRetiresOnItsRun:
    """An eval of an assigned variable runs the variable's value: a branch delete run
    that way retires on its own success, as the plain command does, since git prints
    its success line only on a terminal."""

    _CMD = 'cmd="git branch -' + 'D topic"; eval '

    def _write(self, rig, cmd):
        from shared.merge_guard_common import normalize_command_text

        assert write_token({"operation_type": "branch-delete", "bound_flags": [],
                            "command_text": normalize_command_text(cmd)}, token_dir=rig["config"])

    @pytest.mark.parametrize("tail", ['"$cmd"', "$cmd"])
    def test_the_run_retires_the_token(self, rig, tail):
        cmd = self._CMD + tail
        self._write(rig, cmd)
        _post(rig, cmd, _EMPTY)
        assert _retired(rig)

    @pytest.mark.parametrize("tail", [
        '"$cmd" || true',
        '"$cmd"; echo done',
        '"$cmd" 2>&1 | tail -1',
        '"$cmd" &',
    ])
    def test_a_run_whose_status_is_not_the_deletes_keeps_the_token(self, rig, tail):
        cmd = self._CMD + tail
        self._write(rig, cmd)
        _post(rig, cmd, _EMPTY)
        assert not _retired(rig)
