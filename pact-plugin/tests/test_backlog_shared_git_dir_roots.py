"""
Location: pact-plugin/tests/test_backlog_shared_git_dir_roots.py
Summary: A folder whose `.git` file names another folder's `.git` directory
(`git init --separate-git-dir=<A>/.git <B>`, or a hand-written file of that
shape) shares that git dir, so a backlog write from it keeps the worktrees
git lists for the repository, and no write from either checkout drops them.
Used by: pytest. Real repos, real git, `backlog.py` run as its own process.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BACKLOG = Path(__file__).resolve().parent.parent / "hooks" / "shared" / "backlog.py"


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def _commit(path):
    _git("-C", str(path), "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "c")


def _write(tmp_path, start, store):
    """`backlog.py add` from `start`, declared, and the roots of the one file."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_"))}
    env.update(HOME=str(tmp_path / "home"), CLAUDE_CONFIG_DIR=str(tmp_path / "home" / ".claude"),
               CLAUDE_PROJECT_DIR=str(start))
    result = subprocess.run(
        [sys.executable, str(BACKLOG), "--backlog-dir", str(store), "add", "x"],
        env=env, cwd=str(start), capture_output=True, text=True, timeout=60,
    )
    assert (result.returncode, result.stderr) == (0, ""), result.stderr
    (doc,) = [json.loads(f.read_text(encoding="utf-8")) for f in store.glob("*.json")]
    return doc["roots"]


def test_a_separate_git_dir_folder_keeps_the_listed_worktree_on_every_write(tmp_path):
    """`git init --separate-git-dir=<A>/.git <B>` with a worktree `wt-b`:
    git lists the main worktree at `A`. Writes from B, wt-b and B again each
    keep wt-b among the roots, so its sessions keep finding the file."""
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    a, b, wt = (tmp_path / "A").resolve(), (tmp_path / "B").resolve(), (tmp_path / "wt-b").resolve()
    a.mkdir()
    _git("init", "-q", f"--separate-git-dir={a / '.git'}", str(b))
    _commit(b)
    _git("-C", str(b), "worktree", "add", "-q", str(wt), "-b", "w")
    store = tmp_path / "store"
    store.mkdir()

    for start in (b, wt, b):
        roots = _write(tmp_path, start, store)
        assert str(wt) in roots, (start, roots)
        assert str(start) in roots or start == wt, (start, roots)


def test_a_folder_naming_a_checkouts_git_dir_keeps_its_worktree(tmp_path):
    """A folder whose `.git` file names `plain/.git` (hand-written; it reads
    as the separate-git-dir layout does, adversarial-only) writes plain's
    file and keeps plain's listed worktree `wt-a`, so plain's sessions in
    wt-a keep finding it."""
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    plain, wt = (tmp_path / "plain").resolve(), (tmp_path / "wt-a").resolve()
    _git("init", "-q", str(plain))
    _commit(plain)
    _git("-C", str(plain), "worktree", "add", "-q", str(wt), "-b", "w")
    folder = (tmp_path / "folder").resolve()
    folder.mkdir()
    (folder / ".git").write_text(f"gitdir: {plain / '.git'}\n")
    store = tmp_path / "store"
    store.mkdir()

    assert _write(tmp_path, plain, store) == [str(plain), str(wt)]
    roots = _write(tmp_path, folder, store)
    assert str(wt) in roots and str(plain) in roots, roots
