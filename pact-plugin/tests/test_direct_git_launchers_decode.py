"""Direct `subprocess.run(["git", ...])` readers must survive an undecodable path.

F1 fixed the four sites inside `shared/project_scope.py`. Three reader modules
run git themselves with `text=True`, so the same `UnicodeDecodeError` escaped
them -- and F1 makes them REACHABLE, because session_init now proceeds on a
path it used to die on early. Each arm drives a READER (not
`project_memory_root`) whose first-rung probe misses, so the git launcher runs:

  - `staleness._resolve_project_claude_md_with_base`'s `--git-common-dir`
    launcher (via `get_project_claude_md_path`);
  - `working_memory._resolve_display_claude_md_with_base`'s `--show-toplevel`
    launcher and `_get_claude_md_path`'s `--git-common-dir` launcher;
  - `memory_api.main_repo_root`'s `--git-common-dir` launcher.

The fixture's MAIN checkout directory name contains a byte that is not valid
UTF-8, so every one of those git outputs carries it. The worktree name is
plain ASCII, so the bad byte reaches the output only through the main path --
which is why the assertion is "resolves to MAIN", not "returns anything".

NOT ARMED: `working_memory`'s branch-3 `--git-common-dir` launcher gets the
same treatment, but the branch-2 arm above raises first on the pre-fix code,
so branch 3 has no independent red. Stated rather than implied.
"""
from __future__ import annotations

import os
import subprocess

import pytest

BAD_MAIN = os.fsdecode(b"main-\xff")


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.fixture
def bad_main_repo(tmp_path, monkeypatch):
    """MAIN's directory name is not valid UTF-8; the worktree's name is ASCII."""
    main = tmp_path / BAD_MAIN
    main.mkdir()
    assert _git("init", "-q", ".", cwd=main).returncode == 0
    (main / "README").write_text("seed\n")
    assert _git("add", "README", cwd=main).returncode == 0
    assert _git("commit", "-qm", "seed", cwd=main).returncode == 0
    worktree = main / ".worktrees" / "wt"
    added = _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    assert added.returncode == 0, added.stderr
    main_md = main / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text("# main\n", encoding="utf-8")

    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(worktree)
    return main, worktree, main_md


def test_the_staleness_reader_resolves_main(bad_main_repo):
    """Launcher: `staleness._resolve_project_claude_md_with_base`'s
    `git rev-parse --git-common-dir` (reached through
    `get_project_claude_md_path`, first-rung probe on the worktree misses)."""
    from staleness import get_project_claude_md_path

    main, worktree, main_md = bad_main_repo
    assert get_project_claude_md_path(worktree) == main_md


def test_the_working_memory_readers_resolve_main(bad_main_repo, monkeypatch):
    """Launchers: `--show-toplevel` in
    `_resolve_display_claude_md_with_base` and `--git-common-dir` in
    `_get_claude_md_path` (session-record rung stubbed to a miss so the git
    rungs run)."""
    from scripts import working_memory as wm

    main, _worktree, main_md = bad_main_repo
    monkeypatch.setattr(wm, "get_project_dir_from_session_record", lambda: None)

    found, base = wm._resolve_display_claude_md_with_base()
    assert found == main_md
    assert base == main
    assert wm._get_claude_md_path() == main_md


def test_the_memory_api_root_reader_resolves_main(bad_main_repo):
    """Launcher: `memory_api.main_repo_root`'s
    `git rev-parse --git-common-dir --start <worktree>`."""
    from scripts import memory_api

    main, worktree, _main_md = bad_main_repo
    assert memory_api.main_repo_root(str(worktree)).resolve() == main.resolve()
