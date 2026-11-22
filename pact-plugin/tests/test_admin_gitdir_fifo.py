"""
Location: pact-plugin/tests/test_admin_gitdir_fifo.py
Summary: A start inside a linked worktree's admin dir whose `gitdir` file is
a FIFO: the memory CLI and the project-memory root answer without blocking,
because every admin `gitdir` read goes through the bounded,
regular-file-only reader.
Used by: pytest. Real repos, real git, each read in a child process with a
hard time limit.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parent.parent / "skills" / "pact-memory" / "scripts" / "cli.py"
HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def _git(*args, cwd=None):
    subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
                   cwd=None if cwd is None else str(cwd), check=True, capture_output=True)


def _admin_dir(tmp_path, kind):
    main = tmp_path / "main"
    _git("init", "-q", str(main))
    _git("commit", "-q", "--allow-empty", "-m", "c", cwd=main)
    _git("worktree", "add", "-q", str(tmp_path / "wt"), "-b", "w", cwd=main)
    admin = main / ".git" / "worktrees" / "wt"
    if kind == "fifo":
        (admin / "gitdir").unlink()
        os.mkfifo(admin / "gitdir")
    return admin


def _env(tmp_path):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "GIT_", "PACT_", "PYTEST_"))}
    (tmp_path / "home" / ".claude").mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(tmp_path / "home"), CLAUDE_CONFIG_DIR=str(tmp_path / "home" / ".claude"),
               PACT_TEST_MEMORY_DIR=str(tmp_path / "mem"))
    return env


@pytest.mark.parametrize("kind", ["regular", "fifo"])
def test_project_id_show_from_an_admin_dir_does_not_block(tmp_path, kind):
    """`project-id show` from inside the admin dir answers (the regular
    file is the control that the route reads it) instead of hanging."""
    admin = _admin_dir(tmp_path, kind)
    result = subprocess.run([sys.executable, str(CLI), "project-id", "show"], cwd=str(admin),
                            env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("kind", ["regular", "fifo"])
def test_the_project_memory_root_from_an_admin_dir_does_not_block(tmp_path, kind):
    """The project-memory root of the admin dir: the main checkout through a
    regular `gitdir` file, and an answer (not a hang) through a FIFO."""
    admin = _admin_dir(tmp_path, kind)
    code = (
        "import sys;"
        "from shared.project_scope import memory_root_of;"
        "print(memory_root_of(sys.argv[1])[0])"
    )
    env = {**_env(tmp_path), "PYTHONPATH": str(HOOKS)}
    result = subprocess.run([sys.executable, "-c", code, str(admin)],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    expected = str((tmp_path / "main").resolve()) if kind == "regular" else "None"
    assert result.stdout.strip() == expected, result.stdout
