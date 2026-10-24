"""Token retirement confirmed by the PR's state on GitHub (merge_guard_post Block 3).

A merge or close run through a pipe or without a TTY prints no "Merged pull
request" line, so Block 3's stdout match misses and the consumed token used to
survive for a second use. Block 3 now asks GitHub for the PR's state, and
retires the token only when GitHub reports the state the command produces.
Any doubt keeps the token: a URL, branch or implicit target, an -R/--repo
target, a directory change or repo env assignment in the command, and an
unreachable or slow gh. One directory change is followed instead: a single
`cd <literal absolute existing dir> &&` before the gh leg, where the check runs.
With no cd, the check runs in the session's directory, the hook frame's `cwd`.

Every arm runs the REAL merge_guard_post.py as a subprocess, with a fake `gh`
first on an explicitly built PATH and an isolated CLAUDE_CONFIG_DIR. The fake
logs each call with its working directory, so an arm can assert gh was or was
not asked, and where. "Retry still
authorized" is checked with the real read side (check_merge_authorization) on
the same token directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from merge_guard_post import write_token
from merge_guard_pre import check_merge_authorization
from shared.merge_guard_common import extract_command_context

_HOOK = Path(__file__).resolve().parent.parent / "hooks" / "merge_guard_post.py"
_MERGE = "gh pr " + "merge"
_CLOSE = "gh pr " + "close"


@pytest.fixture
def rig(tmp_path):
    """A token dir under an isolated config root, and a fake gh on PATH."""
    config = tmp_path / ".claude"
    config.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'echo "$(pwd -P) :: $*" >> "$FAKE_GH_LOG"\n'
        'if [ -n "$FAKE_GH_SLEEP" ]; then sleep "$FAKE_GH_SLEEP"; fi\n'
        'echo "$FAKE_GH_STATE"\n'
    )
    gh.chmod(0o755)
    return {"tmp": tmp_path, "config": config, "bin": bin_dir, "log": log}


def _env(rig, state="MERGED", sleep=None, with_gh=True):
    path_dirs = ([str(rig["bin"])] if with_gh else []) + ["/usr/bin", "/bin"]
    env = {
        "PATH": os.pathsep.join(path_dirs),
        "HOME": str(rig["tmp"]),
        "CLAUDE_CONFIG_DIR": str(rig["config"]),
        "FAKE_GH_LOG": str(rig["log"]),
        "FAKE_GH_STATE": state,
    }
    if sleep is not None:
        env["FAKE_GH_SLEEP"] = str(sleep)
    return env


def _mint(rig, command):
    ctx = extract_command_context(command)
    assert ctx.get("operation_type"), ctx
    assert write_token(ctx, token_dir=rig["config"])


def _post(rig, command, stdout, frame_cwd=None, **env_kw):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False},
    }
    if frame_cwd is not None:
        payload["cwd"] = frame_cwd
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=_env(rig, **env_kw),
        cwd=str(rig["tmp"]),
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return time.monotonic() - started


def _gh_log(rig):
    if not rig["log"].exists():
        return []
    return [line.split(" :: ", 1) for line in rig["log"].read_text().splitlines()]


def _gh_calls(rig):
    return [args for _, args in _gh_log(rig)]


def _gh_cwds(rig):
    return [cwd for cwd, _ in _gh_log(rig)]


def _retired(rig):
    return len(list(rig["config"].glob("merge-authorized-*.consumed"))) == 1


class TestStateConfirmsRetirement:
    def test_empty_stdout_merged_retires(self, rig):
        cmd = f"{_MERGE} 1656 --squash"
        _mint(rig, cmd)
        _post(rig, cmd, "", state="MERGED")
        assert _gh_calls(rig) == ["pr view 1656 --json state --jq .state"]
        assert _retired(rig)

    def test_piped_merge_merged_retires(self, rig):
        cmd = f"{_MERGE} 1641 --squash 2>&1 | tail -5"
        _mint(rig, f"{_MERGE} 1641 --squash")
        _post(rig, cmd, "", state="MERGED")
        assert _gh_calls(rig) == ["pr view 1641 --json state --jq .state"]
        assert _retired(rig)

    def test_close_closed_retires(self, rig):
        cmd = f"{_CLOSE} 9"
        _mint(rig, cmd)
        _post(rig, cmd, "", state="CLOSED")
        assert _gh_calls(rig) == ["pr view 9 --json state --jq .state"]
        assert _retired(rig)

    def test_close_reported_merged_keeps(self, rig):
        """A close is confirmed by CLOSED only (piped, so the run's own status
        cannot confirm it)."""
        cmd = f"{_CLOSE} 9 2>&1 | tail -3"
        _mint(rig, f"{_CLOSE} 9")
        _post(rig, cmd, "", state="MERGED")
        assert not _retired(rig)

    @pytest.mark.parametrize("stdout", [
        "✓ Squashed and merged pull request owner/repo#42 (Title)",
        "✓ Rebased and merged pull request owner/repo#42 (Title)",
        "✓ Merged pull request owner/repo#42 (Title)",
    ])
    def test_merge_stdout_retires_without_gh(self, rig, stdout):
        cmd = f"{_MERGE} 42 --squash"
        _mint(rig, cmd)
        _post(rig, cmd, stdout, state="OPEN")
        assert _gh_calls(rig) == []
        assert _retired(rig)


class TestAReadableStateOutranksTheExitStatus:
    """A merge that exits 0 into a merge queue or with --auto leaves the PR OPEN:
    the PR's state, when it can be read, decides, and only an unreadable state
    falls back to the run's own success."""

    @pytest.mark.parametrize("cmd,state", [
        pytest.param(f"{_MERGE} 5 --squash", "OPEN", id="merge-queued"),
        pytest.param(f"{_MERGE} 5 --squash --auto", "OPEN", id="auto-merge"),
        pytest.param(f"{_MERGE} 5 --squash", "CLOSED", id="merge-closed"),
        pytest.param(f"{_CLOSE} 5 --delete-branch", "MERGED", id="close-merged"),
    ])
    def test_a_state_that_is_not_the_ops_keeps_the_token(self, rig, cmd, state):
        _mint(rig, cmd)
        _post(rig, cmd, "", state=state)
        assert _gh_calls(rig) == ["pr view 5 --json state --jq .state"]
        assert not _retired(rig)

    @pytest.mark.parametrize("env", [
        pytest.param({"with_gh": False}, id="gh-missing"),
        pytest.param({"state": "DRAFT"}, id="unknown-answer"),
    ])
    def test_an_unreadable_state_falls_back_to_the_runs_success(self, rig, env):
        cmd = f"{_MERGE} 5 --squash"
        _mint(rig, cmd)
        _post(rig, cmd, "", **env)
        assert _retired(rig)


class TestDoubtKeepsToken:
    def test_refused_piped_merge_open_keeps_and_retry_authorized(self, rig):
        cmd = f"{_MERGE} 1659 --squash 2>&1 | tail -3"
        bare = f"{_MERGE} 1659 --squash"
        _mint(rig, bare)
        _post(rig, cmd, "X Pull request #1659 is not mergeable", state="OPEN")
        assert _gh_calls(rig) == ["pr view 1659 --json state --jq .state"]
        assert not _retired(rig)
        assert check_merge_authorization(bare, token_dir=rig["config"]) is None

    def test_gh_unreachable_keeps(self, rig):
        cmd = f"{_MERGE} 5 --squash 2>&1 | tail -3"
        _mint(rig, f"{_MERGE} 5 --squash")
        _post(rig, cmd, "", with_gh=False)
        assert not _retired(rig)

    def test_gh_hung_keeps_within_cap(self, rig):
        cmd = f"{_MERGE} 5 --squash 2>&1 | tail -3"
        _mint(rig, f"{_MERGE} 5 --squash")
        elapsed = _post(rig, cmd, "", state="MERGED", sleep=20)
        assert elapsed < 15, elapsed
        assert not _retired(rig)

    @pytest.mark.parametrize("cmd", [
        f"{_MERGE} 5 -R other/repo",
        f"{_MERGE} 5 --repo=other/repo --squash",
        f"{_MERGE} -Rother/repo 5",
        f"{_MERGE} https://github.com/o/r/pull/77 --squash",
        f"{_MERGE} feature-x --squash",
    ])
    def test_target_not_a_session_repo_number_never_calls_gh(self, rig, cmd):
        _mint(rig, cmd)
        _post(rig, cmd + " 2>&1 | tail -3", "", state="MERGED")
        assert _gh_calls(rig) == []
        assert not _retired(rig)

    @pytest.mark.parametrize("cmd", [
        f"{_MERGE} 5 -R other/repo",
        f"{_MERGE} 5 --repo=other/repo --squash",
        f"{_MERGE} https://github.com/o/r/pull/77 --squash",
        f"{_MERGE} feature-x --squash",
        f"GH_REPO=o/other {_MERGE} 5 --squash",
        f"pushd {{repo}} && {_MERGE} 5 --squash",
        f"cd ~/other && {_MERGE} 5 --squash",
    ])
    def test_an_unpiped_run_retires_on_its_own_success(self, rig, cmd):
        """A failed command reaches PostToolUseFailure, which this hook is not
        registered on, so this event says the merge, the command's last leg,
        exited 0. The approval covers that one run, whatever the target."""
        repo = rig["tmp"] / "repo"
        repo.mkdir()
        cmd = cmd.format(repo=repo)
        _mint(rig, cmd)
        _post(rig, cmd, "", state="OPEN")
        assert _gh_calls(rig) == []
        assert _retired(rig)

    def test_an_implicit_target_retires_on_its_run_without_calling_gh(self, rig):
        """The current branch is read when the command runs, so no PR state can say
        which PR merged; the successful run itself retires the token."""
        cmd = f"{_MERGE} --squash"
        _mint(rig, cmd)
        _post(rig, cmd, "", state="MERGED")
        assert _gh_calls(rig) == []
        assert _retired(rig)

    @pytest.mark.parametrize("cmd", [
        f"cd ../other && {_MERGE} 5 --squash 2>&1 | tail -3",
        f"cd {{missing}} && {_MERGE} 5 --squash 2>&1 | tail -3",
        f"cd $HOME/other && {_MERGE} 5 --squash 2>&1 | tail -3",
        f"cd ~/other && {_MERGE} 5 --squash 2>&1 | tail -3",
        f"cd {{repo}}/* && {_MERGE} 5 --squash 2>&1 | tail -3",
        f"cd {{repo}}/../other && {_MERGE} 5 --squash 2>&1 | tail -3",
        f"cd {{repo}}; {_MERGE} 5 --squash 2>&1 | tail -3",
        f"cd {{repo}} && cd sub && {_MERGE} 5 --squash 2>&1 | tail -3",
        f"{_MERGE} 5 --squash 2>&1 | tail -3 && cd {{repo}} && true",
        f"pushd {{repo}} && {_MERGE} 5 --squash 2>&1 | tail -3",
        f"git -C ../other fetch && {_MERGE} 5 --squash 2>&1 | tail -3",
        f"GH_REPO=o/other {_MERGE} 5 --squash 2>&1 | tail -3",
        f"GH_HOST=ghe.example.com {_MERGE} 5 --squash 2>&1 | tail -3",
        f"GIT_DIR=../other/.git {_MERGE} 5 --squash 2>&1 | tail -3",
    ])
    def test_repo_redirect_never_calls_gh_and_retry_authorized(self, rig, cmd):
        """The state check would read PR 5 in a repo the command may not have
        targeted. Retiring on that PR's state could refuse the faithful retry, so
        every redirect other than one literal absolute `cd ... &&` is doubt."""
        repo = rig["tmp"] / "repo"
        repo.mkdir()
        (repo / "sub").mkdir()
        (rig["tmp"] / "other").mkdir()  # so only the `..` segment makes that path doubt
        cmd = cmd.format(repo=repo, missing=rig["tmp"] / "missing")
        bare = f"{_MERGE} 5 --squash"
        _mint(rig, bare)
        _post(rig, cmd, "X Pull request #5 is not mergeable", state="MERGED")
        assert _gh_calls(rig) == []
        assert not _retired(rig)
        assert check_merge_authorization(bare, token_dir=rig["config"]) is None


class TestLiteralAbsoluteCd:
    """`cd <worktree> && gh pr merge N ...` is the common lead shape. A single cd
    to a literal absolute existing directory is followed: the check runs there."""

    def test_merged_retires_with_check_in_target_dir(self, rig):
        repo = rig["tmp"] / "repo"
        repo.mkdir()
        cmd = f"cd {repo} && {_MERGE} 1659 --squash 2>&1 | tail -3"
        _mint(rig, f"{_MERGE} 1659 --squash")
        _post(rig, cmd, "", state="MERGED")
        assert _gh_calls(rig) == ["pr view 1659 --json state --jq .state"]
        assert _gh_cwds(rig) == [os.path.realpath(repo)]
        assert _retired(rig)

    def test_open_keeps_and_retry_authorized(self, rig):
        repo = rig["tmp"] / "repo"
        repo.mkdir()
        cmd = f"cd {repo} && {_MERGE} 1659 --squash 2>&1 | tail -3"
        bare = f"{_MERGE} 1659 --squash"
        _mint(rig, bare)
        _post(rig, cmd, "X Pull request #1659 is not mergeable", state="OPEN")
        assert _gh_cwds(rig) == [os.path.realpath(repo)]
        assert not _retired(rig)
        assert check_merge_authorization(bare, token_dir=rig["config"]) is None

    @pytest.mark.parametrize("shape", [
        "bash -c 'cd {repo} && gh \"$@\"' _ pr " + "merge 1659 --squash",
        "bash -c 'cd {repo} && {merge} 1659 --squash'",
    ])
    def test_cd_inside_a_shell_c_string_is_followed(self, rig, shape):
        repo = rig["tmp"] / "repo"
        repo.mkdir()
        cmd = shape.format(repo=repo, merge=_MERGE)
        _mint(rig, f"{_MERGE} 1659 --squash")
        _post(rig, cmd, "", state="MERGED")
        assert _gh_cwds(rig) == [os.path.realpath(repo)]
        assert _retired(rig)

    def test_a_repo_env_outside_the_shell_c_string_is_doubt(self, rig):
        repo = rig["tmp"] / "repo"
        repo.mkdir()
        cmd = f"GH_REPO=o/r bash -c 'cd {repo} && gh \"$@\" 2>&1 | tail -3' _ pr " + "merge 1659 --squash"
        _mint(rig, f"{_MERGE} 1659 --squash")
        _post(rig, cmd, "", state="MERGED")
        assert _gh_calls(rig) == []
        assert not _retired(rig)

    def test_no_cd_runs_in_hook_dir(self, rig):
        cmd = f"{_MERGE} 1656 --squash"
        _mint(rig, cmd)
        _post(rig, cmd, "", state="MERGED")
        assert _gh_cwds(rig) == [os.path.realpath(rig["tmp"])]

    def test_no_cd_runs_in_frame_cwd(self, rig):
        """The command ran in the session's shell directory, which the hook frame
        reports as `cwd`; an earlier Bash call may have moved it away from the
        hook's own directory."""
        session = rig["tmp"] / "session"
        session.mkdir()
        cmd = f"{_MERGE} 1656 --squash"
        _mint(rig, cmd)
        _post(rig, cmd, "", frame_cwd=str(session), state="MERGED")
        assert _gh_cwds(rig) == [os.path.realpath(session)]
        assert _retired(rig)

    @pytest.mark.parametrize("frame_cwd", ["missing", "rel", ""])
    def test_unusable_frame_cwd_runs_in_hook_dir(self, rig, frame_cwd):
        (rig["tmp"] / "rel").mkdir()  # exists relative to the hook's dir, still refused
        if frame_cwd == "missing":
            frame_cwd = str(rig["tmp"] / "missing")
        cmd = f"{_MERGE} 1656 --squash"
        _mint(rig, cmd)
        _post(rig, cmd, "", frame_cwd=frame_cwd, state="MERGED")
        assert _gh_cwds(rig) == [os.path.realpath(rig["tmp"])]

    def test_literal_cd_overrides_frame_cwd(self, rig):
        repo = rig["tmp"] / "repo"
        session = rig["tmp"] / "session"
        repo.mkdir()
        session.mkdir()
        cmd = f"cd {repo} && {_MERGE} 1656 --squash"
        _mint(rig, f"{_MERGE} 1656 --squash")
        _post(rig, cmd, "", frame_cwd=str(session), state="MERGED")
        assert _gh_cwds(rig) == [os.path.realpath(repo)]

    def test_missing_dir_is_doubt_before_gh(self, rig):
        """A missing directory is doubt in the hook itself, not only through gh
        failing to start there."""
        from merge_guard_post import _state_check_dir

        missing = rig["tmp"] / "missing"
        assert _state_check_dir(f"cd {missing} && {_MERGE} 5") == (False, None)
        assert _state_check_dir(f"cd {rig['tmp']} && {_MERGE} 5") == (True, str(rig["tmp"]))
