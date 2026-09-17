"""
Location: pact-plugin/tests/test_resolver_git_env.py
Summary: The resolvers that pick a CLAUDE.md or a project root run git without
         an inherited GIT_DIR, GIT_WORK_TREE or GIT_COMMON_DIR -- the same
         environment the memory write guard uses. An inherited GIT_DIR (a git
         hook exports one for its own repository) otherwise makes git answer
         from that repository whatever the working directory is.
Used by: pytest.
"""
import ast
import subprocess
import textwrap
from pathlib import Path

import pytest

import scripts.memory_api as memory_api  # noqa: E402
import scripts.working_memory as wm  # noqa: E402
import staleness  # noqa: E402

_PLUGIN = Path(__file__).resolve().parent.parent
_SEED = "# Project Memory\n\n## Working Memory\n"
_IDENTITY = ("-c", "user.email=t@example.invalid", "-c", "user.name=t")


def _git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"


def _repo(path: Path, document: bool = True) -> Path:
    path.mkdir(parents=True)
    _git("init", "-q", ".", cwd=path)
    (path / "README").write_text("seed\n", encoding="utf-8")
    _git("add", "README", cwd=path)
    _git(*_IDENTITY, "commit", "-qm", "seed", cwd=path)
    if document:
        _document(path)
    return path


def _document(directory: Path) -> Path:
    target = directory / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_SEED, encoding="utf-8")
    return target


@pytest.fixture
def unrelated(tmp_path, monkeypatch):
    """An unrelated repository U with its own CLAUDE.md, and nothing declared.

    With CLAUDE_PROJECT_DIR unset and the session record unreachable in a test
    process, every resolver below reaches its git branch.
    """
    probe = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "--git-dir"],
                           capture_output=True, text=True, timeout=30)
    assert probe.returncode != 0, f"tmp_path sits inside a repository: {probe.stdout}"
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return _repo(tmp_path / "U")


def _inherit_git_dir(monkeypatch, unrelated: Path) -> None:
    # Set only after every repository is built: the fixture's own git calls
    # must not run under it.
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))


def _same(a, b) -> bool:
    return a is not None and Path(a).resolve() == Path(b).resolve()


def test_get_claude_md_path_ignores_an_inherited_GIT_DIR(tmp_path, monkeypatch, unrelated):
    repo = _repo(tmp_path / "R")
    monkeypatch.chdir(repo)
    _inherit_git_dir(monkeypatch, unrelated)

    found = wm._get_claude_md_path()

    assert _same(found, repo / ".claude" / "CLAUDE.md"), (
        f"_get_claude_md_path answered {found} from the inherited GIT_DIR, "
        "not the repository the process runs in"
    )


def test_resolve_display_claude_md_with_base_ignores_an_inherited_GIT_DIR(
    tmp_path, monkeypatch, unrelated
):
    """The `--show-toplevel` call. Under an inherited GIT_DIR git reports the
    working directory as the top of the tree, so a subdirectory holding its own
    CLAUDE.md is answered instead of the repository root."""
    repo = _repo(tmp_path / "R")
    _document(repo / "sub")
    monkeypatch.chdir(repo / "sub")
    _inherit_git_dir(monkeypatch, unrelated)

    found, base = wm._resolve_display_claude_md_with_base()

    assert _same(found, repo / ".claude" / "CLAUDE.md") and _same(base, repo), (
        f"the display resolver answered {found} (base {base}); its --show-toplevel "
        "call is reading the inherited GIT_DIR"
    )


def test_resolve_display_claude_md_with_base_common_dir_call_ignores_an_inherited_GIT_DIR(
    tmp_path, monkeypatch, unrelated
):
    """The `--git-common-dir` call, reached from a linked worktree with no
    CLAUDE.md of its own, which must land on the main checkout's file."""
    main = _repo(tmp_path / "M")
    worktree = tmp_path / "W"
    _git("worktree", "add", "-q", str(worktree), "-b", "w", cwd=main)
    monkeypatch.chdir(worktree)
    _inherit_git_dir(monkeypatch, unrelated)

    found, base = wm._resolve_display_claude_md_with_base()

    assert _same(found, main / ".claude" / "CLAUDE.md") and _same(base, main), (
        f"the display resolver answered {found} (base {base}); its --git-common-dir "
        "call is reading the inherited GIT_DIR"
    )


def test_resolve_project_claude_md_with_base_ignores_an_inherited_GIT_DIR(
    tmp_path, monkeypatch, unrelated
):
    repo = _repo(tmp_path / "R")
    monkeypatch.chdir(repo)
    _inherit_git_dir(monkeypatch, unrelated)

    found, base = staleness._resolve_project_claude_md_with_base()

    assert _same(found, repo / ".claude" / "CLAUDE.md") and _same(base, repo), (
        f"staleness resolved {found} (base {base}) from the inherited GIT_DIR"
    )


def test_main_repo_root_ignores_an_inherited_GIT_DIR(tmp_path, monkeypatch, unrelated):
    repo = _repo(tmp_path / "R")
    monkeypatch.chdir(repo)
    _inherit_git_dir(monkeypatch, unrelated)

    root = memory_api.main_repo_root()

    assert _same(root, repo), (
        f"main_repo_root answered {root} from the inherited GIT_DIR"
    )


# (file, function) -> the number of git subprocess calls it holds. A scan that
# finds fewer is not reading the function it names.
_RESOLVER_GIT_CALLS = {
    ("skills/pact-memory/scripts/working_memory.py", "_get_claude_md_path"): 1,
    ("skills/pact-memory/scripts/working_memory.py", "_resolve_display_claude_md_with_base"): 2,
    ("hooks/staleness.py", "_resolve_project_claude_md_with_base"): 1,
    ("skills/pact-memory/scripts/memory_api.py", "main_repo_root"): 1,
}


def _git_calls(function: ast.FunctionDef):
    """Yield (call, passes_location_free_env) for each `subprocess.run` git call.

    The argv counts as git when it is a list literal starting with "git", or a
    name the function assigned a list literal starting with "git".
    """
    git_names = {
        target.id
        for node in ast.walk(function)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List)
        and node.value.elts and isinstance(node.value.elts[0], ast.Constant)
        and node.value.elts[0].value == "git"
        for target in node.targets if isinstance(target, ast.Name)
    }
    for node in ast.walk(function):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess" and node.args):
            continue
        argv = node.args[0]
        is_git = (
            isinstance(argv, ast.List) and argv.elts
            and isinstance(argv.elts[0], ast.Constant) and argv.elts[0].value == "git"
        ) or (isinstance(argv, ast.Name) and argv.id in git_names)
        if not is_git:
            continue
        passes = any(
            kw.arg == "env" and isinstance(kw.value, ast.Call)
            and isinstance(kw.value.func, ast.Name)
            and kw.value.func.id == "git_env_without_location"
            for kw in node.keywords
        )
        yield node, passes


def _function(source: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def test_every_resolver_git_call_passes_the_location_free_env():
    """Guard arm: a later git call added to a resolver without the environment
    fails here, whichever form its argv takes."""
    missing = []
    for (rel, name), floor in _RESOLVER_GIT_CALLS.items():
        calls = list(_git_calls(_function((_PLUGIN / rel).read_text(encoding="utf-8"), name)))
        assert len(calls) >= floor, (
            f"{rel}::{name}: found {len(calls)} git calls, expected at least {floor}"
        )
        missing += [f"{rel}::{name}:{call.lineno}" for call, passes in calls if not passes]
    assert missing == [], f"git calls without env=git_env_without_location(): {missing}"


def test_the_scan_sees_both_argv_forms_and_a_missing_env():
    """Live control for the guard arm above."""
    source = textwrap.dedent("""
        def resolver():
            subprocess.run(["git", "rev-parse"], env=git_env_without_location())
            command = ["git"]
            command += ["rev-parse"]
            subprocess.run(command)
            subprocess.run(["ls"])
    """)
    calls = list(_git_calls(_function(source, "resolver")))
    assert [passes for _call, passes in calls] == [True, False]
