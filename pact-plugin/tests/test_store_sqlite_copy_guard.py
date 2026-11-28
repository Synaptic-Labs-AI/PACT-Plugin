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
library, unless the allowlist below names why it needs the standard module.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "skills" / "pact-memory" / "scripts"
MEMORY_MODULES = frozenset(path.stem for path in SCRIPTS.glob("*.py")) - {"__init__"}
# Memory scripts some tests import by their bare name (the scripts directory is
# on sys.path); the generic names (`config`, `cli`, `search`) are left out,
# since other modules share them.
BARE_MEMORY_MODULES = frozenset({
    "database", "memory_api", "memory_init", "embedding_catchup", "working_memory", "setup_memory",
})

# Modules that need the standard library module for a reason unrelated to the
# store they share, each with that reason.
ALLOWED = {
    "test_connection_transaction_mode.py": (
        "compares the standard driver with pysqlite3 on connections of its own"
    ),
    "test_file_permissions.py": (
        "patches database.sqlite3 with the standard module, so every connection the test "
        "makes, the store's included, is one SQLite copy"
    ),
    "test_shred_detect.py": (
        "shred_detect is a standalone tool that reads the store with the standard module "
        "by design (it needs no extension), and the test drives it the same way"
    ),
    "test_memory_graph.py": (
        "runs graph.py's duplicate-write arms on a standard-library connection to a "
        "private database the store code never opens, beside the production class"
    ),
    "test_earlier_canonical_bucket.py": (
        "lowers SQLITE_LIMIT_VARIABLE_NUMBER with Connection.setlimit, which pysqlite3 "
        "does not provide, on the one connection that test reads through"
    ),
}


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
