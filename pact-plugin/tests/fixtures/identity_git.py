"""Real-git fixtures for the repository-identity read contract.

Consumed by the test_project_identity_* modules. These are direct-import
helpers, not pytest fixtures (the thin-conftest rule).

Why real repositories: the failure modes under test are integration-shaped
(what `git config` answers, what `rev-parse --git-common-dir` reports for a
linked worktree or a --separate-git-dir checkout). A mocked git would pass
while the seam it replaces is the one that is broken.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Optional


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

def seed_session_evidence(
    config_dir, project_dir: Path, session_id: str, started_at: Optional[str] = None
) -> Path:
    """Record a session for `project_dir`, where the legacy-admission check looks.

    Writes `pact-session-context.json` into the session directory
    `reconstruct_session_dir(project_dir, session_id)` names, with the
    `project_dir` it records and a zone-aware `started_at` (now, unless
    given). A legacy bucket whose rows carry `session_id` is then read by a
    session whose identity root is `project_dir`, provided the repository's
    `description` was created before that start.
    `config_dir` is the isolated config root the file must land under (the
    suite sandboxes it at `tmp_path / "home" / ".claude"`); the helper
    refuses to write anywhere else, so a test can never seed a real session.
    """
    import json
    from datetime import datetime, timezone

    from shared.pact_context import reconstruct_session_dir

    session_dir = Path(reconstruct_session_dir(str(project_dir), session_id))
    assert Path(config_dir).resolve() in session_dir.resolve().parents, (
        f"{session_dir} is not under the isolated config root {config_dir}"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    context = session_dir / "pact-session-context.json"
    context.write_text(
        json.dumps({
            "project_dir": str(project_dir),
            "session_id": session_id,
            "started_at": started_at or datetime.now(timezone.utc).isoformat(),
        }),
        encoding="utf-8",
    )
    return context


# ---------------------------------------------------------------------------
# Source scanners over the memory scripts. They share ONE resolution of the
# shapes a call or a name can take (imports, aliases, attribute chains,
# keyword arguments, module-level code), so no scanner reads a shape another
# one misses.
# ---------------------------------------------------------------------------


def import_aliases(tree: ast.AST) -> dict:
    """Each local name an import binds, mapped to the qualified name it
    refers to: `import a.b` binds `a`, `import a.b as c` binds `c` to `a.b`,
    `from a import b as c` binds `c` to `a.b`. Relative imports keep their
    module name without the dots."""
    aliases: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    aliases[head] = head
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                qualified = f"{node.module}.{alias.name}" if node.module else alias.name
                aliases[alias.asname or alias.name] = qualified
    return aliases


def qualified_name(node, aliases: dict):
    """The dotted name an expression refers to through the module's imports
    (`sp.run` -> `subprocess.run` after `import subprocess as sp`, `run` ->
    `subprocess.run` after `from subprocess import run`), or None when it is
    not a plain name or attribute chain."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = qualified_name(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    return None


def call_argument(call: ast.Call, position: int, keyword: str):
    """The argument at `position`, or the keyword argument `keyword` when it
    was passed by name; None when neither is present."""
    positional = [argument for argument in call.args if not isinstance(argument, ast.Starred)]
    if len(positional) > position and len(positional) == len(call.args):
        return positional[position]
    return next((kw.value for kw in call.keywords if kw.arg == keyword), None)


def bindings(scope, top_level_only: bool = False) -> dict:
    """Every value each plain name is assigned inside `scope`, or only in its
    top-level statements (a module's constants), so a name in one function
    is never resolved through another function's assignment."""
    found: dict = {}
    for node in (scope.body if top_level_only else ast.walk(scope)):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found.setdefault(target.id, []).append(value)
    return found


def _opens_scope(node_class: type) -> bool:
    fields = set(node_class._fields)
    return "generators" in fields or ("body" in fields and bool(fields & {"args", "bases"}))


# Every ast node type that opens a scope, read from the ast module itself: a
# node with a body and parameters or bases (a function, a lambda, a class) or
# with generators (a comprehension). A scope kind a newer Python adds with
# that shape joins without an edit here.
SCOPE_KINDS = tuple(
    sorted(
        (
            node_class for node_class in vars(ast).values()
            if isinstance(node_class, type) and issubclass(node_class, ast.AST)
            and _opens_scope(node_class)
        ),
        key=lambda node_class: node_class.__name__,
    )
)


def scopes(tree: ast.Module):
    """Yield `(nodes, bindings)` once per scope: the module, then every node
    of a `SCOPE_KINDS` type (function, async function, lambda, class body,
    comprehension), wherever it sits. Every node of the tree is in exactly
    one scope, with the scope's owner first; a nested scope's nodes are not
    repeated in the scope that contains it.

    The bindings are the names the scope can see: the module's, then each
    enclosing function's, then its own, the inner one taking precedence. A
    class body sees its own top-level assignments, and, as in Python, a
    function inside a class does not."""
    module_bindings = bindings(tree, top_level_only=True)
    pending = [(tree, module_bindings)]
    while pending:
        owner, seen = pending.pop(0)
        if owner is tree:
            visible, inherited = module_bindings, module_bindings
        elif isinstance(owner, ast.ClassDef):
            visible = {**seen, **bindings(owner, top_level_only=True)}
            inherited = seen
        else:
            visible = {**seen, **bindings(owner)}
            inherited = visible
        nodes, stack = [owner], list(ast.iter_child_nodes(owner))
        while stack:
            node = stack.pop()
            if isinstance(node, SCOPE_KINDS):
                pending.append((node, inherited))
                continue
            nodes.append(node)
            stack.extend(ast.iter_child_nodes(node))
        yield nodes, visible


def resolve_constant(node, scope_bindings: dict, seen: frozenset = frozenset()):
    """The string constant an expression holds, following name bindings
    through aliases; a name with any binding to the target resolves to it.
    None when it cannot be resolved statically (a parameter, a call)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id not in seen and node.id in scope_bindings:
        values = {
            resolve_constant(value, scope_bindings, seen | {node.id})
            for value in scope_bindings[node.id]
        }
        values.discard(None)
        return "reason" if "reason" in values else (min(values) if values else None)
    return None


def _is_text(node, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _pair_keys(argument) -> list:
    """The first elements of a list or tuple of pairs (`[("k", v), ...]`)."""
    if not isinstance(argument, (ast.List, ast.Tuple)):
        return []
    return [
        element.elts[0] for element in argument.elts
        if isinstance(element, (ast.Tuple, ast.List)) and element.elts
    ]


def reason_dict_sites(tree: ast.AST, exempt: frozenset = frozenset()) -> list:
    """Every place the code puts a `reason` key into a dict, as
    `(lineno, has_path)`, in each statically visible form, in module-level
    code and in every function alike.

    The forms: a dict display (`{"reason": ...}`, beside `**` splices too); a
    `dict(...)` call with a `reason` keyword or a `("reason", ...)` pair;
    `.update(...)` with a `reason` keyword or a list of pairs holding one;
    `.setdefault("reason", ...)`; and a subscript store whose key is the
    constant `reason`, written inline or through a name bound to it.
    `has_path` is True when the same construct also sets `path`, so a caller
    can exempt the `{path, reason}` pair. Nodes whose `id()` is in `exempt`
    (a builder's own body) are skipped.

    NOT COVERED: a key or keyword name that cannot be resolved statically (a
    parameter, a call's result such as `dict(zip(keys, values))`, a
    comprehension's loop target such as `{k: v for k, v in pairs}`, `**kwargs`
    built at run time). Flagging every such store would flag ordinary dict
    code.
    """
    aliases = import_aliases(tree) if isinstance(tree, ast.Module) else {}
    module = tree if isinstance(tree, ast.Module) else ast.Module(body=[tree], type_ignores=[])
    sites, seen = [], set()
    for nodes, scope_bindings in scopes(module):
        for node in nodes:
            if id(node) in exempt or id(node) in seen:
                continue
            seen.add(id(node))
            site = _reason_site(node, aliases, scope_bindings)
            if site is not None:
                sites.append(site)
    return sorted(set(sites))


def _reason_site(node, aliases: dict, scope_bindings: dict):
    def is_reason(key):
        return resolve_constant(key, scope_bindings) == "reason"

    def is_path(key):
        return resolve_constant(key, scope_bindings) == "path"

    if isinstance(node, ast.Dict):
        if any(key is not None and is_reason(key) for key in node.keys):
            return (node.lineno, any(key is not None and is_path(key) for key in node.keys))
        return None
    if isinstance(node, ast.Call):
        names = {kw.arg for kw in node.keywords}
        pairs = [key for argument in node.args for key in _pair_keys(argument)]
        target = qualified_name(node.func, aliases)
        method = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if target == "dict" or method == "update":
            if "reason" in names or any(is_reason(key) for key in pairs):
                return (node.lineno, "path" in names or any(is_path(key) for key in pairs))
        elif method == "setdefault":
            first = call_argument(node, 0, "key")
            if first is not None and is_reason(first):
                return (node.lineno, False)
        return None
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(t, ast.Subscript) and is_reason(t.slice) for t in targets):
            return (node.lineno, False)
    return None


def positional_constructions(tree: ast.AST, class_name: str) -> list:
    """Line numbers where `class_name` is built with a positional field, in
    module-level code and in every function alike.

    The class is recognised through the module's imports (`from m import
    Name as N`), as an attribute (`module.Name`), through any name bound to
    it (aliases followed to a fixed point), as a subclass of it, and from
    inside the class or a subclass: the first parameter of a classmethod
    (`cls`), and `type(self)` or `self.__class__` in any other method. A
    construction is positional when it passes a positional argument (a
    starred one included), when it goes through `_make` (always
    positional), when `functools.partial` (imported under any name) binds
    positional arguments to it, or when the partial's result is called with
    positional arguments.

    NOT COVERED: a class reached through `getattr`, a registry or a call's
    result, which only adversarial code would write.
    """
    imports = import_aliases(tree) if isinstance(tree, ast.Module) else {}
    names = {name for name, target in imports.items() if target.split(".")[-1] == class_name}
    names.add(class_name)

    def is_class(node):
        return _is_class(node, names, class_name)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_class(node.value):
                bound = [target.id for target in node.targets if isinstance(target, ast.Name)]
            elif isinstance(node, ast.ClassDef) and any(is_class(base) for base in node.bases):
                bound = [node.name]
            else:
                continue
            for name in bound:
                if name not in names:
                    names.add(name)
                    changed = True
    lines = set(_positional_calls(ast.walk(tree), is_class, imports))
    for owner in ast.walk(tree):
        if not (isinstance(owner, ast.ClassDef) and owner.name in names):
            continue
        for method in owner.body:
            refers = _self_reference(method, imports)
            if refers is not None:
                lines.update(_positional_calls(
                    ast.walk(method), lambda node, refers=refers: is_class(node) or refers(node),
                    imports,
                ))
    return sorted(lines)


def _self_reference(method, imports: dict):
    """A predicate for the expressions by which `method`, defined in a class
    body, names its own class: the first parameter of a classmethod, or
    `type(p)` / `p.__class__` for the first parameter `p` of an instance
    method. None for a static method or a method with no parameter."""
    if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    params = method.args.posonlyargs + method.args.args
    decorators = {qualified_name(node, imports) for node in method.decorator_list}
    if not params or "staticmethod" in decorators:
        return None
    own = params[0].arg

    def is_own(node):
        return isinstance(node, ast.Name) and node.id == own

    if "classmethod" in decorators:
        return is_own
    return lambda node: (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "type" and len(node.args) == 1 and is_own(node.args[0])
    ) or (isinstance(node, ast.Attribute) and node.attr == "__class__" and is_own(node.value))


def _positional_calls(nodes, is_class, imports: dict):
    """Yield the line of each call among `nodes` that builds the class
    `is_class` recognises with a positional field."""
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if is_class(func) and node.args:
            yield node.lineno
        elif isinstance(func, ast.Attribute) and func.attr == "_make" and is_class(func.value):
            yield node.lineno
        elif _is_partial_of(node, imports, is_class) and len(node.args) > 1:
            yield node.lineno
        elif isinstance(func, ast.Call) and _is_partial_of(func, imports, is_class) and node.args:
            yield node.lineno


def is_partial(call: ast.Call, aliases: dict) -> bool:
    """A `functools.partial(...)` call, the name imported in any form."""
    return (qualified_name(call.func, aliases) or "").split(".")[-1] == "partial"


def _is_partial_of(call, imports: dict, is_class) -> bool:
    return is_partial(call, imports) and bool(call.args) and is_class(call.args[0])


def _is_class(node, names: set, class_name: str) -> bool:
    return (isinstance(node, ast.Name) and node.id in names) or (
        isinstance(node, ast.Attribute) and node.attr == class_name
    )
