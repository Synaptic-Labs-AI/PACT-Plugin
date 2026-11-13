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

from fixtures.identity_git import bindings as shared_bindings
from fixtures.identity_git import (
    call_argument,
    import_aliases,
    positional_constructions,
    qualified_name,
    reason_dict_sites,
    scopes,
)

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


def _program(node, bindings: dict, seen: frozenset = frozenset()):
    """The program an argv expression runs, or None when it cannot be
    resolved statically.

    A string names its first word (the `shell=True` form). A list or tuple
    names its first element, and a starred first element names whatever its
    operand names. A `+` concatenation names what its left operand names, or
    its right operand when the left is an empty literal. A name names what
    its bindings name, followed through aliases to a fixed point: any
    binding that names git makes the name git, and any binding that cannot
    be resolved makes the name unresolvable. `sys.executable` names the
    Python interpreter. Anything else (a call, a subscript, an imported
    attribute) cannot be resolved.
    """
    if isinstance(node, ast.Constant):
        words = node.value.split() if isinstance(node.value, str) else []
        return words[0] if words else None
    if isinstance(node, (ast.List, ast.Tuple)):
        if not node.elts:
            return None
        first = node.elts[0]
        if isinstance(first, ast.Starred):
            return _program(first.value, bindings, seen)
        return _program(first, bindings, seen)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = node.left
        if isinstance(left, (ast.List, ast.Tuple)) and not left.elts:
            return _program(node.right, bindings, seen)
        return _program(left, bindings, seen)
    if isinstance(node, ast.Name):
        if node.id in seen or node.id not in bindings:
            return None
        names = {_program(value, bindings, seen | {node.id}) for value in bindings[node.id]}
        if "git" in names:
            return "git"
        if None in names:
            return None
        return min(names)
    if (
        isinstance(node, ast.Attribute) and node.attr == "executable"
        and isinstance(node.value, ast.Name) and node.value.id == "sys"
    ):
        return "python"
    return None


def _git_calls(scope, scope_bindings: dict = None, aliases: dict = None):
    """Yield (call, passes_location_free_env) for each `subprocess` call
    (`run`, `check_output`, `check_call`, `call` or `Popen`) whose argv runs
    git OR cannot be resolved statically.

    `scope` is a function node or a list of nodes (one scope from
    `fixtures.identity_git.scopes`, module-level code included). The call is
    recognised through the module's imports (`import subprocess as sp`,
    `from subprocess import run`), and its argv is read by position or as
    `args=`. The program is resolved from the argv expression (`_program`);
    an argv that cannot be resolved, or cannot be read, FAILS CLOSED: it is
    treated as git, so it must carry the location-free environment too.
    """
    if isinstance(scope, ast.AST):
        nodes = list(ast.walk(scope))
        if scope_bindings is None:
            scope_bindings = shared_bindings(scope)
    else:
        nodes = scope
    scope_bindings = scope_bindings or {}
    aliases = aliases or {}
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        module, _, function = (qualified_name(node.func, aliases) or "").rpartition(".")
        if module != "subprocess" or function not in _SUBPROCESS_CALLS:
            continue
        argv = call_argument(node, 0, "args")
        program = _program(argv, scope_bindings) if argv is not None else None
        if program is not None and program != "git":
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
    """Every git call in a module, module-level code and every function, each
    once (`fixtures.identity_git.scopes`). A call inside a nested function is
    reached from both functions' walks, so calls are keyed by position."""
    tree = ast.parse(source)
    aliases = import_aliases(tree)
    seen = {}
    for nodes, scope_bindings in scopes(tree):
        owner = nodes[0]
        name = owner.name if hasattr(owner, "name") else "<module>"
        for call, passes in _git_calls(nodes, scope_bindings, aliases):
            seen[(call.lineno, call.col_offset)] = (name, call, passes)
    return list(seen.values())


def test_every_resolver_git_call_passes_the_location_free_env():
    """Guard arm: every subprocess call in every memory script whose argv
    runs git, or cannot be resolved statically, passes
    `env=git_env_without_location()` (or an inline key filter of it). The
    call is found in module-level code and in every function, through the
    module's imports (`import subprocess`, `import subprocess as sp`, `from
    subprocess import run [as r]`). Its argv is read by position or as
    `args=`, and the program is resolved from it whatever its form (a
    string, a list or tuple, a starred splice, a `+` concatenation, a name
    bound in the function or the module); an argv that cannot be resolved
    (a call's return value, an imported constant) is treated as git. A git
    call added in that directory in any of those forms without the
    environment fails here.

    NOT COVERED: a subprocess function reached through `getattr`, `exec` or
    a re-export from another module, which only adversarial code would
    write."""
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


def test_the_scan_resolves_every_argv_form_and_fails_closed():
    """Live control for the guard arm above. Each call in the fixture carries
    the verdict the scan must reach: `ok` (git, with the environment), `bad`
    (git or unresolvable, without it) or `skip` (resolves to another
    program). The expected verdicts are read from those markers, so a form
    the resolver misreads reds here by line. Every resolvable form has a
    non-git twin marked `skip`, because a form the resolver stopped reading
    would fail closed to `bad` and look like a correct git verdict."""
    source = textwrap.dedent("""
        import sys
        import subprocess as sp
        from subprocess import run
        from subprocess import run as launch
        BASE = ["git"]
        FROZEN = ("git",)
        LISTER = ["ls"]
        subprocess.run(["git", "status"])  # bad

        def resolver(args):
            subprocess.run(["git", "rev-parse"], env=git_env_without_location())  # ok
            command = ["git"]
            command += ["rev-parse"]
            subprocess.run(command)  # bad
            subprocess.run(("git", "status"))  # bad
            frozen = ("git", "log")
            subprocess.run(frozen)  # bad
            subprocess.run("git status", shell=True)  # bad
            local = ["git"]
            subprocess.run([*local, "status"])  # bad
            subprocess.run([*BASE, "status"])  # bad
            subprocess.run((*FROZEN, "status"))  # bad
            subprocess.run(["git"] + args)  # bad
            joined = BASE + ["status"]
            alias = joined
            subprocess.run(alias)  # bad
            subprocess.run([] + ["git", "log"])  # bad
            subprocess.run(build_argv())  # bad
            subprocess.run(args)  # bad
            subprocess.run(["ls"])  # skip
            subprocess.run("gitk", shell=True)  # skip
            subprocess.run([sys.executable, "-m", "pip"])  # skip
            listing = ["ls", "-l"]
            subprocess.run(listing + args)  # skip
            subprocess.run(("ls", "-l"))  # skip
            near = ["ls"]
            subprocess.run([*near, "-l"])  # skip
            subprocess.run([*LISTER, "-l"])  # skip
            lister_alias = listing
            subprocess.run(lister_alias)  # skip
            subprocess.run(args=["git", "log"])  # bad
            subprocess.run(args=["ls"])  # skip
            sp.run(["git", "log"])  # bad
            sp.run(["ls"])  # skip
            run(["git", "log"])  # bad
            launch(("git", "log"))  # bad
    """)
    expected = {
        number: line.rsplit("# ", 1)[1]
        for number, line in enumerate(source.splitlines(), start=1)
        if line.rstrip().endswith(("# ok", "# bad", "# skip"))
    }
    seen = {
        call.lineno: ("ok" if passes else "bad")
        for _name, call, passes in _module_git_calls(source)
    }
    assert len(expected) == 28, expected  # every marked fixture line was read
    got = {number: seen.get(number, "skip") for number in expected}
    assert got == expected
    assert sorted(seen) == sorted(n for n, v in expected.items() if v != "skip")


# THE THREE SOURCE SCANNERS GUARD PROPERTIES OVER THE SAME SCRIPTS, so each
# must read the shapes they share the same way. One snippet per shared shape
# goes through all three; a shape one scanner reads and another misses fails
# here by name.
_PARITY_TARGETS = {
    "git call": ('subprocess.run(["git", "status"])', "import subprocess as sp", 'sp.run(["git", "status"])'),
    "reason dict": ('payload = {"reason": "x"}', None, None),
    "positional identity": (
        '_DetectedIdentity("a", source="b")',
        "from scripts.memory_api import _DetectedIdentity as DI",
        'DI("a", source="b")',
    ),
}
_PARITY_SHAPES = {
    "module level": "{statement}",
    "function body": "def f():\n    {statement}",
    "class body": "class C:\n    {statement}",
    "nested function": "def f():\n    def g():\n        {statement}",
}


def _parity_lines(scanner: str, source: str) -> list:
    tree = ast.parse(source)
    if scanner == "git call":
        return sorted(call.lineno for _name, call, passes in _module_git_calls(source) if not passes)
    if scanner == "reason dict":
        return sorted(line for line, _has_path in reason_dict_sites(tree))
    return positional_constructions(tree, "_DetectedIdentity")


def _parity_cases():
    for scanner, (statement, alias_import, alias_statement) in _PARITY_TARGETS.items():
        for shape, template in _PARITY_SHAPES.items():
            yield scanner, shape, "import subprocess\n" + template.format(statement=statement)
        if alias_import:
            yield scanner, "import alias", f"{alias_import}\n{alias_statement}"


def test_the_three_scanners_read_every_shared_shape_alike():
    """Parity arm: every shared shape (module level, function body, class
    body, nested function, an import alias) is found by each scanner whose
    target it can hold. RED WHEN one scanner stops reading a shape the
    others read."""
    misses = []
    for scanner, shape, source in _parity_cases():
        last = len(source.splitlines())
        if _parity_lines(scanner, source) != [last]:
            misses.append(f"{scanner} / {shape}: found {_parity_lines(scanner, source)}, expected [{last}]")
    assert len(list(_parity_cases())) == 14
    assert misses == []


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
