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
_ENVIRON = frozenset({"os.environ", "dict(os.environ)", "os.environ.copy()"})
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
_SCOPES = (*_FUNCTIONS, ast.ClassDef, ast.Lambda)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_REBINDS = frozenset({"bind", "unknown", "unbind", "aug-merge", "aug-add"})
_WRITES = frozenset({"add", "strip", "maybe-add", "maybe-strip", "merge", "aug-merge",
                     "aug-add", "escape"})
# A pseudo-name: os.environ's own HOME, which this scope may remove or set.
_OS_ENVIRON = "os.environ"
# Methods that neither write nor hand the dict to code that might.
_READ_METHODS = frozenset({"get", "copy", "items", "keys", "values"})
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


def _walk(node, chain=()):
    """(node, chain) for every node of one scope, not entering a nested
    function, class or lambda (yielded, not entered). `chain` names every
    statement body, branch and conditional sub-expression enclosing the node."""
    for field, value in ast.iter_fields(node):
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
            else:
                sub = chain
            yield item, sub
            if not isinstance(item, _SCOPES):
                yield from _walk(item, sub)


def _store_names(target) -> list:
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]


def _dict_name(node):
    """The name a write through `node` lands on: a bare name, os.environ, or None."""
    if isinstance(node, ast.Name):
        return node.id
    return _OS_ENVIRON if ast.unparse(node) == "os.environ" else None


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
        self.loop_bodies = {(id(n), "body") for n, _ in self.pairs if isinstance(n, _LOOPS)}
        self.nodes = {id(n): n for n, _ in self.pairs}
        if isinstance(node, _FUNCTIONS):
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

    def _add(self, name, node, kind, value, chain):
        self.events.setdefault(name, []).append(
            ((node.lineno, node.col_offset), kind, value, chain))

    def _keyed(self, name, node, key, sure, maybe, chain):
        if _is_home_key(key):
            self._add(name, node, sure, None, chain)
        elif not isinstance(key, ast.Constant):
            self._add(name, node, maybe, None, chain)

    def _merged(self, name, node, value, chain, kind="merge"):
        if isinstance(value, ast.Dict) and any(_is_home_key(k) for k in value.keys):
            self._add(name, node, "add" if kind == "merge" else "aug-add", None, chain)
        elif not _home_less_dict(value) or kind == "aug-merge":
            self._add(name, node, kind, value, chain)

    def _bind(self, target, value, node, chain):
        source = _unwrap(value) if value is not None else None
        if isinstance(source, ast.Name) and not isinstance(target, ast.Name):
            # Stored into a container or an attribute: it may change elsewhere.
            self._add(source.id, node, "escape", None, chain)
        if isinstance(target, ast.Name):
            self._add(target.id, node, "bind" if value is not None else "unknown", value, chain)
            shared = _dict_name(source) if source is not None else None
            if shared is not None and shared != target.id:
                self.aliases.append((target.id, shared, (node.lineno, node.col_offset)))
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
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for name in _store_names(node.target):
                self._add(name, node, "unknown", None, body)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for name in _store_names(item.optional_vars):
                        self._add(name, node, "unknown", None, body)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self._add(node.name, node, "unknown", None, body)
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
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)) and isinstance(
                getattr(node, "ctx", ast.Load()), ast.Load):
            for element in node.elts:
                element = element.value if isinstance(element, ast.Starred) else element
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
            arg = arg.value if isinstance(arg, ast.Starred) else arg
            if ast.unparse(arg) == "os.environ" or (
                    ast.unparse(arg) == "os" and _call_name(func) in ("setattr", "delattr")):
                self._add(_OS_ENVIRON, node, "escape", None, chain)
            elif isinstance(arg, ast.Name) and not copier:
                self._add(arg.id, node, "escape", None, chain)
        if ast.unparse(func) in ("os.putenv", "os.unsetenv"):
            self._add(_OS_ENVIRON, node, "escape", None, chain)
            return
        if isinstance(func, ast.Attribute) and func.attr in ("setenv", "delenv") and node.args:
            sure, maybe = ("add", "maybe-add") if func.attr == "setenv" else ("strip",
                                                                               "maybe-strip")
            self._keyed(_OS_ENVIRON, node, node.args[0], sure, maybe, chain)
            return
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
        """Whether this scope writes, strips or rebinds a module name it does
        not own (a write to it, or a rebinding under `global`)."""
        if name in self.locals or name in self.nonlocals:
            return False
        return bool(self.events.get(name))

    def verdict(self, node, at, chain, seen=frozenset()) -> str:
        """Whether `node`, evaluated at position `at` under `chain`, carries HOME."""
        while isinstance(node, ast.Call) and getattr(node.func, "id", "") == "carry_clock_shift" \
                and node.args:
            node = node.args[0]
        if isinstance(node, ast.NamedExpr):
            node = node.value
        if (isinstance(node, ast.Constant) and node.value is None) \
                or ast.unparse(node) in _ENVIRON:
            state = self._name_state(_OS_ENVIRON, at, chain, seen)
            return UNKNOWN if state is None else state
        if isinstance(node, ast.Name):
            state = self._name_state(node.id, at, chain, seen)
            return UNKNOWN if state is None else state
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            sides = {self.verdict(node.left, at, chain, seen),
                     self.verdict(node.right, at, chain, seen)}
            return HAS if HAS in sides else LACKS if sides == {LACKS} else UNKNOWN
        if isinstance(node, ast.Dict):
            if any(_is_home_key(k) for k in node.keys):
                return HAS
            if any(k is not None and not isinstance(k, ast.Constant) for k in node.keys):
                return UNKNOWN
            parts = [v for k, v in zip(node.keys, node.values) if k is None]
            return self._parts(parts, at, chain, seen)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "dict":
            if any(k.arg == "HOME" for k in node.keywords):
                return HAS
            parts = [*node.args[:1], *(k.value for k in node.keywords if k.arg is None)]
            return self._parts(parts, at, chain, seen)
        return UNKNOWN

    def _parts(self, parts, at, chain, seen) -> str:
        verdicts = {self.verdict(part, at, chain, seen) for part in parts}
        return HAS if HAS in verdicts else UNKNOWN if UNKNOWN in verdicts else LACKS

    def _name_state(self, name, at, chain, seen):
        if name == _OS_ENVIRON:
            state = HAS
        elif name in self.nonlocals:
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
        if back:
            state = _join(state, self._apply(name, back, chain, state, seen))
        if self._alias_written(name, at, loops):
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
                state = self._step(name, state, entry, True, seen)
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
        for first, second, since in self.aliases:
            if since >= at or name not in (first, second):
                continue
            partner = second if name == first else first
            for when, kind, _value, chain in self.events.get(partner, ()):
                if kind in _WRITES and when > since and (
                        when < at or any(loop in chain for loop in loops)):
                    return True
        return False

    def binds_freely(self, name) -> bool:
        """Whether a nested function's free `name` refers to this function
        (or one enclosing it) rather than to the module."""
        if not isinstance(self.node, _FUNCTIONS):
            return False
        return name in self.locals or (self.parent is not None and self.parent.binds_freely(name))

    def module_state(self, name, user, seen):
        key = ("module", name)
        if key in seen:
            return UNKNOWN
        if any(scope.mutates(name) for scope in self.all_scopes if scope is not user
               and scope is not self):
            return UNKNOWN
        state = self._name_state(name, (float("inf"), 0), ((id(self.node), "body"),),
                                 seen | {key})
        return UNKNOWN if state is None else state

    def _step(self, name, state, entry, definite, seen):
        at, kind, value, chain = entry
        if kind == "bind":
            key = (id(self.node), name, at)
            new = UNKNOWN if key in seen else self.verdict(value, at, chain, seen | {key})
        elif kind in ("unknown", "escape"):
            new = UNKNOWN
        elif kind == "unbind":
            new = None
        elif kind in ("add", "aug-add"):
            new = HAS
        elif kind == "strip":
            new = LACKS
        elif kind == "maybe-add":
            new = HAS if state == HAS else UNKNOWN
        elif kind == "maybe-strip":
            new = LACKS if state == LACKS else UNKNOWN
        else:  # merge, aug-merge
            merged = self.verdict(value, at, chain, seen)
            current = UNKNOWN if state is None else state
            new = HAS if HAS in (current, merged) else \
                UNKNOWN if UNKNOWN in (current, merged) else LACKS
        return new if definite else _join(state, new)


def _bare_runners(tree) -> set:
    """Local names bound to a subprocess runner by `from subprocess import ...`."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            names.update(alias.asname or alias.name for alias in node.names
                         if alias.name in _SUBPROCESS_RUNNERS)
    return names


def _scopes(tree) -> list:
    """The module scope and one scope per function and class, each knowing the
    function (or module) that encloses it."""
    module = _Scope(tree, None, None)
    scopes = [module]

    def visit(node, parent):
        for item, _chain in _walk(node):
            if isinstance(item, (*_FUNCTIONS, ast.ClassDef)):
                scope = _Scope(item, parent, module)
                scopes.append(scope)
                visit(item, scope if isinstance(item, _FUNCTIONS) else parent)

    visit(tree, None)
    module.all_scopes = scopes
    return scopes


def _env_judgements(tree) -> list:
    """(lineno, verdict) for every runner call's `env=` in a parsed module,
    each judged in the scope (function, class or module body) it sits in."""
    bare = _bare_runners(tree)
    judged = []
    for scope in _scopes(tree):
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
                        kw.value, (node.lineno, node.col_offset), chain)))
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
    `monkeypatch.delenv("HOME")`; `.setenv("HOME", ...)` restores it). A dict
    literal or dict(...) with a HOME key or keyword has HOME. Otherwise its
    spreads and its dict() argument decide: HAS if any has HOME, UNKNOWN if any
    is unjudged, LACKS if all lack it (no spread at all: LACKS). `a | b` is HAS
    if either side has HOME, LACKS if both lack it, otherwise UNKNOWN.
    carry_clock_shift(x) and `(n := x)` are judged as x.

    NAMES. A name bound in the scope is judged from its bindings and writes
    before the call, in line order. Bindings it can read: `=` (tuple and list
    unpacking paired element by element with a same-length tuple or list),
    `x: T = v` and `(x := v)`. Bindings it cannot read make the name UNKNOWN:
    a for target, a with or except target, an import, a starred or unpaired
    unpacking, an augmented assignment other than `|=`, and a match capture;
    `del x` unbinds it. Writes: `env["HOME"] = `, `env.update(HOME=...)`,
    `env.setdefault("HOME", ...)` and a merge of something with HOME
    (`env.update(x)`, `env.update(**x)`, `env |= x`) make it HAS;
    `env.pop("HOME")`, `del env["HOME"]` and `env.clear()` make it LACKS; a
    write or removal with a non-constant key (`env[k] = v`, `env.pop(k)`,
    `env.popitem()`) and a merge of something unjudged make it UNKNOWN unless
    the verdict already rules that out; another constant key, or a merge of a
    dict with no HOME key, changes nothing. A name becomes UNKNOWN from the
    point it is passed to any call other than dict(), carry_clock_shift() or
    a runner's env=, stored into a container, a dict value or an attribute, or
    has any method called other than the writes above and get, copy, items,
    keys and values; and while another name shares its dict (`b = a`), a write
    through either makes both UNKNOWN. os.environ itself becomes UNKNOWN from
    the point os.environ is passed to any call, `os` is passed to setattr or
    delattr (including `monkeypatch.setattr(os, "environ", ...)`), os.putenv
    or os.unsetenv is called, or os.environ gets a method call other than the
    writes and reads listed above for a name (its writes, such as
    `os.environ.update(...)`, `.setdefault(...)` and `.clear()`, are judged
    exactly as they are for a name).

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
    function or class body in the module writes, strips, rebinds (including
    under `global`) or passes it on, in which case
    it is UNKNOWN. A name an enclosing function binds (a closure) is UNKNOWN.

    EVERY OTHER SHAPE IS UNJUDGED AND READ AS UNKNOWN, so it is never flagged.
    Examples: a helper call (`_env(root)`, `world.env()`); a parameter; an
    attribute or subscript (`w.env`, `calls[0][1]["env"]`); a conditional
    expression or `and`/`or` as the value itself (`a if c else b`, `p or {}`);
    a comprehension (`{k: v for k, v in os.environ.items()}`); a change to
    os.environ made in another function or at import; calls inside a lambda;
    and runners taking env positionally (os.execve, os.spawn*e,
    os.posix_spawn).
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
    # A spread of something unjudged makes the verdict unknown (R4-6).
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
    # Every binding form rebinds the name (R4-4).
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
    # conditional: it joins the verdict before it (R4-5).
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = dict(os.environ)\nif c:\n    env.pop('HOME')", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    pass\nelse:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    pass\nelif d:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nfor x in y:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nfor x in y:\n    pass\nelse:\n    env['HOME'] = '/h'", UNKNOWN),
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
    # The auditor's battery: rebinds and writes in branches, joined per statement.
    ("env", "env = dict(os.environ)\nif c:\n    env = {'PATH': '/bin'}", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env['HOME'] = '/h'\nelse:\n    env['HOME'] = '/h'", HAS),
    ("env", "env = dict(os.environ)\nif c:\n    env.pop('HOME')\nelse:\n    del env['HOME']", LACKS),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env['HOME'] = '/h'\nelif d:\n    env['HOME'] = '/h'\n"
            "else:\n    env['HOME'] = '/h'", HAS),
    ("env", "env = {'PATH': '/bin'}\nif c:\n    env['HOME'] = '/h'\nelif d:\n    env['HOME'] = '/h'", UNKNOWN),
    ("env", "if c:\n    env = dict(os.environ)\nelse:\n    env = {'HOME': '/h'}", HAS),
    ("env", "x = (env := {'X': '1'}) if c else (env := {'Y': '2'})", LACKS),
    ("env", "env = {'PATH': '/bin'}\nmatch x:\n    case 1:\n        env['HOME'] = '/h'", UNKNOWN),
    ("env", "env = {'PATH': '/bin'}\nwhile c:\n    env.update(os.environ)", UNKNOWN),
    ("{**helper()}", "", UNKNOWN),
    ("{**b}", "b = {'PATH': '/bin'}", LACKS),
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
    # A name the function never binds resolves through the module (R4-3).
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
    ("def t():\n    os.environ.__delitem__('HOME')\n    subprocess.run([], env=os.environ.copy())\n",
     [UNKNOWN]),
    ("def t():\n    e = os.environ\n    e.pop('HOME')\n    subprocess.run([], env=dict(os.environ))\n",
     [UNKNOWN]),
    ("@pytest.mark.skipif(not hasattr(os, 'geteuid'), reason='x')\ndef t():\n"
     "    subprocess.run([], env=dict(os.environ))\n", [HAS]),
    # Conditional expressions and and/or as the value are unjudged.
    ("def t(c):\n    e = {'X': '1'} if c else {'Y': '2'}\n    subprocess.run([], env=e)\n",
     [UNKNOWN]),
    ("def t(p):\n    e = p or {'X': '1'}\n    subprocess.run([], env=e)\n", [UNKNOWN]),
    ("def t(c):\n    x = (e := {'X': '1'}) if c else 0\n    subprocess.run([], env=e)\n",
     [LACKS]),
    # Class bodies are scopes of their own.
    ("class C:\n    e = {'X': '1'}\n    subprocess.run([], env=e)\n", [LACKS]),
    ("class C:\n    e = {'X': '1'}\n\n    def t(self):\n        subprocess.run([], env=e)\n",
     [UNKNOWN]),
])
def test_the_scan_judges_scope_and_control_flow(source, verdicts):
    assert _judge_module(source) == verdicts
