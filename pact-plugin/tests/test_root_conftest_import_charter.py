"""
Location: pact-plugin/tests/test_root_conftest_import_charter.py
Summary: Enforces the IMPORT CHARTER of pact-plugin/conftest.py.
Used by: the suite.

The charter: at any scope, guarded or not, pact-plugin/conftest.py imports only
the standard library, pytest (`pytest` and `_pytest`), and modules in tests/
whose own imports, followed through every tests/ module they import, are only
those. A name is resolved as the session resolves it once tests/conftest.py has
loaded: first every directory that file puts on sys.path with sys.path.insert,
read from its source (an insert this pin cannot read is a violation), then
tests/, then the plugin root and every skills/*/scripts directory (which the
root conftest puts on sys.path), and only then the standard library. So a
module in one of those directories that shadows a tests/ or standard-library
name is a violation. That over-refuses an import that runs while the root
conftest loads, before tests/conftest.py has inserted anything. A relative
import is a violation. A module there is a .py file or a directory with
__init__.py. A directory without __init__.py is not one, since Python imports
it only when no sys.path entry holds a regular module of that name, which
depends on what is installed: a data directory named like an installed package
or a standard-library module does not hide or shadow it. So an import of a
tests/ directory with no __init__.py is judged like any other name, and refused
unless it is the standard library or pytest, though no installed package of
that name exists: an over-refusal.

No file the walk reaches may name a dynamic import callable (DYNAMIC_IMPORTS),
called or not, imported by name or reached as an attribute, or bind
pytest_plugins (PLUGIN_LIST) at any scope: as a name or an attribute target,
or by an import or import alias, a def or class, an except handler, a match
capture, a parameter or a type parameter. A binding inside a function binds a
local, not the module's plugin list, and is refused too: an over-refusal. A
star import binds it only from a module that binds it itself, which is refused
where the walk reads that module. Bound through setattr or globals(), it is not
seen. All of this holds except where
DEVIATIONS allows it: one (file, enclosing function) site, with an exact count
for each callable. A deviation the real tree no longer uses is reported too, so
the charter cannot keep naming a deviation that is gone.

Each deviation runs only behind its gate, checked in two halves. This pin
proves nothing else reaches the deviation: GATED_CHAINS names the function each
deviation sits in and every function that leads to it, up to the hook that
checks the gate, with the only functions that may name each one. A reference
anywhere else is refused: at module scope (a default, a decorator, a class body
or a lambda there included), in another function, or as an alias. It is read
by name in the deviation's own file, and in any other file the walk reaches
when it resolves through that file's imports to the deviation's module. A link
the real tree no longer uses is reported, like an unused deviation. The
behavioural tests prove the other half: each hook checks its gate before it
calls the chain. A gated function named by a string (getattr, globals()) or
reached through a module bound other than by an import is not seen, and a
local in a deviation's own file that shares a chain name is refused: an
over-refusal. A def or class statement's decorators, defaults, annotations and
bases are read in the scope that defines it, since they run there.

A standard-library helper that imports a module it is given by name is matched
by what it is, not by its spelling:
- IMPORTING_HELPERS by their dotted path, resolved through the file's own import
  aliases (`import pydoc`, `import logging.config as lc`, `from pydoc import
  locate as find`), so `timeit.default_timer` or a local named `locate` is not
  one. The alias map is file-wide, so a local that rebinds an imported module's
  name (`def f(pydoc): pydoc.locate`) is still read as the module: an
  over-refusal. Only an import binds a module here, so a helper whose module is
  reached through a parameter, an assignment or a star import is not seen, and
  one under an alias name reused for another module may not be seen. An object
  that runs source text it is given (`code.InteractiveConsole`,
  `code.InteractiveInterpreter`, `code.interact`, `trace.Trace`) is matched here
  where it is made, so an instance used only to call a callable (a Trace's
  runfunc) is refused too: an over-refusal. So is a function type made from a
  code object (`types.FunctionType`, `types.LambdaType`), which runs it when
  called; one reached other than through `types` (`type(f)`), or a code object
  assigned to an existing function's `__code__`, is not seen.
- TIMEIT_RUNNERS (`timeit.timeit`, `timeit.repeat`, `timeit.Timer`) only when a
  statement or setup it is given is not a lambda, since a statement string is
  compiled and run; with none given it times `pass`.
- LITERAL_NAME_HELPERS (pydoc's resolve, render_doc, doc, writedoc and help, and
  the package doctest.testfile and DocFileSuite read a file from) only when the
  argument naming the module is a literal string, since each imports only a
  module named by a string; a module object, or a name the pin cannot read as a
  literal, is not seen.
- IMPORTING_METHODS, reached through an instance (`TestLoader().loadTestsFromName`,
  `Profile().runctx`, `config.pluginmanager.import_plugin`), by attribute name
  alone, so any object's method of that name is refused.

The script the CI deviation loads, scripts/verify_ci_environment.py, is read
statically too: at every scope it imports only the standard library and
`packaging` (or pip's vendored copy), it does import `packaging`, as the
charter says, and it makes no dynamic import.
"""
import ast
import sys
from collections import Counter
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
VERIFY_SCRIPT = PLUGIN_ROOT.parent / "scripts" / "verify_ci_environment.py"
VERIFY_SCRIPT_PACKAGES = ("packaging", "pip._vendor.packaging")
PYTEST_MODULES = frozenset({"pytest", "_pytest"})
DYNAMIC_IMPORTS = frozenset({
    "__import__", "import_module", "reload", "find_spec", "spec_from_file_location",
    "spec_from_loader", "module_from_spec", "exec_module", "load_module",
    "SourceFileLoader", "SourcelessFileLoader", "ExtensionFileLoader",
    "run_path", "run_module", "exec", "eval", "importorskip",
})
# Standard-library helpers that import a module they are given by name (each
# measured to import it on Python 3.12), by dotted path.
IMPORTING_HELPERS = frozenset({
    "pkgutil.resolve_name", "pkgutil.walk_packages", "pkgutil.get_loader",
    "pkgutil.find_loader", "pydoc.locate", "doctest.DocTestSuite",
    "logging.config.dictConfig", "logging.config.fileConfig", "xml.sax.make_parser",
    "site.addsitedir", "warnings._setoption", "xml.dom.getDOMImplementation",
    "xml.dom.domreg.getDOMImplementation", "cProfile.runctx", "profile.runctx",
    "test.support.import_helper.import_fresh_module",
    # Each imports or executes what it is given on every call: a module name, a
    # file path, a package to walk, or the test names in sys.argv.
    "pydoc.safeimport", "pydoc.importfile", "pydoc.apropos", "pkgutil.get_data",
    "unittest.main", "unittest.TestProgram", "unittest.main.main",
    "unittest.main.TestProgram",
    # Objects that run source text they are given (an InteractiveConsole's push,
    # an interpreter's runsource and runcode, interact's REPL, a Trace's run and
    # runctx), refused where they are made, since `push` and `run` are too common
    # to refuse as method names. A Trace used only for runfunc, which calls a
    # callable, is refused too: an over-refusal.
    "code.InteractiveConsole", "code.InteractiveInterpreter", "code.interact",
    "trace.Trace",
    # A function made from a code object runs it when called, so a code object
    # compiled from source imports what the source imports. `compile` itself
    # runs nothing and is not refused.
    "types.FunctionType", "types.LambdaType",
})
# Standard-library helpers that import a module only when it is named by a
# string: dotted path -> (position, keyword) of that argument. Each is refused
# only when that argument is a literal string; given a module object, or a
# string the pin cannot read, it is not seen.
LITERAL_NAME_HELPERS = {
    "pydoc.resolve": (0, "thing"), "pydoc.render_doc": (0, "thing"),
    "pydoc.doc": (0, "thing"), "pydoc.writedoc": (0, "thing"),
    "pydoc.help": (0, "request"),
    # The package a module-relative doctest file is read from.
    "doctest.testfile": (3, "package"), "doctest.DocFileSuite": (None, "package"),
}
TIMEIT_RUNNERS = frozenset({"timeit.timeit", "timeit.repeat", "timeit.Timer"})
# The same, reached through an instance. `runctx` is also a module function of
# cProfile and profile, so IMPORTING_HELPERS names those too. The rest are
# pytest plugin-manager methods, each measured to import a module named by a
# string on pytest 9.2 (consider_env reads the name from PYTEST_PLUGINS,
# consider_module and consider_conftest from a module's pytest_plugins, and
# load_setuptools_entrypoints from an entry-point group).
IMPORTING_METHODS = frozenset({"loadTestsFromName", "loadTestsFromNames", "runsource",
                               "runctx", "runeval", "discover", "import_plugin",
                               "consider_pluginarg",
                               "consider_preparse", "consider_env", "consider_module",
                               "consider_conftest", "load_setuptools_entrypoints"})
# pytest imports every module a conftest or plugin names in this variable.
PLUGIN_LIST = "pytest_plugins"
# Type parameters (Python 3.12), each binding a name.
TYPE_PARAMS = tuple(getattr(ast, kind) for kind in ("TypeVar", "ParamSpec", "TypeVarTuple")
                    if hasattr(ast, kind))
# (file, enclosing function) -> the dynamic callables it may name, and how often.
DEVIATIONS = {
    ("tests/ci_environment_binding.py", "_load_verify_script"):
        Counter({"spec_from_file_location": 1, "module_from_spec": 1, "exec_module": 1}),
    ("tests/import_identity_map.py", "resolve_origins"): Counter({"find_spec": 1}),
}
# Each deviation's file -> the deviation's function and every function that
# leads to it -> the only functions that may name it. Each chain ends in a hook
# that checks its gate first.
GATED_CHAINS = {
    "tests/ci_environment_binding.py": {
        "_load_verify_script": {"_check"},
        "_check": {"CIEnvironmentBinding.pytest_sessionstart",
                   "CIEnvironmentBinding.pytest_sessionfinish"},
    },
    "tests/import_identity_map.py": {
        "resolve_origins": {"capture_map"},
        "capture_map": {"pytest_sessionfinish"},
    },
}


def _imports(tree):
    """(dotted module, names it imports from, line) for every import in `tree`,
    at any scope; a relative import yields a module of None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, (), node.lineno
        elif isinstance(node, ast.ImportFrom):
            module = None if node.level else node.module
            yield module, tuple(alias.name for alias in node.names), node.lineno


def _import_aliases(tree):
    """{bound name: dotted module or object} for every import in `tree`, at any
    scope."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    aliases[top] = top
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _dotted(node, aliases):
    """The dotted path a Name or Attribute chain resolves to through `aliases`,
    or None."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    return None


def _times_only_lambdas(call):
    """True when every statement or setup a timeit runner is given is a lambda."""
    given = list(call.args[:2]) + [k.value for k in call.keywords if k.arg in ("stmt", "setup")]
    return all(isinstance(value, ast.Lambda) for value in given)


def _names_a_literal_module(call, aliases):
    """The LITERAL_NAME_HELPERS path `call` calls when the argument naming the
    module is a literal string, else None."""
    dotted = _dotted(call.func, aliases)
    if dotted not in LITERAL_NAME_HELPERS:
        return None
    position, keyword = LITERAL_NAME_HELPERS[dotted]
    given = [k.value for k in call.keywords if k.arg == keyword]
    if position is not None and len(call.args) > position:
        given.append(call.args[position])
    literal = any(isinstance(value, ast.Constant) and isinstance(value.value, str)
                  for value in given)
    return dotted if literal else None


def _header(node):
    """The parts of a def or class statement that run where it is defined: its
    decorators, a function's defaults and annotations, a class's bases and
    keywords, and type parameters."""
    parts = [*node.decorator_list, *getattr(node, "type_params", [])]
    if isinstance(node, ast.ClassDef):
        return parts + [*node.bases, *node.keywords]
    args = node.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs,
             *(arg for arg in (args.vararg, args.kwarg) if arg)]
    return parts + [*args.defaults, *(d for d in args.kw_defaults if d),
                    *(arg.annotation for arg in every if arg.annotation),
                    *([node.returns] if node.returns else [])]


def _scoped_nodes(tree):
    """(enclosing function or class, dotted; node) for every node in `tree`, a
    node before the nodes inside it. A def or class statement's header is in
    the scope that defines it, its body in its own."""
    def visit(node, scope):
        yield scope, node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = f"{scope}.{node.name}" if scope else node.name
            for part in _header(node):
                yield from visit(part, scope)
            if not isinstance(node, ast.ClassDef):
                args = node.args
                # A parameter binds in the function's own scope; its annotation
                # is in the header.
                for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                            *(arg for arg in (args.vararg, args.kwarg) if arg)):
                    yield inner, arg
            for part in node.body:
                yield from visit(part, inner)
        else:
            for child in ast.iter_child_nodes(node):
                yield from visit(child, scope)

    for child in ast.iter_child_nodes(tree):
        yield from visit(child, "")


def _bound_names(node):
    """The names `node` binds other than as a Name or Attribute target: an
    import or import alias, a def or class, an except handler, a match capture,
    a parameter and a type parameter."""
    if isinstance(node, ast.Import):
        return [alias.asname or alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [alias.asname or alias.name for alias in node.names]
    if isinstance(node, ast.MatchMapping):
        name = node.rest
    elif isinstance(node, ast.arg):
        name = node.arg
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                           ast.ExceptHandler, ast.MatchAs, ast.MatchStar, *TYPE_PARAMS)):
        name = node.name
    else:
        name = None
    return [name] if name else []


def _dynamic_references(tree):
    """(enclosing function or class, dotted; callable, line) for every dynamic
    import callable `tree` refers to or imports by name, and for every binding
    of pytest_plugins, as a name or an attribute."""
    aliases = _import_aliases(tree)
    harmless = set()  # timeit runners given only lambdas
    for scope, child in _scoped_nodes(tree):
        if isinstance(child, ast.Call) and _dotted(child.func, aliases) in TIMEIT_RUNNERS \
                and _times_only_lambdas(child):
            harmless.add(id(child.func))
        if isinstance(child, ast.Call):
            literal = _names_a_literal_module(child, aliases)
            if literal:
                yield scope, literal, child.lineno
        names = []
        if isinstance(child, ast.Name):
            names.append(child.id)
        elif isinstance(child, ast.Attribute):
            names.append(child.attr)
        elif isinstance(child, ast.ImportFrom):
            names.extend(alias.name for alias in child.names)
        if (isinstance(getattr(child, "ctx", None), ast.Store) and PLUGIN_LIST in names) \
                or PLUGIN_LIST in _bound_names(child):
            yield scope, PLUGIN_LIST, child.lineno
        for name in names:
            if name in DYNAMIC_IMPORTS or (
                    name in IMPORTING_METHODS and isinstance(child, ast.Attribute)):
                yield scope, name, child.lineno
        dotted = _dotted(child, aliases)
        if dotted in IMPORTING_HELPERS or (
                dotted in TIMEIT_RUNNERS and id(child) not in harmless):
            yield scope, dotted, child.lineno


def _gate_references(where, tree):
    """(name, scope, line) for every reference in `tree`, the file at `where`,
    to a function GATED_CHAINS names: by name in that function's own file, and
    in any other file when it resolves through that file's imports to the
    function's module."""
    own = GATED_CHAINS.get(where.as_posix(), {})
    other = {f"{Path(file).stem}.{name}": name
             for file, chain in GATED_CHAINS.items() if file != where.as_posix()
             for name in chain}
    aliases = _import_aliases(tree)
    for scope, node in _scoped_nodes(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in own:
            yield node.id, scope, node.lineno
        elif isinstance(node, (ast.Name, ast.Attribute)) \
                and isinstance(getattr(node, "ctx", None), ast.Load) \
                and _dotted(node, aliases) in other:
            yield other[_dotted(node, aliases)], scope, node.lineno


def _path_value(node, here, names):
    """The path `node` builds from `Path(__file__)` in the file at `here`
    (through str(), .parent, .resolve(), `/ "literal"` and a name assigned
    once in that file), or None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("str", "Path") and len(node.args) == 1:
        if node.func.id == "Path" and isinstance(node.args[0], ast.Name) \
                and node.args[0].id == "__file__":
            return here
        return _path_value(node.args[0], here, names)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "resolve" and not node.args:
        return _path_value(node.func.value, here, names)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _path_value(node.value, here, names)
        return base.parent if base else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) \
            and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
        base = _path_value(node.left, here, names)
        return base / node.right.value if base else None
    if isinstance(node, ast.Name) and node.id in names:
        rest = {name: value for name, value in names.items() if name != node.id}
        return _path_value(names[node.id], here, rest)
    return None


def ahead_of_tests(plugin_root):
    """(the directories tests/conftest.py inserts on sys.path, other than tests/
    itself, so ahead of tests/; the lines of inserts this pin cannot read)."""
    conftest = plugin_root / "tests" / "conftest.py"
    if not conftest.is_file():
        return [], []
    tree = ast.parse(conftest.read_text(encoding="utf-8"), str(conftest))
    aliases = _import_aliases(tree)
    assigned = Counter(target.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Name))
    names = {node.targets[0].id: node.value for node in ast.walk(tree)
             if isinstance(node, ast.Assign) and len(node.targets) == 1
             and isinstance(node.targets[0], ast.Name) and assigned[node.targets[0].id] == 1}
    tests = (plugin_root / "tests").resolve()
    ahead, unread = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "insert" and _dotted(node.func.value, aliases) == "sys.path":
            path = _path_value(node.args[1], conftest, names) if len(node.args) == 2 else None
            if path is None:
                unread.append(node.lineno)
            elif path.resolve() != tests and path.resolve() not in ahead:
                ahead.append(path.resolve())
    return ahead, sorted(unread)


def _in_dir(directory, dotted):
    """Whether `dotted`'s top-level name is a module or a regular package (one
    with __init__.py) in `directory`. A directory without __init__.py is not
    one: Python imports such a directory only when no sys.path entry holds a
    regular module of that name, which depends on what is installed."""
    top = dotted.split(".")[0]
    return (directory / f"{top}.py").is_file() or (directory / top / "__init__.py").is_file()


def _tests_files(tests, dotted, names):
    """The tests/ files an import of `dotted` (and of `names` from it) runs."""
    parts = dotted.split(".")
    files = []
    for depth in range(1, len(parts) + 1):
        package = tests.joinpath(*parts[:depth])
        if (package / "__init__.py").is_file():
            files.append(package / "__init__.py")
    module = tests.joinpath(*parts[:-1], parts[-1] + ".py")
    if module.is_file():
        files.append(module)
    for name in names:
        submodule = tests.joinpath(*parts, name + ".py")
        if submodule.is_file():
            files.append(submodule)
    return files


def _dynamic_violations(where, tree, used):
    """Violations for the dynamic references in `tree`, counting what each
    deviation site uses into `used`."""
    violations = []
    counts = Counter()
    for scope, name, line in _dynamic_references(tree):
        key = (where.as_posix(), scope)
        counts[key, name] += 1
        if counts[key, name] > DEVIATIONS.get(key, Counter())[name]:
            violations.append(f"{where}:{line}: dynamic import `{name}` in "
                              f"`{scope or '<module>'}` is not a charter deviation")
    for (key, name), count in counts.items():
        if key in DEVIATIONS:
            used[key][name] += min(count, DEVIATIONS[key][name])
    return violations


def charter_violations(plugin_root):
    """(violations, tests/ files followed, deviations left unused) for
    `plugin_root`/conftest.py."""
    tests = plugin_root / "tests"
    shadowing = [plugin_root, *sorted(plugin_root.glob("skills/*/scripts"))]
    ahead, unread = ahead_of_tests(plugin_root)
    violations = [f"tests/conftest.py:{line}: a sys.path insert this pin cannot read"
                  for line in unread]
    followed = []
    used = {key: Counter() for key in DEVIATIONS}
    gated = set()
    pending, seen = [plugin_root / "conftest.py"], set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        where = path.relative_to(plugin_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        violations += _dynamic_violations(where, tree, used)
        for name, scope, line in _gate_references(where, tree):
            if scope in GATED_CHAINS.get(where.as_posix(), {}).get(name, ()):
                gated.add((where.as_posix(), name, scope))
            else:
                violations.append(f"{where}:{line}: `{name}` is named in "
                                  f"`{scope or '<module>'}`, outside its gated caller")
        for dotted, names, line in _imports(tree):
            if dotted is None:
                violations.append(f"{where}:{line}: a relative import")
            elif any(_in_dir(directory, dotted) for directory in ahead):
                violations.append(f"{where}:{line}: `{dotted}` resolves to a directory "
                                  "tests/conftest.py puts ahead of tests/")
            elif _in_dir(tests, dotted):
                files = _tests_files(tests, dotted, names)
                followed.extend(files)
                pending.extend(files)
            elif any(_in_dir(directory, dotted) for directory in shadowing):
                violations.append(f"{where}:{line}: `{dotted}` resolves outside tests/")
            elif dotted.split(".")[0] not in sys.stdlib_module_names | PYTEST_MODULES:
                violations.append(
                    f"{where}:{line}: `{dotted}` is not the standard library or pytest")
    unused = [f"{file}:{function}: `{name}`"
              for (file, function), allowed in DEVIATIONS.items()
              for name in allowed if used[file, function][name] < allowed[name]]
    unused += [f"{file}:{caller}: gated call of `{name}`"
               for file, chain in GATED_CHAINS.items() for name, callers in chain.items()
               for caller in sorted(callers) if (file, name, caller) not in gated]
    return violations, sorted(set(followed)), unused


def verify_script_violations(script, plugin_root):
    """Violations for `script`, the file the CI deviation loads. It runs on this
    conftest's sys.path, so a name that tests/, the plugin root or a
    skills/*/scripts directory holds shadows the module it means."""
    tree = ast.parse(script.read_text(encoding="utf-8"), str(script))
    shadowing = [*ahead_of_tests(plugin_root)[0], plugin_root / "tests", plugin_root,
                 *sorted(plugin_root.glob("skills/*/scripts"))]
    violations = [f"{script.name}:{line}: dynamic import `{name}`"
                  for _, name, line in _dynamic_references(tree)]
    imports_packaging = False
    for dotted, _, line in _imports(tree):
        if dotted is None:
            violations.append(f"{script.name}:{line}: a relative import")
        elif any(_in_dir(directory, dotted) for directory in shadowing):
            violations.append(f"{script.name}:{line}: `{dotted}` resolves to a plugin module")
        elif any(dotted == package or dotted.startswith(package + ".")
                 for package in VERIFY_SCRIPT_PACKAGES):
            imports_packaging = True
        elif dotted.split(".")[0] not in sys.stdlib_module_names:
            violations.append(
                f"{script.name}:{line}: `{dotted}` is not the standard library or packaging")
    if not imports_packaging:
        violations.append(f"{script.name}: imports no `packaging`, as the charter says it does")
    return violations


def test_the_root_conftest_keeps_its_import_charter():
    violations, followed, unused = charter_violations(PLUGIN_ROOT)
    assert not violations, violations
    # Every deviation the charter names is still in use, exactly as allowed.
    assert not unused, unused
    # The walk reaches the tests/ modules the conftest imports today.
    names = {path.stem for path in followed}
    assert {"import_identity_map", "randomly_seed_gate", "home_census",
            "child_pytest_census", "ci_environment_binding",
            "ci_full_run_record"} <= names, names


def test_the_script_the_ci_deviation_loads_imports_only_what_the_charter_says():
    # The deviation loads this file, so the pin reads the file it loads.
    binding = (PLUGIN_ROOT / "tests" / "ci_environment_binding.py").read_text(encoding="utf-8")
    assert '"scripts" / "verify_ci_environment.py"' in binding
    assert VERIFY_SCRIPT.is_file(), VERIFY_SCRIPT
    assert verify_script_violations(VERIFY_SCRIPT, PLUGIN_ROOT) == []


def test_the_conftest_states_the_charter_and_names_this_pin():
    docstring = " ".join(ast.get_docstring(ast.parse((PLUGIN_ROOT / "conftest.py").read_text(
        encoding="utf-8"))).split())
    for phrase in ("IMPORT CHARTER: at any scope, guarded or not",
                   "No dynamic import call",
                   "tests/ci_environment_binding.py, `_load_verify_script`",
                   "GITHUB_ACTIONS=true", "imports `packaging`",
                   "tests/import_identity_map.py, `resolve_origins`",
                   "PACT_IDENTITY_MAP_OUT is set",
                   "tests/test_root_conftest_import_charter.py enforces",
                   "It cannot see a callable reached through getattr or a computed name.",
                   ("Nor can it see a deviation's function, or a function that leads to it, "
                    "reached that way or through a module bound other than by an import."),
                   "It cannot see a helper it matches by dotted path when that helper's "
                   "module is bound other than by an import (a parameter, an assignment, "
                   "a star import)",
                   "and it may not see one whose import name is reused for another module "
                   "elsewhere in the file.",
                   "It cannot see a module named by a string to a standard-library helper "
                   "whose name is too common to refuse",
                   "Exactly two functions deviate",
                   "Any other helper that imports a module by name is forbidden here too",
                   "too common to refuse, among them",
                   "a multiprocessing spawn, whose child imports the parent's main module",
                   "a standard-library helper that imports a module it is given by name",
                   "unittest.mock.patch's target", "pickle.load and pickle.loads",
                   "importlib.resources.files, an importlib.metadata entry point's load()",
                   "pydoc.locate, pydoc.safeimport, unittest.main or",
                   "It cannot see a pydoc helper that imports a module only when it is "
                   "named by a string (resolve, render_doc, doc, writedoc, help) unless that "
                   "name is a literal string, nor doctest.testfile or DocFileSuite given a "
                   "package that is not a literal string, the help method of a pydoc.Helper "
                   "instance, the run method of a pydoc.ModuleScanner, or an encoding named "
                   "by a string, as codecs.lookup, str.encode and bytes.decode take one.",
                   "the run of cProfile, profile or pdb",
                   "a warning category named in a filter string",
                   "pytest.importorskip, or a pytest plugin-manager method that imports a "
                   "plugin by name",
                   "none of them binds pytest_plugins",
                   ("It cannot see pytest_plugins bound through setattr or globals(), a code "
                    "object run through a function type reached other than through types, or "
                    "assigned to a function's __code__, or a directory tests/conftest.py puts "
                    "on sys.path other than by sys.path.insert.")):
        assert phrase in docstring, (phrase, docstring)


def test_this_pin_states_its_import_binding_blind_spot():
    docstring = " ".join(__doc__.split())
    assert ("Only an import binds a module here, so a helper whose module is reached through "
            "a parameter, an assignment or a star import is not seen, and one under an alias "
            "name reused for another module may not be seen.") in docstring, docstring
    assert ("That over-refuses an import that runs while the root conftest loads, before "
            "tests/conftest.py has inserted anything.") in docstring, docstring


def _rig(tmp_path, conftest, modules=None, scripts=None):
    root = tmp_path / "plugin"
    (root / "tests").mkdir(parents=True)
    (root / "conftest.py").write_text(conftest, encoding="utf-8")
    for name, text in (modules or {}).items():
        target = root / "tests" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for name, text in (scripts or {}).items():
        target = root / "skills" / "demo" / "scripts" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


BINDING = ("import importlib.util\n\ndef _load_verify_script(script):\n"
           "    spec = importlib.util.spec_from_file_location('v', script)\n"
           "    module = importlib.util.module_from_spec(spec)\n"
           "    spec.loader.exec_module(module)\n    return module\n")
IDENTITY = ("import importlib.util\n\ndef resolve_origins(names):\n"
            "    return {n: importlib.util.find_spec(n) for n in names}\n")
DEVIATION_CONFTEST = "import ci_environment_binding\nimport import_identity_map\n"
# The two deviations with their gated chains, shaped as the real files call them.
GATED_BINDING = BINDING + (
    "\nimport os\n\ndef _gated_root():\n"
    "    return os.environ.get('GITHUB_ACTIONS') == 'true' or None\n\n"
    "def _check(root, label):\n    verify = _load_verify_script(root)\n    return verify\n\n"
    "class CIEnvironmentBinding:\n"
    "    def pytest_sessionstart(self, session):\n        root = _gated_root()\n"
    "        if root is None:\n            return\n        _check(root, 'start')\n\n"
    "    def pytest_sessionfinish(self, session, exitstatus):\n        root = _gated_root()\n"
    "        if root is None:\n            return\n        _check(root, 'end')\n")
GATED_IDENTITY = IDENTITY + (
    "\nimport os\n\ndef capture_map(paths):\n"
    "    return {p: resolve_origins([p]) for p in paths}\n\n"
    "def pytest_sessionfinish(session, exitstatus):\n"
    "    if os.environ.get('PACT_IDENTITY_MAP_OUT'):\n        capture_map([])\n")
# A tests/conftest.py that puts tests/ and then hooks/ on sys.path, as the real one does.
INSERTS = ("import sys\nfrom pathlib import Path\n"
           "sys.path.insert(0, str(Path(__file__).parent))\n"
           "sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))\n")


@pytest.mark.parametrize("conftest, modules, scripts, expected", [
    ("import yaml\n", None, None, 'conftest.py:1: `yaml` is not the standard library or pytest'),
    ("def pytest_configure(config):\n    import yaml\n", None, None, 'conftest.py:2: `yaml` is not the standard library or pytest'),
    ("try:\n    import yaml\nexcept ImportError:\n    yaml = None\n", None, None, 'conftest.py:2: `yaml` is not the standard library or pytest'),
    ("from requests import get\n", None, None, 'conftest.py:1: `requests` is not the standard library or pytest'),
    ("import helper\n", {"helper.py": "import yaml\n"}, None, 'tests/helper.py:1: `yaml` is not the standard library or pytest'),
    ("def f():\n    from helper import x\n", {"helper.py": "def g():\n    import yaml\n"}, None, 'tests/helper.py:2: `yaml` is not the standard library or pytest'),
    ("import helper\n", {"helper.py": "import deeper\n", "deeper.py": "import yaml\n"}, None, 'tests/deeper.py:1: `yaml` is not the standard library or pytest'),
    ("import pkg.mod\n", {"pkg/__init__.py": "import yaml\n", "pkg/mod.py": ""}, None, 'tests/pkg/__init__.py:1: `yaml` is not the standard library or pytest'),
    ("from pkg import mod\n", {"pkg/__init__.py": "", "pkg/mod.py": "import yaml\n"}, None, 'tests/pkg/mod.py:1: `yaml` is not the standard library or pytest'),
    ("import tool\n", None, {"tool.py": ""}, 'conftest.py:1: `tool` resolves outside tests/'),
    ("import json\n", None, {"json.py": ""}, 'conftest.py:1: `json` resolves outside tests/'),
    ("from . import x\n", None, None, 'conftest.py:1: a relative import'),
    ("import helper\n", {"helper.py": "from . import other\n"}, None, 'tests/helper.py:1: a relative import'),
    # Dynamic imports, at module scope, in a function, by alias and transitively.
    ("__import__('yaml')\n", None, None, 'conftest.py:1: dynamic import `__import__` in `<module>` is not a charter deviation'),
    ("import importlib\n\ndef f():\n    importlib.import_module('yaml')\n", None, None, 'conftest.py:4: dynamic import `import_module` in `f` is not a charter deviation'),
    ("from importlib import import_module as load\n", None, None, 'conftest.py:1: dynamic import `import_module` in `<module>` is not a charter deviation'),
    ("import importlib\nimportlib.reload(importlib)\n", None, None, 'conftest.py:2: dynamic import `reload` in `<module>` is not a charter deviation'),
    ("def f():\n    exec('import yaml')\n", None, None, 'conftest.py:2: dynamic import `exec` in `f` is not a charter deviation'),
    ("def f(v=__import__('yaml')):\n    return v\n", None, None, 'conftest.py:1: dynamic import `__import__` in `<module>` is not a charter deviation'),
    ("import helper\n", {"helper.py": "import runpy\n\ndef g():\n    runpy.run_path('x.py')\n"},
     None, 'tests/helper.py:4: dynamic import `run_path` in `g` is not a charter deviation'),
    ("import helper\n", {"helper.py": "from importlib.util import find_spec\n"}, None, 'tests/helper.py:1: dynamic import `find_spec` in `<module>` is not a charter deviation'),
    # A deviation's calls in a file or function the charter does not name.
    ("import helper\n", {"helper.py": BINDING}, None, 'tests/helper.py:4: dynamic import `spec_from_file_location` in `_load_verify_script` is not a charter deviation'),
    (DEVIATION_CONFTEST, {"ci_environment_binding.py": BINDING.replace(
        "_load_verify_script", "_load_other"), "import_identity_map.py": IDENTITY}, None, 'tests/ci_environment_binding.py:4: dynamic import `spec_from_file_location` in `_load_other` is not a charter deviation'),
    (DEVIATION_CONFTEST, {"ci_environment_binding.py": BINDING, "import_identity_map.py":
     IDENTITY + "\ndef capture_map(n):\n    return importlib.util.find_spec(n)\n"}, None, 'tests/import_identity_map.py:7: dynamic import `find_spec` in `capture_map` is not a charter deviation'),
    # A third call, or a repeated call, inside a deviation's own function.
    (DEVIATION_CONFTEST, {"ci_environment_binding.py": BINDING.replace(
        "    return module\n", "    importlib.import_module('yaml')\n    return module\n"),
        "import_identity_map.py": IDENTITY}, None, 'tests/ci_environment_binding.py:7: dynamic import `import_module` in `_load_verify_script` is not a charter deviation'),
    (DEVIATION_CONFTEST, {"ci_environment_binding.py": BINDING.replace(
        "    return module\n", "    spec.loader.exec_module(module)\n    return module\n"),
        "import_identity_map.py": IDENTITY}, None, 'tests/ci_environment_binding.py:7: dynamic import `exec_module` in `_load_verify_script` is not a charter deviation'),
    # A plugin list in a module the walk follows.
    ("import helper\n", {"helper.py": "pytest_plugins = ['yaml']\n"}, None, 'tests/helper.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    # A star import binds it only from a module that binds it, refused there.
    ("from helper import *\n", {"helper.py": "pytest_plugins = ['yaml']\n"}, None, 'tests/helper.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    # A name a directory tests/conftest.py puts ahead of tests/ holds, read
    # through a literal path, a name assigned once and .resolve().
    ("import helper\n", {"helper.py": "", "conftest.py": INSERTS, "../hooks/helper.py": ""},
     None, 'conftest.py:1: `helper` resolves to a directory tests/conftest.py puts ahead of tests/'),
    ("def pytest_configure(config):\n    import helper\n",
     {"helper.py": "", "conftest.py": INSERTS, "../hooks/helper.py": ""}, None, 'conftest.py:2: `helper` resolves to a directory tests/conftest.py puts ahead of tests/'),
    ("import json\n", {"conftest.py": INSERTS, "../hooks/json.py": ""}, None, 'conftest.py:1: `json` resolves to a directory tests/conftest.py puts ahead of tests/'),
    ("import helper\n", {"helper.py": "", "../scripts/helper.py": "", "conftest.py": (
        "import sys\nfrom pathlib import Path\n"
        "d = Path(__file__).resolve().parent.parent / 'scripts'\nsys.path.insert(0, str(d))\n")},
     None, 'conftest.py:1: `helper` resolves to a directory tests/conftest.py puts ahead of tests/'),
    # An insert the pin cannot read, including a name assigned twice.
    ("import os\n", {"conftest.py": "import sys\nsys.path.insert(0, where())\n"}, None, 'tests/conftest.py:2: a sys.path insert this pin cannot read'),
    ("import os\n", {"conftest.py": (
        "import sys\nfrom pathlib import Path\nd = Path(__file__).parent\nd = 'x'\n"
        "sys.path.insert(0, str(d))\n")}, None, 'tests/conftest.py:5: a sys.path insert this pin cannot read'),
    # A directory with no __init__.py is not a module there: a data directory
    # named like a package does not hide its import, and an import of a tests/
    # namespace directory is judged like any other name (a stated over-refusal).
    ("import yaml\n", {"yaml/data.txt": ""}, None,
     'conftest.py:1: `yaml` is not the standard library or pytest'),
    ("from yaml import x\n", {"yaml/x.py": ""}, None,
     'conftest.py:1: `yaml` is not the standard library or pytest'),
    ("import yaml\n", None, {"yaml/data.txt": ""},
     'conftest.py:1: `yaml` is not the standard library or pytest'),
    ("import zzns\n", {"zzns/mod.py": ""}, None,
     'conftest.py:1: `zzns` is not the standard library or pytest'),
    ("from zzns import mod\n", {"zzns/mod.py": ""}, None,
     'conftest.py:1: `zzns` is not the standard library or pytest'),
])
def test_a_planted_import_outside_the_charter_is_found(tmp_path, conftest, modules, scripts,
                                                       expected):
    violations, _, _ = charter_violations(_rig(tmp_path, conftest, modules, scripts))
    # The violation the row plants, by file, line and kind, not just any one.
    assert expected in violations, (expected, violations)


def test_a_directory_ahead_of_tests_with_no_shared_name_passes(tmp_path):
    root = _rig(tmp_path, "import helper\nimport json\n",
                {"helper.py": "", "conftest.py": INSERTS, "../hooks/other.py": ""})
    violations, followed, _ = charter_violations(root)
    assert violations == [], violations
    assert [path.stem for path in followed] == ["helper"]
    assert ahead_of_tests(root) == ([(root / "hooks").resolve()], [])


def test_the_real_suite_conftest_inserts_are_all_read():
    """The directories the real tests/conftest.py puts ahead of tests/ are read
    from that file, and none of its inserts is beyond this pin."""
    ahead, unread = ahead_of_tests(PLUGIN_ROOT)
    assert unread == [], unread
    found = {path.relative_to(PLUGIN_ROOT.resolve()).as_posix() for path in ahead}
    assert {"hooks", "skills/pact-memory", "skills/pact-memory/scripts", "scripts"} <= found, found


def test_imports_inside_the_charter_pass(tmp_path):
    conftest = ("import sys\nfrom pathlib import Path\nimport pytest\nfrom _pytest import nodes\n"
                "import helper\n\ndef pytest_configure(config):\n    import loop_a\n")
    modules = {"helper.py": "import json\nimport pytest\n",
               "loop_a.py": "import loop_b\nimport os\n",
               "loop_b.py": "import loop_a\nimport re\n"}
    violations, followed, _ = charter_violations(_rig(tmp_path, conftest, modules))
    assert violations == []
    assert {path.stem for path in followed} == {"helper", "loop_a", "loop_b"}


# A data directory named like a standard-library module (no __init__.py) does
# not shadow it: Python imports the standard library's regular package first.
@pytest.mark.parametrize("modules, scripts", [
    ({"json/data.txt": ""}, None),
    (None, {"json/data.txt": ""}),
    ({"conftest.py": "import sys\nfrom pathlib import Path\n"
      "sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))\n",
      "../hooks/json/data.txt": ""}, None),
], ids=["tests", "scripts", "ahead-of-tests"])
def test_a_data_directory_named_like_the_standard_library_passes(tmp_path, modules, scripts):
    root = _rig(tmp_path, "import json\n", modules, scripts)
    violations, followed, _ = charter_violations(root)
    assert violations == [], violations
    assert followed == []


def test_a_loaded_script_beside_a_standard_library_named_data_directory_passes(tmp_path):
    target, root = _script_rig(tmp_path, "import packaging\nimport json\n",
                               {"json/data.txt": ""})
    (root / "tests" / "json").mkdir()
    (root / "tests" / "json" / "data.txt").write_text("", encoding="utf-8")
    assert verify_script_violations(target, root) == []


def test_the_two_deviations_pass_exactly_as_named(tmp_path):
    root = _rig(tmp_path, DEVIATION_CONFTEST, {"ci_environment_binding.py": GATED_BINDING,
                                               "import_identity_map.py": GATED_IDENTITY})
    violations, _, unused = charter_violations(root)
    assert violations == []
    assert unused == []


def test_a_deviation_no_longer_in_use_is_reported(tmp_path):
    root = _rig(tmp_path, DEVIATION_CONFTEST, {"ci_environment_binding.py": GATED_BINDING,
                                               "import_identity_map.py": "import os\n"})
    _, _, unused = charter_violations(root)
    assert unused == ["tests/import_identity_map.py:resolve_origins: `find_spec`",
                      "tests/import_identity_map.py:capture_map: gated call of `resolve_origins`",
                      ("tests/import_identity_map.py:pytest_sessionfinish: gated call of "
                       "`capture_map`")]


# A deviation runs only behind its gate: its function, and each function that
# leads to it, is named only in the next function of its chain. The hook at the
# chain's end checks its gate first, which the behavioural tests below prove.
@pytest.mark.parametrize("binding, identity, others, expected", [
    # The cold probe's shape: an eager module-scope call.
    (GATED_BINDING + "_EAGER = _load_verify_script('x')\n", GATED_IDENTITY, None,
     'tests/ci_environment_binding.py:30: `_load_verify_script` is named in `<module>`, outside its gated caller'),
    # An assigned alias, a lambda, a default, a decorator and a class body, all
    # at module scope.
    (GATED_BINDING + "_load = _load_verify_script\n", GATED_IDENTITY, None,
     'tests/ci_environment_binding.py:30: `_load_verify_script` is named in `<module>`, outside its gated caller'),
    (GATED_BINDING + "_lazy = lambda: _check('r', 'l')\n", GATED_IDENTITY, None,
     'tests/ci_environment_binding.py:30: `_check` is named in `<module>`, outside its gated caller'),
    (GATED_BINDING + "def f(v=_load_verify_script('x')):\n    return v\n", GATED_IDENTITY,
     None, 'tests/ci_environment_binding.py:30: `_load_verify_script` is named in `<module>`, outside its gated caller'),
    (GATED_BINDING + "@_check\ndef g():\n    pass\n", GATED_IDENTITY, None,
     'tests/ci_environment_binding.py:30: `_check` is named in `<module>`, outside its gated caller'),
    (GATED_BINDING + "class Eager:\n    v = _load_verify_script('x')\n", GATED_IDENTITY,
     None, 'tests/ci_environment_binding.py:31: `_load_verify_script` is named in `Eager`, outside its gated caller'),
    # Another function, and an ungated hook, in the deviation's own file.
    (GATED_BINDING + "def other():\n    return _load_verify_script('x')\n", GATED_IDENTITY,
     None, 'tests/ci_environment_binding.py:31: `_load_verify_script` is named in `other`, outside its gated caller'),
    (GATED_BINDING + "def pytest_configure(config):\n    _check('r', 'l')\n",
     GATED_IDENTITY, None, 'tests/ci_environment_binding.py:31: `_check` is named in `pytest_configure`, outside its gated caller'),
    (GATED_BINDING, GATED_IDENTITY + "capture_map(['x'])\n", None,
     'tests/import_identity_map.py:14: `capture_map` is named in `<module>`, outside its gated caller'),
    (GATED_BINDING, GATED_IDENTITY + "def nested():\n    return resolve_origins(['x'])\n",
     None, 'tests/import_identity_map.py:15: `resolve_origins` is named in `nested`, outside its gated caller'),
    # Another file the walk reaches, through any import form.
    (GATED_BINDING, GATED_IDENTITY, {"conftest.py": DEVIATION_CONFTEST +
                                     "ci_environment_binding._load_verify_script('x')\n"},
     'conftest.py:3: `_load_verify_script` is named in `<module>`, outside its gated caller'),
    (GATED_BINDING, GATED_IDENTITY, {"conftest.py": DEVIATION_CONFTEST + "import helper\n",
                                     "helper.py": ("from import_identity_map import "
                                                   "resolve_origins as r\n\ndef f():\n"
                                                   "    return r(['x'])\n")},
     'tests/helper.py:4: `resolve_origins` is named in `f`, outside its gated caller'),
    (GATED_BINDING, GATED_IDENTITY, {"conftest.py": DEVIATION_CONFTEST + "import helper\n",
                                     "helper.py": ("import ci_environment_binding as b\n"
                                                   "CHECK = b._check\n")},
     'tests/helper.py:2: `_check` is named in `<module>`, outside its gated caller'),
])
def test_a_deviation_named_outside_its_gated_caller_is_refused(
        tmp_path, binding, identity, others, expected):
    others = dict(others or {})
    conftest = others.pop("conftest.py", DEVIATION_CONFTEST)
    root = _rig(tmp_path, conftest, {"ci_environment_binding.py": binding,
                                     "import_identity_map.py": identity, **others})
    violations, _, _ = charter_violations(root)
    assert expected in violations, (expected, violations)


# A name a gated function shares, in a file that does not import its module, is
# not refused; the blind spots, pinned as listed: a gated function reached by a
# string or through a module bound other than by an import is not seen.
@pytest.mark.parametrize("binding, others", [
    (GATED_BINDING, {"helper.py": "def _check():\n    return 1\n\n_check()\n"}),
    (GATED_BINDING + "_E = globals()['_load_verify_script']\n", None),
    (GATED_BINDING, {"helper.py": ("import ci_environment_binding\n"
                                   "_E = getattr(ci_environment_binding, '_check')\n")}),
    (GATED_BINDING, {"helper.py": "def f(module):\n    return module._load_verify_script('x')\n"}),
], ids=["unrelated-local", "globals", "getattr", "parameter-module"])
def test_a_gated_function_reached_unresolved_is_not_seen(tmp_path, binding, others):
    conftest = DEVIATION_CONFTEST + ("import helper\n" if others else "")
    root = _rig(tmp_path, conftest, {"ci_environment_binding.py": binding,
                                     "import_identity_map.py": GATED_IDENTITY, **(others or {})})
    violations, _, unused = charter_violations(root)
    assert violations == [], violations
    assert unused == [], unused


def _script_rig(tmp_path, script, scripts=None):
    root = _rig(tmp_path, "", scripts=scripts)
    target = tmp_path / "scripts" / "verify_ci_environment.py"
    target.parent.mkdir()
    target.write_text(script, encoding="utf-8")
    return target, root


@pytest.mark.parametrize("script, scripts, expected", [
    ("import yaml\nfrom packaging.version import Version\n", None, 'verify_ci_environment.py:1: `yaml` is not the standard library or packaging'),
    ("from packaging.version import Version\n\ndef f():\n    import requests\n", None, 'verify_ci_environment.py:4: `requests` is not the standard library or packaging'),
    ("import packaging\ntry:\n    import tomli\nexcept ImportError:\n    tomli = None\n", None, 'verify_ci_environment.py:3: `tomli` is not the standard library or packaging'),
    ("import packagingx\nfrom packaging import version\n", None, 'verify_ci_environment.py:1: `packagingx` is not the standard library or packaging'),
    ("import packaging\nfrom pip import main\n", None, 'verify_ci_environment.py:2: `pip` is not the standard library or packaging'),
    ("import packaging\nfrom . import x\n", None, 'verify_ci_environment.py:2: a relative import'),
    ("import packaging\nimport tool\n", {"tool.py": ""}, 'verify_ci_environment.py:2: `tool` resolves to a plugin module'),
    ("import packaging\nimport json\n", {"json.py": ""}, 'verify_ci_environment.py:2: `json` resolves to a plugin module'),
    ("import packaging\nimport importlib\nimportlib.import_module('yaml')\n", None, 'verify_ci_environment.py:3: dynamic import `import_module`'),
    ("import os\n", None, 'verify_ci_environment.py: imports no `packaging`, as the charter says it does'),
])
def test_a_planted_import_in_the_loaded_script_is_found(tmp_path, script, scripts, expected):
    target, root = _script_rig(tmp_path, script, scripts)
    violations = verify_script_violations(target, root)
    assert expected in violations, (expected, violations)


def test_a_loaded_script_inside_the_charter_passes(tmp_path):
    target, root = _script_rig(tmp_path, (
        "import os\nfrom collections.abc import Mapping\n\ndef f():\n"
        "    try:\n        from packaging.version import Version\n    except ImportError:\n"
        "        from pip._vendor.packaging.version import Version\n"))
    assert verify_script_violations(target, root) == []


# Each standard-library helper that imports a module it is given by name, refused
# by its name (all measured to import the named module on Python 3.12), each with
# the violation it plants.
@pytest.mark.parametrize("conftest, expected", [
    ("import pkgutil\npkgutil.resolve_name('yaml')\n", 'conftest.py:2: dynamic import `pkgutil.resolve_name` in `<module>` is not a charter deviation'),
    ("import pkgutil\nlist(pkgutil.walk_packages(['.']))\n", 'conftest.py:2: dynamic import `pkgutil.walk_packages` in `<module>` is not a charter deviation'),
    ("import pkgutil\npkgutil.get_loader('yaml.x')\n", 'conftest.py:2: dynamic import `pkgutil.get_loader` in `<module>` is not a charter deviation'),
    ("import pkgutil\npkgutil.find_loader('yaml.x')\n", 'conftest.py:2: dynamic import `pkgutil.find_loader` in `<module>` is not a charter deviation'),
    ("import pydoc\npydoc.locate('yaml')\n", 'conftest.py:2: dynamic import `pydoc.locate` in `<module>` is not a charter deviation'),
    ("import unittest\nunittest.TestLoader().loadTestsFromName('yaml')\n", 'conftest.py:2: dynamic import `loadTestsFromName` in `<module>` is not a charter deviation'),
    ("import unittest\nunittest.TestLoader().loadTestsFromNames(['yaml'])\n", 'conftest.py:2: dynamic import `loadTestsFromNames` in `<module>` is not a charter deviation'),
    ("import doctest\ndoctest.DocTestSuite('yaml')\n", 'conftest.py:2: dynamic import `doctest.DocTestSuite` in `<module>` is not a charter deviation'),
    ("import logging.config\nlogging.config.dictConfig({'version': 1})\n", 'conftest.py:2: dynamic import `logging.config.dictConfig` in `<module>` is not a charter deviation'),
    ("from logging.config import fileConfig as load\nload('x.ini')\n", 'conftest.py:2: dynamic import `logging.config.fileConfig` in `<module>` is not a charter deviation'),
    ("import xml.sax\nxml.sax.make_parser(['yaml'])\n", 'conftest.py:2: dynamic import `xml.sax.make_parser` in `<module>` is not a charter deviation'),
    ("import site\nsite.addsitedir('x')\n", 'conftest.py:2: dynamic import `site.addsitedir` in `<module>` is not a charter deviation'),
    ("import code\ncode.InteractiveInterpreter().runsource('import yaml')\n", 'conftest.py:2: dynamic import `runsource` in `<module>` is not a charter deviation'),
    ("import cProfile\ncProfile.runctx('import yaml', {}, {})\n", 'conftest.py:2: dynamic import `runctx` in `<module>` is not a charter deviation'),
    ("import timeit\ntimeit.timeit('import yaml', number=1)\n", 'conftest.py:2: dynamic import `timeit.timeit` in `<module>` is not a charter deviation'),
    # A module function from-imported and called by its bare name, or an alias.
    ("from cProfile import runctx\nrunctx('import yaml', {}, {})\n", 'conftest.py:2: dynamic import `cProfile.runctx` in `<module>` is not a charter deviation'),
    ("from profile import runctx as run\nrun('import yaml', {}, {})\n", 'conftest.py:2: dynamic import `profile.runctx` in `<module>` is not a charter deviation'),
    ("import warnings\nwarnings._setoption('ignore::yaml.YAMLError')\n", 'conftest.py:2: dynamic import `warnings._setoption` in `<module>` is not a charter deviation'),
    ("import xml.dom\nxml.dom.getDOMImplementation('minidom')\n", 'conftest.py:2: dynamic import `xml.dom.getDOMImplementation` in `<module>` is not a charter deviation'),
    ("from xml.dom import domreg\ndomreg.getDOMImplementation('minidom')\n", 'conftest.py:2: dynamic import `xml.dom.domreg.getDOMImplementation` in `<module>` is not a charter deviation'),
    # Through an alias, and from a function's own import.
    ("import pydoc as docs\ndocs.locate('yaml')\n", 'conftest.py:2: dynamic import `pydoc.locate` in `<module>` is not a charter deviation'),
    ("def f():\n    from pydoc import locate\n    return locate('yaml')\n", 'conftest.py:3: dynamic import `pydoc.locate` in `f` is not a charter deviation'),
    ("import logging.config as lc\nlc.dictConfig({'version': 1})\n", 'conftest.py:2: dynamic import `logging.config.dictConfig` in `<module>` is not a charter deviation'),
    # A timeit runner given a statement that is not a lambda.
    ("import timeit\ndef f(statement):\n    return timeit.timeit(statement)\n", 'conftest.py:3: dynamic import `timeit.timeit` in `f` is not a charter deviation'),
    ("import timeit\ntimeit.Timer(stmt='import yaml')\n", 'conftest.py:2: dynamic import `timeit.Timer` in `<module>` is not a charter deviation'),
    ("import timeit\ntimeit.Timer(lambda: None, 'import yaml')\n", 'conftest.py:2: dynamic import `timeit.Timer` in `<module>` is not a charter deviation'),
    ("from timeit import repeat\nrepeat('import yaml')\n", 'conftest.py:2: dynamic import `timeit.repeat` in `<module>` is not a charter deviation'),
    # An instance-method helper is matched by attribute name on any object.
    ("def f(loader):\n    return loader.loadTestsFromName('yaml')\n", 'conftest.py:2: dynamic import `loadTestsFromName` in `f` is not a charter deviation'),
    # The alias map is file-wide: a local that rebinds the module's name is
    # still read as the module (an over-refusal).
    ("import pydoc\ndef f(pydoc):\n    return pydoc.locate\n", 'conftest.py:3: dynamic import `pydoc.locate` in `f` is not a charter deviation'),
    # pytest's own ways of importing a module by name.
    ("import pytest\npytest.importorskip('yaml')\n", 'conftest.py:2: dynamic import `importorskip` in `<module>` is not a charter deviation'),
    ("from pytest import importorskip\nimportorskip('yaml')\n", 'conftest.py:1: dynamic import `importorskip` in `<module>` is not a charter deviation'),
    ("def pytest_configure(config):\n    config.pluginmanager.import_plugin('yaml')\n", 'conftest.py:2: dynamic import `import_plugin` in `pytest_configure` is not a charter deviation'),
    ("def pytest_configure(config):\n    config.pluginmanager.consider_pluginarg('yaml')\n", 'conftest.py:2: dynamic import `consider_pluginarg` in `pytest_configure` is not a charter deviation'),
    ("def pytest_configure(config):\n    config.pluginmanager.consider_preparse(['-p', 'yaml'])\n", 'conftest.py:2: dynamic import `consider_preparse` in `pytest_configure` is not a charter deviation'),
    ("def pytest_configure(config):\n    config.pluginmanager.consider_env()\n", 'conftest.py:2: dynamic import `consider_env` in `pytest_configure` is not a charter deviation'),
    ("def pytest_configure(config):\n    config.pluginmanager.consider_module(config)\n", 'conftest.py:2: dynamic import `consider_module` in `pytest_configure` is not a charter deviation'),
    ("def pytest_configure(config):\n    config.pluginmanager.consider_conftest(config, 'x')\n", 'conftest.py:2: dynamic import `consider_conftest` in `pytest_configure` is not a charter deviation'),
    ("def pytest_configure(config):\n"
    "    config.pluginmanager.load_setuptools_entrypoints('pytest11')\n", 'conftest.py:2: dynamic import `load_setuptools_entrypoints` in `pytest_configure` is not a charter deviation'),
    # A plugin list, however it is bound by name or attribute.
    ("pytest_plugins = ['yaml']\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("pytest_plugins = 'yaml'\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("pytest_plugins: list = ['yaml']\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("pytest_plugins += ['yaml']\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("def f():\n    global pytest_plugins\n    pytest_plugins = ['yaml']\n", 'conftest.py:3: dynamic import `pytest_plugins` in `f` is not a charter deviation'),
    ("import sys\nsys.modules[__name__].pytest_plugins = ['yaml']\n", 'conftest.py:2: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    # ... and however a statement binds it other than as a target: an import or
    # import alias, a def or class, an except handler, a match capture, a
    # parameter and a type parameter.
    ("from json import loads as pytest_plugins\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("import json as pytest_plugins\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("from os import pytest_plugins\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("def pytest_plugins():\n    return ['yaml']\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("class pytest_plugins:\n    pass\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("try:\n    pass\nexcept Exception as pytest_plugins:\n    pass\n", 'conftest.py:3: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("match ['yaml']:\n    case pytest_plugins:\n        pass\n", 'conftest.py:2: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("match ['yaml']:\n    case [*pytest_plugins]:\n        pass\n", 'conftest.py:2: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("match {}:\n    case {**pytest_plugins}:\n        pass\n", 'conftest.py:2: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    ("def f(pytest_plugins):\n    return pytest_plugins\n", 'conftest.py:1: dynamic import `pytest_plugins` in `f` is not a charter deviation'),
    ("def f[pytest_plugins]():\n    pass\n", 'conftest.py:1: dynamic import `pytest_plugins` in `<module>` is not a charter deviation'),
    # A function made from a code object runs it when called.
    ("import types\ntypes.FunctionType(compile('import yaml', 's', 'exec'), {})()\n", 'conftest.py:2: dynamic import `types.FunctionType` in `<module>` is not a charter deviation'),
    ("from types import LambdaType\nLambdaType(compile('import yaml', 's', 'exec'), {})()\n", 'conftest.py:2: dynamic import `types.LambdaType` in `<module>` is not a charter deviation'),
    ("from types import FunctionType as F\nF(compile('import yaml', 's', 'exec'), {})()\n", 'conftest.py:2: dynamic import `types.FunctionType` in `<module>` is not a charter deviation'),
    # Helpers that import or execute what they are given on every call.
    ("import pydoc\npydoc.safeimport('yaml')\n", 'conftest.py:2: dynamic import `pydoc.safeimport` in `<module>` is not a charter deviation'),
    ("import pydoc\npydoc.importfile('x.py')\n", 'conftest.py:2: dynamic import `pydoc.importfile` in `<module>` is not a charter deviation'),
    ("import pydoc\npydoc.apropos('yaml')\n", 'conftest.py:2: dynamic import `pydoc.apropos` in `<module>` is not a charter deviation'),
    ("import pkgutil\npkgutil.get_data('yaml', 'x')\n", 'conftest.py:2: dynamic import `pkgutil.get_data` in `<module>` is not a charter deviation'),
    ("import unittest\nunittest.main(module='yaml')\n", 'conftest.py:2: dynamic import `unittest.main` in `<module>` is not a charter deviation'),
    ("from unittest import TestProgram\nTestProgram(module='yaml')\n", 'conftest.py:2: dynamic import `unittest.TestProgram` in `<module>` is not a charter deviation'),
    ("from unittest.main import TestProgram\nTestProgram(module='yaml')\n", 'conftest.py:2: dynamic import `unittest.main.TestProgram` in `<module>` is not a charter deviation'),
    ("from unittest.main import main\nmain(module='yaml')\n",'conftest.py:2: dynamic import `unittest.main.main` in `<module>` is not a charter deviation'),
    ("def f(loader):\n    return loader.discover('.')\n", 'conftest.py:2: dynamic import `discover` in `f` is not a charter deviation'),
    # Helpers that import only a module named by a literal string, given that way.
    ("import pydoc\npydoc.resolve('yaml')\n", 'conftest.py:2: dynamic import `pydoc.resolve` in `<module>` is not a charter deviation'),
    ("import pydoc\npydoc.render_doc(thing='yaml')\n", 'conftest.py:2: dynamic import `pydoc.render_doc` in `<module>` is not a charter deviation'),
    ("import pydoc\npydoc.doc('yaml')\n", 'conftest.py:2: dynamic import `pydoc.doc` in `<module>` is not a charter deviation'),
    ("import pydoc\npydoc.writedoc('yaml')\n", 'conftest.py:2: dynamic import `pydoc.writedoc` in `<module>` is not a charter deviation'),
    ("import pydoc\npydoc.help('yaml')\n", 'conftest.py:2: dynamic import `pydoc.help` in `<module>` is not a charter deviation'),
    ("import doctest\ndoctest.testfile('d.txt', package='yaml')\n", 'conftest.py:2: dynamic import `doctest.testfile` in `<module>` is not a charter deviation'),
    ("import doctest\ndoctest.testfile('d.txt', True, None, 'yaml')\n", 'conftest.py:2: dynamic import `doctest.testfile` in `<module>` is not a charter deviation'),
    ("from doctest import DocFileSuite\nDocFileSuite('d.txt', package='yaml')\n", 'conftest.py:2: dynamic import `doctest.DocFileSuite` in `<module>` is not a charter deviation'),
    # Objects that run source text, refused where they are made, so the direct
    # and the assigned-instance forms are both seen; and runeval by its name.
    ("import code\ncode.InteractiveConsole().push('import yaml')\n",
     'conftest.py:2: dynamic import `code.InteractiveConsole` in `<module>` is not a charter deviation'),
    ("import code\nc = code.InteractiveConsole()\nc.push('import yaml')\n",
     'conftest.py:2: dynamic import `code.InteractiveConsole` in `<module>` is not a charter deviation'),
    ("from code import InteractiveInterpreter as I\nI().runcode(compile('import yaml', 's', 'exec'))\n",
     'conftest.py:2: dynamic import `code.InteractiveInterpreter` in `<module>` is not a charter deviation'),
    ("import code\ndef f():\n    code.interact(banner='')\n",
     'conftest.py:3: dynamic import `code.interact` in `f` is not a charter deviation'),
    ("import trace\ntrace.Trace(count=0, trace=0).run('import yaml')\n",
     'conftest.py:2: dynamic import `trace.Trace` in `<module>` is not a charter deviation'),
    ("from trace import Trace\nt = Trace()\nt.run('import yaml')\n",
     'conftest.py:2: dynamic import `trace.Trace` in `<module>` is not a charter deviation'),
    ("import pdb\npdb.runeval('__import__(\"yaml\")')\n",
     'conftest.py:2: dynamic import `runeval` in `<module>` is not a charter deviation'),
    ("def f(debugger):\n    return debugger.runeval('__import__(\"yaml\")')\n",
     'conftest.py:2: dynamic import `runeval` in `f` is not a charter deviation'),
])
def test_a_helper_that_imports_by_name_is_refused(tmp_path, conftest, expected):
    violations, _, _ = charter_violations(_rig(tmp_path, conftest))
    assert expected in violations, (expected, violations)


# A name a helper shares, or a helper's harmless use, imports nothing.
@pytest.mark.parametrize("conftest", [
    "import timeit\ntimeit.default_timer()\n",
    "def f():\n    locate = 1\n    return locate\n",
    "import timeit\ntimeit.Timer(lambda: None).timeit(1)\n",
    "import timeit\ntimeit.timeit(stmt=lambda: None, setup=lambda: None, number=1)\n",
    "import timeit\ntimeit.Timer()\n",
    "def f():\n    runsource = 1\n    return runsource\n",
    # `run` and `push` are refused only on an object made by a source-running
    # constructor, never as bare method names.
    "import subprocess\nsubprocess.run(['true'], check=False)\n",
    "import contextlib\ncontextlib.ExitStack().push(lambda *exc: None)\n",
])
def test_a_name_a_helper_shares_is_not_refused(tmp_path, conftest):
    violations, _, _ = charter_violations(_rig(tmp_path, conftest))
    assert violations == [], (conftest, violations)


# The charter's blind spot, pinned as listed: each of these imports the named
# module, and the pin does not see it.
@pytest.mark.parametrize("conftest", [
    "from unittest import mock\nmock.patch('yaml.safe_load').start()\n",
    "import pickle\npickle.loads(b'cyaml\\nYAMLObject\\n.')\n",
    "import importlib.resources\nimportlib.resources.files('yaml')\n",
    # Given a module object or a name the pin cannot read as a literal string.
    "import pydoc, json\npydoc.render_doc(json)\n",
    "import pydoc\ndef f(name):\n    return pydoc.resolve(name)\n",
    "import doctest\ndef f(package):\n    return doctest.testfile('d.txt', package=package)\n",
    "import pydoc\npydoc.Helper().help('yaml')\n",
    "import pydoc\npydoc.ModuleScanner().run(print, key='yaml')\n",
    "import codecs\ncodecs.lookup('rot13')\n",
    "'x'.encode('idna')\n",
    "from importlib.metadata import EntryPoint\nEntryPoint('e', 'yaml:load', 'g').load()\n",
    "import cProfile\ncProfile.run('import yaml')\n",
    "import profile\nprofile.run('import yaml')\n",
    "import pdb\npdb.run('import yaml')\n",
    "import pickle\npickle.load(open('x', 'rb'))\n",
    "def pytest_configure(config):\n"
    "    config.addinivalue_line('filterwarnings', 'ignore::yaml.YAMLError')\n",
    "import importlib\ngetattr(importlib, 'import_module')('yaml')\n",
    "import multiprocessing\nmultiprocessing.get_context('spawn').Process(target=print).start()\n",
    "import sys\nsetattr(sys.modules[__name__], 'pytest_plugins', ['yaml'])\n",
    "globals()['pytest_plugins'] = ['yaml']\n",
    # A code object run through a function type reached other than through
    # `types`, or swapped into an existing function.
    "type(lambda: 0)(compile('import yaml', 's', 'exec'), {})()\n",
    "def f():\n    pass\n\nf.__code__ = compile('import yaml', 's', 'exec')\nf()\n",
])
def test_a_listed_blind_spot_is_not_seen(tmp_path, conftest):
    violations, _, _ = charter_violations(_rig(tmp_path, conftest))
    assert violations == [], (conftest, violations)


# The same for a directory tests/conftest.py puts on sys.path other than by
# sys.path.insert: the pin reads only those calls.
@pytest.mark.parametrize("suite_conftest", [
    "import sys\nsys.path[:0] = [__file__.rsplit('/', 2)[0] + '/hooks']\n",
    "import sys\nsys.path.append(__file__.rsplit('/', 2)[0] + '/hooks')\n",
])
def test_a_path_entry_made_other_than_by_insert_is_not_seen(tmp_path, suite_conftest):
    root = _rig(tmp_path, "import helper\n",
                {"helper.py": "", "conftest.py": suite_conftest, "../hooks/helper.py": ""})
    violations, _, _ = charter_violations(root)
    assert violations == [], (suite_conftest, violations)


# The binding blind spot, pinned as stated: a helper matched by dotted path is
# not seen when its module is bound other than by an import.
@pytest.mark.parametrize("conftest", [
    "import pydoc\ndef f(m):\n    return m.locate('yaml')\nf(pydoc)\n",
    "import pydoc\nm = pydoc\nm.locate('yaml')\n",
    "from pydoc import *\nlocate('yaml')\n",
    # The alias map keeps a name's last import, so this order hides it.
    "def b():\n    import pydoc as m\n    return m.locate('yaml')\n"
    "def a():\n    import json as m\n    return m\n",
], ids=["parameter", "assignment", "star-import", "reused-alias"])
def test_a_helper_bound_other_than_by_an_import_is_not_seen(tmp_path, conftest):
    violations, _, _ = charter_violations(_rig(tmp_path, conftest))
    assert violations == [], (conftest, violations)


# The blind spot is the helper's module, not the helper: a helper itself
# assigned to a name is still read through its module's import.
def test_an_assigned_dotted_path_helper_is_seen(tmp_path):
    conftest = "import pydoc\nloc = pydoc.locate\nloc('yaml')\n"
    violations, _, _ = charter_violations(_rig(tmp_path, conftest))
    assert violations, conftest


# A dynamic import callable and an instance-method helper are matched by name,
# so the binding blind spot does not reach them.
@pytest.mark.parametrize("conftest", [
    "import importlib\ndef f(m):\n    return m.import_module('yaml')\nf(importlib)\n",
    "import importlib\nm = importlib\nm.import_module('yaml')\n",
    "from importlib import *\nimport_module('yaml')\n",
    "def b():\n    import importlib as m\n    return m.import_module('yaml')\n"
    "def a():\n    import json as m\n    return m\n",
    "import cProfile\nm = cProfile\nm.runctx('import yaml', {}, {})\n",
], ids=["parameter", "assignment", "star-import", "reused-alias", "method-assignment"])
def test_a_callable_matched_by_name_is_seen_whatever_binds_it(tmp_path, conftest):
    violations, _, _ = charter_violations(_rig(tmp_path, conftest))
    assert violations, conftest


# The deviations' gates, as the charter states them: each hook at the end of a
# GATED_CHAINS chain checks its gate before it calls the chain.
@pytest.mark.parametrize("value", [None, "", "false", "1"])
def test_the_ci_binding_loads_no_script_unless_github_actions_is_true(monkeypatch, value):
    import ci_environment_binding as binding
    if value is None:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    else:
        monkeypatch.setenv("GITHUB_ACTIONS", value)

    def refuse(script):
        raise AssertionError(f"_load_verify_script ran with GITHUB_ACTIONS={value!r}")

    monkeypatch.setattr(binding, "_load_verify_script", refuse)
    plugin = binding.CIEnvironmentBinding()
    plugin.pytest_sessionstart(None)
    plugin.pytest_sessionfinish(None, 0)


class _Reached(Exception):
    pass


def _reached(*args):
    raise _Reached(args)


def test_the_ci_binding_loads_its_script_when_github_actions_is_true(monkeypatch):
    import ci_environment_binding as binding
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(binding, "_load_verify_script", _reached)
    plugin = binding.CIEnvironmentBinding()
    with pytest.raises(_Reached):
        plugin.pytest_sessionstart(None)
    with pytest.raises(_Reached):
        plugin.pytest_sessionfinish(None, 0)


def _identity_map_session(monkeypatch):
    import import_identity_map as identity
    monkeypatch.setitem(identity._state, "nested", False)
    monkeypatch.setitem(identity._state, "sessionstart_path", list(sys.path))
    monkeypatch.setitem(identity._state, "collected_files", {__file__})
    return identity


@pytest.mark.parametrize("value", [None, ""])
def test_the_identity_map_resolves_no_origins_unless_its_out_variable_is_set(
        monkeypatch, value):
    identity = _identity_map_session(monkeypatch)
    if value is None:
        monkeypatch.delenv("PACT_IDENTITY_MAP_OUT", raising=False)
    else:
        monkeypatch.setenv("PACT_IDENTITY_MAP_OUT", value)

    def refuse(names):
        raise AssertionError(f"resolve_origins ran with PACT_IDENTITY_MAP_OUT={value!r}")

    monkeypatch.setattr(identity, "resolve_origins", refuse)
    identity.pytest_sessionfinish(None, 0)


def test_the_identity_map_resolves_origins_when_its_out_variable_is_set(
        monkeypatch, tmp_path):
    identity = _identity_map_session(monkeypatch)
    monkeypatch.setenv("PACT_IDENTITY_MAP_OUT", str(tmp_path / "map.json"))
    monkeypatch.setattr(identity, "resolve_origins", _reached)
    with pytest.raises(_Reached):
        identity.pytest_sessionfinish(None, 0)


# The matcher on its own, where the static rule cannot also red the row: the
# `test` package is outside sys.stdlib_module_names, so any charter rig that
# imports import_fresh_module is refused for the import alone.
@pytest.mark.parametrize("source, callable_", [
    ("from cProfile import runctx\nrunctx('import yaml', {}, {})\n", "cProfile.runctx"),
    ("from profile import runctx as run\nrun('import yaml', {}, {})\n", "profile.runctx"),
    ("import cProfile\ncProfile.Profile().runctx('import yaml', {}, {})\n", "runctx"),
    ("from test.support.import_helper import import_fresh_module\nimport_fresh_module('yaml')\n",
     "test.support.import_helper.import_fresh_module"),
    ("from test.support import import_helper\nimport_helper.import_fresh_module('yaml')\n",
     "test.support.import_helper.import_fresh_module"),
])
def test_a_helper_reached_through_any_import_form_is_seen(source, callable_):
    names = {name for _, name, _ in _dynamic_references(ast.parse(source))}
    assert callable_ in names, names
