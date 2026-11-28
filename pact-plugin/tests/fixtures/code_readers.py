"""Code readers the identity censuses share.

Location: pact-plugin/tests/fixtures/code_readers.py

Each reader takes Python source (a module path, a parsed tree or a code
object) and answers one question the censuses ask of it: the names a
module's code can bind and read (`unread_bindings`, `name_values`), the
string constants a module defines (`module_string_constants`), the forms
that can reach a function by a computed name (`dynamic_access_forms`,
`dynamic_reaches`, `named_anchor_strings`), the callees whose value is a
call result (`call_result_callees`) and the strings a chooser can return
(`chooser_elements`). Used by test_identity_rung_parity.py,
test_remediation_identity_resolver.py, test_remediation_identity_parser.py
and test_remediation_skill_prose.py. These are direct-import helpers, not
pytest fixtures.
"""

from __future__ import annotations

import ast
import builtins
import collections
import functools
import sys
from pathlib import Path

from fixtures.identity_git import import_aliases


def _own_returns(function) -> list:
    """The `return` statements of `function` itself, not of a def, lambda
    or class nested in it."""
    found, stack = [], list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def chooser_elements(tree, node) -> tuple:
    """(texts, unresolved) for `node`, an expression of the module `tree`
    read as `name[i]`: element `i` of what a function of the module returns,
    where every binding of `name` (`_CalleeReader`, every binding form the
    compiler defines) is a call to such a function. Each `return` of that
    function is None (the root passes: no element) or a tuple whose element
    `i` is a string, which is one text, None, which is none, or a name every
    binding of which holds a string (`name_values`), each of them a text.

    FAIL CLOSED: any other expression, a binding of the name that is not
    such a call, and a return or element of any other form are listed in
    `unresolved` as source text, so a reader of this census never takes an
    arm it cannot see for one that does not exist."""
    if not (
        isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
        and isinstance(node.slice, ast.Constant) and type(node.slice.value) is int
    ):
        return set(), [ast.unparse(node)]
    reader = _reader_for(tree)
    functions = {
        definition.name: definition for definition in tree.body
        if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    sources = reader.held(node.value.id, reader.scope.get(id(node)))
    texts, unresolved = set(), []
    if not sources:
        return set(), [ast.unparse(node)]
    for kind, value in sources:
        if not (
            kind == "value" and isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id in functions
        ):
            unresolved.append(f"{ast.unparse(node)}: {kind} {ast.unparse(value) if value is not None else None}")
            continue
        for returned in _own_returns(functions[value.func.id]):
            result = returned.value
            if result is None or (isinstance(result, ast.Constant) and result.value is None):
                continue
            if not isinstance(result, ast.Tuple) or any(isinstance(e, ast.Starred) for e in result.elts) \
                    or node.slice.value >= len(result.elts):
                unresolved.append(f"{returned.lineno}: return {ast.unparse(result)}")
                continue
            element = result.elts[node.slice.value]
            if isinstance(element, ast.Constant) and (element.value is None or isinstance(element.value, str)):
                texts |= {element.value} - {None}
                continue
            held = name_values(element, None, tree) if isinstance(element, ast.Name) else None
            if held:
                texts |= held
            else:
                unresolved.append(f"{returned.lineno}: {ast.unparse(element)}")
    return texts, unresolved


# THE WAYS A RUNTIME NAME REACHES WHAT IT NAMES, pinned rather than measured:
# {qualified callable: ((kind, index of its name argument), ...)}. A key is
# "builtins.x" for a builtin, "<module>.x" for a function of a standard
# module, and ".x" for an attribute every object can carry. Kinds: "attribute"
# (the name is an attribute name), "code" (a line of code), "import" (a module
# name), "namespace" (the call or attribute gives a whole namespace, so it
# reaches every name; index None). The trace hooks are namespaces: a trace
# function receives every frame.
#
# A TABLE, NOT A SWEEP. Calling every callable of every imported standard
# module with made-up arguments runs shell commands and file writers, and
# its answer moves with the Python version. Add a form here when a scope
# module starts importing a module that has one.
_DYNAMIC_ACCESS_FORMS = {
    "builtins.getattr": (("attribute", 1),),
    "builtins.hasattr": (("attribute", 1),),
    "builtins.setattr": (("attribute", 1),),
    "builtins.delattr": (("attribute", 1),),
    "builtins.vars": (("namespace", None),),
    "builtins.globals": (("namespace", None),),
    "builtins.locals": (("namespace", None),),
    "builtins.eval": (("code", 0),),
    "builtins.exec": (("code", 0),),
    "builtins.compile": (("code", 0),),
    "builtins.__import__": (("import", 0),),
    ".__getattribute__": (("attribute", 0),),
    ".__setattr__": (("attribute", 0),),
    ".__delattr__": (("attribute", 0),),
    ".__dict__": (("namespace", None),),
    ".__globals__": (("namespace", None),),
    ".f_globals": (("namespace", None),),
    ".f_locals": (("namespace", None),),
    "importlib.import_module": (("import", 0),),
    "inspect.getattr_static": (("attribute", 1),),
    "operator.attrgetter": (("attribute", 0),),
    "pkgutil.resolve_name": (("import", 0),),
    "sys.settrace": (("namespace", None),),
    "threading.settrace_all_threads": (("namespace", None),),
}


def _scope_standard_modules(paths) -> list:
    """Every standard-library module the files at `paths` import."""
    names = set()
    for path in paths:
        for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".")[0])
    return sorted(name for name in names if name in sys.stdlib_module_names and name != "__future__")


def dynamic_access_forms(paths) -> dict:
    """{qualified callable: [(kind, index of its name argument)]} for every
    way the language, and each standard module the files at `paths`
    import, reaches what a runtime name names: by attribute name, as a
    namespace, as a line of code, or as a module name (`_DYNAMIC_ACCESS_FORMS`).
    `.x` keys are attributes of every object (`__getattribute__`,
    `__dict__`)."""
    modules = {"", "builtins", *_scope_standard_modules(paths)}
    return {
        name: list(kinds) for name, kinds in _DYNAMIC_ACCESS_FORMS.items()
        if name.rsplit(".", 1)[0] in modules
    }


_READERS: dict = {}


def _reader_for(tree):
    """The binding reader of the module `tree`, built once per tree."""
    held = _READERS.get(id(tree))
    if held is None or held[0] is not tree:
        held = _READERS[id(tree)] = (tree, _CalleeReader(tree, compile(tree, "<census>", "exec")))
    return held[1]


def module_string_constants(tree) -> dict:
    """{name: text} for each module-level name of `tree` whose every
    binding, by every binding form the compiler defines (`_CalleeReader`),
    assigns the same string constant. A name with any other binding is left
    out, so no reader of the table takes it for that constant.

    ADVERSARIAL-ONLY, not read: a rebinding through the module object
    (`sys.modules[__name__].X = ...`), which sets a module's own constant
    by attribute so that no binding of the name shows it."""
    reader = _reader_for(tree)
    found = {}
    for (key, name), sources in reader.sources.items():
        if key is not None or (None, name) in reader.unread:
            continue
        if all(kind == "value" and isinstance(v, ast.Constant) and isinstance(v.value, str) for kind, v in sources):
            texts = {value.value for _kind, value in sources}
            if len(texts) == 1:
                found[name] = texts.pop()
    return found


def name_values(node, function, tree, seen=frozenset()):
    """The strings `node` can hold, or None when they cannot be read from
    the code: a constant; or a name each of whose bindings, by every
    binding form the compiler defines (`_CalleeReader`), holds only such
    values: an assignment of one, an element of a literal container of
    them that nothing changes after its literal (a `for` target, an
    unpacking; `_changed_containers`), a parameter whose default and
    every value a call in the module passes are such values. FAIL CLOSED:
    any other binding (a call result, a `with` target, an augmented
    assignment, an import, an unread binding) makes the name unreadable.
    `function` is unused: the reader finds the scope the name is read in."""
    if isinstance(node, ast.Constant):
        return {node.value} if isinstance(node.value, str) else set()
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return _element_values(node, tree, seen)
    if not isinstance(node, ast.Name) or id(node) in seen:
        return None if not isinstance(node, ast.Name) else set()
    reader = _reader_for(tree)
    sources = reader.held(node.id, reader.scope.get(id(node)))
    if not sources:
        return None
    values = set()
    for kind, value in sources:
        if kind == "value":
            found = name_values(value, None, tree, seen | {id(node)})
        elif kind == "element" and value is not None:
            found = _element_values(value, tree, seen | {id(node)})
        elif kind == "param":
            passed = reader._param_values(*value)
            found = None if passed is None else set()
            for given, _scope, whole in passed or ():
                part = (
                    name_values(given, None, tree, seen | {id(node)}) if whole
                    else _element_values(given, tree, seen | {id(node)})
                )
                if part is None:
                    found = None
                    break
                found |= part
        else:
            found = None
        if found is None:
            return None
        values |= found
    return values


def _element_values(container, tree, seen=frozenset()):
    """The strings an element of `container` can hold, or None: each element
    of a literal container (nested literals included), or of the literal a
    name holds."""
    if isinstance(container, (ast.List, ast.Tuple, ast.Set)):
        values = set()
        for element in container.elts:
            element = element.value if isinstance(element, ast.Starred) else element
            found = (
                _element_values(element, tree, seen) if isinstance(element, (ast.List, ast.Tuple, ast.Set))
                else name_values(element, None, tree, seen)
            )
            if found is None:
                return None
            values |= found
        return values
    if isinstance(container, ast.Name) and id(container) not in seen:
        reader = _reader_for(tree)
        if reader.changes(container.id, reader.scope.get(id(container))):
            return None  # its contents can change after its literal
        sources = reader.held(container.id, reader.scope.get(id(container)))
        if not sources:
            return None
        values = set()
        for kind, value in sources:
            found = _element_values(value, tree, seen | {id(container)}) if kind in ("value", "element") else None
            if found is None:
                return None
            values |= found
        return values
    return None


def dynamic_reaches(paths, forms, reaches_by_name, read_outright=lambda path, call: False) -> list:
    """Each use, in the files at `paths`, of a dynamic access form (`forms`,
    from `dynamic_access_forms`) that can reach a census anchor, as
    (file, line, source): a form named by attribute (`x.__dict__`,
    `x.__getattribute__(...)`) or as a namespace always can; one handed a
    name, line of code or module name can unless every value it can hold
    (`name_values`) is known and `reaches_by_name(kind, value)` is false for
    each. A form the census reads outright (`read_outright(path, call)`) is the
    census's, not this ban's. A dynamic callable referenced other than as
    the target of a call (stored, passed on) always can."""
    found = []
    for path in paths:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        aliases = import_aliases(tree)
        functions = [f for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]

        def enclosing(node):
            inner = [f for f in functions if any(n is node for n in ast.walk(f))]
            return min(inner, key=lambda f: len(list(ast.walk(f))), default=None)

        def qualified(func):
            if isinstance(func, ast.Name):
                if func.id in aliases:
                    return aliases[func.id]
                return "builtins." + func.id
            if isinstance(func, ast.Attribute):
                if "." + func.attr in forms:
                    return "." + func.attr
                if isinstance(func.value, ast.Name) and func.value.id in aliases:
                    return aliases[func.value.id] + "." + func.attr
            return None

        targets = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = qualified(node.func)
                if name not in forms or read_outright(path, node):
                    continue
                function = enclosing(node)
                for kind, index in forms[name]:
                    if kind == "namespace":
                        hit = True
                    else:
                        arguments = node.args[index:index + 1]
                        values = name_values(arguments[0], function, tree) if arguments else None
                        hit = values is None or any(reaches_by_name(kind, value) for value in values)
                    if hit:
                        found.append((Path(path).name, node.lineno, ast.unparse(node)))
                        break
            elif isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in targets:
                name = qualified(node)
                if name in forms and isinstance(getattr(node, "ctx", None), ast.Load):
                    found.append((Path(path).name, node.lineno, ast.unparse(node)))
    return sorted(set(found))


def _resolver_walk(value) -> set:
    """Every module and attribute name a name resolver walks to resolve
    `value`: `pkgutil.resolve_name`, and `importlib.import_module` for a
    name whose every `.`-separated part is an identifier (a leading-dot name
    relative to a stand-in package), each run with a stand-in for the import
    system that records each module it is asked for and imports nothing. A module is asked for with each of its parents
    first, so each `.` prefix of an asked name is walked too. `getattr`
    takes a whole string as one name, and every identifier `getattr` could
    take as an anchor is a name `pkgutil.resolve_name` already walks."""
    import importlib
    import pkgutil

    walked, asked = set(), []

    class StandIn:
        def __getattr__(self, name):
            walked.add(name)
            return StandIn()

    def find_and_load(name, _import):
        asked.append(name)
        return StandIn()

    pkgutil.resolve_name("os")  # the resolver builds its pattern on first use, with the real import system
    bootstrap = importlib._bootstrap
    real = bootstrap._find_and_load
    level = len(value) - len(value.lstrip(".")) if isinstance(value, str) else 0
    package = ".".join(["<stand-in>"] * max(level, 1))
    bootstrap._find_and_load = find_and_load
    try:
        dotted = isinstance(value, str) and all(part.isidentifier() for part in value.lstrip(".").split("."))
        for resolve in (pkgutil.resolve_name, lambda name: importlib.import_module(name, package)):
            if resolve is not pkgutil.resolve_name and not dotted:
                continue  # a module name is identifiers joined by dots
            try:
                resolve(value)
            except Exception:  # this resolver does not take `value` as a name
                pass
    finally:
        bootstrap._find_and_load = real
    for name in asked:
        while name:
            name, _dot, part = name.rpartition(".")
            walked.add(part)
    return walked


def named_anchor_strings(paths, named, read_outright=lambda path, parent: False) -> list:
    """Each string constant in the files at `paths` that a name resolver can
    take as a name reaching a census anchor, as (file, line, text): one name
    `_resolver_walk` walks for the stripped string (`O_X`, `os.O_X`,
    `os:O_X`, `.O_X`) is a name `named` accepts. Whatever receives it, such a
    string can reach the anchor by a form no census reads, docstrings
    included. Prose around a name is not a name any resolver takes. A string
    whose parent node the census reads outright (`read_outright(path,
    parent)`) is the census's.

    ADVERSARIAL-ONLY, not read (no good-faith edit writes them to reach a
    function):
    - a format field (`"{0.O_X}".format(os)`): a format string written to
      read a module's attribute by name hides the access in text formatting.
    - a name kept as bytes and decoded (`b"O_X".decode()`): no resolver takes
      bytes (each is run on them and refuses), and decoding a name before a
      lookup only hides it; the lookup still has a computed name, which the
      dynamic-access rule reds, or a call result as its callee, which the
      call-result rule reds."""
    found = []
    for path in paths:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, (str, bytes)):
                continue
            if any(named(name) for name in _resolver_walk(node.value.strip())) and not read_outright(
                path, parents.get(id(node))
            ):
                found.append((Path(path).name, node.lineno, node.value))
    return sorted(set(found))


_SCOPES = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)


_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


_LITERALS = (ast.List, ast.Tuple, ast.Set, ast.Dict)


def _wrapper_types() -> frozenset:
    """The builtin types that, made from one callable, call that callable
    when they are called (`staticmethod`, `classmethod`), measured by making
    each builtin type from a probe function."""

    def probe():
        return None

    found = set()
    for name, value in vars(builtins).items():
        if not isinstance(value, type):
            continue
        try:
            made = value(probe)
        except Exception:  # a type that is not made from one callable
            continue
        if getattr(made, "__func__", None) is probe or getattr(made, "__wrapped__", None) is probe:
            found.add(name)
    return frozenset(found)


def _stored_names(code) -> set:
    """Every name the compiled `code` object binds: each STORE_ instruction
    that names a variable (not an attribute or item), and its parameters."""
    import dis

    names = set()
    for instruction in dis.get_instructions(code):
        if instruction.opname.startswith("STORE_") and instruction.opname not in (
            "STORE_ATTR", "STORE_SUBSCR", "STORE_SLICE",
        ):
            value = instruction.argval
            names.update(value if isinstance(value, tuple) else [value])
    flags = code.co_flags
    count = code.co_argcount + code.co_kwonlyargcount + bool(flags & 0x04) + bool(flags & 0x08)
    names.update(code.co_varnames[:count])
    return {name for name in names if isinstance(name, str)}


def _code_objects(code):
    yield code
    for constant in code.co_consts:
        if hasattr(constant, "co_code"):
            yield from _code_objects(constant)


_BINDING_PROBE = '''"""A module docstring."""
import os.path as p, sys
from os import sep as s
a: int = 1
b += 1
(c := 2)
d, *e = f, g = [1, 2]
for h in (): pass
async def i(j, /, k=1, *l, m, **n):
    async for o in j: pass
    async with j as q: pass
    return [r async for r in j]
with p as (t, u): pass
try: pass
except Exception as v: pass
match a:
    case [w, *x] | {"k": w, **x}: pass
    case Exception(y) as x: pass
    case z: pass
@p
class A:
    """A class docstring."""
    B: int
    def m(self, D, E: int = 1) -> int:
        global F
        F = 1
        def G():
            nonlocal D
            D = super()
        return [H for H in ()] + list(I for I in ()) and {J: K for J, K in ()} and {L for L in ()}
X = lambda M: M
'''


# The scopes a binding is probed in: the module, and each statement that
# opens a scope of its own (checked against `_SCOPES` below).
_SCOPE_WRAPPERS = {
    "Module": "{}",
    "FunctionDef": "def w():\n{}",
    "AsyncFunctionDef": "async def w():\n{}",
    "ClassDef": "class W:\n{}",
}


# Each type parameter kind spelled as the parameter `X`, and a statement of
# each owner of type parameters holding one (both checked against the
# grammar below).
_TYPE_PARAM_SPELLINGS = {"TypeVar": "X", "ParamSpec": "**X", "TypeVarTuple": "*X"}


_TYPE_PARAM_OWNERS = {
    "FunctionDef": "def f[{}]():\n    pass",
    "AsyncFunctionDef": "async def f[{}]():\n    pass",
    "ClassDef": "class C[{}]:\n    pass",
    "TypeAlias": "type A[{}] = int",
}


def _binding_probes(refused: "list | None" = None) -> list:
    """(tree, code) for every probe of a binding form the compiler accepts:
    `_BINDING_PROBE`, and, in each scope of `_SCOPE_WRAPPERS`, each form of
    the grammar's identifier fields (`_IDENTIFIER_FORMS`, an `as` alias and
    the dotted imports) and each type parameter kind in each owner. A form
    the compiler refuses in a scope (a `nonlocal` with nothing to bind)
    stores nothing there; its source goes in `refused`."""
    import textwrap

    forms = [*_IDENTIFIER_FORMS.values(), "import y as X", *_DOTTED_FORMS]
    forms += [owner.format(spelled) for owner in _TYPE_PARAM_OWNERS.values() for spelled in _TYPE_PARAM_SPELLINGS.values()]
    sources = [_BINDING_PROBE] + [
        wrapper.format(form if wrapper == "{}" else textwrap.indent(form, "    "))
        for wrapper in _SCOPE_WRAPPERS.values() for form in forms
    ]
    probes = []
    for source in sources:
        tree = ast.parse(source)
        try:
            probes.append((tree, compile(tree, "<probe>", "exec")))
        except SyntaxError:
            if refused is not None:
                refused.append(source)
    return probes


@functools.lru_cache(maxsize=None)
def _implicit_bindings() -> frozenset:
    """The names the compiler binds that no source line writes (a class
    body's `__module__`, `__qualname__` and `__doc__`, the `__class__` cell,
    a generator's `.0`, a generic's `.type_params`), read from compiling
    every binding probe (`_binding_probes`) and subtracting, per probe, each
    name its own source spells."""
    implicit = set()
    for tree, code in _binding_probes():
        spelled = {
            getattr(node, field) for node in ast.walk(tree)
            for field in ("id", "arg", "name", "asname", "rest")
            if isinstance(getattr(node, field, None), str)
        } | {part for node in ast.walk(tree) if isinstance(node, ast.alias) for part in node.name.split(".")}
        spelled |= {name for node in ast.walk(tree) if isinstance(node, (ast.Global, ast.Nonlocal)) for name in node.names}
        implicit |= {name for compiled in _code_objects(code) for name in _stored_names(compiled)} - spelled
    return frozenset(implicit)


class _CalleeReader:
    """Where every name, attribute and parameter in one module gets its
    value, read from each binding form the compiler defines, so a callee can
    be followed to every place it can come from.

    Binding forms read: assignment, annotated assignment, `:=`, augmented
    assignment (an arithmetic result, holding the elements of what it adds),
    unpacking into a tuple or list (paired by position with a literal, else
    any element), `for` and `async for` targets and comprehension and
    generator targets (an element of what they iterate), `with ... as` (what
    `__enter__` returns), def and class with their decorators, import
    aliases, `except ... as`, match captures, and parameters (the default
    and what each call or decorator in the file passes). `global` and `nonlocal`
    bind in the scope they name. FAIL CLOSED: a binding the compiler makes
    (`_stored_names` on the compiled module) that no form here read is
    unread, and an unread name may hold anything."""

    def __init__(self, tree, code, imported=None):
        self.imported = imported or {}
        self.parent, self.scope = {}, {}
        self._index(tree, None)
        self.tree = tree
        self.sources = collections.defaultdict(list)
        self.attrs = collections.defaultdict(list)
        self.class_names = collections.defaultdict(lambda: collections.defaultdict(list))
        self.declared = {}
        self.methods = {}
        self.wrappers = _wrapper_types()
        self._collect()
        self.unread = self._unread(code)
        self.changed, self.changed_attrs = self._changed_containers()
        self._active = set()

    def _index(self, node, scope):
        for child in ast.iter_child_nodes(node):
            self.parent[id(child)] = node
            self.scope[id(child)] = scope
            self._index(child, child if isinstance(child, _SCOPES) else scope)

    @staticmethod
    def _key(scope):
        return None if scope is None else id(scope)

    def _enclosing_function(self, scope):
        scope = self.scope.get(id(scope))
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            scope = self.scope.get(id(scope))
        return scope

    def _home(self, scope, name, walrus=False):
        """The scope a binding of `name` made in `scope` binds in."""
        if walrus:
            while isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                scope = self.scope.get(id(scope))
        if scope is not None:
            kind = self.declared.get((id(scope), name))
            if kind == "global":
                return None
            if kind == "nonlocal":
                return self._enclosing_function(scope)
        return scope

    def _add(self, scope, name, source, walrus=False):
        home = self._home(scope, name, walrus)
        self.sources[(self._key(home), name)].append(source)
        if isinstance(home, ast.ClassDef):
            self.class_names[id(home)][name].append(source)

    def _target_source(self, target):
        """(kind, expression) for what a store target receives."""
        node, path = target, []
        parent = self.parent[id(node)]
        while isinstance(parent, (ast.Tuple, ast.List, ast.Starred)):
            if isinstance(parent, ast.Starred):
                path.append(None)
            else:
                path.append(next(i for i, e in enumerate(parent.elts) if e is node))
            node, parent = parent, self.parent[id(parent)]
        path.reverse()
        if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            if parent.value is None:
                return ("nothing", None)
            value = parent.value
            for index in path:
                if index is None or not isinstance(value, (ast.Tuple, ast.List)) or any(
                    isinstance(e, ast.Starred) for e in value.elts
                ):
                    return ("element", value)
                value = value.elts[index]
            return ("value", value)
        if isinstance(parent, ast.AugAssign):
            return ("augmented", parent)
        if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)) and node is parent.target:
            return ("element", parent.iter)
        if isinstance(parent, ast.withitem):
            return ("enter", parent.context_expr)
        return ("unknown", None)

    def _match_subject(self, node):
        parent = self.parent.get(id(node))
        while parent is not None and not isinstance(parent, ast.Match):
            parent = self.parent.get(id(parent))
        return parent.subject if parent is not None else None

    def _collect(self):
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                for name in node.names:
                    self.declared[(id(self.scope[id(node)]), name)] = (
                        "global" if isinstance(node, ast.Global) else "nonlocal"
                    )
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, _DEF_NODES) and not any(
                        getattr(d, "id", None) == "staticmethod" for d in member.decorator_list
                    ):
                        self.methods[id(member)] = node
        for node in ast.walk(self.tree):
            scope = self.scope.get(id(node))
            target = self.parent.get(id(node))
            if isinstance(target, ast.AnnAssign) and target.value is None and node is target.target:
                continue  # an annotation alone declares a name and binds nothing
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                kind, value = self._target_source(node)
                self._add(scope, node.id, (kind, value), walrus=isinstance(self.parent[id(node)], ast.NamedExpr))
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                self.attrs[node.attr].append(self._target_source(node))
            elif isinstance(node, (*_DEF_NODES, ast.ClassDef)):
                self._add(scope, node.name, ("def", node))
            elif isinstance(node, ast.alias):
                bound = node.asname or node.name.split(".")[0]
                self._add(scope, bound, ("import", (self.parent[id(node)], node)))
            elif isinstance(node, ast.ExceptHandler) and node.name:
                self._add(scope, node.name, ("nothing", None))
            elif isinstance(node, ast.arg):
                function = self.parent[id(self.parent[id(node)])]
                self.sources[(id(function), node.arg)].append(("param", (function, node.arg)))
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                subject = self._match_subject(node)
                whole = isinstance(node, ast.MatchAs) and node.pattern is None and isinstance(
                    self.parent.get(id(node)), ast.match_case
                )
                self._add(scope, node.name, ("value" if whole else "element", subject))
            elif isinstance(node, ast.MatchMapping) and node.rest:
                self._add(scope, node.rest, ("element", self._match_subject(node)))
            elif isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
                # bound in the owner's annotation scope (a def's or class's
                # own, a type alias's around it), never a class attribute; no
                # form reads what it holds
                self.sources[(self._key(scope), node.name)].append(("type_param", node))

    def _scopes_by_code(self):
        found = collections.defaultdict(list)
        names = {ast.ListComp: "<listcomp>", ast.SetComp: "<setcomp>", ast.DictComp: "<dictcomp>",
                 ast.GeneratorExp: "<genexpr>", ast.Lambda: "<lambda>"}
        for node in ast.walk(self.tree):
            if isinstance(node, (*_DEF_NODES, ast.ClassDef)):
                first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                found[(node.name, first)].append(node)
                if node.type_params:
                    found[(f"<generic parameters of {node.name}>", first)].append(node)
            elif isinstance(node, ast.TypeAlias):
                # its value and its type parameters compile apart from the
                # scope around it, and bind there
                outer = self.scope.get(id(node))
                found[(node.name.id, node.lineno)].append(outer)
                found[(f"<generic parameters of {node.name.id}>", node.lineno)].append(outer)
            elif type(node) in names:
                found[(names[type(node)], node.lineno)].append(node)
        return found

    def _read_in(self, scope, name):
        """Whether a binding of `name` made by code compiled for `scope` was read."""
        if (self._key(scope), name) in self.sources:
            return True
        # comprehensions the compiler inlines bind in the function around them
        return any(
            (id(node), name) in self.sources for node in ast.walk(scope if scope is not None else self.tree)
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        )

    def _bound_here(self, scope, name):
        """Whether a binding of `name` compiled for `scope` was read: in that
        scope, or where its `global` or `nonlocal` statement sends it."""
        if self._read_in(scope, name):
            return True
        kind = self.declared.get((id(scope), name)) if scope is not None else None
        if kind == "global":
            return (None, name) in self.sources
        if kind == "nonlocal":
            return (self._key(self._enclosing_function(scope)), name) in self.sources
        return False

    def _unread(self, code):
        """(scope key, name) for every binding the compiled `code` makes that
        no form here read."""
        implicit = _implicit_bindings()
        by_code = self._scopes_by_code()
        unread = set()
        for compiled in _code_objects(code):
            if compiled.co_name == "<module>":
                scopes = [None]
            else:
                scopes = by_code.get((compiled.co_name, compiled.co_firstlineno), [])
            for name in _stored_names(compiled) - implicit:
                if scopes and any(self._bound_here(scope, name) for scope in scopes):
                    continue
                for scope in scopes or [None]:
                    unread.add((self._key(scope), name))
        return unread

    def _holder_key(self, name, scope):
        """The key of the scope whose binding of `name` a read in `scope`
        finds, as `held` walks to it (the module's when none binds it)."""
        current, first = scope, True
        while current is not None:
            if (self._key(current), name) in self.sources and (first or not isinstance(current, ast.ClassDef)):
                return self._key(current)
            first = False
            current = self.scope.get(id(current))
        return None

    def _read_in_place(self, node):
        """Whether the container `node` (a name or attribute) is only read
        where it stands: subscripted for a value, its `.get` called,
        iterated, compared, tested for truth, or handed to a builtin.
        Anything else (a method call, a subscript store or delete, handing it
        to a call that is not a builtin, binding, returning or formatting it)
        may change what it holds, or let other code change it."""
        parent = self.parent.get(id(node))
        if isinstance(parent, ast.Subscript) and parent.value is node:
            return isinstance(parent.ctx, ast.Load)
        if isinstance(parent, ast.Attribute) and parent.attr == "get":
            call = self.parent.get(id(parent))
            return isinstance(call, ast.Call) and call.func is parent
        if isinstance(parent, ast.Starred):
            node, parent = parent, self.parent.get(id(parent))
        if isinstance(parent, ast.keyword):
            node, parent = parent, self.parent.get(id(parent))
        if isinstance(parent, ast.Call) and parent.func is not node:
            # a builtin changes no container handed to it, except the two that
            # run code in it (which the dynamic-access rule bans in scope)
            func = parent.func
            return (
                isinstance(func, ast.Name) and hasattr(builtins, func.id) and func.id not in ("exec", "eval")
                and not self.held(func.id, self.scope.get(id(parent)))
            )
        if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)):
            return parent.iter is node
        if isinstance(parent, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            return parent.test is node
        return isinstance(parent, (ast.Compare, ast.BoolOp)) or (
            isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not)
        )

    def _changed_containers(self):
        """((scope key, name) set, attribute name set) of every container
        whose contents can change after the value it was bound to: one used
        anywhere other than read in place (`_read_in_place`), such as
        `rows.append(x)`, `table[k] = v`, `table.update(...)` or a store inside
        a function. FAIL CLOSED: what such a container holds is not read
        from its literal."""
        names, attributes = set(), set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and not self._read_in_place(node):
                names.add((self._holder_key(node.id, self.scope.get(id(node))), node.id))
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and not self._read_in_place(node):
                attributes.add(node.attr)
        return names, attributes

    def changes(self, name, scope):
        """Whether the container `name`, read in `scope`, can change after
        its binding (`_changed_containers`)."""
        return (self._holder_key(name, scope), name) in self.changed

    def held(self, name, scope):
        """The sources of `name` read where `scope` reads it: its own scope's,
        else each enclosing function's, then the module's (a class body is
        seen only from itself). A builtin holds nothing here; any other name
        the file never binds (a star import, a name set from outside) may
        hold anything."""
        current, first = scope, True
        while True:
            key = self._key(current)
            if (key, name) in self.unread:
                return [("unknown", None)]
            found = self.sources.get((key, name))
            if found and (first or not isinstance(current, ast.ClassDef)):
                return found
            if current is None:
                return [] if hasattr(builtins, name) else [("unknown", None)]
            first = False
            current = self.scope.get(id(current))

    def attribute_sources(self, node, scope):
        """What `node` (`receiver.attr`) can hold: each attribute target of
        that name in the file and each class body's name of that name (a
        class, an instance, a subclass and `self` all read it), or, for a
        module imported from another file in scope, its module-level name."""
        found = list(self.attrs.get(node.attr, []))
        receiver = node.value
        held = self.held(receiver.id, scope) if isinstance(receiver, ast.Name) else []
        if not held or any(kind != "import" for kind, _value in held):
            for names in self.class_names.values():
                found += names.get(node.attr, [])
        for kind, value in held:
            if kind == "import":
                statement, alias = value
                stem = alias.name.split(".")[-1]
                if node.attr in self.imported.get(stem, ()):
                    found.append(("unknown", None))
        return found

    def _callers(self, function):
        """The calls in the file that call `function`."""
        calls = []
        for call in ast.walk(self.tree):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if func is function:
                calls.append((call, 0))
            elif isinstance(function, _DEF_NODES) and getattr(func, "id", getattr(func, "attr", None)) == function.name:
                shift = 1 if id(function) in self.methods and isinstance(func, ast.Attribute) else 0
                calls.append((call, shift))
            elif isinstance(function, ast.Lambda) and isinstance(func, ast.Name) and any(
                kind == "value" and value is function for kind, value in self.held(func.id, self.scope.get(id(call)))
            ):
                calls.append((call, 0))
        return calls

    def _passed_elsewhere(self, function):
        """Whether `function` is used other than by a call or a decorator
        in this file (handed to `map`, stored in a table), so a caller this
        file does not show may pass its parameters."""
        parent = self.parent.get(id(function))
        if isinstance(function, ast.Lambda):
            if self._decorates(function) or isinstance(parent, ast.Call) and parent.func is function:
                return False
            if not (isinstance(parent, ast.Assign) and all(isinstance(t, ast.Name) for t in parent.targets)):
                return True
            names = {target.id for target in parent.targets}
        else:
            names = {function.name}
        called = {id(call.func) for call in ast.walk(self.tree) if isinstance(call, ast.Call)}
        called |= {id(d) for node in ast.walk(self.tree) for d in getattr(node, "decorator_list", ())}
        return any(
            isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load)
            and getattr(node, "id", getattr(node, "attr", None)) in names and id(node) not in called
            for node in ast.walk(self.tree)
        )

    def _decorates(self, function):
        """Whether `function` is a decorator applied in this file, so its
        first parameter receives the def or class it decorates."""
        name = getattr(function, "name", None)
        return any(
            d is function or name is not None and getattr(d, "id", getattr(d, "attr", None)) == name
            for node in ast.walk(self.tree) for d in getattr(node, "decorator_list", ())
        )

    def _param_values(self, function, name):
        """(expression, scope, whole) for each value the parameter `name` of
        `function` can receive in this file: its default, and what each call
        passes (a starred or `**` argument by its elements, `whole` False).
        None when it cannot be paired: `*args` or `**kwargs`, no call or
        decorator in the file passes it, or the function is used elsewhere."""
        positional = [a.arg for a in [*function.args.posonlyargs, *function.args.args]]
        keyword_only = [a.arg for a in function.args.kwonlyargs]
        if name not in positional + keyword_only or self._passed_elsewhere(function):
            return None
        outer = self.scope.get(id(function))
        defaults = dict(zip(positional[len(positional) - len(function.args.defaults):], function.args.defaults))
        defaults.update((arg, d) for arg, d in zip(keyword_only, function.args.kw_defaults) if d is not None)
        values = [(defaults[name], outer, True)] if name in defaults else []
        passed = self._decorates(function) and positional[:1] == [name]
        for call, shift in self._callers(function):
            passed = True
            scope = self.scope.get(id(call))
            for keyword in call.keywords:
                if keyword.arg == name or keyword.arg is None:
                    values.append((keyword.value, scope, keyword.arg is not None))
            if name not in positional:
                continue
            # a method called on its class (`Cls.m(obj, a)`) shifts by one less
            indexes = {positional.index(name) - shift, positional.index(name)}
            starred = next((i for i, a in enumerate(call.args) if isinstance(a, ast.Starred)), len(call.args))
            for position, arg in enumerate(call.args):
                if isinstance(arg, ast.Starred):
                    values.append((arg.value, scope, False))
                elif position in indexes or position >= starred:
                    values.append((arg, scope, True))
        return values if passed else None

    def _param_may_be(self, function, name):
        params = [a.arg for a in [*function.args.posonlyargs, *function.args.args]]
        if id(function) in self.methods and params[:1] == [name]:
            return False  # the instance or class the method is called on
        values = self._param_values(function, name)
        if values is None:
            return True
        return any(self.may_be(v, s) if whole else self.element_may_be(v, s) for v, s, whole in values)

    def _returns(self, function):
        return [
            node.value for node in ast.walk(function)
            if isinstance(node, ast.Return) and node.value is not None
            and self._enclosing_def(node) is function
        ]

    def _enclosing_def(self, node):
        scope = self.scope.get(id(node))
        while scope is not None and not isinstance(scope, (*_DEF_NODES, ast.Lambda)):
            scope = self.scope.get(id(scope))
        return scope

    def _decorator_may_be(self, kind, value, prev):
        """What applying a decorator held as (kind, value) to a value that
        `prev` says may or may not be a call result gives."""
        if kind == "def" and isinstance(value, _DEF_NODES):
            params = [a.arg for a in [*value.args.posonlyargs, *value.args.args]]
            for returned in self._returns(value):
                if isinstance(returned, ast.Name) and params[:1] == [returned.id]:
                    if prev:
                        return True
                elif self.may_be(returned, value):
                    return True
            return False
        if kind == "def":
            return False  # a class: an instance of it
        if kind == "import":
            return prev  # a decorator from another module (`functools.lru_cache`)
        return self.source_may_be((kind, value))

    def _decorated_may_be(self, node):
        value = False  # the def or class itself
        scope = self.scope.get(id(node))
        for decorator in reversed(node.decorator_list):
            value = self._applied_may_be(decorator, value, scope)
        return value

    def _applied_may_be(self, decorator, prev, scope):
        if isinstance(decorator, ast.Lambda):
            return self.may_be(decorator.body, decorator)
        if isinstance(decorator, ast.Name):
            sources = self.held(decorator.id, scope)
        elif isinstance(decorator, ast.Attribute):
            sources = self.attribute_sources(decorator, scope)
        elif isinstance(decorator, ast.Call):
            factory = decorator.func
            if isinstance(factory, ast.Name):
                makers = self.held(factory.id, scope)
            elif isinstance(factory, ast.Attribute):
                makers = self.attribute_sources(factory, scope)
            else:
                return True
            if not makers:
                return prev  # a factory from another module (`functools.wraps(f)`)
            for kind, value in makers:
                if kind == "def" and isinstance(value, _DEF_NODES):
                    for returned in self._returns(value):
                        inner = self.held(returned.id, value) if isinstance(returned, ast.Name) else [("value", returned)]
                        if any(self._decorator_may_be(k, v, prev) for k, v in inner):
                            return True
                elif kind == "import":
                    continue
                elif self.source_may_be((kind, value)):
                    return True
            return prev
        else:
            return True
        if not sources:
            return prev  # a builtin decorator (`staticmethod`, `property`)
        return any(self._decorator_may_be(kind, value, prev) for kind, value in sources)

    def _import_may_be(self, value):
        statement, alias = value
        if isinstance(statement, ast.ImportFrom):
            stem = (statement.module or "").split(".")[-1]
            return alias.name in self.imported.get(stem, ())
        return False

    def source_may_be(self, source):
        kind, value = source
        if kind == "value":
            return self.may_be(value, self.scope.get(id(value)))
        if kind == "element":
            return value is None or self.element_may_be(value, self.scope.get(id(value)))
        if kind == "def":
            return self._decorated_may_be(value)
        if kind == "import":
            return self._import_may_be(value)
        if kind == "param":
            return self._param_may_be(*value)
        if kind in ("nothing", "augmented"):
            return False
        return True  # enter, unknown

    def element_may_be(self, container, scope):
        """Whether an element of `container` may be a call result."""
        token = ("element", id(container))
        if token in self._active:
            return False
        self._active.add(token)
        try:
            if isinstance(container, ast.Dict):
                parts = [*(k for k in container.keys if k is not None), *container.values]
            elif isinstance(container, (ast.List, ast.Tuple, ast.Set)):
                parts = container.elts
            elif isinstance(container, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                return self.may_be(container.elt, container) or (
                    isinstance(container.elt, _LITERALS) and self.element_may_be(container.elt, container)
                )
            elif isinstance(container, ast.DictComp):
                return self.may_be(container.value, container) or self.may_be(container.key, container)
            elif isinstance(container, ast.Name):
                if self.changes(container.id, scope):
                    return True
                return any(self._source_elements_may_be(s) for s in self.held(container.id, scope))
            elif isinstance(container, ast.Attribute):
                if container.attr in self.changed_attrs:
                    return True
                return any(self._source_elements_may_be(s) for s in self.attribute_sources(container, scope))
            elif isinstance(container, (ast.Subscript, ast.Starred)):
                return self.element_may_be(container.value, scope)
            elif isinstance(container, ast.Call):
                return self._returned_elements_may_be(container, scope)
            elif isinstance(container, ast.BinOp):
                return self.element_may_be(container.left, scope) or self.element_may_be(container.right, scope)
            elif isinstance(container, ast.Constant):
                return False
            else:
                return True  # a call's result or another expression: its elements are not read here
            for part in parts:
                if isinstance(part, ast.Starred):
                    if self.element_may_be(part.value, scope):
                        return True
                elif self.may_be(part, scope) or (
                    isinstance(part, _LITERALS) and self.element_may_be(part, scope)
                ):
                    return True
            return False
        finally:
            self._active.discard(token)

    def _returned_elements_may_be(self, call, scope):
        """Whether an element of what `call` returns may be a call result:
        read from the returns of the undecorated plain function of this file
        it calls, and taken to be one for any other callee (a generator, a
        coroutine, a decorated or imported function, a method)."""
        if not isinstance(call.func, ast.Name):
            return True
        made = self.held(call.func.id, scope)
        if not made or not all(
            kind == "def" and isinstance(value, ast.FunctionDef) and not value.decorator_list
            and not any(isinstance(n, (ast.Yield, ast.YieldFrom)) and self._enclosing_def(n) is value
                        for n in ast.walk(value))
            for kind, value in made
        ):
            return True
        return any(self.element_may_be(returned, value) for _kind, value in made for returned in self._returns(value))

    def _source_elements_may_be(self, source):
        kind, value = source
        if kind in ("value", "element"):
            return value is None or self.element_may_be(value, self.scope.get(id(value)))
        if kind == "augmented":
            return self.element_may_be(value.value, self.scope.get(id(value)))
        if kind in ("def", "import", "nothing"):
            return False
        if kind == "param":
            values = self._param_values(*value)
            return values is None or any(self.element_may_be(v, s) for v, s, _whole in values)
        return True

    def may_be(self, node, scope):
        """Whether `node`, read in `scope`, may be another call's result."""
        token = (id(node), self._key(scope))
        if token in self._active:
            return False  # a cycle adds nothing a binding outside it does not
        self._active.add(token)
        try:
            return self._may_be(node, scope)
        finally:
            self._active.discard(token)

    def _is_partial(self, func, scope):
        """Whether `func` names `functools.partial` (imported from the
        module, or read as an attribute of the imported module)."""
        if isinstance(func, ast.Name):
            held = self.held(func.id, scope)
            return bool(held) and all(
                kind == "import" and isinstance(value[0], ast.ImportFrom) and value[0].module == "functools"
                and value[0].level == 0 and value[1].name == "partial"
                for kind, value in held
            )
        if isinstance(func, ast.Attribute) and func.attr == "partial" and isinstance(func.value, ast.Name):
            held = self.held(func.value.id, scope)
            return bool(held) and all(
                kind == "import" and isinstance(value[0], ast.Import) and value[1].name == "functools"
                for kind, value in held
            )
        return False

    def _may_be(self, node, scope):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name) and func.id in self.wrappers and not self.held(func.id, scope)
                and len(node.args) == 1 and not node.keywords
            ):
                return self.may_be(node.args[0], scope)
            if self._is_partial(func, scope) and node.args:
                return self.may_be(node.args[0], scope)  # calling the partial calls its first argument
            if (
                isinstance(func, ast.Attribute) and func.attr == "get" and isinstance(func.value, ast.Name)
                and not self.changes(func.value.id, scope)
            ):
                held = self.held(func.value.id, scope)
                if held and all(kind == "value" and isinstance(value, ast.Dict) for kind, value in held):
                    return any(self.may_be(v, self.scope.get(id(v))) for _k, d in held for v in d.values) or any(
                        self.may_be(a, scope) for a in node.args[1:]
                    )
            return True
        if isinstance(node, ast.Name):
            return any(self.source_may_be(source) for source in self.held(node.id, scope))
        if isinstance(node, ast.Attribute):
            return any(self.source_may_be(source) for source in self.attribute_sources(node, scope))
        if isinstance(node, ast.Subscript):
            return self.element_may_be(node.value, scope)
        if isinstance(node, ast.IfExp):
            return self.may_be(node.body, scope) or self.may_be(node.orelse, scope)
        if isinstance(node, ast.BoolOp):
            return any(self.may_be(value, scope) for value in node.values)
        if isinstance(node, (ast.NamedExpr, ast.Starred)):
            return self.may_be(node.value, scope)
        return False


def _callee_readers(paths) -> list:
    """(path, tree, reader) for each file at `paths`, each reader told which
    module-level names of the other files may hold a call result, so a name
    imported from one of them is read too."""
    stems = sorted(Path(path).stem for path in paths)
    # An import names a module by its last component, which is how `imported`
    # is looked up; two files of one stem would make that lookup ambiguous.
    assert len(set(stems)) == len(stems), f"two files share a module stem, so an import cannot name one: {stems}"
    parsed = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        parsed.append((path, ast.parse(text), compile(text, str(path), "exec")))
    imported = {}
    for path, tree, code in parsed:
        reader = _CalleeReader(tree, code)
        imported[Path(path).stem] = {
            name for (key, name), sources in reader.sources.items()
            if key is None and any(reader.source_may_be(s) for s in sources)
        }
    return [(path, tree, _CalleeReader(tree, code, imported)) for path, tree, code in parsed]


def unread_bindings(paths) -> list:
    """Each binding the compiler makes in the files at `paths` that the
    callee reader does not read, as (file, name)."""
    return sorted(
        (Path(path).name, name) for path, _tree, reader in _callee_readers(paths) for _key, name in reader.unread
    )


def call_result_callees(paths) -> list:
    """Each call in the files at `paths` whose callee can be another call's
    result, as (file, line, source). Such a callee (`methodcaller(...)`,
    `attrgetter(...)`) can reach any anchor through a name computed at run
    time, which neither the form table nor the string rule reads.

    The callee is followed to every place it can come from (`_CalleeReader`):
    a call; a name bound by any binding form the compiler defines; an
    attribute target or class-body name of that name anywhere in the file,
    or a module-level name of another file in scope it is imported from; an element of a literal container, with `.get` on a
    literal dict read as its values and default, when nothing changes the
    container after its literal (`_changed_containers`); a builtin wrapper made from
    one callable (`staticmethod(f)`), and `functools.partial(f, ...)`, as
    that callable; a decorated def as
    what its decorators return; an element of what a plain function of the
    file returns, read from its returns. FAIL CLOSED: an element of another
    container or of one filled after its literal (`rows.append(...)`, a
    subscript store, `.update`), a parameter no call in the file passes or of a function the
    file hands elsewhere, a `with` target, and a binding no form reads are
    taken to be one.

    ADVERSARIAL-ONLY, not read (no good-faith edit writes them to call a
    function):
    - `setattr(obj, name, call_result)`: a callable is stored by a name the
      reader of the line cannot see, which hides it on purpose.
    - a starred first argument to `functools.partial`, `staticmethod` or
      `classmethod` (`partial(*callables)`): unpacking a container into the
      one callable the wrapper takes only hides which callable it wraps.
    - an attribute set from another file (`other_module.get = ...`): one
      module rewriting another's attributes is monkeypatching, never a
      call an edit to this code means to make.
    - a library factory that builds a name-based callable applied as a
      decorator (`@operator.methodcaller(...)`): a decorator replaces a def
      with a wrapper of it, and using one to swap a function for a
      name-reader is obfuscation."""
    found = []
    for path, tree, reader in _callee_readers(paths):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and reader.may_be(node.func, reader.scope.get(id(node))):
                found.append((Path(path).name, node.lineno, ast.unparse(node)))
    return sorted(set(found))


# One statement per identifier field of the grammar, holding the name `X`
# in that field and nowhere else, and in a Name only in the given context.
_IDENTIFIER_FORMS = {
    ("AsyncFunctionDef", "name"): "async def X():\n    pass",
    ("Attribute", "attr"): "y.X = 1",
    ("ClassDef", "name"): "class X:\n    pass",
    ("ExceptHandler", "name"): "try:\n    pass\nexcept E as X:\n    pass",
    ("FunctionDef", "name"): "def X():\n    pass",
    ("Global", "names"): "def f():\n    global X",
    ("ImportFrom", "module"): "from X import y",
    ("MatchAs", "name"): "match v:\n    case X:\n        pass",
    ("MatchClass", "kwd_attrs"): "match v:\n    case C(X=1):\n        pass",
    ("MatchMapping", "rest"): "match v:\n    case {**X}:\n        pass",
    ("MatchStar", "name"): "match v:\n    case [*X]:\n        pass",
    ("Name", "id"): "X = 1",
    ("Nonlocal", "names"): "def f():\n    def g():\n        nonlocal X",
    ("ParamSpec", "name"): "def f[**X]():\n    pass",
    ("TypeVar", "name"): "def f[X]():\n    pass",
    ("TypeVarTuple", "name"): "def f[*X]():\n    pass",
    ("alias", "name"): "import X",
    ("arg", "arg"): "def f(X):\n    pass",
    ("keyword", "arg"): "f(X=1)",
}


# A dotted name binds its first part.
_DOTTED_FORMS = ["import X.y", "from X.y import z"]


# --- source-text pins -------------------------------------------------------
# Calls that return Python source or a docstring as text.
_SOURCE_TEXT_CALLS = frozenset({"getsource", "getsourcelines", "getdoc", "get_docstring", "get_source_segment"})
# str methods that look a literal up in text.
_TEXT_LOOKUPS = frozenset({
    "split", "rsplit", "find", "rfind", "index", "rindex", "count", "startswith", "endswith",
    "partition", "rpartition", "replace",
})


def _names_a_python_file(node, python_paths) -> bool:
    """True when `node` names a Python file: a `.py` string literal, a
    module's `__file__`, or a module-level name bound to either."""
    return any(
        (isinstance(part, ast.Constant) and isinstance(part.value, str) and part.value.endswith(".py"))
        or (isinstance(part, ast.Attribute) and part.attr == "__file__")
        or (isinstance(part, ast.Name) and part.id in python_paths)
        for part in ast.walk(node)
    )


def _reads_source_text(node, python_paths) -> bool:
    """True when `node` itself evaluates to source or docstring text: a
    `__doc__` attribute, an `inspect`/`ast` source call, or `read_text()`
    of a Python file."""
    if isinstance(node, ast.Attribute):
        return node.attr == "__doc__"
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name in _SOURCE_TEXT_CALLS:
        return True
    return name == "read_text" and isinstance(func, ast.Attribute) and _names_a_python_file(func.value, python_paths)


def _carries_source_text(node, text_names, python_paths) -> bool:
    """True when `node` holds source text: a read, or a name bound from one,
    anywhere in it except under `ast.parse(...)`, which turns text into
    structure."""
    if isinstance(node, ast.Call):
        func = node.func
        if (func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")) == "parse":
            return False
    if _reads_source_text(node, python_paths) or (isinstance(node, ast.Name) and node.id in text_names):
        return True
    return any(_carries_source_text(child, text_names, python_paths) for child in ast.iter_child_nodes(node))


def _text_names(function, python_paths) -> set:
    """The local names `function` binds, at any depth, from source text."""
    names, grew = set(), True
    while grew:
        grew = False
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
                targets, value = [node.target], node.value
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                targets, value = [node.target], node.iter
            else:
                continue
            if _carries_source_text(value, names, python_paths):
                new = {m.id for target in targets for m in ast.walk(target) if isinstance(m, ast.Name)} - names
                names |= new
                grew = grew or bool(new)
    return names


def _text_lookups(function, python_paths) -> list:
    """The lines where `function` looks something up in source text: `x in
    text`, a str lookup method on text, or an `re` call over text."""
    names = _text_names(function, python_paths)

    def text(node):
        return _carries_source_text(node, names, python_paths)

    found = []
    for node in ast.walk(function):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for index, op in enumerate(node.ops):
                if isinstance(op, (ast.In, ast.NotIn)) and text(operands[index + 1]) and not text(operands[index]):
                    found.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver, name = node.func.value, node.func.attr
            if name in _TEXT_LOOKUPS and node.args and text(receiver):
                found.append(node.lineno)
            elif isinstance(receiver, ast.Name) and receiver.id == "re" and len(node.args) > 1 and text(node.args[1]):
                found.append(node.lineno)
    return sorted(set(found))


def source_text_pins(source: str) -> dict:
    """{qualified function name: [line, ...]} for each function or method in
    `source` that looks something up in text read from Python source or a
    docstring (`__doc__`, `inspect.getsource`, `ast.get_docstring`,
    `read_text()` of a `.py` file). Text read from any other file (a
    Markdown skill, a JSON fixture) and code read as a tree (`ast.parse`)
    are not source text. A nested def is reported under its outermost
    function."""
    tree = ast.parse(source)
    python_paths = {
        target.id
        for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
        if _names_a_python_file(node.value, set())
    }
    pins = {}

    def visit(body, prefix):
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines = _text_lookups(node, python_paths)
                if lines:
                    pins[prefix + node.name] = lines

    visit(tree.body, "")
    return pins


def kept_pin_mismatch(module, table: dict) -> dict:
    """{"unlisted": ..., "stale": ...} between the functions of `module`
    that pin source text (`source_text_pins` over its file) and the
    function names `table` keeps. A name in `table` must exist in `module`
    and carry a reason; a name that is not a function (a data table) is
    checked for that only."""
    import inspect

    found = set(source_text_pins(Path(module.__file__).read_text(encoding="utf-8")))
    listed = set()
    for name, reason in table.items():
        owner = module
        for part in name.split("."):
            assert hasattr(owner, part), f"{name} is not in {module.__name__}"
            owner = getattr(owner, part)
        assert reason, f"{name} has no reason"
        if inspect.isfunction(owner):
            listed.add(name)
    return {"unlisted": sorted(found - listed), "stale": sorted(listed - found)}
