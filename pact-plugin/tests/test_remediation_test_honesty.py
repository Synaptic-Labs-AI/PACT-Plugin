"""Arms for guards the repository-identity suite left unexercised.

Each class pins one guard that a deliberate mutation of production code could
remove while every other test stayed green:

- the location-divergence WARNING stays silent on an ordinary in-repo save
  (the envelope's `location_divergence` field is computed separately, so an
  envelope assertion cannot see the warning channel);
- the read-scope cache key includes the store's `-wal` sibling, so a commit
  that another connection left only in the WAL is still seen;
- the same key creates the `-wal` before it stamps, so the read that would
  create it does not make the next read decide again, while a writer that
  commits and closes (deleting the `-wal`), a checkpoint that empties it,
  and a write landing during a read still make the next read decide again;
- `rekey_project` itself refuses a non-canonical target (the CLI checks first,
  so no CLI arm reaches the library refusal);
- the test-data copies of the working-memory comment equal the shipped
  constants, so a copy cannot drift from the string it stands for.
- the shipped memory scripts and this change's own test modules carry no
  planning labels.

Every fixture is a real repository and a real on-disk store under tmp_path.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path

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
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


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


def _seed_canonical(store):
    with db_connection(store) as conn:
        ensure_initialized(conn)
        _insert_memory(conn, "c1", CANONICAL)
        conn.commit()


def _commit_verified_legacy_row(other, repo):
    """Commit a VERIFIED legacy row (an absolute file row under the repo root)
    through `other`, a separate connection whose autocheckpoint is off, so the
    commit sits only in the `-wal` file."""
    other.execute("PRAGMA wal_autocheckpoint=0")
    _insert_memory(other, "l1", LEGACY)
    other.execute(
        "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
        ("f1", str(repo.resolve() / "src" / "a.py"), LEGACY),
    )
    other.commit()


class TestTheReadScopeCacheServesTheSecondRead:
    """A read of a store with no `-wal` is immutable and creates no sidecar,
    so it leaves the key it is cached under unchanged and the second read is
    served from the cache. The key keeps the `-wal`'s own stamp when one
    exists, so the journal being deleted, recreated or emptied still moves
    the key. A read the store moved under is repeated with locking. The
    cache lives for one detection pass, so each arm reads inside one, as a
    command does."""

    @pytest.fixture(autouse=True)
    def _one_pass(self):
        with memory_api.detection_pass():
            yield

    def _count_decisions(self, monkeypatch):
        from scripts import database

        decisions = []
        real = database.project_bucket_counts

        def counting(connection):
            decisions.append(1)
            return real(connection)

        monkeypatch.setattr(database, "project_bucket_counts", counting)
        return decisions

    def test_repeated_reads_decide_the_scope_once(self, repo_env, monkeypatch):
        """Three reads of a store with no `-wal` decide the scope once and
        leave no `-wal` or `-shm` behind: the open is immutable, so the read
        does not change the key it is cached under. RED WHEN a read of a
        settled store opens with locking: SQLite then creates the sidecars,
        and the `-wal` appearing after the first stamp makes the second read
        decide again."""
        _repo, store = repo_env
        _seed_canonical(store)
        wal = Path(str(store) + "-wal")
        shm = Path(str(store) + "-shm")
        # PRECONDITION, not the subject: the store starts settled.
        assert not wal.exists() and not shm.exists(), "the seeded store has sidecars; the arm would not read a settled store"
        memory = PACTMemory(session_id="s", db_path=store)
        decisions = self._count_decisions(monkeypatch)
        scopes = [memory._read_project_scopes() for _ in range(3)]
        assert scopes == [(CANONICAL,)] * 3, scopes
        assert not wal.exists() and not shm.exists(), "a read of a settled store created a sidecar"
        assert len(decisions) == 1, f"3 reads decided the scope {len(decisions)} times"

    def test_a_read_after_a_truncating_checkpoint_decides_again(self, repo_env, monkeypatch):
        """A TRUNCATE checkpoint after a full backfill empties the `-wal`.
        The read after it must miss because the `-wal` leg moved, and return
        the backfilled rows. RED WHEN the `-wal` leg is dropped from the key:
        that read is served from the cache instead of deciding again.

        The TRUNCATE may also move the main file's mtime (measured on a CI
        Linux runner: 3 ms, size unchanged); whether it does is SQLite's and
        the filesystem's choice. The arm's subject is the `-wal` leg, so it
        holds the main leg constant: it restores the main file's mtime to its
        pre-truncate value and checks the size did not change. That is the
        isolation, not a faked state: the checkpoint and the stat key the
        production code reads are real, and only the main leg is pinned."""
        repo, store = repo_env
        _seed_canonical(store)
        memory = PACTMemory(session_id="s", db_path=store)
        decisions = self._count_decisions(monkeypatch)
        assert memory._read_project_scopes() == (CANONICAL,)
        other = sqlite3.connect(str(store))
        try:
            _commit_verified_legacy_row(other, repo)
            busy, frames, backfilled = other.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            assert busy == 0 and frames == backfilled > 0, (busy, frames, backfilled)
            assert memory._read_project_scopes() == (CANONICAL, LEGACY)
            main_before = store.stat()
            assert other.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
            wal = Path(str(store) + "-wal")
            # PRECONDITION, not the subject: the truncate emptied the `-wal`
            # and changed no row in the main file.
            assert wal.stat().st_size == 0
            assert store.stat().st_size == main_before.st_size, "the truncate resized the main file"
            # ISOLATION: hold the main leg at its pre-truncate stamp, so the
            # `-wal` leg is the only part of the key that can have moved.
            os.utime(store, ns=(store.stat().st_atime_ns, main_before.st_mtime_ns))
            main_after = store.stat()
            assert (main_after.st_mtime_ns, main_after.st_size) == (
                main_before.st_mtime_ns, main_before.st_size,
            ), "the main file's stamp could not be held; the arm would not isolate the -wal leg"
            seen = len(decisions)
            assert memory._read_project_scopes() == (CANONICAL, LEGACY), (
                "the read after the truncate lost the backfilled rows"
            )
            assert len(decisions) == seen + 1, "the read after the truncate was served from the cache"
        finally:
            other.close()

    def _commit_and_close(self, store, repo):
        """Another process's commit and close: the close checkpoints the
        commit into the main file and deletes the `-wal`."""
        other = sqlite3.connect(str(store))
        try:
            _commit_verified_legacy_row(other, repo)
        finally:
            other.close()
        # PRECONDITION, not the subject: no `-wal` is left behind.
        assert not Path(str(store) + "-wal").exists(), "the writer's close left the -wal"

    def test_a_commit_and_close_that_moves_the_main_stamp_is_read(self, repo_env, monkeypatch):
        """The read after another process's commit and close is decided
        again when the main file's stamp moved. RED WHEN the key leaves out
        the main file's leg: that read is served the scope from before the
        commit."""
        repo, store = repo_env
        _seed_canonical(store)
        memory = PACTMemory(session_id="s", db_path=store)
        decisions = self._count_decisions(monkeypatch)
        assert memory._read_project_scopes() == (CANONICAL,)
        main_before = store.stat()
        self._commit_and_close(store, repo)
        # PRECONDITION, not the subject: the commit moved the main stamp.
        os.utime(store, ns=(store.stat().st_atime_ns, main_before.st_mtime_ns + 1_000_000))
        assert (store.stat().st_mtime_ns, store.stat().st_size) != (
            main_before.st_mtime_ns, main_before.st_size,
        )
        seen = len(decisions)
        assert memory._read_project_scopes() == (CANONICAL, LEGACY), (
            "the read after the writer's commit and close was served from the cache"
        )
        assert len(decisions) == seen + 1

    def test_a_commit_and_close_inside_one_tick_is_the_named_limit(self, repo_env, monkeypatch):
        """THE NAMED LIMIT of `_store_stat_key`: with no `-wal` before or
        after, a commit and close that leaves the main file's
        `(mtime_ns, size)` unchanged (one timestamp tick, no growth) is not
        seen by the second scope read of the same command. The arm holds the
        main leg with `os.utime`, standing in for that tick. RED WHEN the
        limit changes, in either direction, without this pin and the
        docstring paragraph that names it changing too."""
        repo, store = repo_env
        _seed_canonical(store)
        memory = PACTMemory(session_id="s", db_path=store)
        decisions = self._count_decisions(monkeypatch)
        assert memory._read_project_scopes() == (CANONICAL,)
        assert not Path(str(store) + "-wal").exists(), "the read created a -wal"
        main_before = store.stat()
        self._commit_and_close(store, repo)
        assert store.stat().st_size == main_before.st_size, "the commit resized the main file"
        os.utime(store, ns=(store.stat().st_atime_ns, main_before.st_mtime_ns))
        seen = len(decisions)
        assert memory._read_project_scopes() == (CANONICAL,)
        assert len(decisions) == seen, "the read inside the tick decided again"
        assert "inside one timestamp tick" in memory_api._store_stat_key.__doc__

    def test_a_write_landing_during_the_read_is_not_served_from_the_cache(
        self, repo_env, monkeypatch
    ):
        """RED WHEN the cache entry is keyed by a stamp taken AFTER the read:
        a commit landing after the read counted the buckets is then filed
        under a key that already includes it, and the next read is served the
        scope decided without it. The writer's connection has read the store
        first, so a `-wal` exists and the read is a locking one, which
        `_stable_read_only` does not repeat."""
        from scripts import database

        repo, store = repo_env
        _seed_canonical(store)
        memory = PACTMemory(session_id="s", db_path=store)
        other = sqlite3.connect(str(store))
        try:
            other.execute("SELECT COUNT(*) FROM memories").fetchone()
            # PRECONDITION, not the subject: the read below is not immutable.
            assert Path(str(store) + "-wal").exists(), "no -wal; the read would be immutable and repeated"
            landed = []
            real = database.project_bucket_counts

            def counted_then_written(connection):
                buckets = real(connection)
                if not landed:
                    # The read has counted the buckets; the write lands
                    # before it finishes and stores its decision.
                    _commit_verified_legacy_row(other, repo)
                    landed.append(True)
                return buckets

            monkeypatch.setattr(database, "project_bucket_counts", counted_then_written)
            assert memory._read_project_scopes() == (CANONICAL,)
            assert landed, "the write was never injected; the arm measured nothing"
            assert memory._read_project_scopes() == (CANONICAL, LEGACY), (
                "a commit that landed during the read was served from the cache"
            )
        finally:
            other.close()

    def test_an_immutable_read_the_store_moved_under_is_repeated(
        self, repo_env, monkeypatch
    ):
        """A read of a settled store is immutable, and SQLite trusts an
        immutable file not to change. When a write lands during it, the read
        is repeated with locking, and the repeat's answer is the one returned
        and cached. RED WHEN `_stable_read_only` returns the first answer
        although the store's stamp moved."""
        from scripts import database

        repo, store = repo_env
        _seed_canonical(store)
        # PRECONDITION, not the subject: the first read is immutable.
        assert not Path(str(store) + "-wal").exists()
        memory = PACTMemory(session_id="s", db_path=store)
        other = sqlite3.connect(str(store))
        try:
            calls = []
            real = database.project_bucket_counts

            def counted_then_written(connection):
                buckets = real(connection)
                if not calls:
                    _commit_verified_legacy_row(other, repo)
                calls.append(1)
                return buckets

            monkeypatch.setattr(database, "project_bucket_counts", counted_then_written)
            assert memory._read_project_scopes() == (CANONICAL, LEGACY), (
                "the answer read before the write landed was returned"
            )
            assert len(calls) == 2, f"the read ran {len(calls)} times, not twice"
            assert memory._read_project_scopes() == (CANONICAL, LEGACY)
            assert len(calls) == 2, "the repeated answer was not served from the cache"
        finally:
            other.close()

    def test_a_read_that_raises_under_an_immutable_open_is_repeated(
        self, repo_env, monkeypatch
    ):
        """An immutable open reading a store being rewritten can raise
        "database disk image is malformed". That is the store moving, not the
        store being corrupt: the read is repeated with locking, and only the
        repeat's error would stand. RED WHEN the first error escapes, or the
        repeat is not run with locking."""
        from scripts import database

        _repo, store = repo_env
        _seed_canonical(store)
        assert not Path(str(store) + "-wal").exists()
        memory = PACTMemory(session_id="s", db_path=store)
        locked = []
        real = database.project_bucket_counts

        def malformed_once(connection):
            locked.append(memory_api._LOCKED_READS_ONLY.get())
            if len(locked) == 1:
                raise memory_api.sqlite3.DatabaseError("database disk image is malformed")
            return real(connection)

        monkeypatch.setattr(database, "project_bucket_counts", malformed_once)
        assert memory._read_project_scopes() == (CANONICAL,)
        assert locked == [False, True], locked


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


class TestIdentityFilesCarryNoPlanningLabels:
    """The shipped memory scripts, the backlog resolver that derives the same
    identity, and this change's own test modules carry no planning labels.

    A label such as an issue number, a review-finding id, a corpus-row id or a
    test-matrix tag points at a tracker or a document the reader of the file
    does not have, so it explains nothing and goes stale silently. The
    behavioural sentence beside it is what stays.
    """

    SCRIPTS = (
        "skills/pact-memory/scripts/memory_api.py",
        "skills/pact-memory/scripts/project_identity.py",
        "skills/pact-memory/scripts/cli.py",
        "skills/pact-memory/scripts/database.py",
        "hooks/shared/backlog.py",
    )
    # The test modules this change added, by the prefixes it introduced. A
    # later module with one of these prefixes is scanned too.
    TEST_GLOBS = (
        "tests/test_project_identity_*.py",
        "tests/test_identity_*_remediation.py",
        "tests/test_remediation_*.py",
        "tests/test_remote_parser_agreement.py",
        "tests/fixtures/identity_git.py",
        "tests/fixtures/zero_create_worker.py",
    )
    # The added modules at the time this arm was written. The glob may grow;
    # it must never match fewer, or the scan has silently lost its subject.
    MIN_TEST_MODULES = 17

    LABELS = (
        (re.compile(r"#\d{3,4}\b"), "issue or PR number"),
        # A design surface tag in any case, with or without a separator.
        (re.compile(r"(?i)(?<![a-z])surface[ _-]?\d"), "design surface tag"),
        (re.compile(r"\bSEC-\d"), "security finding id"),
        (re.compile(r"\bPREPARE\b"), "planning phase name"),
        (re.compile(r"\bP0\b"), "test-matrix priority tag"),
        # One capital letter and one or two digits, which also catches the
        # first id of a range or a suffixed id. NOT after `%`, `+`, `-` or a
        # word character, and not before `:<digit>`, so a percent-escape, a
        # code point, an ISO timestamp and `UTF-8` never match.
        (re.compile(r"(?<![%+\w-])[A-Z]\d{1,2}(?![\w+])(?!:\d)"),
         "finding, corpus-row or design id"),
        # One capital letter, a hyphen and one or two digits. The same
        # boundaries as above, so `UTF-8` and an ISO timestamp never match.
        (re.compile(r"(?<![%+\w-])[A-Z]-\d{1,2}(?![\w+])"), "lettered finding id"),
        # The same id inside a camel-case name: after a lowercase letter that
        # is not a regex escape (`\b`), and before a capital, an underscore or
        # the end of the word. A version token inside a name (as in
        # `getV2Api`) is part of the name, not a label.
        (re.compile(r"(?<=[a-z])(?<!\\[a-z])(?!V\d)[A-Z]-?\d{1,2}(?=[A-Z_]|\b)(?![+])"),
         "finding, corpus-row or design id"),
        # Review-process labels: a remediation cycle, a bare cycle and a
        # review round, each followed by its number. The bare-cycle pattern
        # skips the remediation prefix so each label has one owning pattern.
        (re.compile(r"(?i)\bremediation cycle[- ]?\d"), "remediation cycle label"),
        (re.compile(r"(?i)(?<!remediation )\bcycle[- ]\d+\b"), "cycle label"),
        (re.compile(r"(?i)\bround[- ]\d+\b"), "review round label"),
    )
    # The Unicode control-set names are terms, not labels. (`[C]` keeps this
    # pattern's own source text from reading as a label to the scan.)
    CONTROL_SET = re.compile(
        r"\bC0\b|\bC1(?= controls?\b)|(?<=/)[C]1\b|\bC1(?=/DEL)"
    )

    @classmethod
    def _hits(cls, text):
        found = []
        for number, line in enumerate(text.splitlines(), start=1):
            scrubbed = cls.CONTROL_SET.sub("", line)
            starts = set()
            for pattern, kind in cls.LABELS:
                for match in pattern.finditer(scrubbed):
                    if match.start() not in starts:
                        starts.add(match.start())
                        found.append((number, kind, match.group(0)))
        return found

    @classmethod
    def _test_modules(cls):
        return sorted({
            path for pattern in cls.TEST_GLOBS for path in PLUGIN_ROOT.glob(pattern)
        })

    # Each planted label is ASSEMBLED, so this source file never contains the
    # label itself and the scan of this module stays meaningful.
    PLANTED = (
        ("# see " + "#" + "1699", "issue or PR number"),
        ("# " + "SURFACE" + " 4: the candidate", "design surface tag"),
        ('"""' + "Sur" + "face 4 carries the size", "design surface tag"),
        ("store = tmp_path / " + "sur" + "face4.db", "design surface tag"),
        ("class Test" + "Review" + "B" + "6IncidentFlow:", "finding, corpus-row or design id"),
        ("class Test" + "Review" + "B" + "-2Seams:", "finding, corpus-row or design id"),
        ("def test_" + "row" + "A" + "13_case():", "finding, corpus-row or design id"),
        ('("' + "SEC" + "-1" + '")', "security finding id"),
        ("# the " + "PREP" + "ARE corpus", "planning phase name"),
        ("(" + "P" + "0 matrix)", "test-matrix priority tag"),
        ("# " + "U" + "45 row", "finding, corpus-row or design id"),
        ("# " + "A" + "0-" + "A" + "13: cases", "finding, corpus-row or design id"),
        ('"""' + "B" + "6-T: flow", "finding, corpus-row or design id"),
        ('"""' + "S" + "2-sec: surface", "finding, corpus-row or design id"),
        ("see " + "R" + "2-5 in merge", "finding, corpus-row or design id"),
        ('"""' + "A" + "4/" + "S" + "1: read", "finding, corpus-row or design id"),
        ("(" + "G" + "4/" + "G" + "5): seams", "finding, corpus-row or design id"),
        ("parses under " + "R" + "1.", "finding, corpus-row or design id"),
        ('"""' + "N" + "-7 (cascade half): the row", "lettered finding id"),
        ("# Remediation " + "cyc" + "le 3: update ordering", "remediation cycle label"),
        ("# fixed in " + "cyc" + "le 2 of the review", "cycle label"),
        ("# see verify " + "rou" + "nd 4 synthesis", "review round label"),
    )
    CLEAN = (
        "# Reject C0, DEL and C1 controls and lone surrogates",
        "# Characters that must not reach a rendered envelope raw: C0/DEL/C1 controls",
        '("https://host/owner/r%C3%A9po", "host/owner/r%C3%A9po")',
        "`utf-8-sig` drops an encoding BOM (U+FEFF)",
        'f"2026-01-0{i + 1}T00:00:00+00:00"',
        "an ISO-8601 stamp read as UTF-8 by sha256",
        "the 255-byte cap",
        "a sha-1 digest and the x-1 offset",
        "the hook's lifecycle 3 times over, then round(x, 2)",
        "a round-trip through the cycle-free graph",
        "_R1_GRAMMAR = re.compile(CANONICAL_ID_RE)",
        "SELECT r1.id FROM memories AS r1 JOIN files AS f2 ON f2.id = r1.id",
        "IPv4 and IPv6 hosts, a Utf8Decoder, an X509Store, sha256Hex, toBase64, int64",
        're.compile(r"\\bP0\\b|\\bC1\\b")',
        "the surface of the store, resurfaced 3 times",
    )

    # Honest names that carry a version token inside a camel-case name.
    VERSION_NAMES = (
        "def getV2Api(): return api_v2",
        "class ApiV3Client: pass",
        "readV1Header(raw)",
        "parseV10Response",
        "toPy312(), fromPy39_layout",
        "self.useV2_store = True",
    )

    @pytest.mark.parametrize("line", VERSION_NAMES)
    def test_a_version_token_in_a_name_is_not_flagged(self, line):
        """NEGATIVE CONTROL: a version token inside a camel-case name (as in
        `getV2Api`, `parseV10Response`, `toPy312`) is part of the name, not a
        planning label."""
        assert self._hits(line) == []

    @pytest.mark.parametrize(("line", "kind"), PLANTED)
    def test_each_label_shape_is_flagged(self, line, kind):
        """POSITIVE CONTROL: every shape the scan claims to catch is caught."""
        assert [hit_kind for _n, hit_kind, _t in self._hits(line)][:1] == [kind]

    @pytest.mark.parametrize("line", CLEAN)
    def test_real_terms_are_not_flagged(self, line):
        """NEGATIVE CONTROL: control-set names, percent-escapes, code points,
        timestamps and encodings are not labels."""
        assert self._hits(line) == []

    def test_the_scan_population_is_whole(self):
        """The glob still finds every module it was written for."""
        modules = self._test_modules()
        assert len(modules) >= self.MIN_TEST_MODULES, modules
        assert all((PLUGIN_ROOT / relpath).is_file() for relpath in self.SCRIPTS)

    @pytest.mark.parametrize(
        "relpath",
        SCRIPTS + tuple(TEST_GLOBS),
    )
    def test_the_files_carry_no_planning_label(self, relpath):
        paths = sorted(PLUGIN_ROOT.glob(relpath)) if "*" in relpath else [PLUGIN_ROOT / relpath]
        assert paths, f"{relpath} matched no file"
        found = {}
        for path in paths:
            text = path.read_text(encoding="utf-8")
            assert len(text) > 200, f"{path} read as empty; the scan would pass vacuously"
            hits = self._hits(text)
            if hits:
                found[str(path.relative_to(PLUGIN_ROOT))] = hits
        assert found == {}, f"planning labels: {found}"
