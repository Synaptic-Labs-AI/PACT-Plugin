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
import asyncio
import collections
import inspect
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from fixtures.identity_git import bindings as shared_bindings
from fixtures.identity_git import (
    call_argument,
    import_aliases,
    is_partial,
    positional_constructions,
    qualified_name,
    reason_dict_sites,
    scopes,
    SCOPE_KINDS,
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
# The public subprocess callables that do NOT run a command. Every other
# public callable of the module, exception classes aside, is treated as one
# that does, so a runner a newer Python adds is scanned without an edit here.
_NOT_A_COMMAND = {
    "CompletedProcess": "builds the result record of a finished run; runs nothing",
}


def _argv_keyword(function) -> str:
    """The name a runner's command takes as a keyword: its first parameter,
    or Popen's when the runner forwards `*popenargs` to Popen."""
    first = next(iter(inspect.signature(function).parameters.values()))
    if first.kind is inspect.Parameter.VAR_POSITIONAL:
        return _argv_keyword(subprocess.Popen)
    return first.name


def _command_runners() -> dict:
    """{name: argv keyword} for every public subprocess callable that runs a
    command, read from `subprocess.__all__`."""
    runners = {}
    for name in subprocess.__all__:
        value = getattr(subprocess, name)
        if not callable(value) or name in _NOT_A_COMMAND:
            continue
        if isinstance(value, type) and issubclass(value, BaseException):
            continue
        runners[name] = _argv_keyword(value)
    return runners


_SUBPROCESS_CALLS = _command_runners()


def _other_runners() -> dict:
    """{qualified name: position of the program argument} for every os and
    asyncio callable that runs a command (`os.system`, `os.popen`, the
    `os.exec*`, `os.spawn*` and `os.posix_spawn*` families,
    `asyncio.create_subprocess_*`), read from the modules. None of them is
    checked for the location-free environment, so a git call through one
    never passes: run git through subprocess with the environment."""
    runners = {}
    for name in dir(os):
        if name.startswith(("system", "popen", "exec", "spawn", "posix_spawn")) and callable(
            getattr(os, name)
        ):
            runners[f"os.{name}"] = 1 if name.startswith("spawn") else 0
    for name in dir(asyncio):
        if name.startswith("create_subprocess"):
            runners[f"asyncio.{name}"] = 0
    return runners


_OTHER_RUNNERS = _other_runners()


def _is_git(program: str) -> bool:
    """`git`, named bare or by a path (`/usr/bin/git`, `git.exe`)."""
    return os.path.basename(program) in ("git", "git.exe")


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


def _runner(node, aliases: dict):
    """The argv keyword of the subprocess runner `node` names, or None."""
    module, _, function = (qualified_name(node, aliases) or "").rpartition(".")
    return _SUBPROCESS_CALLS.get(function) if module == "subprocess" else None


def _git_calls(scope, scope_bindings: dict = None, aliases: dict = None):
    """Yield (call, passes_location_free_env) for each call of a subprocess
    runner (`_SUBPROCESS_CALLS`, read from the subprocess module: `run`,
    `Popen`, the `check_*` and `call` helpers, `getoutput`,
    `getstatusoutput`) whose argv runs git OR cannot be resolved statically.

    `scope` is a function node or a list of nodes (one scope from
    `fixtures.identity_git.scopes`, module-level code included). The runner
    is recognised through the module's imports (`import subprocess as sp`,
    `from subprocess import run`), called directly or wrapped by
    `functools.partial` (imported under any name), whose bound arguments are
    read as the call's. The argv is read by position or by the runner's own
    keyword (`args=`, `cmd=`). The program is resolved from the argv
    expression (`_program`); an argv that cannot be resolved, or is not
    passed (a partial that binds none), FAILS CLOSED: it is treated as git,
    so it must carry the location-free environment too. `getoutput` and
    `getstatusoutput` take no environment, so a git call through them never
    passes.
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
        keyword, first = _runner(node.func, aliases), 0
        wrapped = is_partial(node, aliases) and bool(node.args)
        if keyword is None and wrapped:
            keyword, first = _runner(node.args[0], aliases), 1
        if keyword is None:
            callee = node.args[0] if wrapped else node.func
            position = _OTHER_RUNNERS.get(qualified_name(callee, aliases) or "")
            if position is None:
                continue
            position += 1 if wrapped else 0
            argv = node.args[position] if len(node.args) > position else None
            program = _program(argv, scope_bindings) if argv is not None else None
            if program is None or _is_git(program):
                yield node, False
            continue
        argv = call_argument(node, first, keyword)
        program = _program(argv, scope_bindings) if argv is not None else None
        if program is not None and not _is_git(program):
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


def _unread_runner_references(source: str) -> list:
    """Line of each use of a command runner (`_SUBPROCESS_CALLS`,
    `_OTHER_RUNNERS`) other than as the callee of a call or the function a
    `functools.partial` wraps, outside annotations. A runner held in a name
    or a container, or passed as an argument, is called where the scan
    cannot read its argv or its environment, so the guard FAILS CLOSED on
    it."""
    tree = ast.parse(source)
    aliases = import_aliases(tree)
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    annotations = set()
    for node in ast.walk(tree):
        for field in ("annotation", "returns"):
            value = getattr(node, field, None)
            if isinstance(value, ast.AST):
                annotations.update(id(inner) for inner in ast.walk(value))
    lines = []
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load)):
            continue
        if id(node) in annotations or (
            _runner(node, aliases) is None
            and (qualified_name(node, aliases) or "") not in _OTHER_RUNNERS
        ):
            continue
        parent = parents.get(id(node))
        if isinstance(parent, ast.Call) and (
            parent.func is node
            or (is_partial(parent, aliases) and parent.args and parent.args[0] is node)
        ):
            continue
        lines.append(node.lineno)
    return sorted(lines)


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
    """Guard arm: every call of a subprocess runner (every public
    subprocess callable that runs a command, `_SUBPROCESS_CALLS`) in every
    memory script whose argv runs git, or cannot be resolved statically,
    passes `env=git_env_without_location()` (or an inline key filter of it).
    The call is found in every scope (`fixtures.identity_git.scopes`: the
    module, class bodies, functions, lambdas and comprehensions), through
    the module's imports (`import subprocess`, `import subprocess as sp`,
    `from subprocess import run [as r]`), called directly or wrapped by
    `functools.partial`. Its argv is read by position or by the runner's
    keyword, and the program is resolved from it whatever its form (a
    string, a list or tuple, a starred splice, a `+` concatenation, a name
    bound in the scope, an enclosing function or the module); an argv that
    cannot be resolved (a call's return value, an imported constant) is
    treated as git, and git named by a path (`/usr/bin/git`) is git. A call
    of an os or asyncio command runner (`_OTHER_RUNNERS`) whose program is
    git or cannot be resolved never passes. A runner used as a value (held
    in a name or a container, passed as an argument) fails closed
    (`_unread_runner_references`). A git call added in that directory in
    any of those forms without the environment fails here.

    NOT COVERED: a runner reached through `getattr`, `exec` or a re-export
    from another module, and a call of a partial's RESULT that overrides
    the environment the partial bound (the partial itself is checked),
    which only adversarial code would write."""
    missing, counts, unread = [], {}, []
    for path in sorted((_PLUGIN / _SCRIPTS_DIR).glob("*.py")):
        source = path.read_text(encoding="utf-8")
        calls = _module_git_calls(source)
        counts[path.name] = len(calls)
        missing += [
            f"{_SCRIPTS_DIR}/{path.name}::{name}:{call.lineno}"
            for name, call, passes in calls if not passes
        ]
        unread += [f"{_SCRIPTS_DIR}/{path.name}:{line}" for line in _unread_runner_references(source)]
    for name, floor in _GIT_CALL_FLOORS.items():
        assert counts.get(name, 0) >= floor, (
            f"{name}: found {counts.get(name, 0)} git calls, expected at least {floor}"
        )
    assert missing == [], f"git calls without env=git_env_without_location(): {missing}"
    assert unread == [], f"command runners used as values, where the scan cannot read them: {unread}"


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
        from subprocess import getoutput
        import asyncio
        import os
        import functools
        from functools import partial
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
            subprocess.getoutput("git status")  # bad
            subprocess.getstatusoutput(cmd="git status")  # bad
            subprocess.getoutput("ls -l")  # skip
            getoutput("git log")  # bad
            subprocess.run(["/usr/bin/git", "log"])  # bad
            subprocess.run(["/usr/bin/gitk"])  # skip
            os.system("git status")  # bad
            os.system("ls -l")  # skip
            os.popen(args)  # bad
            os.execvp("git", ["git", "log"])  # bad
            os.spawnlp(os.P_WAIT, "git", "git", "log")  # bad
            os.spawnlp(os.P_WAIT, "ls", "ls")  # skip
            asyncio.create_subprocess_exec("git", "log", env=git_env_without_location())  # bad
            functools.partial(os.system, "git status")  # bad
            functools.partial(subprocess.run, ["git", "log"])  # bad
            functools.partial(subprocess.run, ["git", "log"], env=git_env_without_location())  # ok
            functools.partial(subprocess.run, ["ls"])  # skip
            partial(sp.check_output)  # bad
            near = ["ls", "-l"]
            far = ["git", "log"]
            inner = lambda: subprocess.run(near)  # skip
            inner = lambda: subprocess.run(far)  # bad

            def nested():
                subprocess.run(near)  # skip
                subprocess.run(far)  # bad

        lone = lambda: subprocess.run(["git", "status"])  # bad
        listed = [subprocess.run(["git", "status"]) for _ in ()]  # bad

        class Holder:
            CMD = ["ls"]
            probe = lambda: subprocess.run(["git", "status"])  # bad
            listing = subprocess.run(CMD)  # skip

            async def method(self):
                subprocess.run(["git", "status"])  # bad
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
    assert len(expected) == 55, expected  # every marked fixture line was read
    got = {number: seen.get(number, "skip") for number in expected}
    assert got == expected
    assert sorted(seen) == sorted(n for n, v in expected.items() if v != "skip")


def test_a_runner_used_as_a_value_fails_closed():
    """Live control for `_unread_runner_references`: each fixture line
    carries `unread` (a runner held or passed where the scan cannot read the
    call) or `read` (a call, a partial, an annotation, a non-runner)."""
    source = textwrap.dedent("""
        import os
        import subprocess
        import functools
        from subprocess import run as launch

        def resolver(argv, proc: subprocess.Popen) -> subprocess.Popen:
            runner = subprocess.run  # unread
            table = {"run": subprocess.run}  # unread
            list(map(subprocess.check_output, argv))  # unread
            hooks = [launch]  # unread
            shell = os.system  # unread
            subprocess.run(argv)  # read
            launch(argv)  # read
            functools.partial(subprocess.run, argv)  # read
            os.system("ls")  # read
            subprocess.CompletedProcess  # read
            subprocess.CalledProcessError  # read
            os.path.join("a", "b")  # read
    """)
    expected = {
        number: line.rsplit("# ", 1)[1]
        for number, line in enumerate(source.splitlines(), start=1)
        if line.rstrip().endswith(("# unread", "# read"))
    }
    assert len(expected) == 12, expected
    found = set(_unread_runner_references(source))
    assert {number: ("unread" if number in found else "read") for number in expected} == expected
    assert found <= set(expected), sorted(found - set(expected))


# THE THREE SOURCE SCANNERS GUARD PROPERTIES OVER THE SAME SCRIPTS, so each
# must read the shapes they share the same way. Every target form goes
# through every scope kind; a shape one scanner reads and another misses
# fails here by name.
#
# The targets, one per scanner, in each form more than one scanner reads (an
# import alias and a `functools.partial` wrapper are read by the git and the
# constructor scans; a dict holds no call, so the decline scan has the plain
# form only).
_PARITY_TARGETS = {
    "git call": {
        "plain": ("import subprocess", 'subprocess.run(["git", "status"])'),
        "import alias": ("import subprocess as sp", 'sp.run(["git", "status"])'),
        "partial": (
            "import functools, subprocess",
            'functools.partial(subprocess.run, ["git", "status"])',
        ),
    },
    "reason dict": {
        "plain": (None, '{"reason": "x"}'),
    },
    "positional identity": {
        "plain": (None, '_DetectedIdentity("a", source="b")'),
        "import alias": (
            "from scripts.memory_api import _DetectedIdentity as DI",
            'DI("a", source="b")',
        ),
        "partial": ("import functools", 'functools.partial(_DetectedIdentity, "a")'),
    },
}
# One template per scope the shared helper models: the module, every scope
# kind it derives from the ast module (`SCOPE_KINDS`), and the nestings that
# decide which bindings a scope sees. The target is always the last line.
_PARITY_SCOPES = {
    "Module": "{target}",
    "FunctionDef": "def f():\n    {target}",
    "AsyncFunctionDef": "async def f():\n    {target}",
    "Lambda": "f = lambda: {target}",
    "ClassDef": "class C:\n    {target}",
    "ListComp": "[{target} for _ in ()]",
    "SetComp": "{{{target} for _ in ()}}",
    "DictComp": "{{0: {target} for _ in ()}}",
    "GeneratorExp": "list({target} for _ in ())",
    "nested function": "def f():\n    def g():\n        {target}",
    "lambda in a class body": "class C:\n    f = lambda: {target}",
    "lambda in a function": "def f():\n    g = lambda: {target}",
    "comprehension in a lambda": "f = lambda: [{target} for _ in ()]",
}


def _parity_lines(scanner: str, source: str) -> list:
    tree = ast.parse(source)
    if scanner == "git call":
        return sorted(call.lineno for _name, call, passes in _module_git_calls(source) if not passes)
    if scanner == "reason dict":
        return sorted(line for line, _has_path in reason_dict_sites(tree))
    return positional_constructions(tree, "_DetectedIdentity")


def _parity_cases():
    for scanner, forms in _PARITY_TARGETS.items():
        for form, (header, target) in forms.items():
            for scope, template in _PARITY_SCOPES.items():
                body = template.format(target=target)
                yield scanner, f"{form} / {scope}", f"{header}\n{body}" if header else body


def test_the_parity_scopes_cover_every_scope_kind():
    """Every scope kind the shared helper derives from the ast module has a
    parity template, so a kind a newer Python adds reds here until the
    scanners are shown to read it."""
    kinds = {kind.__name__ for kind in SCOPE_KINDS} | {"Module"}
    assert kinds <= set(_PARITY_SCOPES), sorted(kinds - set(_PARITY_SCOPES))
    assert {"FunctionDef", "Lambda", "ClassDef", "GeneratorExp"} <= kinds  # known-present control


def test_the_three_scanners_read_every_shared_shape_alike():
    """Parity arm: every target form, in every scope (each scope kind and
    the nestings above), is found by its scanner. RED WHEN one scanner stops
    reading a shape the others read."""
    misses = []
    cases = list(_parity_cases())
    for scanner, shape, source in cases:
        last = len(source.splitlines())
        found = _parity_lines(scanner, source)
        if found != [last]:
            misses.append(f"{scanner} / {shape}: found {found}, expected [{last}]")
    forms = sum(len(targets) for targets in _PARITY_TARGETS.values())
    assert len(cases) == forms * len(_PARITY_SCOPES) == 7 * 13
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


# THE SHARED SCOPE HELPER DECIDES WHAT EVERY SCANNER SEES. Its scope kinds are
# checked against the compiler's own scope table (`symtable`), over every
# script the scanners read and every parity source.
# The symtable kinds that hold statements a scanner reads. The others a
# Python 3.12+ compiler emits ("type alias", "type parameters", "TypeVar
# bound", "annotation") hold only annotations and type parameters.
_STATEMENT_TABLES = {"function", "class"}


def _scope_sources():
    for path in sorted((_PLUGIN / _SCRIPTS_DIR).glob("*.py")):
        yield f"{_SCRIPTS_DIR}/{path.name}", path.read_text(encoding="utf-8")
    for scanner, shape, source in _parity_cases():
        yield f"parity {scanner} / {shape}", source


def _symtable_scopes(table):
    for child in table.get_children():
        if str(child.get_type()) in _STATEMENT_TABLES:
            yield str(child.get_type()), child.get_lineno()
        yield from _symtable_scopes(child)


def test_every_node_is_in_exactly_one_scope():
    """Every node of every scanned tree is in exactly one scope the shared
    helper yields, so no scanner can skip a region of the code."""
    problems = []
    for label, source in _scope_sources():
        tree = ast.parse(source)
        # Counted, not collected into a set: the parser shares one Load and
        # one Store instance across the tree, so ids repeat in ast.walk too.
        yielded = collections.Counter(
            id(node) for nodes, _bindings in scopes(tree) for node in nodes
        )
        if yielded != collections.Counter(id(node) for node in ast.walk(tree)):
            problems.append(label)
    assert problems == []


def test_every_compiler_scope_is_a_yielded_scope():
    """Every function, lambda, class and generator scope the compiler builds
    (`symtable`) is a scope the shared helper yields, at the same line. A
    scope kind the helper skips, or a newer Python adds, fails here."""
    import symtable

    missing = []
    for label, source in _scope_sources():
        owners = {
            ("class" if isinstance(nodes[0], ast.ClassDef) else "function", nodes[0].lineno)
            for nodes, _bindings in scopes(ast.parse(source))
            if not isinstance(nodes[0], ast.Module)
        }
        compiled = set(_symtable_scopes(symtable.symtable(source, label, "exec")))
        missing += [f"{label}: {kind} at line {line}" for kind, line in sorted(compiled - owners)]
    assert missing == []


def test_the_subprocess_runners_are_read_from_the_module():
    """The git arm's runners are every public subprocess callable except
    exception classes and `_NOT_A_COMMAND`; an exclusion the module no
    longer has, or a runner without a command parameter, fails here."""
    assert set(_NOT_A_COMMAND) <= set(subprocess.__all__)
    assert {"run", "Popen", "getoutput"} <= set(_SUBPROCESS_CALLS)  # known-present control
    for name, keyword in _SUBPROCESS_CALLS.items():
        parameters = inspect.signature(getattr(subprocess, name)).parameters
        assert keyword in parameters or keyword in inspect.signature(subprocess.Popen).parameters
