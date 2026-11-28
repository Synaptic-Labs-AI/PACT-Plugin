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

