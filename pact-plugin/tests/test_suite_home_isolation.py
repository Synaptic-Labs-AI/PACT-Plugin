"""
Location: pact-plugin/tests/test_suite_home_isolation.py
Summary: Guards the suite-wide sandbox HOME that tests/conftest.py sets at import:
a process a test starts must resolve its home and its Claude config root outside
the operator's real home directory.
Used by: pytest (the whole suite depends on the conftest it checks).
"""

import ast
import os
import pwd
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_glob as _shared_tracked_glob

_PLUGIN = Path(__file__).resolve().parent.parent


def _test_source_files():
    """Test sources from the git index, one glob per population.

    An untracked scratch test file must not be judged by this scan; the two
    seeded modes are the known members that keep an empty root loud.
    """
    name = _PLUGIN.name
    return (
        _shared_tracked_glob(
            _PLUGIN.parent, f"{name}/tests/**/*.py",
            f"{name}/tests/test_suite_home_isolation.py")
        + _shared_tracked_glob(
            _PLUGIN.parent, f"{name}/skills/*/test_*.py",
            f"{name}/skills/pact-handoff-harvest/test_skill_loading_harvest.py")
    )
_HOOKS = _PLUGIN / "hooks"

# Read at collection, before any fixture runs: what import-time code in a test
# module, or a child started from it, would see.
_CONFIG_DIR_AT_COLLECTION = os.environ.get("CLAUDE_CONFIG_DIR")
_HOME_AT_COLLECTION = os.environ.get("HOME", "")

_CHILD = (
    "import os, pathlib\n"
    "from shared.paths import get_claude_config_dir\n"
    "config_dir = os.environ.get('CLAUDE_CONFIG_DIR')\n"
    "print(config_dir)\n"
    "print(pathlib.Path.home())\n"
    "print(get_claude_config_dir())\n"
)


def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def _outside_real_home(path: str) -> bool:
    resolved = Path(path).resolve()
    real = _real_home()
    return resolved != real and real not in resolved.parents


def test_a_child_process_resolves_its_home_and_config_root_outside_the_real_home():
    env = dict(os.environ, PYTHONPATH=str(_HOOKS))
    out = subprocess.run(
        [sys.executable, "-c", _CHILD], env=env,
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout.splitlines()
    assert out[0] == "None", out
    assert _outside_real_home(out[1]), out
    assert _outside_real_home(out[2]), out


def test_the_session_home_is_not_the_real_home():
    assert _outside_real_home(os.environ["HOME"])
    assert "CLAUDE_CONFIG_DIR" not in os.environ


def test_collection_already_runs_in_the_sandbox():
    assert _CONFIG_DIR_AT_COLLECTION is None
    assert _outside_real_home(_HOME_AT_COLLECTION)


def test_the_embedding_cache_is_not_moved_into_the_sandbox_home():
    # The sandbox HOME must not hand the embedding tests a cold cache.
    cache = Path(os.environ["HF_HOME"]).resolve()
    sandbox = Path(os.environ["HOME"]).resolve()
    assert cache != sandbox and sandbox not in cache.parents, (cache, sandbox)


HAS, LACKS, UNKNOWN = "has", "lacks", "unknown"
_RUNNER_ATTRS = frozenset({
    "run", "Popen", "check_output", "check_call", "call",
    "create_subprocess_exec", "create_subprocess_shell",
})
_SUBPROCESS_RUNNERS = frozenset({"run", "Popen", "check_output", "check_call", "call"})

# The shapes the scan judges. Every other shape is unjudged and passed; the
# scan's docstring lists examples. Each entry is pinned to a verdict row.
JUDGED_SHAPES = (
    "environ",              # os.environ, dict(os.environ), os.environ.copy()
    "none",                 # env=None: the child inherits
    "dict-literal",         # {...}: an explicit HOME key, else its spreads decide
    "spread",               # {**x}: x judged recursively
    "dict-call",            # dict(x, K=...): HOME= keyword, else x and **kw decide
    "union",                # a | b: has if either side has HOME
    "name",                 # a name, through its bindings and writes in this scope
    "annotated-binding",    # env: T = value
    "unpacking-binding",    # env, x = {...}, 1: paired element by element
    "walrus-binding",       # (env := value)
    "unreadable-binding",   # for/with/except/import targets, starred unpacking: unknown
    "module-name",          # a name the function never binds: the module's bindings
    "post-build-write",     # env["HOME"] = / update / setdefault / |= {"HOME": ...}
    "strip",                # env.pop("HOME") / del env["HOME"] / env.clear()
    "merge-write",          # env.update(x) / env.update(**x) / env |= x: x decides
    "variable-key-write",   # env[key] = / env.pop(key) / env.popitem(): unknown
    "conditional-write",    # a binding or write in a block not enclosing the call
    "escape",               # a name passed to a call or stored away: unknown from there
    "alias",                # b = a: a write through either makes both unknown
    "os-environ-change",    # os.environ HOME removed/restored, or handed on, in scope
    "carry-clock-shift",    # carry_clock_shift(x): x judged
    "bare-runner",          # run/Popen/... imported from subprocess, aliases included
    "asyncio-runner",       # asyncio.create_subprocess_exec / _shell
)

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_FUNCLIKE = (*_FUNCTIONS, ast.Lambda)
_SCOPES = (*_FUNCTIONS, ast.ClassDef, ast.Lambda)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_REBINDS = frozenset({"bind", "unknown", "unbind", "aug-merge"})
_WRITES = frozenset({"add", "strip", "maybe-add", "maybe-strip", "merge", "aug-merge",
                     "escape", "closure"})
# A pseudo-name: os.environ's own HOME, which this scope may remove or set.
_OS_ENVIRON = "os.environ"
# Methods that neither write nor hand the dict to code that might.
_READ_METHODS = frozenset({"get", "copy", "items", "keys", "values"})
# Calls that can read or change any name of a scope or module by text or by
# namespace: a scope that makes one has no name the scan can judge.
_OPAQUE = frozenset({"exec", "eval", "globals", "locals", "vars"})
# os.environ changes made in another scope: one that may remove HOME makes
# os.environ unknown everywhere; one that can only add HOME matters only where
# the call's own scope has already removed it.
_ENVIRON_REMOVALS = frozenset({"strip", "maybe-strip", "escape"})
_ENVIRON_ADDS = frozenset({"add", "maybe-add", "merge"})
# Calls that copy a dict argument rather than keep it.
_COPIERS = frozenset({"dict", "carry_clock_shift"})


def _is_home_key(node) -> bool:
    return isinstance(node, ast.Constant) and node.value == "HOME"


def _home_less_dict(node) -> bool:
    """A dict literal with only constant keys, none of them HOME."""
    return isinstance(node, ast.Dict) and None not in node.keys \
        and all(isinstance(k, ast.Constant) for k in node.keys) \
        and not any(_is_home_key(k) for k in node.keys)


def _join(a, b):
    """The verdict when either of two paths may have run; None is unbound."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a == b else UNKNOWN


def _outer_parts(node) -> list:
    """The expressions of a def, lambda or class that run in the scope around
    it: decorators, default values, and a class's bases and keywords."""
    parts = list(getattr(node, "decorator_list", []))
    if isinstance(node, ast.ClassDef):
        return parts + node.bases + [keyword.value for keyword in node.keywords]
    return parts + node.args.defaults + [d for d in node.args.kw_defaults if d is not None]


# Each node's place in evaluation order, set by _scopes for the module being
# judged: a node comes after everything it evaluates first. And the first
# iterable of every comprehension, which runs once in the enclosing code.
_ORDER: dict = {}
_FIRST_ITERS: frozenset = frozenset()


def _evaluated_children(node) -> list:
    """A node's children in the order Python evaluates them."""
    if isinstance(node, ast.Assign):
        return [node.value, *node.targets]
    if isinstance(node, ast.AnnAssign):
        return [part for part in (node.annotation, node.value, node.target) if part]
    if isinstance(node, ast.Dict):
        return [part for pair in zip(node.keys, node.values) for part in pair if part]
    if isinstance(node, ast.DictComp):
        return [*node.generators, node.key, node.value]
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return [*node.generators, node.elt]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [node.iter, node.target, *node.body, *node.orelse]
    if isinstance(node, _SCOPES):
        return [*_outer_parts(node), *(node.body if isinstance(node.body, list) else [node.body])]
    return list(ast.iter_child_nodes(node))


def _evaluation_order(tree) -> dict:
    order: dict = {}
    stack = [(tree, False)]
    while stack:
        node, children_done = stack.pop()
        if children_done:
            order[id(node)] = len(order)
            continue
        stack.append((node, True))
        stack.extend((child, False) for child in reversed(_evaluated_children(node)))
    return order


def _walk(node, chain=()):
    """(node, chain) for every node of one scope, not entering a nested
    function, class or lambda (yielded, not entered; its outer parts are
    walked). `chain` names every statement body, branch and conditional
    sub-expression enclosing the node."""
    for field, value in ast.iter_fields(node):
        if isinstance(node, _SCOPES) and field != "body":
            continue  # a scope's own walk is its body; the rest runs outside it
        items = value if isinstance(value, list) else [value]
        for index, item in enumerate(items):
            if not isinstance(item, ast.AST):
                continue
            if isinstance(item, ast.stmt):
                sub = chain + ((id(node), field),)
            elif isinstance(item, (ast.excepthandler, ast.match_case)):
                sub = chain + ((id(item), "branch"),)
            elif isinstance(node, ast.IfExp) and field in ("body", "orelse"):
                sub = chain + ((id(node), field),)
            elif isinstance(node, ast.BoolOp) and index:
                sub = chain + ((id(node), index),)
            elif isinstance(node, _COMPREHENSIONS):
                sub = chain + ((id(node), "comprehension"),)
            elif isinstance(node, ast.comprehension) and id(item) in _FIRST_ITERS:
                sub = chain[:-1]  # the first iterable runs once, before the loop
            else:
                sub = chain
            yield item, sub
            for part in _outer_parts(item) if isinstance(item, _SCOPES) else [item]:
                if part is not item:
                    yield part, sub
                yield from _walk(part, sub)


def _store_names(target) -> list:
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]


# The module's names for os and for os.environ (`import os as o`, `from os
# import environ as e`), set by _scopes before a module's scopes are built.
_OS_NAMES: set = {"os"}
_ENVIRON_NAMES: set = set()
_ENV_FUNCTION_NAMES: set = set()


def _module_aliases(tree):
    """Record how this module names os, os.environ, os.putenv and os.unsetenv."""
    _OS_NAMES.clear()
    _ENVIRON_NAMES.clear()
    _ENV_FUNCTION_NAMES.clear()
    _OS_NAMES.add("os")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    _OS_NAMES.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "environ":
                    _ENVIRON_NAMES.add(alias.asname or alias.name)
                elif alias.name in ("putenv", "unsetenv"):
                    _ENV_FUNCTION_NAMES.add(alias.asname or alias.name)


def _globals_key(node):
    """The constant name that `<obj>.setitem(globals(), "NAME", ...)` or
    `<obj>.delitem(globals(), "NAME")` rebinds, and the globals() call it uses."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("setitem", "delitem") and len(node.args) > 1):
        target, key = node.args[0], node.args[1]
        if (isinstance(target, ast.Call) and isinstance(target.func, ast.Name)
                and target.func.id == "globals" and not target.args and not target.keywords
                and isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return target, key.value


def _imports_os(node, name) -> bool:
    """Whether `node` is an import that binds `name` to os or to os.environ."""
    if isinstance(node, ast.Import):
        return any((alias.asname or alias.name.split(".")[0]) == name
                   and alias.name.split(".")[0] == "os" for alias in node.names)
    return isinstance(node, ast.ImportFrom) and node.module == "os" and any(
        (alias.asname or alias.name) == name and alias.name == "environ"
        for alias in node.names)


def _is_os(node) -> bool:
    return isinstance(node, ast.Name) and node.id in _OS_NAMES


def _is_environ(node) -> bool:
    """os.environ itself, under any of the module's names for it."""
    if isinstance(node, ast.Name):
        return node.id in _ENVIRON_NAMES
    return isinstance(node, ast.Attribute) and node.attr == "environ" and _is_os(node.value)


def _environ_value(node) -> bool:
    """os.environ or os.environ.copy() (dict(os.environ) is judged as a dict
    of os.environ)."""
    return _is_environ(node) or (isinstance(node, ast.Call) and not node.args and not node.keywords
            and isinstance(node.func, ast.Attribute) and node.func.attr == "copy"
            and _is_environ(node.func.value))


def _dict_name(node):
    """The name a write through `node` lands on: a bare name, os.environ, or None."""
    if _is_environ(node):
        return _OS_ENVIRON
    if isinstance(node, ast.Name):
        return node.id


def _call_name(func) -> str:
    """The bare or attribute name a call goes through: `setattr` for both
    setattr(...) and monkeypatch.setattr(...)."""
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _unwrap(node):
    while isinstance(node, ast.Call) and getattr(node.func, "id", "") == "carry_clock_shift" \
            and node.args:
        node = node.args[0]
    return node.value if isinstance(node, ast.NamedExpr) else node


class _Scope:
    """One function, class or module body: its nodes with their chains, and
    every binding, write and strip of each name, in source order."""

    def __init__(self, node, parent, module):
        self.node, self.parent = node, parent
        self.module = module or self
        self.pairs = list(_walk(node))
        self.events: dict = {}
        self.globals: set = set()
        self.nonlocals: set = set()
        self.aliases: list = []
        self.opaque = False
        self.global_items = {id(found[0]): (found[1], n, chain) for n, chain in self.pairs
                             if (found := _globals_key(n))}
        self.call_funcs = {id(n.func) for n, _ in self.pairs if isinstance(n, ast.Call)}
        self.loop_bodies = {(id(n), "body") for n, _ in self.pairs if isinstance(n, _LOOPS)}
        self.nodes = {id(n): n for n, _ in self.pairs}
        if isinstance(node, _FUNCLIKE):
            arguments = node.args
            for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                        arguments.vararg, arguments.kwarg):
                if arg is not None:
                    self._add(arg.arg, node, "unknown", None, ((id(node), "body"),))
        for item, chain in self.pairs:
            self._collect(item, chain)
        for entries in self.events.values():
            entries.sort(key=lambda entry: entry[0])
        self.locals = {name for name, entries in self.events.items()
                       if any(entry[1] in _REBINDS for entry in entries)} \
            - self.globals - self.nonlocals
        for key, item, item_chain in self.global_items.values():
            # A rebind through globals() acts as `global NAME; NAME = ...`, which a
            # function with its own local of that name cannot be read through.
            if self is not self.module and key in self.locals:
                self.opaque = True
            self._add(key, item, "unknown", None, item_chain)
            self.events[key].sort(key=lambda entry: entry[0])
        for first, second, _since, item, item_chain in self.aliases:
            # A dict shared with a module name or os.environ can be written through
            # either name from another scope, where this scope's aliases are not seen.
            if self is self.module or not {first, second} <= self.locals:
                for name in (first, second):
                    self._add(name, item, "escape", None, item_chain)
                    self.events[name].sort(key=lambda entry: entry[0])

    def _add(self, name, node, kind, value, chain):
        if kind in _REBINDS and name in _OS_NAMES | _ENVIRON_NAMES and not _imports_os(
                node, name):
            self.opaque = True  # the module's name for os or os.environ means something else
        self.events.setdefault(name, []).append(
            (-1 if node is self.node else _ORDER[id(node)], kind, value, chain))

    def _keyed(self, name, node, key, sure, maybe, chain):
        if _is_home_key(key):
            self._add(name, node, sure, None, chain)
        elif not isinstance(key, ast.Constant):
            self._add(name, node, maybe, None, chain)

    def _merged(self, name, node, value, chain, kind="merge"):
        if not _home_less_dict(value) or kind == "aug-merge":
            self._add(name, node, kind, value, chain)

    def _bind(self, target, value, node, chain):
        source = _unwrap(value)
        if isinstance(source, ast.Name) and not isinstance(target, ast.Name):
            # Stored into a container or an attribute: it may change elsewhere.
            self._add(source.id, node, "escape", None, chain)
        if isinstance(target, ast.Name):
            self._add(target.id, node, "bind", value, chain)  # no value: unjudged
            shared = _dict_name(source)
            if shared is not None and shared != target.id:
                self.aliases.append((target.id, shared, _ORDER[id(node)],
                                     node, chain))
        elif isinstance(target, (ast.Tuple, ast.List)):
            pairable = (value is not None and isinstance(value, (ast.Tuple, ast.List))
                        and len(value.elts) == len(target.elts)
                        and not any(isinstance(e, ast.Starred)
                                    for e in (*target.elts, *value.elts)))
            for index, element in enumerate(target.elts):
                self._bind(element, value.elts[index] if pairable else None, node, chain)
        elif isinstance(target, ast.Starred):
            self._bind(target.value, None, node, chain)
        elif isinstance(target, ast.Subscript) and _dict_name(target.value):
            self._keyed(_dict_name(target.value), node, target.slice, "add", "maybe-add", chain)

    def _collect(self, node, chain):
        body = chain + ((id(node), "body"),)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._bind(target, node.value, node, chain)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            self._bind(node.target, node.value, node, chain)
        elif isinstance(node, ast.NamedExpr):
            self._bind(node.target, node.value, node, chain)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.op, ast.BitOr):
                self._merged(node.target.id, node, node.value, chain, "aug-merge")
            else:
                self._add(node.target.id, node, "unknown", None, chain)
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr) \
                and _dict_name(node.target) == _OS_ENVIRON:
            self._merged(_OS_ENVIRON, node, node.value, chain)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for name in _store_names(node.target):
                self._add(name, node.target, "unknown", None, body)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for name in _store_names(item.optional_vars):
                        self._add(name, item.optional_vars, "unknown", None, body)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self._add(node.name, node.type, "unknown", None, body)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                self._add((alias.asname or alias.name).split(".")[0], node, "unknown",
                          None, chain)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            self._add(node.name, node, "unknown", None, chain)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            self._add(node.rest, node, "unknown", None, chain)
        elif isinstance(node, ast.comprehension):
            for name in _store_names(node.target):
                self._add(name, node.target, "unknown", None, chain)
        elif isinstance(node, ast.Global):
            self.globals.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            self.nonlocals.update(node.names)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._add(target.id, node, "unbind", None, chain)
                elif isinstance(target, ast.Subscript) and _dict_name(target.value):
                    self._keyed(_dict_name(target.value), node, target.slice, "strip",
                                "maybe-strip", chain)
        if isinstance(node, ast.Call):
            self._call(node, chain)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) \
                and id(node) not in self.call_funcs and node.attr not in _READ_METHODS \
                and not _is_environ(node) and not _is_os(node.value):
            # A bound method or attribute taken off the dict (`f = env.pop`) may
            # change it wherever it is called.
            self._add(_dict_name(node.value), node, "escape", None, chain)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)) and isinstance(
                getattr(node, "ctx", ast.Load()), ast.Load):
            for element in node.elts:
                if isinstance(element, ast.Name):
                    self._add(element.id, node, "escape", None, chain)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is not None and isinstance(value, ast.Name):
                    self._add(value.id, node, "escape", None, chain)

    def _call(self, node, chain):
        func = node.func
        copier = isinstance(func, ast.Name) and func.id in _COPIERS
        runner = isinstance(func, ast.Attribute) and func.attr in _RUNNER_ATTRS
        for arg in [*node.args, *(k.value for k in node.keywords if k.arg is not None
                                  and not (runner and k.arg == "env"))]:
            if (_is_environ(arg) and not copier) or (
                    _is_os(arg) and _call_name(func) in ("setattr", "delattr")
                    and not (len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                             and node.args[1].value != "environ")):
                self._add(_OS_ENVIRON, node, "escape", None, chain)
            elif isinstance(arg, ast.Name) and not copier:
                self._add(arg.id, node, "escape", None, chain)
        if isinstance(func, ast.Name) and func.id in _OPAQUE \
                and id(node) not in self.global_items:
            self.opaque = True
        if (isinstance(func, ast.Attribute) and func.attr in ("putenv", "unsetenv")
                and _is_os(func.value)) or (isinstance(func, ast.Name)
                                            and func.id in _ENV_FUNCTION_NAMES):
            self._add(_OS_ENVIRON, node, "escape", None, chain)
        if isinstance(func, ast.Attribute) and func.attr in ("setenv", "delenv") and node.args:
            sure, maybe = ("add", "maybe-add") if func.attr == "setenv" else ("strip",
                                                                               "maybe-strip")
            self._keyed(_OS_ENVIRON, node, node.args[0], sure, maybe, chain)
        name = _dict_name(func.value) if isinstance(func, ast.Attribute) else None
        if name is not None:
            method = func.attr
            if method == "pop" and node.args:
                self._keyed(name, node, node.args[0], "strip", "maybe-strip", chain)
            elif method == "popitem":
                self._add(name, node, "maybe-strip", None, chain)
            elif method == "clear":
                self._add(name, node, "strip", None, chain)
            elif method == "setdefault" and node.args:
                self._keyed(name, node, node.args[0], "add", "maybe-add", chain)
            elif method == "update":
                if any(k.arg == "HOME" for k in node.keywords):
                    self._add(name, node, "add", None, chain)
                for value in [*node.args, *(k.value for k in node.keywords if k.arg is None)]:
                    self._merged(name, node, value, chain)
            elif method not in _READ_METHODS:
                self._add(name, node, "escape", None, chain)

    def mutates(self, name) -> bool:
        """Whether this scope writes, strips, rebinds or passes on a name it
        does not own (a free name, or one under `global` or `nonlocal`)."""
        if name in self.locals:
            return False
        return bool(self.events.get(name))

    def any_opaque(self) -> bool:
        """Whether some scope of the module calls exec, eval, globals, locals or vars."""
        return any(scope.opaque for scope in self.module.all_scopes)

    def environ_changed_elsewhere(self, kinds) -> bool:
        """Whether another scope of the module changes os.environ in one of `kinds`."""
        return any(entry[1] in kinds for scope in self.module.all_scopes
                   if scope is not self for entry in scope.events.get(_OS_ENVIRON, ()))

    def verdict(self, node, at, chain, seen=frozenset()) -> str:
        """Whether `node`, used at `at` (its place in evaluation order) under
        `chain`, carries HOME. A name or os.environ is read as it stands at
        `at`; a spread, dict() or `|` copies it when that expression runs."""
        while isinstance(node, ast.Call) and getattr(node.func, "id", "") == "carry_clock_shift" \
                and node.args:
            node = node.args[0]
        if isinstance(node, ast.NamedExpr):
            node = node.value
        if (isinstance(node, ast.Constant) and node.value is None) \
                or _environ_value(node):
            if self.module.any_opaque() or self.environ_changed_elsewhere(_ENVIRON_REMOVALS):
                return UNKNOWN
            state = self._name_state(_OS_ENVIRON, at, chain, seen)
            if state != HAS and self.environ_changed_elsewhere(_ENVIRON_ADDS):
                return UNKNOWN
            return state
        if isinstance(node, ast.Name):
            if self.module.any_opaque():
                return UNKNOWN
            state = self._name_state(node.id, at, chain, seen)
            return UNKNOWN if state is None else state
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            when = _ORDER[id(node)]
            sides = {self.verdict(node.left, when, chain, seen),
                     self.verdict(node.right, when, chain, seen)}
            return HAS if HAS in sides else LACKS if sides == {LACKS} else UNKNOWN
        if isinstance(node, ast.Dict):
            if any(_is_home_key(k) for k in node.keys):
                return HAS
            if any(k is not None and not isinstance(k, ast.Constant) for k in node.keys):
                return UNKNOWN
            parts = [(v, _ORDER[id(v)]) for k, v in zip(node.keys, node.values) if k is None]
            return self._parts(parts, chain, seen)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "dict":
            if any(k.arg == "HOME" for k in node.keywords):
                return HAS
            parts = [*node.args[:1], *(k.value for k in node.keywords if k.arg is None)]
            return self._parts([(part, _ORDER[id(node)]) for part in parts], chain, seen)
        return UNKNOWN

    def _parts(self, parts, chain, seen) -> str:
        verdicts = {self.verdict(part, when, chain, seen) for part, when in parts}
        return HAS if HAS in verdicts else UNKNOWN if UNKNOWN in verdicts else LACKS

    def _name_state(self, name, at, chain, seen):
        if name == _OS_ENVIRON:
            state = HAS
        elif self is self.module and at != float("inf") and any(
                scope.mutates(name) for scope in self.all_scopes if scope is not self):
            # Module code may call a function that writes the name before the call
            # (module_state, reading the module's end for a function, checks itself).
            return UNKNOWN
        elif name in self.locals:
            state = None
        elif self.parent is not None and self.parent.binds_freely(name):
            return UNKNOWN
        elif self is self.module:
            state = None
        else:
            state = self.module.module_state(name, self, seen)
        loops = [key for key in chain if key in self.loop_bodies]
        entries = self.events.get(name, ())
        before = [e for e in entries if e[0] < at]
        back = [e for e in entries if e[0] > at and any(loop in e[3] for loop in loops)]
        state = self._apply(name, before, chain, state, seen)
        state = _join(state, self._apply(name, back, chain, state, seen))
        if self._alias_written(name, at, loops) or any(
                entry[1] == "closure" for entry in before + back):
            return UNKNOWN
        return state

    def _apply(self, name, entries, chain, state, seen):
        """Apply entries in order to `state`, as seen from code whose enclosing
        bodies are `chain`. An entry in a body that encloses that code is
        definite. Entries in one compound statement that does not are taken
        together: each arm runs from the state before it, and the arms' results
        are joined with that state, except that an if with an else (or a
        conditional expression) joins its two arms alone, since one of them
        always runs."""
        index = 0
        while index < len(entries):
            entry = entries[index]
            if entry[3] == chain[:len(entry[3])]:
                state = self._step(name, state, entry, seen)
                index += 1
                continue
            depth = next(i for i, key in enumerate(entry[3])
                         if i >= len(chain) or key != chain[i])
            owner = entry[3][depth][0]
            arms: dict = {}
            while index < len(entries):
                candidate = entries[index][3]
                if len(candidate) <= depth or candidate[:depth] != entry[3][:depth] \
                        or candidate[depth][0] != owner:
                    break
                arms.setdefault(candidate[depth], []).append(entries[index])
                index += 1
            results = [self._apply(name, arm, arm[0][3][:depth + 1], state, seen)
                       for arm in arms.values()]
            node = self.nodes.get(owner)
            if isinstance(node, (ast.If, ast.IfExp)) and node.orelse and len(results) == 2:
                state = _join(*results)
            else:
                for result in results:
                    state = _join(state, result)
        return state

    def _alias_written(self, name, at, loops) -> bool:
        """Whether a name sharing this dict (`x = name` or `name = x`) is written
        after the sharing began and before the call (or later in its loop)."""
        for first, second, since, _node, alias_chain in self.aliases:
            if name not in (first, second) or (
                    since >= at and not any(loop in alias_chain for loop in loops)):
                continue
            partner = second if name == first else first
            for when, kind, _value, chain in self.events.get(partner, ()):
                if kind in _WRITES and when > since and (
                        when < at or any(loop in chain for loop in loops)):
                    return True

    def binds_freely(self, name) -> bool:
        """Whether a nested function's free `name` refers to this function
        (or one enclosing it) rather than to the module."""
        return name in self.locals or (self.parent is not None and self.parent.binds_freely(name))

    def module_state(self, name, user, seen):
        if any(scope.mutates(name) for scope in self.all_scopes if scope is not user
               and scope is not self):
            return UNKNOWN
        state = self._name_state(name, float("inf"), ((id(self.node), "body"),),
                                 seen)
        return UNKNOWN if state is None else state

    def _step(self, name, state, entry, seen):
        at, kind, value, chain = entry
        if kind == "bind":
            key = (id(self.node), name, at)
            new = UNKNOWN if key in seen else self.verdict(value, at, chain, seen | {key})
        elif kind in ("unknown", "escape", "closure"):
            new = UNKNOWN
        elif kind == "unbind":
            new = None
        elif kind == "add":
            new = HAS
        elif kind == "strip":
            new = LACKS
        elif kind == "maybe-strip":
            new = LACKS if state == LACKS else UNKNOWN
        else:  # merge, aug-merge, and maybe-add (a merge of something unjudged)
            merged = self.verdict(value, at, chain, seen)
            current = UNKNOWN if state is None else state
            new = HAS if HAS in (current, merged) else \
                UNKNOWN if UNKNOWN in (current, merged) else LACKS
        return new


def _bare_runners(tree) -> set:
    """Local names bound to a subprocess runner by `from subprocess import ...`."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            names.update(alias.asname or alias.name for alias in node.names
                         if alias.name in _SUBPROCESS_RUNNERS)
    return names


def _scopes(tree) -> list:
    """The module scope and one scope per function, lambda and class. Each knows
    the function (or module) its free names resolve to, and the scope whose
    body defines it. A name a nested scope writes, strips, rebinds or passes on
    gets a closure write in the nearest enclosing function that binds it, at
    the statement that defines the nested scope: UNKNOWN from there on."""
    _module_aliases(tree)
    global _ORDER, _FIRST_ITERS
    _ORDER = _evaluation_order(tree)
    _FIRST_ITERS = frozenset(id(node.generators[0].iter) for node in ast.walk(tree)
                             if isinstance(node, _COMPREHENSIONS))
    module = _Scope(tree, None, None)
    scopes = [module]

    def visit(node, parent, container):
        for item, chain in _walk(node):
            if isinstance(item, (*_FUNCLIKE, ast.ClassDef)):
                scope = _Scope(item, parent, module)
                scope.container, scope.def_chain = container, chain
                scopes.append(scope)
                visit(item, scope if isinstance(item, _FUNCLIKE) else parent, scope)

    visit(tree, None, module)
    module.all_scopes = scopes
    for scope in scopes[1:]:
        for name in [n for n in scope.events if n != _OS_ENVIRON and scope.mutates(n)]:
            inner = scope
            while inner.container is not module:
                outer = inner.container
                if isinstance(outer.node, _FUNCLIKE) and name in outer.locals:
                    outer._add(name, inner.node, "closure", None, inner.def_chain)
                    break
                inner = outer
    return scopes


def _env_judgements(tree) -> list:
    """(lineno, verdict) for every runner call's `env=` in a parsed module,
    each judged in the scope (function, class or module body) it sits in."""
    bare = _bare_runners(tree)
    judged = []
    for scope in _scopes(tree):
        if isinstance(scope.node, ast.Lambda):
            continue  # calls inside a lambda are unjudged
        for node, chain in scope.pairs:
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not ((isinstance(func, ast.Attribute) and func.attr in _RUNNER_ATTRS)
                    or (isinstance(func, ast.Name) and func.id in bare)):
                continue
            for kw in node.keywords:
                if kw.arg == "env":
                    judged.append((node.lineno, scope.verdict(
                        kw.value, _ORDER[id(node)], chain)))
    return judged


def _seeded_rig(tmp_path, monkeypatch, extra=None):
    """A git-indexed plugin tree with 102 clean test sources, plus `extra`
    ({relative path: source}) tracked beside them; the scan reads it."""
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    tests_dir = plugin / "tests"
    tests_dir.mkdir(parents=True)
    # The production arm carries its own `len(files) > 100` denominator guard,
    # so the fixture must clear it to reach the assertion under test.
    (tests_dir / "test_suite_home_isolation.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")
    for index in range(100):
        (tests_dir / f"test_known_{index}.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8")
    known = plugin / "skills/pact-handoff-harvest/test_skill_loading_harvest.py"
    known.parent.mkdir(parents=True)
    known.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    for relative, source in (extra or {}).items():
        (plugin / relative).write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    monkeypatch.setitem(globals(), "_PLUGIN", plugin)
    return plugin


@pytest.mark.parametrize("relative", [
    "tests/zzprobe.py", "skills/zzprobe/test_zzprobe.py",
], ids=["tests", "skills"])
def test_untracked_test_file_cannot_join_the_home_scan(
        tmp_path, monkeypatch, relative):
    """Both populations read the index; an untracked test file is invisible.

    Reverting ``_test_source_files`` to the two filesystem globs keeps the
    shipped tree green; this arm drives the HOME scan and reddens naming the
    untracked file. The seeded tracked files are the positive control.
    """
    plugin = _seeded_rig(tmp_path, monkeypatch)

    assert len(_test_source_files()) == 102, (
        f"positive control: expected the 102 seeded sources, got "
        f"{len(_test_source_files())}")
    probe = plugin / relative
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "import subprocess\n\n\n"
        "def test_child():\n"
        "    env = {'X': '1'}\n"
        "    subprocess.run(['true'], env=env)\n", encoding="utf-8")

    test_no_child_environment_built_in_a_test_leaves_out_home()


def test_no_child_environment_built_in_a_test_leaves_out_home():
    """Every `env=` a test passes to a subprocess runner carries HOME, as far as
    the shapes below can show. A child env without HOME finds its home through
    the password database: the real one.

    RUNNERS read: a call whose attribute is run, Popen, check_output,
    check_call, call, create_subprocess_exec or create_subprocess_shell (any
    receiver), and a bare name imported from subprocess (`from subprocess
    import run`, aliases included).

    JUDGED SHAPES (JUDGED_SHAPES; each is pinned to a verdict row). Every
    function, class body and module body is judged on its own; a verdict is
    HAS, LACKS or UNKNOWN, and only LACKS is flagged.

    VALUES. os.environ, dict(os.environ), os.environ.copy() and env=None
    (inherit) have HOME, unless this scope removed HOME from os.environ before
    the call (`os.environ.pop("HOME")`, `del os.environ["HOME"]`,
    `monkeypatch.delenv("HOME")`; `.setenv("HOME", ...)` restores it). Another
    scope of the module (another function, a fixture, a nested function or
    lambda, a class body, or module-level code) changes the verdict in two
    ways. A change there that may remove HOME or hands os.environ on (a HOME or
    non-constant-key pop, del, delenv, clear or popitem, os.environ passed to a
    call, `setattr(os, "environ", ...)`, os.putenv or os.unsetenv) makes them
    UNKNOWN in every scope. A change there that can only add (a HOME or
    non-constant-key setenv or assignment, setdefault, update or `|=`) makes
    them UNKNOWN only where this scope has already removed HOME, since it might
    restore it; otherwise they still have HOME. A setenv or delenv of a
    constant key other than HOME, or an update with a dict literal that has no
    HOME key, changes nothing anywhere. A dict literal or dict(...) with a HOME
    key or keyword has HOME; one with a key that is not a constant is UNKNOWN.
    Otherwise its spreads and its dict() argument decide: HAS if any has HOME,
    UNKNOWN if any is unjudged, LACKS if all lack it (no spread at all: LACKS).
    `a | b` is HAS if either side has HOME, LACKS if both lack it, otherwise
    UNKNOWN. carry_clock_shift(x) and `(n := x)` are judged as x.

    ORDER. Bindings and writes are applied in the order Python evaluates them,
    not by line and column: an assignment's value before its target, a
    conditional expression's test before its arms, a comprehension's first
    iterable once before the loop and the rest of it on every pass, a loop,
    with or except target before its block, a nested def's decorators before
    its default values, a call's arguments before the call. A name or
    os.environ given as env= is read as it stands when the runner is called; a
    spread is copied where it stands, and dict() and `|` copy when they run.

    NAMES. A name bound in the scope is judged from its bindings and writes
    before the call (see ORDER). Bindings it can read: `=` (tuple and list
    unpacking paired element by element with a same-length tuple or list),
    `x: T = v` and `(x := v)`. Bindings it cannot read make the name UNKNOWN:
    a for target, a with or except target, an import, a starred or unpaired
    unpacking, an augmented assignment other than `|=`, and a match capture;
    `del x` unbinds it (a path where it is unbound adds nothing to a join).
    Like Python, a scope that assigns, deletes or `|=`s a name has its own
    local of that name. Writes: `env["HOME"] = `, `env.update(HOME=...)`,
    `env.setdefault("HOME", ...)` and a merge of something with HOME
    (`env.update(x)`, `env.update(**x)`, `env |= x`) make it HAS;
    `env.pop("HOME")`, `del env["HOME"]` and `env.clear()` make it LACKS; a
    write or removal with a non-constant key (`env[k] = v`, `env.pop(k)`,
    `env.popitem()`) and a merge of something unjudged make it UNKNOWN unless
    the verdict already rules that out; another constant key, or a merge of a
    dict with no HOME key, changes nothing. A name becomes UNKNOWN from the
    point it is passed to any call other than dict(), carry_clock_shift() or
    a runner's env= (`*env`, which hands on only its keys, does not count),
    stored into a container, a dict value or an attribute, or
    has any method called or taken off it (`f = env.pop`) other than the
    writes above and get, copy, items, keys and values. While another name
    shares its dict (`b = a`, `b = (n := a)`, `b = carry_clock_shift(a)`), a
    write through either makes both UNKNOWN, as does an alias made later in a
    loop around the call; when
    the sharing happens at module level, or one of the two is a module name or
    os.environ, both are UNKNOWN from that point, since either may be written
    from another scope. os.environ itself becomes UNKNOWN from the point
    os.environ is passed to any call, `os` is passed to setattr or delattr
    with an attribute name that is not a constant other than "environ"
    (including `monkeypatch.setattr(os, "environ", ...)`), os.putenv or
    os.unsetenv is called, a method is taken off os.environ, or os.environ gets
    a method call other than the writes and reads listed above for a name (its
    writes, such as `os.environ.update(...)`, `.setdefault(...)` and
    `.clear()`, are judged exactly as they are for a name).

    SPELLINGS. The scan sees os only as the name `os` or a name bound by `import
    os as o`; os.environ only as `<os name>.environ` or a name bound by `from
    os import environ` (aliases included); and os.putenv and os.unsetenv only
    as `<os name>.putenv(...)` and `<os name>.unsetenv(...)` or names bound by
    `from os import putenv, unsetenv`. A module that binds its name for os or
    os.environ to anything else (a parameter, an assignment, an import of
    something else) has every name, and os.environ, UNKNOWN.

    TEXT AND NAMESPACES. A module in which any scope calls exec, eval,
    globals, locals or vars has every name, and os.environ, UNKNOWN, with one
    exception: `<obj>.setitem(globals(), "NAME", ...)` and
    `<obj>.delitem(globals(), "NAME")` with a constant NAME count only as a
    rebind of NAME by the scope that makes them (see MODULE NAMES). A function
    that also binds its own local NAME makes the module UNKNOWN throughout
    instead.

    CONDITIONAL CODE. A binding or write counts as definite only when the
    statement body that holds it also holds the call. Anything else is read as
    conditional, one compound statement at a time: each of its arms is applied
    to the verdict before the statement, and the results are joined (equal
    verdicts stay, unequal ones become UNKNOWN). For an if with an else, and a
    conditional expression, the two arms are joined with each other alone,
    since one of them always runs; for every other statement the verdict before
    it joins in too, since none of its arms need run. That covers every if,
    elif and else branch, every for and while body and their else, every try
    body, except handler, else and finally, every with body (read as possibly
    not running), every match case, a conditional expression branch, the right
    side of `and`/`or`, and a comprehension. When the call sits inside a loop,
    the bindings and writes later in that loop are applied the same way and
    joined in (the next pass). Reachability is not read: a return, raise,
    break or continue does not change how the code around it is read.

    MODULE NAMES. A name a function never binds is read from the module's
    top-level bindings and writes (all of them: module statements run before
    any test, so the last top-level binding counts), then from the function's
    own bindings (under `global`) and writes before the call, unless another
    function, lambda or class body in the module writes, strips, rebinds
    (including under `global`) or passes it on, in which case it is UNKNOWN. A
    name an enclosing function binds (a closure) is UNKNOWN. Module-level code
    reads a module name the same way: UNKNOWN when any function, lambda or
    class body writes it, since module code may call that function first.

    NESTED SCOPES. A name that a nested function, lambda or class body writes,
    strips, rebinds (including under `nonlocal`) or passes on is UNKNOWN in the
    nearest enclosing function that binds it, from the statement that defines
    the nested scope onwards, a later rebinding included (class bodies in
    between are skipped, as Python skips them for closures; a class body's own
    names are not seen by its methods). This holds however deep the nesting,
    and whether or not the nested scope is ever called. A nested scope's
    decorators, default values and class bases and keywords run in the scope
    around it and are read there; annotations are not read. A method that
    changes a module name
    is covered by MODULE NAMES, and one that changes an attribute of self
    touches an attribute, which is unjudged.

    EVERY OTHER SHAPE IS UNJUDGED AND READ AS UNKNOWN, so it is never flagged.
    Examples: a helper call (`_env(root)`, `world.env()`); a parameter; an
    attribute or subscript (`w.env`, `calls[0][1]["env"]`); a conditional
    expression or `and`/`or` as the value itself (`a if c else b`, `p or {}`);
    a comprehension (`{k: v for k, v in os.environ.items()}`); calls inside a
    lambda; and runners taking env positionally (os.execve, os.spawn*e,
    os.posix_spawn).

    NOT SEEN, and judged as if they never happened (a test that uses one is
    outside the scan): a change made by code in another module (a conftest
    fixture, an imported helper) to os.environ, the process environment or
    this module's names; and os.environ, os or this module's names reached
    through any spelling not listed above, including:
    - os.environ through another name made by an expression (`e = os.environ
      or {}`, `box = [os.environ]`), or replaced or deleted (`os.environ = {}`,
      `del os.environ`);
    - os bound by assignment (`o = os`), or os names from `from os import *`;
    - `getattr(os, "environ")` or `getattr(os, name)`;
    - `sys.modules["os"]`, `importlib.import_module("os")`, `__import__("os")`;
    - another module's `.os` attribute (`subprocess.os.environ`);
    - os.environb, and os.putenv or os.unsetenv taken as values (`f =
      os.unsetenv`) or imported from another module (`from posix import
      unsetenv`); `os.__dict__`;
    - a monkeypatch target given as a string (`monkeypatch.setattr(
      "os.environ", ...)`, or one naming this module);
    - this module's names through `__globals__`, `f_globals`, `__dict__`,
      `sys.modules[__name__]` or an import of the module itself.
    """
    files = _test_source_files()
    misses = []
    for path in files:
        if "fixtures" in path.parts:
            continue
        tree = ast.parse(path.read_text(), str(path))
        misses += [f"{path.relative_to(_PLUGIN)}:{line}"
                   for line, verdict in _env_judgements(tree) if verdict == LACKS]
    assert len(files) > 100, files
    assert not sorted(set(misses)), sorted(set(misses))


def _verdict(source: str, scope_source: str = "") -> str:
    """The scan's verdict for one env expression, inside a function whose body
    is `scope_source`; it goes through _env_judgements, the scan's own path."""
    module = ast.parse(f"def probe(param):\n{textwrap.indent(scope_source or 'pass', '    ')}\n"
                       f"    subprocess.run([], env={source})\n")
    call_line = module.body[0].body[-1].lineno
    found = [verdict for line, verdict in _env_judgements(module) if line == call_line]
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("source, scope_source, verdict", [
    # The os.environ allowance: a spread or copy of it carries the sandbox HOME.
    ("{**os.environ}", "", HAS),
    ("{**os.environ, 'X': '1'}", "", HAS),
    ("{**dict(os.environ), 'X': '1'}", "", HAS),
    ("{**os.environ.copy()}", "", HAS),
    ("{**base, 'X': '1'}", "base = os.environ.copy()", HAS),
    ("dict(os.environ, X='1')", "", HAS),
    ("os.environ.copy()", "", HAS),
    ("None", "", HAS),
    # An explicit HOME key wins over any spread.
    ("{**param, 'HOME': '/sandbox'}", "", HAS),
    ("dict(param, HOME='/sandbox')", "", HAS),
    # A spread of anything not shown to carry HOME, with no HOME key, lacks it.
    # A spread of something unjudged makes the verdict unknown.
    ("{**param}", "", UNKNOWN),
    ("{**param, 'X': '1'}", "", UNKNOWN),
    ("{**helper(), 'X': '1'}", "", UNKNOWN),
    ("dict(**param)", "", UNKNOWN),
    ("dict(**param, X='1')", "", UNKNOWN),
    ("{**param, 'HOME': '/h'}", "", HAS),
    ("{**{'X': '1'}, **{'Y': '2'}}", "", LACKS),
    ("{**{'X': '1'}, **param}", "", UNKNOWN),
    ("{**base}", "base = {'PATH': '/bin'}", LACKS),
    ("{'X': '1'}", "", LACKS),
    ("dict(X='1')", "", LACKS),
    ("dict(base, X='1')", "base = {'PATH': '/bin'}", LACKS),
    # Names resolve through this scope, including through carry_clock_shift.
    ("carry_clock_shift(env)", "env = {'PATH': '/bin'}", LACKS),
    ("carry_clock_shift(env)", "env = {'HOME': '/h'}", HAS),
    ("env", "base = {'PATH': '/bin'}\nenv = carry_clock_shift(base)", LACKS),
    # A name bound only after the use is unbound there.
    ("env", "env = carry_clock_shift(base)\nbase = {'PATH': '/bin'}", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv = dict(env, HOME='/h')", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv = dict(env, X='1')", LACKS),
    # HOME stripped from a name after it is built.
    ("env", "env = dict(os.environ)\nenv.pop('HOME', None)", LACKS),
    ("env", "env = dict(os.environ)\ndel env['HOME']", LACKS),
    ("carry_clock_shift(env)", "env = dict(os.environ)\nenv.pop('HOME')", LACKS),
    # HOME stripped, then the name spread or copied: still lacking.
    ("{**env}", "env = dict(os.environ)\nenv.pop('HOME')", LACKS),
    ("dict(env, X='1')", "env = dict(os.environ)\nenv.pop('HOME')", LACKS),
    ("carry_clock_shift({**env})", "env = dict(os.environ)\ndel env['HOME']", LACKS),
    # HOME written after the dict is built.
    ("env", "env = {'PATH': '/bin'}\nenv['HOME'] = '/h'", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.update(HOME='/h')", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.update({'HOME': '/h'})", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.setdefault('HOME', '/h')", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv |= {'HOME': '/h'}", HAS),
    ("env", "env = dict(os.environ)\nenv.pop('HOME')\nenv['HOME'] = '/h'", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv['HOME'] = '/h'\nenv.pop('HOME')", LACKS),
    ("env", "env = {'PATH': '/bin'}\nenv['X'] = '1'", LACKS),
    # Merges, clear() and writes whose key is not a constant.
    ("env", "env = {'PATH': '/bin'}\nenv.update(os.environ)", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.update(dict(os.environ))", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.update(**os.environ)", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv |= os.environ", HAS),
    ("env", "base = dict(os.environ)\nenv = {'PATH': '/bin'}\nenv.update(base)", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.update(param)", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv |= param", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv.update({'X': '1'})", LACKS),
    ("env", "env = dict(os.environ)\nenv.update({'X': '1'})", HAS),
    ("env", "env = {'PATH': '/bin'}\nenv.update(param)\nenv['HOME'] = '/h'", HAS),
    ("env", "env = {'PATH': '/bin'}\nfor k in ('HOME', 'PATH'):\n    env[k] = os.environ[k]",
     UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv[key] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv.setdefault(key, '/h')", UNKNOWN),
    ("env", "env = dict(os.environ)\nenv[key] = '/h'", HAS),
    ("env", "env = {'PATH': '/bin', 'HOME': '/h'}\nenv.clear()", LACKS),
    ("env", "env = dict(os.environ)\nenv.pop(key)", UNKNOWN),
    ("env", "env = dict(os.environ)\ndel env[key]", UNKNOWN),
    ("env", "env = dict(os.environ)\nenv.popitem()", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv.pop(key)", LACKS),
    # Unions and annotated assignments.
    ("{'X': '1'} | {'HOME': '/h'}", "", HAS),
    ("os.environ | {'X': '1'}", "", HAS),
    ("{'X': '1'} | {'Y': '2'}", "", LACKS),
    ("{'X': '1'} | param", "", UNKNOWN),
    ("env", "env: dict = {'PATH': '/bin'}", LACKS),
    ("env", "env: dict = dict(os.environ)", HAS),
    # Every binding form rebinds the name.
    ("env", "env = {'PATH': '/bin'}\nfor env in envs:\n    pass", UNKNOWN),
    ("env", "env = dict(os.environ)\nenv, extra = {'PATH': '/bin'}, 1", LACKS),
    ("env", "env = dict(os.environ)\n(env, extra), y = ({'PATH': '/bin'}, 1), 2", LACKS),
    ("env", "env = {'PATH': '/bin'}\nenv, extra = dict(os.environ), 1", HAS),
    ("env", "env = dict(os.environ)\nenv, extra = make()", UNKNOWN),
    ("env", "env = dict(os.environ)\n*env, = [{'PATH': '/bin'}]", UNKNOWN),
    ("env", "env = dict(os.environ)\nfirst, *env = [1, {'PATH': '/bin'}]", UNKNOWN),
    ("env", "if (env := {'PATH': '/bin'}):\n    pass", LACKS),
    ("(env := {'PATH': '/bin'})", "", LACKS),
    ("env", "env = {'PATH': '/bin'}\nwith ctx() as env:\n    pass", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\ntry:\n    pass\nexcept Exception as env:\n    pass", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nimport os as env", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv += extra", UNKNOWN),
    ("env", "env = dict(os.environ)\ndel env", UNKNOWN),
    ("env", "env = dict(os.environ)\nenv: dict = {'PATH': '/bin'}", LACKS),
    # A binding or write in a block that does not enclose the call is
    # conditional: it joins the verdict before it.
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = dict(os.environ)\nif c:\n    env.pop('HOME')", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    pass\nelse:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    pass\nelif d:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nfor x in y:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nfor x in y:\n    pass\nelse:\n"
     "    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nwhile c:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\ntry:\n    env['HOME'] = '/h'\nexcept E:\n    pass", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\ntry:\n    pass\nfinally:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nwith ctx():\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env.update(os.environ)", UNKNOWN),
    ("env", "env = dict(os.environ)\nif c:\n    env['HOME'] = '/h'", HAS),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env.pop('HOME')", LACKS),
    ("env", "if c:\n    env = {'PATH': '/bin'}\nelse:\n    env = {'X': '1'}", LACKS),
    ("env", "if c:\n    env = {'PATH': '/bin'}\nelse:\n    env = dict(os.environ)", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nx = 1 if c else env.update(os.environ)", UNKNOWN),
    # Rebinds and writes in branches, joined per statement.
    ("env", "env = dict(os.environ)\nif c:\n    env = {'PATH': '/bin'}", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env['HOME'] = '/h'\nelse:\n"
     "    env['HOME'] = '/h'", HAS),
    ("env", "env = dict(os.environ)\nif c:\n    env.pop('HOME')\nelse:\n"
     "    del env['HOME']", LACKS),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env['HOME'] = '/h'\nelif d:\n"
     "    env['HOME'] = '/h'\n"
            "else:\n    env['HOME'] = '/h'", HAS),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env['HOME'] = '/h'\nelif d:\n"
     "    env['HOME'] = '/h'", UNKNOWN),
    ("env", "if c:\n    env = dict(os.environ)\nelse:\n    env = {'HOME': '/h'}", HAS),
    ("env", "x = (env := {'X': '1'}) if c else (env := {'Y': '2'})", LACKS),
    ("env", "env = {'PATH': '/bin'}\nmatch x:\n    case 1:\n        env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nwhile c:\n    env.update(os.environ)", UNKNOWN),
    ("{**helper()}", "", UNKNOWN),
    ("{**b}", "b = {'PATH': '/bin'}", LACKS),
    # Writes on the right of and/or, in a comprehension, and match captures.
    ("env", "env = {'PATH': '/bin'}\nok = c and env.update(os.environ)", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nok = c or env.update(os.environ)", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nxs = [env.update(os.environ) for x in ys]", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nxs = {x: env.update(os.environ) for x in ys}", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nxs = {env.update(os.environ) for x in ys}", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nxs = list(env.update(os.environ) for x in ys)", UNKNOWN),
    # A key that is not a constant may be HOME.
    ("{k: '1'}", "", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nmatch x:\n    case {'k': env}:\n        pass", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nmatch x:\n    case {**env}:\n        pass", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nmatch x:\n    case [*env]:\n        pass", UNKNOWN),
    # Not judgeable from this scope: passed.
    ("helper()", "", UNKNOWN),
    ("param", "", UNKNOWN),
    ("dict(param, X='1')", "", UNKNOWN),
    ("w.env", "", UNKNOWN),
    ("{k: v for k, v in os.environ.items() if k != 'X'}", "", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nenv = helper()", UNKNOWN),
    ("env", "env = env", UNKNOWN),
])
def test_the_scanner_judges_each_env_shape(source, scope_source, verdict):
    assert _verdict(source, scope_source) == verdict


@pytest.mark.parametrize("probe, flagged", [
    ("def test_child():\n    base = {'PATH': '/bin'}\n"
     "    subprocess.run(['true'], env={**base, 'X': '1'})\n", True),
    ("def test_child():\n    env = {**os.environ, 'X': '1'}\n"
     "    subprocess.run(['true'], env=env)\n", False),
], ids=["spread-without-home", "environ-spread"])
def test_the_scan_flags_a_spread_env_without_home(tmp_path, monkeypatch, probe, flagged):
    """Through the whole scan: a tracked test whose child env spreads something
    that carries no HOME is named; one spreading os.environ is not."""
    _seeded_rig(tmp_path, monkeypatch, {
        "tests/test_zzspread.py": "import os\nimport subprocess\n\n\n" + probe})
    if flagged:
        with pytest.raises(AssertionError, match=r"tests/test_zzspread\.py:7"):
            test_no_child_environment_built_in_a_test_leaves_out_home()
    else:
        test_no_child_environment_built_in_a_test_leaves_out_home()


@pytest.mark.parametrize("probe, flagged_lines", [
    # The scan's own strip check, through carry_clock_shift.
    ("def test_child():\n    env = dict(os.environ)\n    env.pop('HOME')\n"
     "    subprocess.run(['true'], env=carry_clock_shift(env))\n", [9]),
    # Runners imported by bare name, aliased or not, and asyncio's.
    ("from subprocess import run\n\n\ndef test_child():\n"
     "    run(['true'], env={'X': '1'})\n", [10]),
    ("from subprocess import Popen as launch\n\n\ndef test_child():\n"
     "    launch(['true'], env={'X': '1'})\n", [10]),
    ("async def test_child():\n"
     "    await asyncio.create_subprocess_exec('true', env={'X': '1'})\n", [7]),
    # Names judged per function: a parameter passed in one function is not
    # judged by an unrelated literal bound in another.
    ("def test_one(env):\n    subprocess.run(['true'], env=env)\n\n\n"
     "def test_two():\n    env = {'X': '1'}\n    return env\n", []),
    # Positional env is unjudged: os.execve and posix_spawn are not read.
    ("def test_child():\n    os.execve('/bin/true', ['true'], {'X': '1'})\n", []),
], ids=["strip-through-wrapper", "bare-run", "aliased-popen", "asyncio-exec",
        "per-function", "positional-env-unjudged"])
def test_the_whole_scan_judges_these_shapes(tmp_path, monkeypatch, probe, flagged_lines):
    """Through the real scan, not a copy of its rules."""
    _seeded_rig(tmp_path, monkeypatch, {
        "tests/test_zzshape.py": "import asyncio\nimport os\nimport subprocess\n\n\n" + probe})
    expected = [f"tests/test_zzshape.py:{line}" for line in flagged_lines]
    if expected:
        with pytest.raises(AssertionError) as raised:
            test_no_child_environment_built_in_a_test_leaves_out_home()
        assert repr(expected) in str(raised.value), str(raised.value)
    else:
        test_no_child_environment_built_in_a_test_leaves_out_home()


# One row per JUDGED_SHAPES entry: a module source and the verdict of its
# single runner call, through _env_judgements (the scan's own path).
_SHAPE_ROWS = {
    "environ": ("def t():\n    subprocess.run([], env=os.environ.copy())\n", HAS),
    "none": ("def t():\n    subprocess.run([], env=None)\n", HAS),
    "dict-literal": ("def t():\n    subprocess.run([], env={'X': '1'})\n", LACKS),
    "spread": ("def t():\n    b = {'X': '1'}\n    subprocess.run([], env={**b})\n", LACKS),
    "dict-call": ("def t():\n    subprocess.run([], env=dict(os.environ, X='1'))\n", HAS),
    "union": ("def t():\n    subprocess.run([], env={'X': '1'} | {'Y': '2'})\n", LACKS),
    "name": ("def t():\n    e = {'X': '1'}\n    subprocess.run([], env=e)\n", LACKS),
    "annotated-binding": ("def t():\n    e: dict = {'X': '1'}\n    subprocess.run([], env=e)\n",
                          LACKS),
    "post-build-write": ("def t():\n    e = {'X': '1'}\n    e['HOME'] = '/h'\n"
                         "    subprocess.run([], env=e)\n", HAS),
    "strip": ("def t():\n    e = dict(os.environ)\n    e.pop('HOME')\n"
              "    subprocess.run([], env=e)\n", LACKS),
    "merge-write": ("def t():\n    e = {'X': '1'}\n    e.update(os.environ)\n"
                    "    subprocess.run([], env=e)\n", HAS),
    "variable-key-write": ("def t(k):\n    e = {'X': '1'}\n    e[k] = '1'\n"
                           "    subprocess.run([], env=e)\n", UNKNOWN),
    "unpacking-binding": ("def t():\n    e, x = {'X': '1'}, 1\n"
                          "    subprocess.run([], env=e)\n", LACKS),
    "walrus-binding": ("def t():\n    subprocess.run([], env=(e := {'X': '1'}))\n", LACKS),
    "unreadable-binding": ("def t(es):\n    e = {'X': '1'}\n    for e in es:\n"
                           "        subprocess.run([], env=e)\n", UNKNOWN),
    "module-name": ("E = {'X': '1'}\n\n\ndef t():\n    subprocess.run([], env=E)\n", LACKS),
    "conditional-write": ("def t(c):\n    e = {'X': '1'}\n    if c:\n        e['HOME'] = '/h'\n"
                          "    subprocess.run([], env=e)\n", UNKNOWN),
    "escape": ("def t():\n    e = {'X': '1'}\n    fix(e)\n    subprocess.run([], env=e)\n",
               UNKNOWN),
    "alias": ("def t():\n    e = dict(os.environ)\n    f = e\n    f.pop('HOME')\n"
              "    subprocess.run([], env=e)\n", UNKNOWN),
    "os-environ-change": ("def t(mp):\n    mp.delenv('HOME')\n"
                          "    subprocess.run([], env=os.environ.copy())\n", LACKS),
    "carry-clock-shift": ("def t():\n    e = {'X': '1'}\n"
                          "    subprocess.run([], env=carry_clock_shift(e))\n", LACKS),
    "bare-runner": ("from subprocess import run as go\n\n\ndef t():\n"
                    "    go([], env={'X': '1'})\n", LACKS),
    "asyncio-runner": ("async def t():\n"
                       "    await asyncio.create_subprocess_exec('x', env={'X': '1'})\n", LACKS),
}


def test_every_judged_shape_has_a_row():
    assert set(_SHAPE_ROWS) == set(JUDGED_SHAPES), (
        set(_SHAPE_ROWS) ^ set(JUDGED_SHAPES))


@pytest.mark.parametrize("shape", JUDGED_SHAPES)
def test_each_judged_shape_is_judged(shape):
    source, verdict = _SHAPE_ROWS[shape]
    assert [v for _, v in _env_judgements(ast.parse(source))] == [verdict]



def _judge_module(source: str) -> list:
    """The scan's verdicts for every runner call in a module source, in order."""
    return [verdict for _line, verdict in sorted(_env_judgements(ast.parse(source)))]


@pytest.mark.parametrize("source, verdicts", [
    # A name the function never binds resolves through the module.
    ("_ENV = {'PATH': '/bin'}\n\n\ndef test_x():\n    subprocess.run([], env=_ENV)\n",
     [LACKS]),
    ("_ENV = dict(os.environ)\n\n\ndef test_x():\n    subprocess.run([], env=_ENV)\n",
     [HAS]),
    ("_ENV = {'PATH': '/bin'}\n_ENV['HOME'] = '/h'\n\n\ndef test_x():\n"
     "    subprocess.run([], env=_ENV)\n", [HAS]),
    ("_ENV = {'PATH': '/bin'}\n\n\ndef test_x():\n    _ENV['HOME'] = '/h'\n"
     "    subprocess.run([], env=_ENV)\n", [HAS]),
    ("_ENV = {'HOME': '/h'}\n\n\ndef test_x():\n    subprocess.run([], env=_ENV)\n", [HAS]),
    # Module statements all run before any test, so the last top-level binding counts.
    ("_ENV = {'PATH': '/bin'}\n\n\ndef test_x():\n    subprocess.run([], env=_ENV)\n\n\n"
     "_ENV = dict(os.environ)\n", [HAS]),
    ("_ENV = dict(os.environ)\n\n\ndef test_x():\n    subprocess.run([], env=_ENV)\n\n\n"
     "_ENV = {'PATH': '/bin'}\n", [LACKS]),
    # ...unless another function writes, strips or rebinds it.
    ("_ENV = {'PATH': '/bin'}\n\n\ndef setup():\n    _ENV['HOME'] = '/h'\n\n\n"
     "def test_x():\n    subprocess.run([], env=_ENV)\n", [UNKNOWN]),
    ("_ENV = dict(os.environ)\n\n\ndef setup():\n    global _ENV\n    _ENV = {'X': '1'}\n"
     "\n\ndef test_x():\n    subprocess.run([], env=_ENV)\n", [UNKNOWN]),
    ("_ENV = dict(os.environ)\n\n\ndef setup():\n    _ENV.pop('HOME')\n\n\n"
     "def test_x():\n    subprocess.run([], env=_ENV)\n", [UNKNOWN]),
    # A local, a parameter or a closure variable is not the module's name.
    ("_ENV = {'PATH': '/bin'}\n\n\ndef test_x(_ENV):\n    subprocess.run([], env=_ENV)\n",
     [UNKNOWN]),
    ("_ENV = {'PATH': '/bin'}\n\n\ndef test_x():\n    subprocess.run([], env=_ENV)\n"
     "    _ENV = dict(os.environ)\n", [UNKNOWN]),
    ("def outer():\n    env = {'X': '1'}\n\n    def inner():\n"
     "        subprocess.run([], env=env)\n", [UNKNOWN]),
    ("env = dict(os.environ)\n\n\ndef outer():\n    env = {'X': '1'}\n\n    def inner():\n"
     "        subprocess.run([], env=env)\n", [UNKNOWN]),
    ("if c:\n    _ENV = {'PATH': '/bin'}\nelse:\n    _ENV = dict(os.environ)\n\n\n"
     "def test_x():\n    subprocess.run([], env=_ENV)\n", [UNKNOWN]),
    ("e = {'X': '1'}\n\n\ndef test_x(envs):\n"
     "    [subprocess.run([], env=e) for e in envs]\n", [UNKNOWN]),
    # A write inside the block that encloses the call, before it, is definite.
    ("def test_x(c):\n    env = {'X': '1'}\n    if c:\n        env['HOME'] = '/h'\n"
     "        subprocess.run([], env=env)\n", [HAS]),
    ("def test_x(c):\n    env = dict(os.environ)\n    if c:\n        env.pop('HOME')\n"
     "        subprocess.run([], env=env)\n", [LACKS]),
    # A call in a loop also sees, conditionally, a later write in that loop.
    ("def test_x(ys):\n    env = dict(os.environ)\n    for y in ys:\n"
     "        subprocess.run([], env=env)\n        env.pop('HOME')\n", [UNKNOWN]),
    ("def test_x(ys):\n    env = {'X': '1'}\n    for y in ys:\n"
     "        subprocess.run([], env=env)\n        env['HOME'] = '/h'\n", [UNKNOWN]),
    ("def test_x(ys):\n    env = {'X': '1'}\n    for env in ys:\n"
     "        subprocess.run([], env=env)\n", [UNKNOWN]),
    # A dict handed on, stored away or shared under another name.
    ("def t():\n    e = dict(os.environ)\n    strip_home(e)\n    subprocess.run([], env=e)\n",
     [UNKNOWN]),
    ("def t():\n    e = {'X': '1'}\n    helper(env=e)\n    subprocess.run([], env=e)\n",
     [UNKNOWN]),
    ("def t():\n    e = {'X': '1'}\n    xs = [e]\n    subprocess.run([], env=e)\n", [UNKNOWN]),
    ("def t():\n    e = {'X': '1'}\n    d = {'k': e}\n    subprocess.run([], env=e)\n",
     [UNKNOWN]),
    ("def t(self):\n    e = {'X': '1'}\n    self.e = e\n    subprocess.run([], env=e)\n",
     [UNKNOWN]),
    ("def t():\n    e = dict(os.environ)\n    e.__delitem__('HOME')\n"
     "    subprocess.run([], env=e)\n", [UNKNOWN]),
    ("def t():\n    e = {'X': '1'}\n    e.get('Y')\n    subprocess.run([], env=e)\n", [LACKS]),
    ("def t():\n    e = {'X': '1'}\n    f = {**e}\n    subprocess.run([], env=e)\n", [LACKS]),
    ("def t():\n    e = dict(os.environ)\n    f = e\n    f.pop('HOME')\n"
     "    subprocess.run([], env=e)\n", [UNKNOWN]),
    ("def t():\n    e = dict(os.environ)\n    f = e\n    e.pop('HOME')\n"
     "    subprocess.run([], env=f)\n", [UNKNOWN]),
    ("def t():\n    e = dict(os.environ)\n    a, b = e, 1\n    a.pop('HOME')\n"
     "    subprocess.run([], env=e)\n", [UNKNOWN]),
    ("E = {'X': '1'}\n\n\ndef h():\n    fix(E)\n\n\ndef t():\n"
     "    subprocess.run([], env=E)\n", [UNKNOWN]),
    # os.environ's own HOME, changed in this scope.
    ("def t(mp):\n    mp.delenv('HOME')\n    subprocess.run([], env=os.environ.copy())\n",
     [LACKS]),
    ("def t(mp):\n    mp.delenv('HOME')\n    subprocess.run([], env=None)\n", [LACKS]),
    ("def t():\n    os.environ.pop('HOME')\n    subprocess.run([], env=dict(os.environ))\n",
     [LACKS]),
    ("def t():\n    del os.environ['HOME']\n    subprocess.run([], env=os.environ)\n", [LACKS]),
    ("def t(mp):\n    mp.delenv('HOME')\n    mp.setenv('HOME', '/h')\n"
     "    subprocess.run([], env=os.environ.copy())\n", [HAS]),
    ("def t(mp):\n    e = dict(os.environ)\n    mp.delenv('HOME')\n"
     "    subprocess.run([], env=e)\n", [HAS]),
    ("def t(mp):\n    mp.setattr(os, 'environ', {})\n    subprocess.run([], env=None)\n",
     [UNKNOWN]),
    ("def t():\n    os.unsetenv('HOME')\n    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("def t():\n    os.environ.__delitem__('HOME')\n"
     "    subprocess.run([], env=os.environ.copy())\n",
     [UNKNOWN]),
    ("def t():\n    e = os.environ\n    e.pop('HOME')\n"
     "    subprocess.run([], env=dict(os.environ))\n",
     [UNKNOWN]),
    ("@pytest.mark.skipif(not hasattr(os, 'geteuid'), reason='x')\ndef t():\n"
     "    subprocess.run([], env=dict(os.environ))\n", [HAS]),
    # Conditional expressions and and/or as the value are unjudged.
    ("def t(c):\n    e = {'X': '1'} if c else {'Y': '2'}\n    subprocess.run([], env=e)\n",
     [UNKNOWN]),
    ("def t(p):\n    e = p or {'X': '1'}\n    subprocess.run([], env=e)\n", [UNKNOWN]),
    ("def t(c):\n    x = (e := {'X': '1'}) if c else 0\n    subprocess.run([], env=e)\n",
     [LACKS]),
    # A write made by a nested function, lambda or class body (both directions).
    ("def t():\n    env = {'X': '1'}\n\n    def fix():\n        env['HOME'] = '/h'\n"
     "    fix()\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = dict(os.environ)\n\n    def strip():\n        env.pop('HOME')\n"
     "    strip()\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = dict(os.environ)\n    cb = lambda: env.pop('HOME')\n    cb()\n"
     "    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("_E = dict(os.environ)\nstrip = lambda: _E.pop('HOME')\n\n\ndef t():\n"
     "    subprocess.run([], env=_E)\n", [UNKNOWN]),
    ("def t():\n    env = dict(os.environ)\n\n    def g():\n        nonlocal env\n"
     "        env = {'X': '1'}\n    g()\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = dict(os.environ)\n\n    class K:\n        env.pop('HOME')\n"
     "    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = dict(os.environ)\n\n    def outer():\n        def inner():\n"
     "            env.pop('HOME')\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = {'X': '1'}\n\n    def show():\n        return env.get('X')\n"
     "    subprocess.run([], env=env)\n", [LACKS]),
    ("def t():\n    env = {'X': '1'}\n    subprocess.run([], env=env)\n\n    def fix():\n"
     "        env['HOME'] = '/h'\n", [LACKS]),
    ("_E = dict(os.environ)\n\n\nclass T:\n    def helper(self):\n        _E.pop('HOME')\n\n"
     "    def test(self):\n        self.helper()\n        subprocess.run([], env=_E)\n", [UNKNOWN]),
    # An os.environ change outside the call's own scope.
    ("@pytest.fixture\ndef f(monkeypatch):\n    monkeypatch.delenv('HOME')\n\n\n"
     "def t(f):\n    subprocess.run([], env=os.environ.copy())\n", [UNKNOWN]),
    ("os.environ.pop('HOME')\n\n\ndef t():\n    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("class T:\n    def setup(self):\n        os.environ.pop('HOME')\n\n    def test(self):\n"
     "        subprocess.run([], env=dict(os.environ))\n", [UNKNOWN]),
    ("def f(mp, key):\n    mp.delenv(key)\n\n\ndef t():\n    subprocess.run([], env=None)\n",
     [UNKNOWN]),
    ("def t():\n    cb = lambda: os.environ.pop('HOME')\n    subprocess.run([], env=None)\n",
     [UNKNOWN]),
    ("def f(mp):\n    mp.setenv('X', '1')\n    mp.delenv('Y')\n\n\ndef t():\n"
     "    subprocess.run([], env=os.environ.copy())\n", [HAS]),
    ("os.environ.pop('HOME')\nsubprocess.run([], env=None)\n", [LACKS]),
    # os and os.environ under other names.
    ("import os as o\n\n\ndef t():\n    o.environ.pop('HOME')\n    subprocess.run([], env=None)\n",
     [LACKS]),
    ("from os import environ as E\n\n\ndef t():\n    E.pop('HOME')\n"
     "    subprocess.run([], env=dict(E))\n", [LACKS]),
    ("from os import environ\n\n\ndef t():\n    subprocess.run([], env=environ.copy())\n",
     [HAS]),
    ("from os import unsetenv\n\n\ndef t():\n    unsetenv('HOME')\n"
     "    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("import os as o\n\n\ndef f():\n    o.environ.pop('HOME')\n\n\ndef t():\n"
     "    subprocess.run([], env=None)\n", [UNKNOWN]),
    # A method taken off the dict, and code that reaches names by text.
    ("def t():\n    env = dict(os.environ)\n    strip = functools.partial(env.pop, 'HOME')\n"
     "    strip()\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = dict(os.environ)\n    f = env.pop\n    f('HOME')\n"
     "    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    cb = functools.partial(os.environ.pop, 'HOME')\n    cb()\n"
     "    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("def t():\n    env = {'X': '1'}\n    g = env.get\n    subprocess.run([], env=env)\n",
     [LACKS]),
    ("def t():\n    env = dict(os.environ)\n    exec(\"env.pop('HOME')\")\n"
     "    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("_E = dict(os.environ)\n\n\ndef t():\n    globals()['_E'].pop('HOME')\n"
     "    subprocess.run([], env=_E)\n", [UNKNOWN]),
    ("def f():\n    eval(\"os.environ.pop('HOME')\")\n\n\ndef t():\n"
     "    subprocess.run([], env=os.environ.copy())\n", [UNKNOWN]),
    ("def t():\n    subprocess.run([], env={'X': '1'})\n    vars()\n", [LACKS]),
    # A rebind through globals() under a constant name acts as `global NAME`.
    ("_E = {'X': '1'}\n\n\ndef f(mp):\n    mp.setitem(globals(), 'Y', 1)\n\n\ndef t():\n"
     "    subprocess.run([], env=_E)\n", [LACKS]),
    ("_E = {'X': '1'}\n\n\ndef f(mp):\n    mp.setitem(globals(), '_E', {})\n\n\ndef t():\n"
     "    subprocess.run([], env=_E)\n", [UNKNOWN]),
    ("_E = {'X': '1'}\n\n\ndef f(mp):\n    mp.delitem(globals(), '_E')\n\n\ndef t():\n"
     "    subprocess.run([], env=_E)\n", [UNKNOWN]),
    ("_E = {'X': '1'}\nmp.setitem(globals(), '_E', {})\n\n\ndef t():\n"
     "    subprocess.run([], env=_E)\n", [UNKNOWN]),
    ("_E = {'X': '1'}\nY = 1\nmp.setitem(globals(), 'Y', 2)\n\n\ndef t():\n"
     "    subprocess.run([], env=_E)\n", [LACKS]),
    ("_E = {'X': '1'}\n\n\ndef f(mp):\n    _E = 1\n    mp.setitem(globals(), '_E', {})\n\n\n"
     "def t():\n    subprocess.run([], env=_E)\n", [UNKNOWN]),
    ("_E = {'X': '1'}\nK = '_E'\n\n\ndef f(mp):\n    mp.setitem(globals(), K, {})\n\n\n"
     "def t():\n    subprocess.run([], env=_E)\n", [UNKNOWN]),
    # The module's name for os or os.environ bound to something else.
    ("def t(os):\n    os.environ.pop('HOME')\n    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("import fake as os\n\n\ndef t():\n    os.environ.pop('HOME')\n"
     "    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("from mod import os\n\n\ndef t():\n    os.environ.pop('HOME')\n"
     "    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("from os import environ\n\n\ndef t():\n    environ = {}\n    environ.pop('HOME')\n"
     "    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("import os.path\nimport os as o\nfrom os import environ as e\n\n\ndef t():\n"
     "    os.environ.pop('HOME')\n    subprocess.run([], env=None)\n", [LACKS]),
    # A dict shared with a module name or os.environ may be written from any scope.
    ("e = os.environ\n\n\ndef t():\n    e.pop('HOME')\n    subprocess.run([], env=None)\n",
     [UNKNOWN]),
    ("e = os.environ\ne.pop('HOME')\n\n\ndef t():\n    subprocess.run([], env=None)\n",
     [UNKNOWN]),
    ("a = {'X': '1'}\nb = a\n\n\ndef f():\n    b['HOME'] = '/h'\n\n\ndef t():\n"
     "    subprocess.run([], env=a)\n", [UNKNOWN]),
    ("a = dict(os.environ)\nb = a\n\n\ndef f():\n    b.pop('HOME')\n\n\ndef t():\n"
     "    subprocess.run([], env=a)\n", [UNKNOWN]),
    ("a = dict(os.environ)\n\n\ndef f():\n    b = a\n    b.pop('HOME')\n\n\ndef t():\n"
     "    subprocess.run([], env=a)\n", [UNKNOWN]),
    ("def t():\n    a = {'X': '1'}\n    b = a\n    subprocess.run([], env=a)\n", [LACKS]),
    # Every runner the scan reads, by attribute and by bare name.
    ("def t():\n    subprocess.Popen([], env={'X': '1'})\n    subprocess.call([], env={'X': '1'})\n"
     "    subprocess.check_call([], env={'X': '1'})\n"
     "    subprocess.check_output([], env={'X': '1'})\n"
     "    asyncio.create_subprocess_shell('x', env={'X': '1'})\n", [LACKS] * 5),
    ("from subprocess import call, check_call, check_output\n\n\ndef t():\n"
     "    call([], env={'X': '1'})\n    check_call([], env={'X': '1'})\n"
     "    check_output([], env={'X': '1'})\n", [LACKS] * 3),
    ("f = lambda: subprocess.run([], env={'X': '1'})\n", []),
    # Async functions are scopes, and while and async for bodies are loops.
    ("env = {'X': '1'}\n\n\nasync def t(env):\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = dict(os.environ)\n    while c:\n        subprocess.run([], env=env)\n"
     "        env.pop('HOME')\n", [UNKNOWN]),
    ("async def t():\n    env = dict(os.environ)\n    async for x in y:\n"
     "        subprocess.run([], env=env)\n        env.pop('HOME')\n", [UNKNOWN]),
    # `del NAME` and `NAME |= x` make NAME local, as Python does.
    ("_E = {'X': '1'}\n\n\ndef f():\n    del _E\n\n\ndef t():\n    subprocess.run([], env=_E)\n",
     [LACKS]),
    ("_E = {'X': '1'}\n\n\ndef f(x):\n    _E |= x\n\n\ndef t():\n    subprocess.run([], env=_E)\n",
     [LACKS]),
    ("_E = {'X': '1'}\n\n\ndef f():\n    _E = {}\n\n\ndef t():\n    subprocess.run([], env=_E)\n",
     [LACKS]),
    # Every kind of write through a name that shares the dict.
    *[(f"def t():\n    a = {{'X': '1'}}\n    b = a\n    {write}\n    subprocess.run([], env=a)\n",
       [UNKNOWN]) for write in (
        "b['HOME'] = '/h'", "b |= x", "f(b)", "b[k] = '1'", "b.pop(k)", "b.update(x)",
        "def g():\n        b['HOME'] = '/h'")],
    ("def t():\n    a = {'X': '1'}\n    b = carry_clock_shift(a)\n    b['HOME'] = '/h'\n"
     "    subprocess.run([], env=a)\n", [UNKNOWN]),
    ("def t():\n    a = {'X': '1'}\n    b = (n := a)\n    b['HOME'] = '/h'\n"
     "    subprocess.run([], env=a)\n", [UNKNOWN]),
    ("def t():\n    a = {}\n    b = a\n    c = {'X': '1'}\n    b['HOME'] = '/h'\n"
     "    subprocess.run([], env=c)\n", [LACKS]),
    ("def t():\n    a = {'X': '1'}\n    subprocess.run([], env=a)\n    b = a\n"
     "    b['HOME'] = '/h'\n",
     [LACKS]),
    ("def t():\n    a = {'X': '1'}\n    while c:\n        subprocess.run([], env=a)\n"
     "        b = a\n"
     "        b['HOME'] = '/h'\n", [UNKNOWN]),
    # Reads and copies leave a dict judged; `*env` hands on keys only.
    *[(f"def t():\n    env = {{'X': '1'}}\n    {read}\n    subprocess.run([], env=env)\n", [LACKS])
      for read in ("env.copy()", "env.items()", "env.keys()", "env.values()", "env.popitem()",
                   "f(*env)", "xs = [*env]", "del obj.attr")],
    ("def t():\n    f(*os.environ)\n    subprocess.run([], env=None)\n", [HAS]),
    ("def t():\n    env = {'X': '1'}\n    env += {'HOME': '/h'}\n    subprocess.run([], env=env)\n",
     [UNKNOWN]),
    # Names read by text.
    *[(f"def t():\n    env = {{'X': '1'}}\n    {call}()\n    subprocess.run([], env=env)\n",
       [UNKNOWN]) for call in ("locals", "vars")],
    # Another scope's os.environ change that cannot remove HOME.
    ("def f(mp, k):\n    mp.setenv(k, '1')\n\n\ndef t():\n    os.environ.pop('HOME')\n"
     "    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("def f():\n    os.environ.update({'X': '1'})\n\n\ndef t():\n    os.environ.pop('HOME')\n"
     "    subprocess.run([], env=None)\n", [LACKS]),
    # Unbound paths, and merges.
    ("def t():\n    env = {'X': '1'}\n    if c:\n        del env\n"
     "    subprocess.run([], env=env)\n",
     [LACKS]),
    ("def t():\n    x = {'X': '1'}\n    del x\n    subprocess.run([], env={**x})\n", [UNKNOWN]),
    ("def t():\n    o = {'X': '1'}\n    env |= o\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = {'HOME': '/h'}\n    o = {'X': '1'}\n    env.update(o)\n"
     "    subprocess.run([], env=env)\n", [HAS]),
    ("def t():\n    env = {'X': '1'}\n    o = {'Y': '1'}\n    env.update(o)\n"
     "    subprocess.run([], env=env)\n", [LACKS]),
    ("def t():\n    global _E\n    if c:\n        _E['HOME'] = '/h'\n"
     "    subprocess.run([], env=_E)\n",
     [UNKNOWN]),
    ("def t():\n    a = {'X': '1'}\n    b = {'Y': '1'}\n    while c:\n        a = {**b}\n"
     "        b = {**a}\n        subprocess.run([], env=a)\n", [UNKNOWN]),
    # Branches that may not run: a match guard and an except clause's type.
    ("def t():\n    env = {'HOME': '/h'}\n    match x:\n        case 1 if (env := {'X': '1'}):\n"
     "            pass\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = {'HOME': '/h'}\n    try:\n        pass\n    except (env := {'X': '1'}):\n"
     "        pass\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    # Module code, and the order of writes by line.
    ("def f():\n    env['HOME'] = '/h'\n\n\nenv = {'X': '1'}\nf()\nsubprocess.run([], env=env)\n",
     [UNKNOWN]),
    ("mp.setitem(globals(), '_E', {})\n_E = {'X': '1'}\nsubprocess.run([], env=_E)\n", [LACKS]),
    ("a = {'X': '1'}\nb = a\na = {'Y': '1'}\nsubprocess.run([], env=a)\n", [LACKS]),
    ("def t():\n    env = {'X': '1'}\n    d = {'a': env.update(HOME='/h'), env.pop('HOME'): 1}\n"
     "    subprocess.run([], env=env)\n", [LACKS]),
    ("_E = {'X': '1'}\n\n\ndef f():\n    _E = {'HOME': '/h'}\n\n\ndef t():\n"
     "    subprocess.run([], env=_E)\n", [LACKS]),
    # A closure write holds past a later rebinding; it reaches only the nearest binder.
    ("def t():\n    env = {'X': '1'}\n\n    def g():\n        env.pop('HOME')\n"
     "    env = dict(os.environ)\n    g()\n    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = {'X': '1'}\n\n    def m():\n        env = {'Y': '1'}\n\n"
     "        def i():\n            env['HOME'] = '/h'\n    subprocess.run([], env=env)\n",
     [LACKS]),
    ("X = {'X': '1'}\n\n\nclass C:\n    X = {'HOME': '/h'}\n\n    def t(self):\n"
     "        subprocess.run([], env=X)\n", [LACKS]),
    # The module's names for os and os.environ, exactly.
    ("import json as o\n\n\ndef t():\n    o.environ.pop('HOME')\n"
     "    subprocess.run([], env=None)\n",
     [HAS]),
    ("from os import path as E\n\n\ndef t():\n    E.pop('HOME')\n"
     "    subprocess.run([], env=None)\n",
     [HAS]),
    ("from os import getcwd\n\n\ndef t():\n    getcwd()\n    subprocess.run([], env=None)\n",
     [HAS]),
    ("def t():\n    setattr(os, 'environ', {})\n    subprocess.run([], env=None)\n", [UNKNOWN]),
    # Writes are ordered as Python evaluates them, not by column.
    ("def t():\n    env = {'X': '1'}\n    x = env.pop('HOME') if env.update(HOME='/h') else 0\n"
     "    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = dict(os.environ)\n"
     "    x = env.update(HOME='/h') if env.pop('HOME') else env.update(HOME='/h')\n"
     "    subprocess.run([], env=env)\n", [HAS]),
    ("def t():\n    env = dict(os.environ)\n    env = {**env, 'X': env.pop('HOME')}\n"
     "    subprocess.run([], env=env)\n", [HAS]),
    ("def t():\n    env = dict(os.environ)\n    env = dict(env, X=env.pop('HOME'))\n"
     "    subprocess.run([], env=env)\n", [LACKS]),
    ("def t():\n    env = dict(os.environ)\n    subprocess.run([], env=env, x=env.pop('HOME'))\n",
     [LACKS]),
    ("def t():\n    env = dict(os.environ)\n    subprocess.run([], env=env) or env.pop('HOME')\n",
     [HAS]),
    ("def t():\n    a = dict(os.environ)\n    subprocess.run([], env=a | {'X': a.pop('HOME')})\n",
     [LACKS]),
    ("def t():\n    env = {'X': '1'}\n    xs = [env.pop('HOME') for x in env.update(HOME='/h')]\n"
     "    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = {'X': '1'}\n    xs = [x for x in env.update(HOME='/h')]\n"
     "    subprocess.run([], env=env)\n", [HAS]),
    ("def t():\n    env = {'X': '1'}\n    env.update(HOME=env.pop('Y'))\n"
     "    subprocess.run([], env=env)\n", [HAS]),
    ("def t():\n    env = {'X': '1'}\n    x = dict(env)\n    subprocess.run([], env=env)\n"
     "", [LACKS]),
    ("def t():\n    env = {'X': '1'}\n    d[env.pop('HOME')] = env.update(HOME='/h')\n"
     "    subprocess.run([], env=env)\n", [LACKS]),
    ("def t():\n    env = {'X': '1'}\n    d[env.pop('HOME')]: int = env.update(HOME='/h')\n"
     "    subprocess.run([], env=env)\n", [LACKS]),
    ("def t():\n    env = {'X': '1'}\n"
     "    xs = {env.pop('HOME'): 1 for x in env.update(HOME='/h')}\n"
     "    subprocess.run([], env=env)\n", [UNKNOWN]),
    ("def t():\n    env = {'X': '1'}\n    xs = [1 for env in [env.update(HOME='/h')]]\n"
     "    subprocess.run([], env=env)\n", [HAS]),
    ("def t():\n    env = {'X': '1'}\n\n    @mark(env.update(HOME='/h'))\n"
     "    def g(x=env.pop('HOME')):\n        pass\n    subprocess.run([], env=env)\n", [LACKS]),
    ("def t(env):\n    if c:\n        env['HOME'] = '/h'\n    subprocess.run([], env=env)\n",
     [UNKNOWN]),
    # A loop, with or except target is bound before its block runs.
    ("def t():\n    env = dict(os.environ)\n    for env in env.pop('HOME'):\n"
     "        subprocess.run([], env=env)\n", [UNKNOWN]),
    *[(f"def t():\n    env = {{'X': '1'}}\n{block}\n        subprocess.run([], env=env)\n",
       [UNKNOWN]) for block in ("    for env in envs:", "    with f() as env:",
                                "    try:\n        pass\n    except E as env:")],
    # Decorators, defaults and class bases run in the scope around them.
    *[(f"def t():\n    env = dict(os.environ)\n{part}\n    subprocess.run([], env=env)\n", [LACKS])
      for part in ("\n    def g(x=env.pop('HOME')):\n        pass",
                   "\n    def g(*, x=env.pop('HOME')):\n        pass",
                   "\n    @mark(env.pop('HOME'))\n    def g():\n        pass",
                   "\n    class C(env.pop('HOME')):\n        pass",
                   "\n    class C(Base, meta=env.pop('HOME')):\n        pass",
                   "    f = lambda x=env.pop('HOME'): x")],
    # Another scope only adding HOME: harmless, unless this scope removed it first.
    ("def f(mp):\n    mp.setenv('HOME', '/h')\n\n\ndef t():\n"
     "    subprocess.run([], env=os.environ.copy())\n", [HAS]),
    ("def f(mp, k):\n    mp.setenv(k, '1')\n\n\ndef t():\n    subprocess.run([], env=None)\n",
     [HAS]),
    ("def f(mp):\n    mp.setenv('HOME', '/h')\n\n\ndef t():\n    os.environ.pop('HOME')\n"
     "    subprocess.run([], env=os.environ.copy())\n", [UNKNOWN]),
    ("def f(mp):\n    mp.setenv('HOME', '/h')\n\n\ndef t(mp):\n    mp.delenv('HOME')\n"
     "    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("def t():\n    os.environ.pop('HOME')\n    os.environ |= {'HOME': '/h'}\n"
     "    subprocess.run([], env=None)\n", [HAS]),
    ("def f(c):\n    os.environ |= c\n\n\ndef t():\n    os.environ.pop('HOME')\n"
     "    subprocess.run([], env=None)\n", [UNKNOWN]),
    ("def f():\n    os.environ.clear()\n\n\ndef t():\n    subprocess.run([], env=None)\n",
     [UNKNOWN]),
    ("def f(mp):\n    mp.setenv('X', '1')\n    mp.delenv('Y')\n    os.environ['Z'] = '1'\n"
     "    os.environ.pop('W')\n\n\ndef t():\n    os.environ.pop('HOME')\n"
     "    subprocess.run([], env=None)\n", [LACKS]),
    ("def t(mp):\n    mp.delenv('HOME')\n    subprocess.run([], env=None)\n", [LACKS]),
    # Class bodies are scopes of their own.
    ("class C:\n    e = {'X': '1'}\n    subprocess.run([], env=e)\n", [LACKS]),
    ("class C:\n    e = {'X': '1'}\n\n    def t(self):\n        subprocess.run([], env=e)\n",
     [UNKNOWN]),
])
def test_the_scan_judges_scope_and_control_flow(source, verdicts):
    assert _judge_module(source) == verdicts


def test_one_modules_names_for_os_do_not_carry_into_the_next():
    """The scan records each module's names for os, os.environ, os.putenv and
    os.unsetenv; judging another module first must not leave them behind."""
    _judge_module("import os as o\nfrom os import environ as E\nfrom os import unsetenv\n"
                  "xs = [x for x in ys]\n")
    later = ("def t():\n    o.environ.pop('HOME')\n    E.pop('HOME')\n    unsetenv()\n"
             "    subprocess.run([], env=None)\n")
    assert _judge_module(later) == [HAS]
    _judge_module("xs = [x for x in ys]\n")
    assert _judge_module("def t():\n    env = {'X': '1'}\n"
                         "    xs = [x for x in env.update(HOME='/h')]\n"
                         "    subprocess.run([], env=env)\n") == [HAS]
