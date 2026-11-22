"""
Location: pact-plugin/tests/test_backlog_git_dir_start.py
Summary: The backlog CLI run from inside a checkout's git dir files in that
checkout's backlog file, as the memory skill's resolver does for the same
start; a git dir with no checkout (a bare repository, a separate-git-dir
store) still refuses and writes nothing.
Used by: pytest. Real repos, real git, `backlog.py` run as its own process.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BACKLOG = Path(__file__).resolve().parent.parent / "hooks" / "shared" / "backlog.py"


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def _repo(path: Path) -> Path:
    _git("init", "-q", str(path))
    _git("-C", str(path), "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "c")
    return path.resolve()


def _logging_git(tmp_path: Path) -> "tuple[Path, Path]":
    """A `git` first on PATH that appends each command's resolved directory
    and arguments to a log, one line each, then runs the real git."""
    log = tmp_path / "git.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        "question=$(where=$(pwd -P)\n"
        "  while [ $# -ge 2 ] && [ \"$1\" = -C ]; do\n"
        "    case \"$2\" in /*) next=$2 ;; *) next=$where/$2 ;; esac\n"
        "    where=$(cd -P \"$next\" 2>/dev/null && pwd -P || printf '%s' \"$next\")\n"
        "    shift 2\n"
        "  done\n"
        "  printf '%s\\t%s' \"$where\" \"$*\")\n"
        f"printf '%s\\n' \"$question\" >> \"{log}\"\n"
        f"exec \"{shutil.which('git')}\" \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return bin_dir, log


def _run(start: Path, store: Path, tmp_path: Path, declared: bool, *command):
    """One backlog command as its own process from `start`, with the logging
    git; returns (result, the git questions it asked)."""
    bin_dir, log = _logging_git(Path(tempfile.mkdtemp(dir=tmp_path, prefix="git-")))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "GIT_"))}
    env["HOME"] = str(tmp_path / "home")
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "home" / ".claude")
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    if declared:
        env["CLAUDE_PROJECT_DIR"] = str(start)
    result = subprocess.run(
        [sys.executable, str(BACKLOG), "--backlog-dir", str(store), *command],
        env=env, cwd=str(start), capture_output=True, text=True, timeout=60,
    )
    questions = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, questions


def _repeated(questions):
    return sorted({q for q in questions if questions.count(q) > 1})


@pytest.fixture
def layout(tmp_path):
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    main = _repo(tmp_path / "main")
    _git("-C", str(main), "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "w")
    store = tmp_path / "store"
    store.mkdir()
    return main, store


INSIDE = [".git", ".git/hooks", ".git/objects", ".git/worktrees/wt"]


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
@pytest.mark.parametrize("inside", INSIDE)
def test_a_start_inside_a_checkouts_git_dir_files_in_that_checkouts_backlog(
    tmp_path, layout, inside, declared,
):
    """`add`, `show` and `set` from inside `main`'s git dir (the dir itself,
    a folder in it, a linked worktree's admin dir) file in `main.json` with
    project_path `main`, as from `main`, and no command asks git one
    question twice."""
    main, store = layout
    start = main / inside

    added, asked_add = _run(start, store, tmp_path, declared, "add", "x")
    assert (added.returncode, added.stderr) == (0, ""), added.stderr
    files = {f.name: json.loads(f.read_text(encoding="utf-8")) for f in store.glob("*.json")}
    assert list(files) == ["main.json"]
    doc = files["main.json"]
    assert doc["project_path"] == str(main)
    (item,) = doc["items"]

    shown, asked_show = _run(start, store, tmp_path, declared, "show")
    assert shown.returncode == 0, shown.stderr
    assert item["id"] in shown.stdout

    changed, asked_set = _run(start, store, tmp_path, declared, "set", item["id"], "--status", "done")
    assert changed.returncode == 0, changed.stderr
    doc = json.loads((store / "main.json").read_text(encoding="utf-8"))
    assert [i["status"] for i in doc["items"]] == ["done"]
    assert sorted(f.name for f in store.glob("*.json")) == ["main.json"]

    assert asked_add and asked_show and asked_set, "control: the logging git ran"
    assert (_repeated(asked_add), _repeated(asked_show), _repeated(asked_set)) == ([], [], [])


@pytest.mark.parametrize("command", [("add", "x"), ("show",), ("set", "abcd", "--status", "done")],
                         ids=["add", "show", "set"])
@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
@pytest.mark.parametrize("kind", ["bare.git", "store.git"])
def test_a_git_dir_with_no_checkout_still_refuses(tmp_path, kind, declared, command):
    """A bare repository and a separate-git-dir store have no checkout to
    name, so `add`, `show` and `set` from inside either refuse (65, the
    root check's refusal) and write nothing."""
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    git_dir = tmp_path / kind
    if kind == "bare.git":
        _git("init", "-q", "--bare", str(git_dir))
    else:
        _git("init", "-q", f"--separate-git-dir={git_dir}", str(tmp_path / "folder"))
    store = tmp_path / "store"
    store.mkdir()

    result, asked = _run(git_dir, store, tmp_path, declared, *command)

    assert asked, "control: the logging git ran"
    assert result.returncode == 65, (result.returncode, result.stderr)
    assert "root" in result.stderr
    assert list(store.iterdir()) == []
