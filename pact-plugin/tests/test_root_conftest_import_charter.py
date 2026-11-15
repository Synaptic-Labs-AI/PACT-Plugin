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
"""
import ast
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PYTEST_MODULES = frozenset({"pytest", "_pytest"})


def _imports(path):
    """(dotted module, names it imports from, line) for every import in `path`,
    at any scope; a relative import yields a module of None."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, (), node.lineno
        elif isinstance(node, ast.ImportFrom):
            module = None if node.level else node.module
            yield module, tuple(alias.name for alias in node.names), node.lineno


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


def charter_violations(plugin_root):
    """(violations, tests/ files followed) for `plugin_root`/conftest.py."""
    tests = plugin_root / "tests"
    shadowing = [plugin_root, *sorted(plugin_root.glob("skills/*/scripts"))]
    violations, followed = [], []
    pending, seen = [plugin_root / "conftest.py"], set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        where = path.relative_to(plugin_root)
        for dotted, names, line in _imports(path):
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
    return violations, sorted(set(followed))


def test_the_root_conftest_keeps_its_import_charter():
    violations, followed = charter_violations(PLUGIN_ROOT)
    assert not violations, violations
    # The walk reaches the tests/ modules the conftest imports today.
    names = {path.stem for path in followed}
    assert {"import_identity_map", "randomly_seed_gate", "ci_environment_binding",
            "ci_full_run_record"} <= names, names


def test_the_conftest_states_the_charter_and_names_this_pin():
    docstring = ast.get_docstring(ast.parse((PLUGIN_ROOT / "conftest.py").read_text(
        encoding="utf-8")))
    assert "IMPORT CHARTER: at any scope, guarded or not" in docstring, docstring
    assert "tests/test_root_conftest_import_charter.py enforces" in docstring, docstring


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
])
def test_a_planted_import_outside_the_charter_is_found(tmp_path, conftest, modules, scripts):
    violations, _ = charter_violations(_rig(tmp_path, conftest, modules, scripts))
    assert violations, (conftest, modules, scripts)


def test_imports_inside_the_charter_pass(tmp_path):
    conftest = ("import sys\nfrom pathlib import Path\nimport pytest\nfrom _pytest import nodes\n"
                "import helper\n\ndef pytest_configure(config):\n    import loop_a\n")
    modules = {"helper.py": "import json\nimport pytest\n",
               "loop_a.py": "import loop_b\nimport os\n",
               "loop_b.py": "import loop_a\nimport re\n"}
    violations, followed = charter_violations(_rig(tmp_path, conftest, modules))
    assert violations == []
    assert {path.stem for path in followed} == {"helper", "loop_a", "loop_b"}
