"""
Location: pact-plugin/tests/test_root_conftest_import_charter.py
Summary: Enforces the IMPORT CHARTER of pact-plugin/conftest.py.
Used by: the suite.

The charter: at any scope, guarded or not, pact-plugin/conftest.py imports only
the standard library, pytest (`pytest` and `_pytest`), and modules in tests/
whose own imports, followed through every tests/ module they import, are only
those. A name is resolved as that conftest resolves it: tests/ first, then the
plugin root and every skills/*/scripts directory (which it puts on sys.path),
and only then the standard library, so a scripts-dir module that shadows a
standard-library name is a violation. A relative import is a violation.

No file the walk reaches may name a dynamic import callable (DYNAMIC_IMPORTS),
called or not, imported by name or reached as an attribute, except where
DEVIATIONS allows it: one (file, enclosing function) site, with an exact count
for each callable. A deviation the real tree no longer uses is reported too, so
the charter cannot keep naming a deviation that is gone.

A standard-library helper that imports a module it is given by name is matched
by what it is, not by its spelling:
- IMPORTING_HELPERS by their dotted path, resolved through the file's own import
  aliases (`import pydoc`, `import logging.config as lc`, `from pydoc import
  locate as find`), so `timeit.default_timer` or a local named `locate` is not
  one. The alias map is file-wide, so a local that rebinds an imported module's
  name (`def f(pydoc): pydoc.locate`) is still read as the module: an
  over-refusal.
- TIMEIT_RUNNERS (`timeit.timeit`, `timeit.repeat`, `timeit.Timer`) only when a
  statement or setup it is given is not a lambda, since a statement string is
  compiled and run; with none given it times `pass`.
- IMPORTING_METHODS, reached through an instance (`TestLoader().loadTestsFromName`,
  `Profile().runctx`), by attribute name alone, so
  any object's method of that name is refused.

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
    "run_path", "run_module", "exec", "eval",
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
})
TIMEIT_RUNNERS = frozenset({"timeit.timeit", "timeit.repeat", "timeit.Timer"})
# The same, reached through an instance. `runctx` is also a module function of
# cProfile and profile, so IMPORTING_HELPERS names those too.
IMPORTING_METHODS = frozenset({"loadTestsFromName", "loadTestsFromNames", "runsource",
                               "runctx"})
# (file, enclosing function) -> the dynamic callables it may name, and how often.
DEVIATIONS = {
    ("tests/ci_environment_binding.py", "_load_verify_script"):
        Counter({"spec_from_file_location": 1, "module_from_spec": 1, "exec_module": 1}),
    ("tests/import_identity_map.py", "resolve_origins"): Counter({"find_spec": 1}),
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


def _dynamic_references(tree):
    """(enclosing function or class, dotted; callable, line) for every dynamic
    import callable `tree` refers to or imports by name."""
    aliases = _import_aliases(tree)
    harmless = set()  # timeit runners given only lambdas

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and _dotted(child.func, aliases) in TIMEIT_RUNNERS \
                    and _times_only_lambdas(child):
                harmless.add(id(child.func))
            names = []
            if isinstance(child, ast.Name):
                names.append(child.id)
            elif isinstance(child, ast.Attribute):
                names.append(child.attr)
            elif isinstance(child, ast.ImportFrom):
                names.extend(alias.name for alias in child.names)
            for name in names:
                if name in DYNAMIC_IMPORTS or (
                        name in IMPORTING_METHODS and isinstance(child, ast.Attribute)):
                    yield scope, name, child.lineno
            dotted = _dotted(child, aliases)
            if dotted in IMPORTING_HELPERS or (
                    dotted in TIMEIT_RUNNERS and id(child) not in harmless):
                yield scope, dotted, child.lineno
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = f"{scope}.{child.name}" if scope else child.name
            yield from walk(child, inner)

    yield from walk(tree, "")


def _in_dir(directory, dotted):
    """Whether `dotted`'s top-level name is a module or package in `directory`."""
    top = dotted.split(".")[0]
    return (directory / f"{top}.py").is_file() or (directory / top).is_dir()


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
    violations, followed = [], []
    used = {key: Counter() for key in DEVIATIONS}
    pending, seen = [plugin_root / "conftest.py"], set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        where = path.relative_to(plugin_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        violations += _dynamic_violations(where, tree, used)
        for dotted, names, line in _imports(tree):
            if dotted is None:
                violations.append(f"{where}:{line}: a relative import")
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
    return violations, sorted(set(followed)), unused


def verify_script_violations(script, plugin_root):
    """Violations for `script`, the file the CI deviation loads. It runs on this
    conftest's sys.path, so a name that tests/, the plugin root or a
    skills/*/scripts directory holds shadows the module it means."""
    tree = ast.parse(script.read_text(encoding="utf-8"), str(script))
    shadowing = [plugin_root / "tests", plugin_root,
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
    assert {"import_identity_map", "randomly_seed_gate", "ci_environment_binding",
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
                   "reached through getattr or a computed name",
                   "Exactly two functions deviate",
                   "Any other helper that imports a module by name is forbidden here too",
                   "too common to refuse, among them",
                   "a multiprocessing spawn, whose child imports the parent's main module",
                   "a standard-library helper that imports a module it is given by name",
                   "unittest.mock.patch's target", "pickle.load and pickle.loads",
                   "importlib.resources.files", "pkgutil.get_data",
                   "an importlib.metadata entry point's load()",
                   "the run of cProfile, profile or pdb",
                   "a warning category named in a filter string"):
        assert phrase in docstring, (phrase, docstring)


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


@pytest.mark.parametrize("conftest, modules, scripts", [
    ("import yaml\n", None, None),
    ("def pytest_configure(config):\n    import yaml\n", None, None),
    ("try:\n    import yaml\nexcept ImportError:\n    yaml = None\n", None, None),
    ("from requests import get\n", None, None),
    ("import helper\n", {"helper.py": "import yaml\n"}, None),
    ("def f():\n    from helper import x\n", {"helper.py": "def g():\n    import yaml\n"}, None),
    ("import helper\n", {"helper.py": "import deeper\n", "deeper.py": "import yaml\n"}, None),
    ("import pkg.mod\n", {"pkg/__init__.py": "import yaml\n", "pkg/mod.py": ""}, None),
    ("from pkg import mod\n", {"pkg/__init__.py": "", "pkg/mod.py": "import yaml\n"}, None),
    ("import tool\n", None, {"tool.py": ""}),
    ("import json\n", None, {"json.py": ""}),
    ("from . import x\n", None, None),
    ("import helper\n", {"helper.py": "from . import other\n"}, None),
    # Dynamic imports, at module scope, in a function, by alias and transitively.
    ("__import__('yaml')\n", None, None),
    ("import importlib\n\ndef f():\n    importlib.import_module('yaml')\n", None, None),
    ("from importlib import import_module as load\n", None, None),
    ("import importlib\nimportlib.reload(importlib)\n", None, None),
    ("def f():\n    exec('import yaml')\n", None, None),
    ("import helper\n", {"helper.py": "import runpy\n\ndef g():\n    runpy.run_path('x.py')\n"},
     None),
    ("import helper\n", {"helper.py": "from importlib.util import find_spec\n"}, None),
    # A deviation's calls in a file or function the charter does not name.
    ("import helper\n", {"helper.py": BINDING}, None),
    (DEVIATION_CONFTEST, {"ci_environment_binding.py": BINDING.replace(
        "_load_verify_script", "_load_other"), "import_identity_map.py": IDENTITY}, None),
    (DEVIATION_CONFTEST, {"ci_environment_binding.py": BINDING, "import_identity_map.py":
     IDENTITY + "\ndef capture_map(n):\n    return importlib.util.find_spec(n)\n"}, None),
    # A third call, or a repeated call, inside a deviation's own function.
    (DEVIATION_CONFTEST, {"ci_environment_binding.py": BINDING.replace(
        "    return module\n", "    importlib.import_module('yaml')\n    return module\n"),
        "import_identity_map.py": IDENTITY}, None),
    (DEVIATION_CONFTEST, {"ci_environment_binding.py": BINDING.replace(
        "    return module\n", "    spec.loader.exec_module(module)\n    return module\n"),
        "import_identity_map.py": IDENTITY}, None),
])
def test_a_planted_import_outside_the_charter_is_found(tmp_path, conftest, modules, scripts):
    violations, _, _ = charter_violations(_rig(tmp_path, conftest, modules, scripts))
    assert violations, (conftest, modules, scripts)


def test_imports_inside_the_charter_pass(tmp_path):
    conftest = ("import sys\nfrom pathlib import Path\nimport pytest\nfrom _pytest import nodes\n"
                "import helper\n\ndef pytest_configure(config):\n    import loop_a\n")
    modules = {"helper.py": "import json\nimport pytest\n",
               "loop_a.py": "import loop_b\nimport os\n",
               "loop_b.py": "import loop_a\nimport re\n"}
    violations, followed, _ = charter_violations(_rig(tmp_path, conftest, modules))
    assert violations == []
    assert {path.stem for path in followed} == {"helper", "loop_a", "loop_b"}


def test_the_two_deviations_pass_exactly_as_named(tmp_path):
    root = _rig(tmp_path, DEVIATION_CONFTEST,
                {"ci_environment_binding.py": BINDING, "import_identity_map.py": IDENTITY})
    violations, _, unused = charter_violations(root)
    assert violations == []
    assert unused == []


def test_a_deviation_no_longer_in_use_is_reported(tmp_path):
    root = _rig(tmp_path, DEVIATION_CONFTEST, {"ci_environment_binding.py": BINDING,
                                               "import_identity_map.py": "import os\n"})
    _, _, unused = charter_violations(root)
    assert unused == ["tests/import_identity_map.py:resolve_origins: `find_spec`"]


def _script_rig(tmp_path, script, scripts=None):
    root = _rig(tmp_path, "", scripts=scripts)
    target = tmp_path / "scripts" / "verify_ci_environment.py"
    target.parent.mkdir()
    target.write_text(script, encoding="utf-8")
    return target, root


@pytest.mark.parametrize("script, scripts", [
    ("import yaml\nfrom packaging.version import Version\n", None),
    ("from packaging.version import Version\n\ndef f():\n    import requests\n", None),
    ("import packaging\ntry:\n    import tomli\nexcept ImportError:\n    tomli = None\n", None),
    ("import packagingx\nfrom packaging import version\n", None),
    ("import packaging\nfrom pip import main\n", None),
    ("import packaging\nfrom . import x\n", None),
    ("import packaging\nimport tool\n", {"tool.py": ""}),
    ("import packaging\nimport json\n", {"json.py": ""}),
    ("import packaging\nimport importlib\nimportlib.import_module('yaml')\n", None),
    ("import os\n", None),
])
def test_a_planted_import_in_the_loaded_script_is_found(tmp_path, script, scripts):
    target, root = _script_rig(tmp_path, script, scripts)
    assert verify_script_violations(target, root), script


def test_a_loaded_script_inside_the_charter_passes(tmp_path):
    target, root = _script_rig(tmp_path, (
        "import os\nfrom collections.abc import Mapping\n\ndef f():\n"
        "    try:\n        from packaging.version import Version\n    except ImportError:\n"
        "        from pip._vendor.packaging.version import Version\n"))
    assert verify_script_violations(target, root) == []


# Each standard-library helper that imports a module it is given by name, refused
# by its name (all measured to import the named module on Python 3.12).
@pytest.mark.parametrize("conftest", [
    "import pkgutil\npkgutil.resolve_name('yaml')\n",
    "import pkgutil\nlist(pkgutil.walk_packages(['.']))\n",
    "import pkgutil\npkgutil.get_loader('yaml.x')\n",
    "import pkgutil\npkgutil.find_loader('yaml.x')\n",
    "import pydoc\npydoc.locate('yaml')\n",
    "import unittest\nunittest.TestLoader().loadTestsFromName('yaml')\n",
    "import unittest\nunittest.TestLoader().loadTestsFromNames(['yaml'])\n",
    "import doctest\ndoctest.DocTestSuite('yaml')\n",
    "import logging.config\nlogging.config.dictConfig({'version': 1})\n",
    "from logging.config import fileConfig as load\nload('x.ini')\n",
    "import xml.sax\nxml.sax.make_parser(['yaml'])\n",
    "import site\nsite.addsitedir('x')\n",
    "import code\ncode.InteractiveInterpreter().runsource('import yaml')\n",
    "import cProfile\ncProfile.runctx('import yaml', {}, {})\n",
    "import timeit\ntimeit.timeit('import yaml', number=1)\n",
    # A module function from-imported and called by its bare name, or an alias.
    "from cProfile import runctx\nrunctx('import yaml', {}, {})\n",
    "from profile import runctx as run\nrun('import yaml', {}, {})\n",
    "import warnings\nwarnings._setoption('ignore::yaml.YAMLError')\n",
    "import xml.dom\nxml.dom.getDOMImplementation('minidom')\n",
    "from xml.dom import domreg\ndomreg.getDOMImplementation('minidom')\n",
    # Through an alias, and from a function's own import.
    "import pydoc as docs\ndocs.locate('yaml')\n",
    "def f():\n    from pydoc import locate\n    return locate('yaml')\n",
    "import logging.config as lc\nlc.dictConfig({'version': 1})\n",
    # A timeit runner given a statement that is not a lambda.
    "import timeit\ndef f(statement):\n    return timeit.timeit(statement)\n",
    "import timeit\ntimeit.Timer(stmt='import yaml')\n",
    "import timeit\ntimeit.Timer(lambda: None, 'import yaml')\n",
    "from timeit import repeat\nrepeat('import yaml')\n",
    # An instance-method helper is matched by attribute name on any object.
    "def f(loader):\n    return loader.loadTestsFromName('yaml')\n",
    # The alias map is file-wide: a local that rebinds the module's name is
    # still read as the module (an over-refusal).
    "import pydoc\ndef f(pydoc):\n    return pydoc.locate\n",
])
def test_a_helper_that_imports_by_name_is_refused(tmp_path, conftest):
    violations, _, _ = charter_violations(_rig(tmp_path, conftest))
    assert violations, conftest


# A name a helper shares, or a helper's harmless use, imports nothing.
@pytest.mark.parametrize("conftest", [
    "import timeit\ntimeit.default_timer()\n",
    "def f():\n    locate = 1\n    return locate\n",
    "import timeit\ntimeit.Timer(lambda: None).timeit(1)\n",
    "import timeit\ntimeit.timeit(stmt=lambda: None, setup=lambda: None, number=1)\n",
    "import timeit\ntimeit.Timer()\n",
    "def f():\n    runsource = 1\n    return runsource\n",
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
    "import pkgutil\npkgutil.get_data('yaml', 'x')\n",
    "from importlib.metadata import EntryPoint\nEntryPoint('e', 'yaml:load', 'g').load()\n",
    "import cProfile\ncProfile.run('import yaml')\n",
    "import profile\nprofile.run('import yaml')\n",
    "import pdb\npdb.run('import yaml')\n",
    "import pickle\npickle.load(open('x', 'rb'))\n",
    "def pytest_configure(config):\n"
    "    config.addinivalue_line('filterwarnings', 'ignore::yaml.YAMLError')\n",
    "import importlib\ngetattr(importlib, 'import_module')('yaml')\n",
    "import multiprocessing\nmultiprocessing.get_context('spawn').Process(target=print).start()\n",
])
def test_a_listed_blind_spot_is_not_seen(tmp_path, conftest):
    violations, _, _ = charter_violations(_rig(tmp_path, conftest))
    assert violations == [], (conftest, violations)


# The deviations' gates, as the charter states them.
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
