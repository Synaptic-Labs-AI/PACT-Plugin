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
"""
import ast
import sys
from collections import Counter
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PYTEST_MODULES = frozenset({"pytest", "_pytest"})
DYNAMIC_IMPORTS = frozenset({
    "__import__", "import_module", "reload", "find_spec", "spec_from_file_location",
    "spec_from_loader", "module_from_spec", "exec_module", "load_module",
    "SourceFileLoader", "SourcelessFileLoader", "ExtensionFileLoader",
    "run_path", "run_module", "exec", "eval",
})
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


def _dynamic_references(node, scope=""):
    """(enclosing function or class, dotted; name, line) for every name in
    DYNAMIC_IMPORTS that `node` refers to or imports by name."""
    for child in ast.iter_child_nodes(node):
        name = None
        if isinstance(child, ast.Name):
            name = child.id
        elif isinstance(child, ast.Attribute):
            name = child.attr
        elif isinstance(child, ast.ImportFrom):
            for alias in child.names:
                if alias.name in DYNAMIC_IMPORTS:
                    yield scope, alias.name, child.lineno
        if name in DYNAMIC_IMPORTS:
            yield scope, name, child.lineno
        inner = scope
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = f"{scope}.{child.name}" if scope else child.name
        yield from _dynamic_references(child, inner)


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


def test_the_root_conftest_keeps_its_import_charter():
    violations, followed, unused = charter_violations(PLUGIN_ROOT)
    assert not violations, violations
    # Every deviation the charter names is still in use, exactly as allowed.
    assert not unused, unused
    # The walk reaches the tests/ modules the conftest imports today.
    names = {path.stem for path in followed}
    assert {"import_identity_map", "randomly_seed_gate", "ci_environment_binding",
            "ci_full_run_record"} <= names, names


def test_the_conftest_states_the_charter_and_names_this_pin():
    docstring = " ".join(ast.get_docstring(ast.parse((PLUGIN_ROOT / "conftest.py").read_text(
        encoding="utf-8"))).split())
    for phrase in ("IMPORT CHARTER: at any scope, guarded or not",
                   "No dynamic import call",
                   "tests/ci_environment_binding.py, `_load_verify_script`",
                   "GITHUB_ACTIONS=true", "imports `packaging`",
                   "tests/import_identity_map.py, `resolve_origins`",
                   "PACT_IDENTITY_MAP_OUT is set",
                   "tests/test_root_conftest_import_charter.py enforces"):
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
