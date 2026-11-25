"""
Location: pact-plugin/tests/test_catchup_store_postponement.py
Summary: The embedding catch-up's store handling. A read's postponed
migration and catch-up run on the store that read postponed them, never on
another store a later call binds; and a store the integrity check cannot open
is reported as unchecked, not as corrupt.
Used by: pytest; drives scripts.memory_init.ensure_memory_ready and
scripts.embedding_catchup._store_integrity_fault.
"""

from __future__ import annotations

from scripts import embedding_catchup, memory_init
from scripts.config import read_only_scope, resolve_db_path, store_scope


def _postponing_reads(monkeypatch):
    """Fresh initialization state; the migration and catch-up record the
    store they run on; a read always postpones the catch-up."""
    for name, value in (("_initialized", False), ("_migration_deferred", set()),
                        ("_catchup_deferred", set()), ("_last_catchup_result", None)):
        monkeypatch.setattr(memory_init, name, value)
    monkeypatch.setattr(memory_init, "check_and_install_dependencies", lambda: {})
    monkeypatch.setattr(memory_init, "_read_can_catch_up", lambda: False)
    ran = []

    def recorder(kind):
        def run():
            ran.append((kind, resolve_db_path()))
            return {"status": "ok", "message": None}
        return run

    monkeypatch.setattr(memory_init, "maybe_migrate_embeddings", recorder("migration"))
    monkeypatch.setattr(memory_init, "maybe_embed_pending", recorder("catch-up"))
    return ran


def test_a_postponed_migration_runs_on_its_own_store_not_the_next_one(tmp_path, monkeypatch):
    """A read on one store postpones its migration and catch-up; a later
    write-capable call on ANOTHER store does not run them, and the next one
    on the first store does. RED WHEN the postponement is kept per process
    rather than per store."""
    ran = _postponing_reads(monkeypatch)
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    with store_scope(first), read_only_scope():
        memory_init.ensure_memory_ready()
    assert ran == []
    with store_scope(second):
        memory_init.ensure_memory_ready()
    assert ran == [], "the first store's postponed work ran on the second store"
    with store_scope(first):
        memory_init.ensure_memory_ready()
    assert ran == [("migration", first), ("catch-up", first)]


def test_a_store_that_will_not_open_is_unchecked_not_corrupt(tmp_path):
    """An open that fails (here a store whose folder is missing) gives no
    verdict on the store's pages, so the reason says the check could not run.
    RED WHEN an OperationalError is reported as a failed integrity check."""
    with store_scope(tmp_path / "gone" / "store.db"):
        fault = embedding_catchup._store_integrity_fault()
    assert fault is not None and "could not be checked" in fault, fault
    assert "failed its integrity check" not in fault, fault


def test_control_a_file_that_is_not_a_database_still_fails_the_check(tmp_path):
    store = tmp_path / "store.db"
    store.write_bytes(b"not a database" * 512)
    with store_scope(store):
        fault = embedding_catchup._store_integrity_fault()
    assert fault is not None and "failed its integrity check" in fault, fault
