"""Real-git fixtures for the repository-identity read contract.

Consumed by the test_project_identity_* modules. These are direct-import
helpers, not pytest fixtures (the thin-conftest rule).

Why real repositories: the failure modes under test are integration-shaped
(what `git config` answers, what `rev-parse --git-common-dir` reports for a
linked worktree or a --separate-git-dir checkout). A mocked git would pass
while the seam it replaces is the one that is broken.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(*args: str, cwd: Path) -> str:
    """Run git in `cwd` and return stripped stdout; fail loudly on error."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed in {cwd}: {result.stderr!r}"
    )
    return result.stdout.strip()


def assert_outside_repository(path: Path) -> None:
    """Guard: the fixture root must not sit inside a repository.

    Mirrors the existing layout_root guard. Without it `detect_id_for_root`
    can answer from the suite's own checkout and an arm measures the harness.
    """
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0, (
        f"{path} sits inside a repository: {result.stdout!r}"
    )


def make_repo(
    path: Path,
    *,
    remote: str | None = None,
    upstream: str | None = None,
    commit: bool = True,
) -> Path:
    """A real repository, optionally carrying origin/upstream remotes.

    `remote` is written to `.git/config` by `git remote add`; no network is
    contacted (the identity code only reads config).
    """
    path.mkdir(parents=True, exist_ok=True)
    run_git("init", "-q", ".", cwd=path)
    run_git("config", "user.email", "identity-test@example.invalid", cwd=path)
    run_git("config", "user.name", "Identity Test", cwd=path)
    (path / "README").write_text("seed\n", encoding="utf-8")
    run_git("add", "README", cwd=path)
    if commit:
        run_git("commit", "-qm", "seed", cwd=path)
    if remote is not None:
        run_git("remote", "add", "origin", remote, cwd=path)
    if upstream is not None:
        run_git("remote", "add", "upstream", upstream, cwd=path)
    return path


def add_remote(repo: Path, url: str, name: str = "origin") -> None:
    run_git("remote", "add", name, url, cwd=repo)


def make_worktree(main: Path, path: Path, branch: str | None = None) -> Path:
    """A real linked worktree of `main`; its `.git` is a file, not a directory."""
    branch = branch or path.name
    run_git("worktree", "add", "-q", str(path), "-b", branch, cwd=main)
    return path


def common_dir(repo: Path) -> Path:
    """The common dir git reports for `repo`, resolved against `repo`."""
    raw = run_git("rev-parse", "--git-common-dir", cwd=repo)
    path = Path(raw)
    return path if path.is_absolute() else (repo / path).resolve()


def write_common_dir_override(repo: Path, value: str) -> Path:
    """Write `<git-common-dir>/pact-project-id` — the ONLY override location."""
    target = common_dir(repo) / "pact-project-id"
    target.write_text(value + "\n", encoding="utf-8")
    return target


def make_separate_git_dir_repo(path: Path, external_git_dir: Path) -> Path:
    """A real repo whose common dir lives outside the work tree."""
    path.mkdir(parents=True, exist_ok=True)
    external_git_dir.mkdir(parents=True, exist_ok=True)
    run_git("init", "-q", f"--separate-git-dir={external_git_dir}", ".", cwd=path)
    run_git("config", "user.email", "identity-test@example.invalid", cwd=path)
    run_git("config", "user.name", "Identity Test", cwd=path)
    (path / "README").write_text("seed\n", encoding="utf-8")
    run_git("add", "README", cwd=path)
    run_git("commit", "-qm", "seed", cwd=path)
    return path