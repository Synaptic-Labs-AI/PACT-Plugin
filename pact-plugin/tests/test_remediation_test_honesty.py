"""Arms for four guards the repository-identity suite left unexercised.

Each class pins one guard that a deliberate mutation of production code could
remove while every other test stayed green:

- the location-divergence WARNING stays silent on an ordinary in-repo save
  (the envelope's `location_divergence` field is computed separately, so an
  envelope assertion cannot see the warning channel);
- the read-scope cache key includes the store's `-wal` sibling, so a commit
  that another connection left only in the WAL is still seen;
- `rekey_project` itself refuses a non-canonical target (the CLI checks first,
  so no CLI arm reaches the library refusal);
- the test-data copies of the working-memory comment equal the shipped
  constants, so a copy cannot drift from the string it stands for.

Every fixture is a real repository and a real on-disk store under tmp_path.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo
from fixtures.pin_growth import size_rows
from scripts.database import db_connection, ensure_initialized, rekey_project
from scripts.memory_api import PACTMemory
from scripts.working_memory import (
    RETRIEVED_CONTEXT_COMMENT as SCRIPT_RETRIEVED_COMMENT,
)
from scripts.working_memory import (
    WORKING_MEMORY_COMMENT as SCRIPT_WORKING_MEMORY_COMMENT,
)
from shared.claude_md_manager import (
    RETRIEVED_CONTEXT_COMMENT as HOOK_RETRIEVED_COMMENT,
)
from shared.claude_md_manager import (
    WORKING_MEMORY_COMMENT as HOOK_WORKING_MEMORY_COMMENT,
)

from scripts import memory_api

REMOTE = "https://github.com/syn-labs/pact-plugin.git"
CANONICAL = "github.com/syn-labs/pact-plugin"
# The checkout folder name: the pre-change resolver's id for this repository,
# so it is the legacy bucket the read union looks for.
LEGACY = "PACT-prompt"
OTHER = "github.com/other-owner/other-repo"
STAMP = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def repo_env(tmp_path, monkeypatch):
    """A real repository with a canonical origin, entered as the process cwd.

    `CLAUDE_PROJECT_DIR` is removed (the suite's autouse redirect points it at
    tmp_path, which would make the identity a basename), so the git rung
    resolves the canonical id from the origin.
    """
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / LEGACY, remote=REMOTE)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    memory_api._LEGACY_SCOPE_CACHE.clear()
    yield repo, tmp_path / "store.db"
    memory_api._LEGACY_SCOPE_CACHE.clear()


def _insert_memory(conn, memory_id, project):
    conn.execute(
        "INSERT INTO memories "
        "(id, context, project_id, session_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (memory_id, f"context {memory_id}", project, "s", STAMP, STAMP),
    )


def _divergence_records(caplog):
    return [
        record for record in caplog.records
        if "location divergence" in record.getMessage()
    ]


class TestDivergenceWarningIsSilentInsideItsOwnRepository:
    """The warning compares IDS. Comparing the folder name with a canonical id
    differs on every in-repo save, so the warning would fire on all of them."""

    def test_an_in_repo_save_logs_no_divergence_warning(self, repo_env, caplog):
        """RED WHEN the comparison is `root.name == project_id` (or any other
        comparison of a folder name with the canonical id)."""
        _repo, store = repo_env
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory.project_id == CANONICAL, (
            "the fixture did not resolve the canonical id, so the arm would "
            f"not exercise the id comparison: {memory.project_id!r}"
        )
        with caplog.at_level(logging.WARNING, logger=memory_api.logger.name):
            memory.save({"context": "an ordinary in-repo save"}, sync_to_claude=False)
        assert _divergence_records(caplog) == [], (
            "an in-repo save logged a location divergence: "
            f"{[r.getMessage() for r in _divergence_records(caplog)]}"
        )

    def test_a_save_filed_under_another_project_still_warns(self, repo_env, caplog):
        """POSITIVE CONTROL for the arm above: the warning is live and this
        capture sees it, so the silence above is a measurement, not a capture
        that never records anything."""
        _repo, store = repo_env
        memory = PACTMemory(session_id="s", db_path=store)
        with caplog.at_level(logging.WARNING, logger=memory_api.logger.name):
            memory.save(
                {"context": "filed elsewhere", "project_id": OTHER},
                sync_to_claude=False,
            )
        messages = [r.getMessage() for r in _divergence_records(caplog)]
        assert len(messages) == 1, messages
        assert OTHER in messages[0] and CANONICAL in messages[0], messages


class TestTheReadScopeCacheSeesAWalOnlyCommit:
    """The store runs in WAL mode. Another connection's commit can sit only in
    the `-wal` file, leaving the main file's mtime and size unchanged, so a
    cache keyed on the main file alone keeps serving a stale read scope."""

    def test_a_wal_only_commit_from_another_connection_is_read(self, repo_env):
        """RED WHEN `_store_stat_key` drops the `-wal` leg: the second read is
        served from the cache and the legacy row is silently missing.

        The legacy bucket is VERIFIED (an absolute file row under the repo
        root), so admission does not depend on session evidence."""
        repo, store = repo_env
        with db_connection(store) as conn:
            ensure_initialized(conn)
            _insert_memory(conn, "c1", CANONICAL)
            conn.commit()

        memory = PACTMemory(session_id="s", db_path=store)
        assert [m.id for m in memory.list(limit=10)] == ["c1"]

        other = sqlite3.connect(str(store))
        try:
            assert other.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            other.execute("PRAGMA wal_autocheckpoint=0")
            main_before = store.stat()
            _insert_memory(other, "l1", LEGACY)
            other.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                ("f1", str(repo.resolve() / "src" / "a.py"), LEGACY),
            )
            other.commit()
            main_after = store.stat()
            # PRECONDITION, not the subject: the commit really is WAL-only, so
            # only the `-wal` leg of the key can notice it.
            assert (main_after.st_mtime_ns, main_after.st_size) == (
                main_before.st_mtime_ns,
                main_before.st_size,
            ), "the commit reached the main file; the arm would not isolate the WAL leg"

            assert sorted(m.id for m in memory.list(limit=10)) == ["c1", "l1"], (
                "a commit that landed only in the -wal file was not seen"
            )
        finally:
            other.close()


class TestRekeyLibraryRefusesANonCanonicalTarget:
    """The CLI validates `--to` before it calls the library, so the library's
    own refusal is reached only by a direct caller."""

    def _snapshot(self, store):
        with db_connection(store) as conn:
            return sorted(
                (row["id"], row["project_id"])
                for row in conn.execute("SELECT id, project_id FROM memories")
            )

    def test_a_non_canonical_target_raises_and_moves_nothing(self, repo_env):
        """RED WHEN the `is_canonical_id(to_id)` check in `rekey_project` is
        removed: the apply then moves both rows into a basename bucket."""
        _repo, store = repo_env
        with db_connection(store) as conn:
            ensure_initialized(conn)
            _insert_memory(conn, "l1", LEGACY)
            _insert_memory(conn, "l2", LEGACY)
            conn.commit()
        before = self._snapshot(store)

        with db_connection(store) as conn:
            with pytest.raises(ValueError, match="canonical"):
                rekey_project(conn, LEGACY, "PACT-prompt-2", dry_run=False)

        assert self._snapshot(store) == before, "a refused rekey moved rows"

    def test_a_canonical_target_is_accepted(self, repo_env):
        """CONTROL: the refusal is about the target's shape, not about rekeying
        this bucket at all."""
        _repo, store = repo_env
        with db_connection(store) as conn:
            ensure_initialized(conn)
            _insert_memory(conn, "l1", LEGACY)
            conn.commit()
        with db_connection(store) as conn:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=True)
        assert counters["memories"] == 1


class TestCommentCopiesEqualTheShippedConstants:
    """`fixtures/pin_growth/size_rows.py` keeps the managed comments as LITERAL
    data, so the pin-growth fixture does not import production code. This arm
    is what stops that literal drifting from the string it stands for."""

    def test_the_pin_growth_fixture_carries_the_shipped_comments(self):
        """RED WHEN either production copy changes and the fixture does not."""
        assert size_rows.WM == SCRIPT_WORKING_MEMORY_COMMENT
        assert size_rows.WM == HOOK_WORKING_MEMORY_COMMENT
        assert size_rows.RC == SCRIPT_RETRIEVED_COMMENT
        assert size_rows.RC == HOOK_RETRIEVED_COMMENT
