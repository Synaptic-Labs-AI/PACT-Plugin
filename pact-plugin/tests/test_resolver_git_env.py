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

# claude_md_ambient (module-wide): git-anchor env handling is this module's
# subject; the suite-wide tmp redirect would pre-empt the branches under
# test. See tests/conftest.py.
pytestmark = pytest.mark.claude_md_ambient

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


def test_resolve_display_claude_md_with_base_ignores_an_inherited_GIT_DIR(
    tmp_path, monkeypatch, unrelated
):
    """The enclosing-checkout rung. Under an inherited GIT_DIR git reports
    the working directory as the top of the tree, so a subdirectory WITHOUT a
    document of its own would not reach the repository root's."""
    repo = _repo(tmp_path / "R")
    (repo / "sub").mkdir()
    monkeypatch.chdir(repo / "sub")
    _inherit_git_dir(monkeypatch, unrelated)

    found, base = wm._resolve_display_claude_md_with_base()

    assert _same(found, repo / ".claude" / "CLAUDE.md") and _same(base, repo), (
        f"the display resolver answered {found} (base {base}); its enclosing-"
        "checkout rung is reading the inherited GIT_DIR"
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


def test_the_session_reader_ignores_an_inherited_GIT_DIR(
    tmp_path, monkeypatch, unrelated
):
    repo = _repo(tmp_path / "R")
    monkeypatch.chdir(repo)
    _inherit_git_dir(monkeypatch, unrelated)

    found = staleness.resolve_session_claude_md_path()

    assert _same(found, repo / ".claude" / "CLAUDE.md"), (
        f"staleness resolved {found} from the inherited GIT_DIR"
    )


def test_main_repo_root_ignores_an_inherited_GIT_DIR(tmp_path, monkeypatch, unrelated):
    repo = _repo(tmp_path / "R")
    monkeypatch.chdir(repo)
    _inherit_git_dir(monkeypatch, unrelated)

    root = memory_api.main_repo_root()

    assert _same(root, repo), (
        f"main_repo_root answered {root} from the inherited GIT_DIR"
    )


# EVERY git call in the memory scripts is a resolver call: each one answers a
# question about "the repository this session is in", and an inherited GIT_DIR
# would answer it for another repository. The arm below therefore walks EVERY
# function of EVERY module in this directory, so a git call added anywhere
# there without the location-free environment fails it.
_SCRIPTS_DIR = "skills/pact-memory/scripts"
# Per-module floors: the git calls each module holds today. A scan that finds
# fewer is not reading the module it names (a parse or detection regression),
# so the floor fails it rather than letting an empty scan pass.
_GIT_CALL_FLOORS = {
    "memory_api.py": 4,
    "project_identity.py": 3,
}
_SUBPROCESS_CALLS = frozenset({"run", "check_output", "check_call", "call", "Popen"})


def _git_calls(function: ast.FunctionDef):
    """Yield (call, passes_location_free_env) for each `subprocess` git call
    (`run`, `check_output`, `check_call`, `call` or `Popen`).

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
                and node.func.attr in _SUBPROCESS_CALLS
                and isinstance(node.func.value, ast.Name)
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
            kw.arg == "env" and (
                _is_location_free_env_call(kw.value) or _is_key_filter_of_it(kw.value)
            )
            for kw in node.keywords
        )
        yield node, passes


def _is_location_free_env_call(node) -> bool:
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "git_env_without_location"
    )


def _is_key_filter_of_it(node) -> bool:
    """`{k: v for k, v in git_env_without_location().items() if k != "X"}`.

    Checked INLINE and by shape only: a pure key filter can drop variables
    but cannot add one back. A NAME is never accepted, because the scan
    cannot follow what happens to a variable between its assignment and the
    call (an `.update(os.environ)` would restore GIT_DIR).
    """
    if not (isinstance(node, ast.DictComp) and len(node.generators) == 1):
        return False
    generator = node.generators[0]
    target, source = generator.target, generator.iter
    if not (
        isinstance(target, ast.Tuple) and len(target.elts) == 2
        and all(isinstance(element, ast.Name) for element in target.elts)
        and isinstance(source, ast.Call) and not source.args
        and isinstance(source.func, ast.Attribute) and source.func.attr == "items"
        and _is_location_free_env_call(source.func.value)
    ):
        return False
    key, value = (element.id for element in target.elts)
    if not (
        isinstance(node.key, ast.Name) and node.key.id == key
        and isinstance(node.value, ast.Name) and node.value.id == value
    ):
        return False
    return all(
        isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
        and test.left.id == key and len(test.ops) == 1
        and isinstance(test.ops[0], (ast.NotEq, ast.NotIn))
        and isinstance(test.comparators[0], (ast.Constant, ast.Tuple, ast.Set))
        for test in generator.ifs
    )


def _function(source: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def _module_git_calls(source: str):
    """Every git call in a module, found function by function, each once.

    A call inside a nested function is reached from both functions' walks, so
    calls are keyed by their position.
    """
    seen = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for call, passes in _git_calls(node):
                seen[(call.lineno, call.col_offset)] = (node.name, call, passes)
    return list(seen.values())


def test_every_resolver_git_call_passes_the_location_free_env():
    """Guard arm: every git subprocess call in every memory script passes
    `env=git_env_without_location()` (or an inline key filter of it), whatever
    function it sits in and whether its argv is a list literal or a name
    assigned one. A git call added anywhere in that directory without the
    environment fails here."""
    missing, counts = [], {}
    for path in sorted((_PLUGIN / _SCRIPTS_DIR).glob("*.py")):
        calls = _module_git_calls(path.read_text(encoding="utf-8"))
        counts[path.name] = len(calls)
        missing += [
            f"{_SCRIPTS_DIR}/{path.name}::{name}:{call.lineno}"
            for name, call, passes in calls if not passes
        ]
    for name, floor in _GIT_CALL_FLOORS.items():
        assert counts.get(name, 0) >= floor, (
            f"{name}: found {counts.get(name, 0)} git calls, expected at least {floor}"
        )
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


def test_the_scan_accepts_only_an_inline_key_filter_of_the_location_free_env():
    """Live control: what the scan accepts besides the bare helper call.

    Only an inline comprehension that FILTERS keys out of
    `git_env_without_location()` passes. Every name-based form is refused,
    including one assigned from the helper, because a later `.update` or a
    rebinding would put GIT_DIR back without the scan seeing it."""
    source = textwrap.dedent("""
        def resolver():
            subprocess.run(["git", "a"], env={
                key: value for key, value in git_env_without_location().items()
                if key != "GIT_CONFIG"})
            subprocess.run(["git", "b"], env={
                k: v for k, v in git_env_without_location().items()})
            env = git_env_without_location()
            subprocess.run(["git", "c"], env=env)
            env = git_env_without_location()
            env.update(os.environ)
            subprocess.run(["git", "d"], env=env)
            env = git_env_without_location()
            env = dict(os.environ)
            subprocess.run(["git", "e"], env=env)
            subprocess.run(["git", "f"], env={
                k: v for k, v in os.environ.items() if k != "GIT_CONFIG"})
            subprocess.run(["git", "g"], env={
                k: "x" for k, v in git_env_without_location().items()})
            subprocess.run(["git", "h"], env={
                k: v for k, v in git_env_without_location().items() if v})
            subprocess.run(["git", "i"], env={
                **git_env_without_location(), "GIT_DIR": "/x"})
            subprocess.run(["git", "j"], env={
                k: v for k, v in git_env_without_location().items()
                for _ in range(1)})
    """)
    calls = list(_git_calls(_function(source, "resolver")))
    verdicts = {call.args[0].elts[1].value: passes for call, passes in calls}
    assert verdicts == {
        "a": True,   # filter out one key
        "b": True,   # no filter at all: a copy of the location-free env
        "c": False,  # a name, even one assigned from the helper
        "d": False,  # a name widened by .update after the helper
        "e": False,  # a name rebound after the helper
        "f": False,  # a filter over os.environ, not the helper
        "g": False,  # values rewritten
        "h": False,  # a filter on the value, not the key
        "i": False,  # a dict display adding a variable
        "j": False,  # a second generator
    }
