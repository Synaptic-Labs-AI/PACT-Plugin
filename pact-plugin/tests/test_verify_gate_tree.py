"""Arms for `scripts/verify-gate-tree.sh`.

The script runs the test suite against a DECLARED tree and records which tree
it measured. It exists because a suite capture carried a count and not a tree,
so a run in a different checkout produced a clean summary that certified
nothing about the branch under review.

THE PRIMARY OBLIGATION HERE IS THE NON-REFUSAL SIDE. This script sits on a
merge path. A gate that refuses a faithful run is worse than one that misses a
wrong-tree run, because a noisy red teaches a reader to discount a red, and
that is how a true red goes unnoticed. So the faithful cases below are the
load-bearing arms, and the two refusal cases are the enhancement.

Each faithful case asserts the STAMPS were written, not the pytest exit code.
The temp repositories hold no tests, so pytest exits 5 (nothing collected) on
a faithful run. The discriminator between PROCEEDED and REFUSED is therefore
the capture file: a refusal happens before pytest starts and writes no capture.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
# One unconditional subject: an env override here would let an ambient value
# redirect every arm to a different script with no writer and no record.
_SCRIPT = _REPO_ROOT / "scripts" / "verify-gate-tree.sh"

_REFUSED = 2
# A green-but-unattributable run has its own code, distinct from the usage
# error and the red-pytest exit 1, and above pytest's own 0-5 range so no
# preserved pytest status can collide with it.
_UNATTRIBUTABLE = 6


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _make_repo(path: Path) -> Path:
    """Make a git repository with one commit. No tests in it, by design."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--quiet")
    _git(path, "config", "user.email", "arm@example.invalid")
    _git(path, "config", "user.name", "arm")
    (path / "marker.txt").write_text("content\n", encoding="utf-8")
    _git(path, "add", "marker.txt")
    _git(path, "commit", "--quiet", "-m", "seed")
    return path


def _child_env() -> dict[str, str]:
    """The caller's environment with this interpreter's directory first.

    The script starts its child as `python3`, so PATH decides which
    interpreter runs pytest. Resolving that from the test process's own
    interpreter keeps the arms runnable under a venv that is not activated,
    where a bare `python3` has no pytest.
    """
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        [os.path.dirname(sys.executable), env.get("PATH", "")])
    return env


def _run(declared: str, capture: str | Path,
         cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SCRIPT), declared, str(capture), "--collect-only", "-q",
         "-p", "no:randomly"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_child_env(),
    )


def _stamps(capture: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in capture.read_text(encoding="utf-8").splitlines():
        if line.startswith("GATE-"):
            key, _, value = line.partition(" ")
            out[key] = value
    return out


_BLOCKING_TEST = '''\
import os
import time
from pathlib import Path

import pytest


def test_blocking():
    ready = Path(os.environ["PACT_GATE_READY"])
    release = Path(os.environ["PACT_GATE_RELEASE"])
    ready.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 10
    while not release.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("parent did not release the blocking child")
        time.sleep(0.01)
    if os.environ.get("PACT_GATE_CHILD_FAIL") == "1":
        pytest.exit("intentional red child", returncode=7)
'''


def _make_blocking_repo(path: Path) -> Path:
    repo = _make_repo(path)
    (repo / "test_blocking.py").write_text(_BLOCKING_TEST, encoding="utf-8")
    _git(repo, "add", "test_blocking.py")
    _git(repo, "commit", "--quiet", "-m", "blocking child")
    return repo


def _run_with_mutation(repo: Path, tmp_path: Path, mutate,
                       child_fail: bool = False) -> tuple[
        subprocess.CompletedProcess, Path]:
    """Apply `mutate(repo)` only after the real pytest child is blocked."""
    ready = tmp_path / "child-ready"
    release = tmp_path / "child-release"
    capture = tmp_path / "capture.txt"
    env = _child_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PACT_GATE_READY"] = str(ready)
    env["PACT_GATE_RELEASE"] = str(release)
    if child_fail:
        env["PACT_GATE_CHILD_FAIL"] = "1"
    proc = subprocess.Popen(
        ["bash", str(_SCRIPT), str(repo), str(capture), "-q", "-p", "no:cacheprovider",
         "-p", "no:randomly"],
        cwd=str(repo), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and proc.poll() is None:
            if time.monotonic() > deadline:
                pytest.fail("blocking child never became ready")
            time.sleep(0.01)
        if not ready.exists():
            stdout, stderr = proc.communicate(timeout=5)
            pytest.fail(
                f"blocking child exited before ready (rc={proc.returncode})\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}\n"
                f"capture:\n{capture.read_text(encoding='utf-8')}"
            )
        before = capture.read_text(encoding="utf-8")
        assert "GATE-HEAD " in before
        assert "GATE-HEAD-AFTER" not in before, "child was not blocked mid-run"
        mutate(repo)
        release.write_text("release", encoding="utf-8")
        stdout, stderr = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        ready.unlink(missing_ok=True)
        release.unlink(missing_ok=True)
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, stdout=stdout, stderr=stderr), capture


class TestScriptIsPresent:
    def test_script_exists_and_is_executable(self):
        assert _SCRIPT.is_file(), f"missing: {_SCRIPT}"
        assert os.access(_SCRIPT, os.X_OK), f"not executable: {_SCRIPT}"


class TestFaithfulInvocationsAreNotRefused:
    """No faithful case may be refused. This is the cardinal obligation."""

    def test_declared_equals_actual_proceeds_and_stamps(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        capture = tmp_path / "capture.txt"

        result = _run(str(repo), capture, repo)

        assert result.returncode != _REFUSED, result.stderr
        assert capture.is_file(), "a faithful run must write a capture"
        stamps = _stamps(capture)
        assert set(stamps) >= {
            "GATE-TREE",
            "GATE-HEAD",
            "GATE-DIRTY-PATHS",
            "GATE-CWD",
            "GATE-COMMAND",
            "GATE-PYTEST-EXIT",
        }, stamps

    def test_stamped_head_is_the_real_head(self, tmp_path):
        """A constant in place of the real sha must not pass."""
        repo = _make_repo(tmp_path / "repo")
        capture = tmp_path / "capture.txt"

        _run(str(repo), capture, repo)

        assert _stamps(capture)["GATE-HEAD"] == _git(repo, "rev-parse", "HEAD")

    def test_stamped_dirty_count_tracks_the_tree(self, tmp_path):
        """A hardcoded zero must not pass. The count moves with the tree."""
        repo = _make_repo(tmp_path / "repo")
        clean_capture = tmp_path / "clean.txt"
        dirty_capture = tmp_path / "dirty.txt"

        _run(str(repo), clean_capture, repo)
        assert _stamps(clean_capture)["GATE-DIRTY-PATHS"] == "0"

        (repo / "marker.txt").write_text("changed\n", encoding="utf-8")
        _run(str(repo), dirty_capture, repo)
        assert _stamps(dirty_capture)["GATE-DIRTY-PATHS"] == "1"

    def test_trailing_slash_on_declared_path_proceeds(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        capture = tmp_path / "capture.txt"

        result = _run(str(repo) + "/", capture, repo)

        assert result.returncode != _REFUSED, result.stderr
        assert capture.is_file()

    def test_relative_declared_path_proceeds(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        sub = repo / "sub"
        sub.mkdir()
        capture = tmp_path / "capture.txt"

        result = _run("..", capture, sub)

        assert result.returncode != _REFUSED, result.stderr
        assert capture.is_file()

    def test_symlinked_spelling_of_one_tree_proceeds(self, tmp_path):
        """One tree spelled two ways must not read as two trees."""
        repo = _make_repo(tmp_path / "repo")
        link = tmp_path / "link"
        link.symlink_to(repo, target_is_directory=True)
        capture = tmp_path / "capture.txt"

        result = _run(str(link), capture, repo)

        assert result.returncode != _REFUSED, result.stderr
        assert capture.is_file()

    def test_absent_capture_directory_is_made_rather_than_refused(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        capture = tmp_path / "absent" / "deeper" / "capture.txt"

        result = _run(str(repo), capture, repo)

        assert result.returncode != _REFUSED, result.stderr
        assert capture.is_file()

    def test_detached_head_proceeds(self, tmp_path):
        """The continuous-integration checkout shape. A detached HEAD is faithful."""
        repo = _make_repo(tmp_path / "repo")
        _git(repo, "checkout", "--detach", "HEAD", "--quiet")
        capture = tmp_path / "capture.txt"

        result = _run(str(repo), capture, repo)

        assert result.returncode != _REFUSED, result.stderr
        assert capture.is_file()


class TestAfterRunTreeAttribution:
    """A green run counts only when its complete subject stayed unchanged."""

    @staticmethod
    def _tracked(repo):
        (repo / "marker.txt").write_text("tracked mutation\n", encoding="utf-8")

    @staticmethod
    def _move_head(repo):
        TestAfterRunTreeAttribution._tracked(repo)
        _git(repo, "add", "marker.txt")
        _git(repo, "commit", "--quiet", "-m", "move HEAD")

    def test_stable_green_run_is_attributable(self, tmp_path):
        repo = _make_blocking_repo(tmp_path / "repo")

        result, capture = _run_with_mutation(repo, tmp_path, lambda repo: None)

        assert result.returncode == 0, result.stderr
        stamps = _stamps(capture)
        assert stamps["GATE-TREE-ATTRIBUTION"] == "stable"
        assert stamps["GATE-HEAD-AFTER"] == stamps["GATE-HEAD"]
        assert stamps["GATE-DIRTY-PATHS-AFTER"] == "0"
        assert capture.read_text(encoding="utf-8").splitlines()[-1] == (
            "GATE-PYTEST-EXIT 0")

    def test_preexisting_dirt_can_stay_stable(self, tmp_path):
        repo = _make_blocking_repo(tmp_path / "repo")
        (repo / "marker.txt").write_text("already dirty\n", encoding="utf-8")

        result, capture = _run_with_mutation(repo, tmp_path, lambda repo: None)

        assert result.returncode == 0
        stamps = _stamps(capture)
        assert stamps["GATE-DIRTY-PATHS"] == "1"
        assert stamps["GATE-DIRTY-PATHS-AFTER"] == "1"
        assert stamps["GATE-TREE-ATTRIBUTION"] == "stable"

    @pytest.mark.parametrize("kind", ["tracked", "untracked", "head"])
    def test_green_run_with_movement_is_unattributable(self, tmp_path, kind):
        repo = _make_blocking_repo(tmp_path / "repo")
        original_head = _git(repo, "rev-parse", "HEAD")

        def mutate(repo):
            if kind == "tracked":
                self._tracked(repo)
            elif kind == "untracked":
                (repo / "new-untracked.txt").write_text("new\n", encoding="utf-8")
            else:
                self._move_head(repo)

        result, capture = _run_with_mutation(repo, tmp_path, mutate)

        assert result.returncode == _UNATTRIBUTABLE, result.stderr
        stamps = _stamps(capture)
        assert stamps["GATE-PYTEST-EXIT"] == "0"
        assert stamps["GATE-TREE-ATTRIBUTION"] == "unattributable"
        assert stamps["GATE-HEAD-AFTER"] == _git(repo, "rev-parse", "HEAD")
        if kind == "head":
            assert stamps["GATE-HEAD"] == original_head
            assert stamps["GATE-HEAD-AFTER"] != original_head

    def test_equal_dirty_counts_do_not_hide_a_changed_population(self, tmp_path):
        repo = _make_blocking_repo(tmp_path / "repo")
        (repo / "before.txt").write_text("before\n", encoding="utf-8")

        def swap_paths(repo):
            (repo / "before.txt").unlink()
            (repo / "after.txt").write_text("after\n", encoding="utf-8")

        result, capture = _run_with_mutation(repo, tmp_path, swap_paths)

        assert result.returncode == _UNATTRIBUTABLE
        stamps = _stamps(capture)
        assert stamps["GATE-DIRTY-PATHS"] == "1"
        assert stamps["GATE-DIRTY-PATHS-AFTER"] == "1"
        assert stamps["GATE-TREE-ATTRIBUTION"] == "unattributable"

    def test_red_child_status_is_preserved_when_the_tree_moves(self, tmp_path):
        repo = _make_blocking_repo(tmp_path / "repo")

        result, capture = _run_with_mutation(
            repo, tmp_path, self._tracked, child_fail=True)

        assert result.returncode == 7, result.stderr
        stamps = _stamps(capture)
        assert stamps["GATE-PYTEST-EXIT"] == "7"
        assert stamps["GATE-TREE-ATTRIBUTION"] == "unattributable"
        assert capture.read_text(encoding="utf-8").splitlines()[-1] == (
            "GATE-PYTEST-EXIT 7")

    def test_git_config_cannot_hide_a_midrun_untracked_file(self, tmp_path):
        """status.showUntrackedFiles=no must not disarm the guard."""
        repo = _make_blocking_repo(tmp_path / "repo")
        _git(repo, "config", "status.showUntrackedFiles", "no")

        def create_untracked(repo):
            (repo / "hidden-untracked.txt").write_text("x", encoding="utf-8")

        result, capture = _run_with_mutation(repo, tmp_path, create_untracked)

        assert result.returncode == _UNATTRIBUTABLE, result.stderr
        stamps = _stamps(capture)
        assert stamps["GATE-PYTEST-EXIT"] == "0"
        assert stamps["GATE-DIRTY-PATHS-AFTER"] == "1"
        assert stamps["GATE-TREE-ATTRIBUTION"] == "unattributable"

    def test_unreadable_after_status_reports_unknown_not_zero(self, tmp_path):
        """A failed after-run status is not evidence of a clean tree."""
        repo = _make_blocking_repo(tmp_path / "repo")

        def corrupt_index(repo):
            (repo / ".git" / "index").write_bytes(b"garbage")

        result, capture = _run_with_mutation(repo, tmp_path, corrupt_index)

        assert result.returncode == _UNATTRIBUTABLE, result.stderr
        stamps = _stamps(capture)
        assert stamps["GATE-PYTEST-EXIT"] == "0"
        assert stamps["GATE-TREE-ATTRIBUTION"] == "unattributable"
        assert stamps["GATE-DIRTY-PATHS-AFTER"] == "unknown"


class TestAmbientPytestAddoptsCannotNarrowTheRun:
    """The stamp records argv only, so the wrapper clears the env channel."""

    def test_ambient_addopts_cannot_deselect_a_failing_test(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        (repo / "test_two.py").write_text(
            "def test_ok():\n    assert True\n\n"
            "def test_red():\n    assert False\n", encoding="utf-8")
        _git(repo, "add", "test_two.py")
        _git(repo, "commit", "--quiet", "-m", "one pass, one failure")
        capture = tmp_path / "capture.txt"
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTEST_ADDOPTS"] = "-k test_ok"

        result = subprocess.run(
            ["bash", str(_SCRIPT), str(repo), str(capture),
             "-q", "-p", "no:cacheprovider", "-p", "no:randomly"],
            cwd=str(repo), env=env, capture_output=True, text=True)

        assert result.returncode == 1, result.stderr
        stamps = _stamps(capture)
        assert stamps["GATE-PYTEST-EXIT"] == "1"
        assert "deselected" not in capture.read_text(encoding="utf-8")


class TestCapturePathContainment:
    @pytest.mark.parametrize("relative", ["capture.txt", "nested/missing/capture.txt"])
    def test_in_tree_capture_is_refused_before_pytest(self, tmp_path, relative):
        repo = _make_repo(tmp_path / "repo")
        capture = repo / relative

        result = _run(str(repo), capture, repo)

        assert result.returncode == _REFUSED, result.stdout
        assert "capture path must be outside the declared tree" in result.stderr
        assert not capture.exists()
        assert not (repo / "nested").exists()

    def test_relative_in_tree_capture_is_refused(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")

        result = _run(str(repo), "capture.txt", repo)

        assert result.returncode == _REFUSED, result.stdout
        assert "capture path must be outside the declared tree" in result.stderr
        assert "GATE-PYTEST-EXIT" not in result.stdout + result.stderr
        assert not (repo / "capture.txt").exists()

    def test_symlink_target_inside_tree_is_refused(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        capture = tmp_path / "capture-link"
        capture.symlink_to(repo / "capture.txt")

        result = _run(str(repo), capture, repo)

        assert result.returncode == _REFUSED
        assert "capture path must be outside the declared tree" in result.stderr
        assert not (repo / "capture.txt").exists()

    def test_in_tree_symlink_to_outside_capture_proceeds(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        capture = tmp_path / "outside.txt"
        link = repo / "capture-link"
        link.symlink_to(capture)

        result = _run(str(repo), link, repo)

        assert result.returncode != _REFUSED, result.stderr
        assert capture.is_file()
        assert _stamps(capture)["GATE-TREE-ATTRIBUTION"] == "stable"


class TestTempFileAllocationFailureIsARefusal:
    """A `mktemp` failure happens before pytest and writes no capture.

    The canary states that a refusal exits 2; the temporary-file arms used to
    print `GATE REFUSED` and exit 1, which is also the code for a red pytest.
    The exit code is the contract.
    """

    def test_unwritable_tempdir_refuses_with_exit_2_and_no_capture(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        capture = tmp_path / "capture.txt"
        env = _child_env()
        env["TMPDIR"] = str(tmp_path / "missing-tmpdir")

        result = subprocess.run(
            ["bash", str(_SCRIPT), str(repo), str(capture), "-q",
             "-p", "no:randomly"],
            cwd=str(repo), env=env, capture_output=True, text=True)

        assert result.returncode == _REFUSED, result.stderr
        assert "cannot allocate the before-run porcelain capture" in result.stderr
        assert not capture.exists()


class TestUnusableInterpreterIsRefused:
    """The capture containment check runs through python3.

    An empty `capture_real` matches no containment pattern, so an interpreter
    that fails, or prints nothing, must refuse before pytest. Otherwise the
    check is silently disarmed while the run proceeds.
    """

    def _run_with_python3(self, tmp_path, stub_body: str):
        repo = _make_repo(tmp_path / "repo")
        capture = tmp_path / "capture.txt"
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        stub = fake_bin / "python3"
        stub.write_text(stub_body, encoding="utf-8")
        stub.chmod(0o755)
        env = _child_env()
        env["PATH"] = os.pathsep.join([str(fake_bin), env["PATH"]])
        result = subprocess.run(
            ["bash", str(_SCRIPT), str(repo), str(capture),
             "--collect-only", "-q", "-p", "no:randomly"],
            cwd=str(repo), env=env, capture_output=True, text=True)
        return result, capture

    def test_a_failing_python3_refuses_with_exit_2_and_no_capture(self, tmp_path):
        result, capture = self._run_with_python3(tmp_path, "#!/bin/sh\nexit 1\n")

        assert result.returncode == _REFUSED, result.stderr
        assert "could not resolve the capture path" in result.stderr
        assert not capture.exists()

    def test_a_python3_that_prints_no_path_refuses(self, tmp_path):
        result, capture = self._run_with_python3(tmp_path, "#!/bin/sh\nexit 0\n")

        assert result.returncode == _REFUSED, result.stderr
        assert "could not resolve the capture path" in result.stderr
        assert not capture.exists()


class TestWrongTreeIsRefused:
    """The catching leg. It must refuse BEFORE pytest starts."""

    def test_a_different_tree_is_refused_with_no_capture(self, tmp_path):
        declared = _make_repo(tmp_path / "declared")
        other = _make_repo(tmp_path / "other")
        capture = tmp_path / "capture.txt"

        result = _run(str(declared), capture, other)

        assert result.returncode == _REFUSED, result.stdout
        assert not capture.exists(), "a refusal must not leave a capture behind"
        assert "GATE REFUSED" in result.stderr
        assert str(declared.resolve()) in result.stderr
        assert str(other.resolve()) in result.stderr

    def test_a_non_repository_tree_is_refused_with_a_route(self, tmp_path):
        """A refusal with no route is a dead end, and a dead end retires the gate."""
        declared = _make_repo(tmp_path / "declared")
        export = tmp_path / "export"
        export.mkdir()
        capture = tmp_path / "capture.txt"

        result = _run(str(declared), capture, export)

        assert result.returncode == _REFUSED, result.stdout
        assert not capture.exists()
        assert "python3 -m pytest" in result.stderr, (
            "the refusal must name what to run instead"
        )

    def test_a_declared_path_that_is_not_a_directory_is_refused(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        capture = tmp_path / "capture.txt"

        result = _run(str(tmp_path / "no-such-path"), capture, repo)

        assert result.returncode == _REFUSED, result.stdout
        assert not capture.exists()

    def test_a_tree_with_no_commit_is_refused_with_a_named_message(
            self, tmp_path):
        """An unborn branch names no commit, so there is nothing to stamp.

        Without the refusal the run proceeded, stamped an empty `GATE-HEAD`,
        and reported a full green run as unattributable after the fact — a
        late, confusing stand-in for a plain "this tree has no commit".
        """
        repo = tmp_path / "unborn"
        repo.mkdir()
        _git(repo, "init", "--quiet")
        _git(repo, "config", "user.email", "arm@example.invalid")
        _git(repo, "config", "user.name", "arm")
        capture = tmp_path / "capture.txt"

        result = _run(str(repo), capture, repo)

        assert result.returncode == _REFUSED, result.stderr
        assert "no commit at HEAD" in result.stderr, result.stderr
        assert not capture.exists(), "a refusal must not leave a capture behind"


class TestUsage:
    @pytest.mark.parametrize("argv", [[], ["only-one"]])
    def test_too_few_arguments_reports_usage(self, argv):
        result = subprocess.run(
            ["bash", str(_SCRIPT), *argv],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Usage:" in result.stderr
