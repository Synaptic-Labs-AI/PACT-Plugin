"""
Location: pact-plugin/tests/fixtures/sqlite_copy_probe.py
Summary: The standard-sqlite3 allowlist for the store tests and the probe
         that enforces it.
Used by: tests/conftest.py (`_probe_sqlite_copies`, for the ALLOWED modules)
         and tests/test_store_sqlite_copy_guard.py.

Where pysqlite3 links SQLite statically, the standard `sqlite3` module is a
second SQLite copy in the process, and closing any descriptor on a file drops
every POSIX lock the process holds on it. So a standard-library connection to
a store file must never be open while a store-code connection to the same
file is open, opens or closes. `CopyProbe` wraps both drivers' `connect`
and records each such moment, naming the file and both connections.
"""

import os
import sqlite3 as _stdlib_sqlite3
import sys
import weakref
from urllib.parse import unquote, urlparse

# Test modules that import the standard sqlite3 beside the memory scripts.
# Each reason states the condition that makes it safe, and
# `_probe_sqlite_copies` fails any of their tests that breaks it.
ALLOWED = {
    "test_connection_transaction_mode.py": (
        "each driver's arm opens and closes its own connections, so a standard-library "
        "connection is never open while a store-code connection to the same file is"
    ),
    "test_file_permissions.py": (
        "it patches database.sqlite3 to the standard module, so every connection "
        "to its store, the store code's included, is the one standard-library copy"
    ),
    "test_shred_detect.py": (
        "shred_detect reads a store with the standard module by design, and nothing "
        "in this module opens a store-code connection: the test and the tool both "
        "use the one standard-library copy"
    ),
    "test_memory_graph.py": (
        "its standard-library connections are to private database files the store "
        "code never opens"
    ),
    "test_earlier_canonical_bucket.py": (
        "Connection.setlimit, which pysqlite3 lacks, runs on a standard-library "
        "connection opened after the last store-code connection to that file "
        "closed, and the call it drives reads only through that connection"
    ),
}


def _file_of(database, uri):
    """The real path a connection opens, or None for an in-memory one."""
    name = os.fsdecode(database) if isinstance(database, (bytes, os.PathLike)) else str(database)
    if uri or name.startswith("file:"):
        parsed = urlparse(name)
        if "mode=memory" in parsed.query:
            return None
        name = unquote(parsed.path)
    if name in ("", ":memory:"):
        return None
    return os.path.realpath(name)


def _caller():
    frame = sys._getframe(2)
    while frame is not None and frame.f_code.co_filename == __file__:
        frame = frame.f_back
    return "unknown" if frame is None else f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno}"


class CopyProbe:
    """Records every moment a connection of one driver opens or closes on a
    file a connection of the other driver holds open."""

    def __init__(self, stdlib_module, store_module):
        self.drivers = {"standard-library": stdlib_module, "store-code": store_module}
        self.open = {kind: {} for kind in self.drivers}
        self.overlaps = []

    def install(self, monkeypatch):
        if self.drivers["standard-library"] is self.drivers["store-code"]:
            return  # one SQLite copy: nothing can overlap
        for kind, module in self.drivers.items():
            monkeypatch.setattr(module, "connect", self._wrapped(kind, module.connect, module))

    def _other(self, kind):
        return next(other for other in self.drivers if other != kind)

    def _note(self, kind, path, what, where):
        held = self.open[self._other(kind)].get(path)
        if held:
            self.overlaps.append(
                f"{path}: a {kind} connection {what} at {where} while the "
                f"{self._other(kind)} connection opened at {next(iter(held.values()))} was open"
            )

    def _wrapped(self, kind, connect, module):
        probe = self
        classes = {}

        def wrapper(database, *args, **kwargs):
            path = _file_of(database, kwargs.get("uri", False))
            if path is None:
                return connect(database, *args, **kwargs)
            base = kwargs.pop("factory", None) or module.Connection
            if base not in classes:
                def close(self):
                    base.close(self)
                    self._copy_probe_done("closed", _caller())

                def done(self, what, where):
                    state = self.__dict__.get("_copy_probe_state")
                    if state is not None and not state["done"]:
                        state["done"] = True
                        probe.open[kind][path_of(state)].pop(state["id"], None)
                        probe._note(kind, path_of(state), what, where)

                classes[base] = type(
                    "Probed" + base.__name__, (base,), {"close": close, "_copy_probe_done": done}
                )
            where = _caller()
            probe._note(kind, path, "opened", where)
            connection = connect(database, *args, factory=classes[base], **kwargs)
            state = {"done": False, "path": path, "id": id(connection)}
            connection.__dict__["_copy_probe_state"] = state
            probe.open[kind].setdefault(path, {})[state["id"]] = where

            def collected(state=state):
                if not state["done"]:
                    state["done"] = True
                    probe.open[kind][state["path"]].pop(state["id"], None)
                    probe._note(kind, state["path"], "was garbage-collected", "unknown")

            weakref.finalize(connection, collected)
            return connection

        return wrapper


def path_of(state):
    return state["path"]


STDLIB_SQLITE3 = _stdlib_sqlite3
