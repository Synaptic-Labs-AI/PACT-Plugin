"""
Location: pact-plugin/tests/test_store_sqlite_copy_guard.py
Summary: Keeps every test that drives the memory store on the store's own
         SQLite module (`scripts.database.sqlite3`).
Used by: the suite; it reads the test modules' source.

`database.py` uses pysqlite3 when it is installed. Where pysqlite3 links
SQLite statically (CI's manylinux wheel), the standard `sqlite3` module is a
second copy of SQLite in the same process. Two copies do not share their POSIX
lock bookkeeping, and closing any descriptor on a file drops every lock the
process holds on it, so a standard-library connection held open beside the
store's can reset the WAL under it and leave the store reading as "database
disk image is malformed". A test module that imports the memory scripts
therefore imports `sqlite3` from `scripts.database`, never the standard
library, unless ALLOWED (tests/fixtures/sqlite_copy_probe.py) names why it is
safe there; the suite's conftest then runs that module's tests under
`CopyProbe`, which fails any test that breaks the stated condition.
"""

import ast
from pathlib import Path

from fixtures.sqlite_copy_probe import ALLOWED, STDLIB_SQLITE3, CopyProbe

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "skills" / "pact-memory" / "scripts"
MEMORY_MODULES = frozenset(path.stem for path in SCRIPTS.glob("*.py")) - {"__init__"}
# Memory scripts some tests import by their bare name (the scripts directory is
# on sys.path); the generic names (`config`, `cli`, `search`) are left out,
# since other modules share them.
BARE_MEMORY_MODULES = frozenset({
    "database", "memory_api", "memory_init", "embedding_catchup", "working_memory", "setup_memory",
})

def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, None
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for alias in node.names:
                yield node.module, alias.name


def _reads_the_store(tree):
    for module, name in _imports(tree):
        parts = module.split(".")
        if parts[0] == "scripts" and (
            (len(parts) > 1 and parts[1] in MEMORY_MODULES) or name in MEMORY_MODULES
        ):
            return True
        if len(parts) == 1 and name is None and module in BARE_MEMORY_MODULES:
            return True
        if len(parts) == 1 and module in BARE_MEMORY_MODULES:
            return True
    return False


def _imports_stdlib_sqlite3(tree):
    return any(module.split(".")[0] == "sqlite3" for module, _ in _imports(tree))


def _offenders():
    found = set()
    for path in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _reads_the_store(tree) and _imports_stdlib_sqlite3(tree):
            found.add(path.relative_to(TESTS).as_posix())
    return found


def test_no_store_test_imports_the_standard_sqlite3():
    """RED WHEN a module that imports the memory scripts imports the standard
    `sqlite3` (`import sqlite3`, `from sqlite3 import ...`) without an
    allowlisted reason."""
    unexplained = sorted(_offenders() - set(ALLOWED))
    assert not unexplained, (
        f"import sqlite3 from scripts.database in {unexplained}, or name the reason "
        "in ALLOWED: a second SQLite copy beside the store's corrupts its WAL"
    )


def test_every_allowlisted_module_states_why_it_is_safe():
    """RED WHEN an ALLOWED entry has no reason: an entry must name the
    condition under which its standard-library connections never overlap a
    store-code connection to the same file."""
    blank = sorted(path for path, reason in ALLOWED.items() if not str(reason or "").strip())
    assert not blank, blank


def test_every_allowlisted_module_still_needs_its_entry():
    """RED WHEN an allowlisted module no longer imports the standard module
    beside the memory scripts, so its entry has gone stale."""
    stale = sorted(set(ALLOWED) - _offenders())
    assert not stale, stale


def test_the_guard_sees_both_import_shapes(tmp_path):
    """Control: both import shapes, at module and function scope, are caught,
    and the store's own module is not."""
    cases = {
        "import sqlite3\nfrom scripts.database import db_connection\n": True,
        "from scripts import memory_api\ndef f():\n    from sqlite3 import connect\n": True,
        "from scripts.database import sqlite3, db_connection\n": False,
        "import sqlite3\n": False,
    }
    for source, offends in cases.items():
        tree = ast.parse(source)
        assert (_reads_the_store(tree) and _imports_stdlib_sqlite3(tree)) is offends, source


def _fake_store_driver():
    """A second driver object over the standard module, so the probe sees two
    kinds wherever it runs (one SQLite copy or two)."""
    import types

    return types.SimpleNamespace(connect=STDLIB_SQLITE3.connect, Connection=STDLIB_SQLITE3.Connection)


def test_the_probe_catches_a_held_connection_across_a_store_open_and_close(tmp_path, monkeypatch):
    """The 693adf3d shape: a standard-library connection held open while the
    store code opens and closes its own connection to the same file. RED WHEN
    the probe records nothing, or does not name the file and both sites."""
    store_driver = _fake_store_driver()
    probe = CopyProbe(STDLIB_SQLITE3, store_driver)
    probe.install(monkeypatch)
    db = tmp_path / "store.db"
    held = STDLIB_SQLITE3.connect(str(db))
    held.execute("CREATE TABLE t (x)")
    held.commit()
    store = store_driver.connect(str(db))
    store.execute("SELECT * FROM t").fetchall()
    store.close()
    held.close()
    assert len(probe.overlaps) == 2, probe.overlaps
    assert all(str(db.resolve()) in line for line in probe.overlaps), probe.overlaps
    assert "store-code connection opened at test_store_sqlite_copy_guard.py:" in probe.overlaps[0], probe.overlaps
    assert "standard-library connection opened at test_store_sqlite_copy_guard.py:" in probe.overlaps[0], probe.overlaps
    assert "store-code connection closed at" in probe.overlaps[1], probe.overlaps


def test_the_probe_passes_connections_that_never_overlap(tmp_path, monkeypatch):
    """Control: one after the other on one file, and both at once on two
    files, record nothing."""
    store_driver = _fake_store_driver()
    probe = CopyProbe(STDLIB_SQLITE3, store_driver)
    probe.install(monkeypatch)
    first = STDLIB_SQLITE3.connect(str(tmp_path / "a.db"))
    first.close()
    store_driver.connect(str(tmp_path / "a.db")).close()
    other = STDLIB_SQLITE3.connect(str(tmp_path / "b.db"))
    store_driver.connect(str(tmp_path / "a.db")).close()
    other.close()
    assert probe.overlaps == [], probe.overlaps


# The driver modules whose `connect` the probe wraps, by import name.
_DRIVER_MODULES = frozenset({"sqlite3", "sqlite3.dbapi2", "pysqlite3", "pysqlite3.dbapi2"})
# Names a from-import may bind a driver module to.
_DRIVER_NAMES = frozenset({"sqlite3", "pysqlite3", "dbapi2"})


def _probe_bypasses(tree):
    """Every way an ALLOWED module could reach a `connect` the probe does not
    wrap: importing `connect` or `Connection` from a driver, importing a
    driver's `dbapi2` submodule (its own `connect` binding), and taking
    `<driver>.connect` at import time (a module-level alias, a default
    argument, a decorator), before the fixture installs the probe; binding a
    driver re-exported by another module, or importing one by name through
    `importlib.import_module` or `__import__`; and calling
    `<driver>.Connection(...)` anywhere, since the probe wraps `connect`
    only. A call `<driver>.connect(...)` inside a function runs after the
    fixture, so it is wrapped."""
    found = []
    drivers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _DRIVER_MODULES:
                    drivers.add(alias.asname or alias.name.split(".")[0])
                if alias.name.endswith(".dbapi2"):
                    found.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            if node.module in _DRIVER_MODULES:
                found.append(f"line {node.lineno}: from {node.module} import ...")
            for alias in node.names:
                if (node.module, alias.name) == ("scripts.database", "sqlite3"):
                    drivers.add(alias.asname or alias.name)
                elif alias.name in _DRIVER_NAMES and node.module not in _DRIVER_MODULES:
                    # A driver re-exported by another module (`from scripts.graph
                    # import sqlite3`): only database's is the store driver.
                    found.append(f"line {node.lineno}: from {node.module} import {alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            dynamic = (isinstance(func, ast.Name) and func.id == "__import__") or (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            )
            if dynamic and node.args and isinstance(node.args[0], ast.Constant):
                name = node.args[0].value
                if isinstance(name, str) and (name in _DRIVER_MODULES or name.endswith(".dbapi2")):
                    found.append(f"line {node.lineno}: imports {name!r} by name")

    def import_time(node, called=False):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for part in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
                import_time(part)
            for decorator in getattr(node, "decorator_list", []):
                import_time(decorator)
            return
        if isinstance(node, ast.Call):
            import_time(node.func, called=True)
            for part in list(node.args) + [keyword.value for keyword in node.keywords]:
                import_time(part)
            return
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "connect"
            and isinstance(node.value, ast.Name)
            and node.value.id in drivers
            and not called
        ):
            found.append(f"line {node.lineno}: {node.value.id}.connect taken at import time")
        for child in ast.iter_child_nodes(node):
            import_time(child)

    import_time(tree)
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        if (
            isinstance(node, ast.Call)
            and isinstance(func, ast.Attribute)
            and func.attr == "Connection"
            and isinstance(func.value, ast.Name)
            and func.value.id in drivers
        ):
            line = f"line {node.lineno}: {func.value.id}.Connection(...) called"
            if line not in found:
                found.append(line)
    return found


def test_no_allowlisted_module_reaches_an_unwrapped_connect():
    """RED WHEN an ALLOWED module imports `connect` from a driver, imports a
    driver's `dbapi2`, or takes `<driver>.connect` at import time: the probe
    wraps the module attribute when a test starts, so those reach the real
    `connect` and an overlap would go unrecorded."""
    bypasses = {}
    for name in ALLOWED:
        found = _probe_bypasses(ast.parse((TESTS / name).read_text(encoding="utf-8")))
        if found:
            bypasses[name] = found
    assert not bypasses, bypasses


def test_the_bypass_scan_sees_each_form():
    """Control: each bypass form is caught, and a call inside a test is not."""
    cases = {
        "from sqlite3 import connect\n": True,
        "from sqlite3 import Connection\n": True,
        "from sqlite3.dbapi2 import connect\n": True,
        "import sqlite3.dbapi2\n": True,
        "import sqlite3\nconnect = sqlite3.connect\n": True,
        "import sqlite3 as std\nCONNECT = {'c': std.connect}\n": True,
        "import sqlite3\ndef f(c=sqlite3.connect):\n    return c\n": True,
        "from scripts.database import sqlite3 as store\nopen_store = store.connect\n": True,
        "import sqlite3\ndef test_x(tmp_path):\n    sqlite3.connect(str(tmp_path / 'a.db')).close()\n": False,
        "import sqlite3\ndef helper():\n    c = sqlite3.connect\n    return c(':memory:')\n": False,
        "from scripts.graph import sqlite3\n": True,
        "from scripts.search import pysqlite3\n": True,
        "from somewhere import dbapi2\n": True,
        "import importlib\ndef f():\n    return importlib.import_module('sqlite3')\n": True,
        "def f():\n    return __import__('pysqlite3.dbapi2')\n": True,
        "import sqlite3\ndef test_x(p):\n    sqlite3.Connection(str(p))\n": True,
        "from scripts.database import sqlite3 as store\nc = None\ndef f(p):\n    return store.Connection(p)\n": True,
        "from scripts.database import sqlite3\n": False,
        "import importlib\ndef f():\n    return importlib.import_module('json')\n": False,
    }
    for source, bypasses in cases.items():
        assert bool(_probe_bypasses(ast.parse(source))) is bypasses, source


def test_the_probe_passes_a_positional_factory_through(tmp_path, monkeypatch):
    """connect's positional arguments, a factory among them, go through
    unchanged. RED WHEN a positional factory makes the probe raise TypeError,
    or the connection is not of the caller's class."""

    class SubConn(STDLIB_SQLITE3.Connection):
        pass

    store_driver = _fake_store_driver()
    probe = CopyProbe(STDLIB_SQLITE3, store_driver)
    probe.install(monkeypatch)
    connection = STDLIB_SQLITE3.connect(str(tmp_path / "a.db"), 5.0, 0, None, True, SubConn)
    try:
        assert isinstance(connection, SubConn)
        assert connection.isolation_level is None
    finally:
        connection.close()
    assert probe.overlaps == [], probe.overlaps


def test_the_probe_sees_a_hard_link_as_the_same_file(tmp_path, monkeypatch):
    """A file is one file by its device and inode. RED WHEN a store-code
    connection through a hard link escapes a standard-library connection held
    on the original name."""
    import os

    store_driver = _fake_store_driver()
    probe = CopyProbe(STDLIB_SQLITE3, store_driver)
    probe.install(monkeypatch)
    original = tmp_path / "store.db"
    held = STDLIB_SQLITE3.connect(str(original))
    held.execute("CREATE TABLE t (x)")
    held.commit()
    os.link(original, tmp_path / "linked.db")
    store_driver.connect(str(tmp_path / "linked.db")).close()
    held.close()
    assert len(probe.overlaps) == 2, probe.overlaps
