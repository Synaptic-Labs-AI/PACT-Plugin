"""Derived read union, disclosure and read purity.

The model: read scope starts at `(canonical,)` and widens to
`(canonical, legacy_basename)` when the legacy bucket has rows; writes bind
canonical; nothing is persisted; only the rekey command moves rows.

Each arm names the defect it would catch. The highest-risk failure is a
scope that is silently NOT applied (an empty or None scope reaching the
query), which returns the whole store instead of one project.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import shlex
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
    seed_session_evidence,
)
from scripts.database import (
    count_project_memories,
    db_connection,
    ensure_initialized,
    get_memory_count,
    list_memories,
    normalise_project_scopes,
    project_scope_predicate,
    rekey_project,
    search_memories_by_text,
)
from scripts.database import sqlite3 as db_sqlite3
from scripts.graph import (
    get_file_ids,
    get_files_for_memory,
    get_graph_stats,
    get_memories_for_file,
)
from scripts.memory_api import PACTMemory
from scripts.search import _scope_or_none, vector_search

from scripts import database as db
from scripts import memory_api
from scripts import search as search_mod

CANONICAL = "github.com/syn-labs/pact-plugin"
LEGACY = "PACT-prompt"
FOREIGN = "someone-elses-checkout"


class _CursorSpy:
    """A cursor that records through the same hook as its connection.

    `sqlite3.Cursor` has its own `execute`/`executemany`, and a connection
    wrapper that returns the inner cursor unwrapped is blind to a read path
    writing through `conn.cursor().execute(...)` — the same defect as a spy
    that wraps only `Connection.execute`.
    """

    def __init__(self, inner, record):
        self._inner = inner
        self._record = record

    def execute(self, sql, *args, **kwargs):
        self._record(sql)
        return self._inner.execute(sql, *args, **kwargs)

    def executemany(self, sql, seq):
        self._record(sql)
        return self._inner.executemany(sql, seq)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _WriteSpy:
    """A connection wrapper that records WRITE statements through every
    write-capable method, so a read path cannot write unseen.

    The three statement channels a `sqlite3.Connection` runs SQL through —
    `execute`, `executemany`, `executescript` — are wrapped, and `cursor()`
    returns a `_CursorSpy` so the cursor's own `execute`/`executemany` are
    recorded too. A spy that wraps only `execute` reports a clean sweep while
    a batch or cursor write happens.

    THE RULE IS SCOPED TO AN EXISTING STORE: no INSERT/UPDATE/DELETE/CREATE/
    DROP on a read. There is no `IF NOT EXISTS` allowance — that blanket
    exemption is what hid first-use DDL from this clause for its whole life,
    and it would hide the next read-path DDL too. The absent-derived-store
    bootstrap is a separate, separately asserted exception
    (`test_the_derived_route_still_creates_the_store`), so a read here never
    needs to emit schema DDL.

    ALTER is deliberately NOT recorded: `_migrate_ct_fields` issues three
    swallowed ADD COLUMN attempts per connection, pre-existing behaviour
    outside this change.
    """

    _WRITE_HEADS = frozenset(
        {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "REPLACE"}
    )

    def __init__(self, inner, writes):
        self._inner = inner
        self._writes = writes

    def _record(self, sql):
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if head in self._WRITE_HEADS:
            self._writes.append(sql)

    def execute(self, sql, *args, **kwargs):
        self._record(sql)
        return self._inner.execute(sql, *args, **kwargs)

    def executemany(self, sql, seq):
        self._record(sql)
        return self._inner.executemany(sql, seq)

    def executescript(self, sql_script, *args, **kwargs):
        # Unconditional: a read path has no legitimate use for a multi-
        # statement script, and `_record` would read only its first token.
        self._writes.append(sql_script)
        return self._inner.executescript(sql_script, *args, **kwargs)

    def cursor(self, *args, **kwargs):
        return _CursorSpy(self._inner.cursor(*args, **kwargs), self._record)

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def union_env(tmp_path, monkeypatch):
    """A real repo named LEGACY with a canonical origin, and a throwaway store."""
    assert_outside_repository(tmp_path)
    repo = make_repo(
        tmp_path / LEGACY, remote="https://github.com/syn-labs/pact-plugin.git"
    )
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    memory_api._LEGACY_SCOPE_CACHE.clear()
    # THE SEEDED ROWS ARE THIS CHECKOUT'S OWN: `_seed` writes them as session
    # "s", and this records "s" as a session of this repository, which is the
    # evidence an unverified legacy bucket needs to be read.
    seed_session_evidence(tmp_path / "home" / ".claude", repo, "s")
    yield SimpleNamespace(
        repo=repo, store=tmp_path / "union.db", tmp_path=tmp_path
    )
    memory_api._LEGACY_SCOPE_CACHE.clear()


def _memory(env, **kwargs) -> PACTMemory:
    return PACTMemory(session_id="s", db_path=env.store, **kwargs)


def _seed(env, rows, files=()):
    """rows: (id, context, project, created_at); files: (id, path, project)."""
    with db_connection(env.store) as conn:
        ensure_initialized(conn)
        for memory_id, context, project, created_at in rows:
            conn.execute(
                "INSERT INTO memories "
                "(id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, context, project, "s", created_at, created_at),
            )
        for file_id, path, project in files:
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
        conn.commit()


def _ids(memories):
    return [memory.id for memory in memories]


def _seed_hostile(store, bucket, path):
    """One memory and one file row under `bucket`, outside any fixture."""
    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories "
            "(id, context, project_id, session_id, created_at, updated_at) "
            "VALUES ('m1', 'hostile', ?, 's', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (bucket,),
        )
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES ('f1', ?, ?)",
            (path, bucket),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# The widening and its negative controls
# ---------------------------------------------------------------------------

class TestUnionWidening:
    def test_legacy_rows_are_reachable_from_the_canonical_identity(self, union_env):
        """The reported incident, in its reachable form. LIVE-AND-EMPTY first:
        prove the rows exist under the legacy id before asserting the union."""
        _seed(
            union_env,
            [
                ("a1", "old one", LEGACY, "2026-01-01T00:00:00+00:00"),
                ("a2", "old two", LEGACY, "2026-01-02T00:00:00+00:00"),
            ],
        )
        with db_connection(union_env.store) as conn:
            assert count_project_memories(conn, LEGACY) == 2
            assert count_project_memories(conn, CANONICAL) == 0

        memory = _memory(union_env)
        assert memory._read_project_scopes() == (CANONICAL, LEGACY)
        assert sorted(_ids(memory.list(limit=10))) == ["a1", "a2"]

    def test_a_foreign_bucket_is_never_appended(self, union_env):
        """Catches a union that widens to 'every populated bucket'.

        Same-test positive control: the foreign rows ARE visible under their
        own id, so the negative assertion is not measuring an empty store.
        """
        _seed(
            union_env,
            [
                ("a1", "legacy", LEGACY, "2026-01-01T00:00:00+00:00"),
                ("f1", "foreign one", FOREIGN, "2026-01-02T00:00:00+00:00"),
                ("f2", "foreign two", FOREIGN, "2026-01-03T00:00:00+00:00"),
            ],
        )
        with db_connection(union_env.store) as conn:
            assert get_memory_count(conn, (FOREIGN,)) == 2

        memory = _memory(union_env)
        assert sorted(_ids(memory.list(limit=10))) == ["a1"]

    def test_no_legacy_bucket_reads_canonical_alone(self, union_env):
        """The always-on control: a repo with no legacy bucket must NOT widen."""
        _seed(
            union_env,
            [
                ("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00"),
                ("f1", "foreign", FOREIGN, "2026-01-02T00:00:00+00:00"),
            ],
        )
        memory = _memory(union_env)
        assert memory._read_project_scopes() == (CANONICAL,)
        assert sorted(_ids(memory.list(limit=10))) == ["c1"]

    def test_both_buckets_are_merged(self, union_env):
        """Both populated is NORMAL under the union, not a conflict."""
        _seed(
            union_env,
            [
                ("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00"),
                ("l1", "legacy one", LEGACY, "2026-01-02T00:00:00+00:00"),
                ("l2", "legacy two", LEGACY, "2026-01-03T00:00:00+00:00"),
            ],
        )
        memory = _memory(union_env)
        assert memory._read_project_scopes() == (CANONICAL, LEGACY)
        assert sorted(_ids(memory.list(limit=10))) == ["c1", "l1", "l2"]

    def test_writes_bind_canonical_while_reads_widen(self, union_env):
        """A save in a widened session must not add a legacy row.

        Catches a write seam that follows the widening, which would make the
        union permanent and the rekey powerless.
        """
        _seed(
            union_env,
            [("l1", "legacy", LEGACY, "2026-01-01T00:00:00+00:00")],
        )
        memory = _memory(union_env)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            patcher.setattr(
                PACTMemory, "_store_embedding", lambda self, *a, **k: None
            )
            memory.save({"context": "new row"}, sync_to_claude=False)
        with db_connection(union_env.store) as conn:
            assert count_project_memories(conn, CANONICAL) == 1
            assert count_project_memories(conn, LEGACY) == 1


# ---------------------------------------------------------------------------
# Merged limit, deterministic order, post-rekey collapse
# ---------------------------------------------------------------------------

class TestMergeSemantics:
    def test_limit_applies_to_the_merged_result_not_per_bucket(self, union_env):
        """The lead's case: canonical 3 + legacy 3 at limit 3 -> exactly 3.

        A per-bucket LIMIT returns 6, which no simple count assertion on one
        bucket can see.
        """
        rows = [
            (f"c{i}", f"canonical {i}", CANONICAL, f"2026-01-0{i + 1}T00:00:00+00:00")
            for i in range(3)
        ] + [
            (f"l{i}", f"legacy {i}", LEGACY, f"2026-02-0{i + 1}T00:00:00+00:00")
            for i in range(3)
        ]
        _seed(union_env, rows)
        memory = _memory(union_env)
        merged = memory.list(limit=3)
        assert len(merged) == 3, f"got {len(merged)} rows; per-bucket limit returns 6"

    def test_ordering_is_deterministic_across_the_union(self, union_env):
        """Ties are broken by the table-global rowid, so two buckets cannot
        interleave arbitrarily between runs."""
        tied = "2026-03-01T00:00:00+00:00"
        _seed(
            union_env,
            [
                ("c1", "canonical", CANONICAL, tied),
                ("l1", "legacy", LEGACY, tied),
                ("c2", "canonical 2", CANONICAL, tied),
            ],
        )
        memory = _memory(union_env)
        first = _ids(memory.list(limit=10))
        second = _ids(memory.list(limit=10))
        assert first == second, "the union order changed between identical runs"
        # rowid DESC among the tied rows: last inserted first.
        assert first == ["c2", "l1", "c1"]

    def test_the_widening_collapses_after_a_rekey(self, union_env):
        """Once the legacy bucket is empty, the scope must be canonical
        alone. Catches a cached/sticky widening."""
        _seed(
            union_env,
            [
                ("l1", "legacy one", LEGACY, "2026-01-01T00:00:00+00:00"),
                ("l2", "legacy two", LEGACY, "2026-01-02T00:00:00+00:00"),
            ],
        )
        memory = _memory(union_env)
        assert memory._read_project_scopes() == (CANONICAL, LEGACY)

        with db_connection(union_env.store) as conn:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
        assert counters["memories"] == 2

        assert memory._read_project_scopes() == (CANONICAL,), (
            "the widening survived the rekey; a cached legacy membership is "
            "still querying an empty bucket"
        )
        assert sorted(_ids(memory.list(limit=10))) == ["l1", "l2"]


# ---------------------------------------------------------------------------
# The empty-scope trap
# ---------------------------------------------------------------------------

class TestEmptyScopeNeverSelectsTheStore:
    def test_normalisation_and_predicate_never_emit_an_empty_in(self):
        """The structural rule: empty -> `0`, never `IN ()`, never a dropped
        clause."""
        assert normalise_project_scopes([]) == ()
        assert normalise_project_scopes("") == ()
        assert normalise_project_scopes(()) == ()
        assert project_scope_predicate("project_id", ()) == ("0", ())

    def test_every_seam_returns_no_rows_for_an_empty_scope(self, union_env):
        """Catches a truthiness test that turns an empty scope into a
        whole-store read. `None` is the positive control: it means no filter,
        and the same rows must be visible through it."""
        _seed(
            union_env,
            [
                ("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00"),
                ("f1", "foreign", FOREIGN, "2026-01-02T00:00:00+00:00"),
            ],
            # THE FILE ROW IS THE ARM'S OWN CONTROL. Without a row at the
            # probed path, `get_file_ids(..., [])` returns [] under the guard
            # AND under an unscoped implementation, so the assertion below
            # could not tell them apart.
            files=[("file-f1", "any/path.py", FOREIGN)],
        )
        with db_connection(union_env.store) as conn:
            assert list_memories(conn, project_id=[]) == []
            assert list_memories(conn, project_id="") == []
            assert search_memories_by_text(conn, "canonical", project_id=()) == []
            assert get_memory_count(conn, []) == 0
            assert get_file_ids(conn, "any/path.py", []) == []
            # Positive control for the file seam: the row IS reachable under
            # its own bucket, so the [] above is the empty-scope guard's.
            assert get_file_ids(conn, "any/path.py", (FOREIGN,)) == ["file-f1"]
            # Positive control: None keeps its no-filter meaning.
            assert len(list_memories(conn, project_id=None)) == 2


# ---------------------------------------------------------------------------
# The plausibility gate and the disclosure
# ---------------------------------------------------------------------------

class TestDisclosure:
    def test_an_excluded_populated_bucket_is_disclosed_even_when_rows_return(
        self, union_env, caplog
    ):
        """The contract's sharp edge: the disclosure fires even when the scoped
        read returned canonical rows.

        A naive arm builds a zero-row read and passes trivially; this one
        seeds a canonical row so the read succeeds, then asserts BOTH the row
        and the warning.
        """
        _seed(
            union_env,
            [
                ("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00"),
                ("l1", "legacy one", LEGACY, "2026-01-02T00:00:00+00:00"),
                ("l2", "legacy two", LEGACY, "2026-01-03T00:00:00+00:00"),
            ],
            files=[
                ("file-l1", "/tmp/elsewhere/legacy.py", LEGACY),
            ],
        )
        memory = _memory(union_env)
        with caplog.at_level(logging.WARNING):
            rows = memory.list(limit=10)
        assert _ids(rows) == ["c1"], "the canonical read did not return its own row"
        state = memory._legacy_scope_state
        assert state["candidate"] == LEGACY
        assert state["excluded"] is True
        assert state["included"] is False
        assert state["canonical_count"] == 1
        assert LEGACY in caplog.text
        # Its rows belong to the repository their paths name, so the notice
        # hands the reader no command to paste here.
        assert "project-id rekey" not in caplog.text
        assert state["rekey"] is None
        # The warning carries the file count the disclosure requires, so the operator
        # can judge the loss (2 memories and 1 file row in this fixture).
        assert re.search(r"holds 2 memories and 1 file path", caplog.text), (
            f"the exclusion warning omitted the file count: {caplog.text!r}"
        )

    def test_a_relative_only_bucket_is_included_as_unverified(
        self, union_env, caplog
    ):
        """The live store's own shape (relative file rows only). Catches a gate
        that excludes a bucket it cannot judge — which would drop the 2,535-row
        bucket this change exists to reach."""
        _seed(
            union_env,
            [("l1", "legacy", LEGACY, "2026-01-01T00:00:00+00:00")],
            files=[("file-l1", "src/relative.py", LEGACY)],
        )
        memory = _memory(union_env)
        with caplog.at_level(logging.WARNING):
            rows = memory.list(limit=10)
        assert _ids(rows) == ["l1"]
        state = memory._legacy_scope_state
        assert state["included"] is True
        assert state["verified"] is False
        assert state["excluded"] is False
        assert state["session_evidence"] is True
        assert str(state["candidate"]) == LEGACY

    def test_an_ambiguous_folded_match_is_not_guessed(
        self, union_env, caplog, monkeypatch
    ):
        """Two stored buckets differing only by case must not be merged by a
        case-insensitive filesystem accident.

        THE FOLD IS NOW GATED ON THE ROOT'S FILESYSTEM: on a case-SENSITIVE
        host the fold never runs, so the ambiguity set is empty and this arm
        would measure the gate instead of the ambiguity logic. Forcing the
        predicate to "insensitive" keeps the arm on the behaviour it names.
        """
        from scripts import project_identity as pi

        monkeypatch.setattr(
            pi, "filesystem_case_insensitive", lambda *_a, **_k: True, raising=False
        )
        _seed(
            union_env,
            [
                ("p1", "upper", "PACT-PROMPT", "2026-01-01T00:00:00+00:00"),
                ("p2", "mixed", "Pact-Prompt", "2026-01-02T00:00:00+00:00"),
            ],
        )
        memory = _memory(union_env)
        with caplog.at_level(logging.WARNING):
            rows = memory.list(limit=10)
        assert rows == []
        state = memory._legacy_scope_state
        assert state["candidate"] is None
        assert state["ambiguous"] == ["PACT-PROMPT", "Pact-Prompt"]
        assert "matches" in caplog.text and "none is used" in caplog.text

    def test_status_counts_the_union_and_names_the_command(self, union_env):
        """An empty/partial status is the user-visible incident shape."""
        _seed(
            union_env,
            [
                ("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00"),
                ("l1", "legacy", LEGACY, "2026-01-02T00:00:00+00:00"),
            ],
        )
        memory = _memory(union_env)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            status = memory.get_status()
        assert status["memory_count"] == 2
        candidates = {item["id"]: item for item in status["project_candidates"]}
        assert candidates[LEGACY]["memory_count"] == 1
        assert candidates[LEGACY]["relation"] == "legacy_candidate"
        assert (
            f"project-id rekey --from=PACT-prompt --to={CANONICAL}"
            in candidates[LEGACY]["rekey"]
        )


# ---------------------------------------------------------------------------
# Reads never write
# ---------------------------------------------------------------------------

class TestReadsNeverWrite:
    def test_database_reads_work_against_a_read_only_connection(self, union_env):
        """The structural proof: no write privilege, and the reads still work."""
        _seed(
            union_env,
            [("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00")],
        )
        uri = f"file:{union_env.store}?mode=ro"
        # The SAME sqlite3 module production uses, so an exception class it
        # catches is the class this connection raises.
        conn = db_sqlite3.connect(uri, uri=True)
        conn.row_factory = db_sqlite3.Row
        try:
            assert [row["id"] for row in list_memories(conn, project_id=CANONICAL)] == [
                "c1"
            ]
            assert search_memories_by_text(conn, "canonical", project_id=CANONICAL)
            assert get_memory_count(conn, CANONICAL) == 1
            assert get_file_ids(conn, "no/such.py", CANONICAL) == []
        finally:
            conn.close()

    def test_high_level_reads_issue_no_data_writes(self, union_env):
        """Catches a read path that writes (an alias/repair/backfill sneaking
        back in), INCLUDING schema DDL.

        THE STORE EXISTS HERE (the fixture seeds it), so the scoped rule is
        zero INSERT/UPDATE/DELETE/CREATE/DROP — no `IF NOT EXISTS` exemption.
        The absent-store bootstrap is a different case, asserted by
        `test_the_derived_route_still_creates_the_store`."""
        _seed(
            union_env,
            [("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00")],
        )
        writes = []
        real_get_connection = db.get_connection
        memory = _memory(union_env)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            patcher.setattr(
                db,
                "get_connection",
                lambda *a, **k: _WriteSpy(real_get_connection(*a, **k), writes),
            )
            memory.list(limit=5)
            memory.get_status()
            memory.search("canonical", sync_to_claude=False)
            memory.search_by_file("src/x.py", limit=5)
            memory.get("c" * 8)
            memory.sync()
        assert writes == [], f"a read path wrote: {writes}"

    def test_the_write_spy_observes_executemany(self, union_env):
        """The INSTRUMENT pin. A spy that only wraps `execute` cannot see a
        read path that writes through `executemany`; this arm asserts the
        spy records that channel, so the sweep above is not blind to it.

        RED WHEN: `_WriteSpy.executemany` is removed or stops recording —
        the exact mutant this arm exists for.
        """
        writes = []
        conn = db_sqlite3.connect(str(union_env.store))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS spy_probe (x TEXT)")
            _WriteSpy(conn, writes).executemany(
                "INSERT INTO spy_probe (x) VALUES (?)", [("a",), ("b",)]
            )
        finally:
            conn.close()
        assert any("INSERT" in sql.upper() for sql in writes), (
            "the spy did not observe executemany, so a read path could write "
            "through that channel unseen"
        )

    def test_the_write_spy_observes_cursor_execute_and_executescript(self, union_env):
        """The INSTRUMENT pin for the other two channels.

        `conn.cursor().execute(...)` and `conn.executescript(...)` run SQL
        without touching `Connection.execute`, so an unwrapped cursor or a
        delegated script call is a blind spot in the sweep above.

        RED WHEN: `_WriteSpy.cursor` returns the inner cursor unwrapped, or
        `executescript` stops recording.
        """
        writes = []
        conn = db_sqlite3.connect(str(union_env.store))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS spy_probe (x TEXT)")
            spy = _WriteSpy(conn, writes)
            spy.cursor().execute(
                "INSERT INTO spy_probe (x) VALUES ('cursor')"
            )
            spy.executescript(
                "BEGIN; INSERT INTO spy_probe (x) VALUES ('script'); COMMIT;"
            )
        finally:
            conn.close()
        assert any("'cursor'" in sql for sql in writes), (
            "the spy did not observe cursor().execute, so a read path could "
            "write through that channel unseen"
        )
        assert any("'script'" in sql for sql in writes), (
            "the spy did not observe executescript, so a read path could "
            "write through that channel unseen"
        )


# ---------------------------------------------------------------------------
# File seams: the NULL-project exception and scoped reads
# ---------------------------------------------------------------------------

class TestFileSeams:
    def test_get_file_ids_none_is_the_null_project_row_only(self, union_env):
        """The NULL-project exception of `get_file_ids`. Catches a 'cleanup' that routes None
        through the scope builder and returns every project's row."""
        _seed(
            union_env,
            [("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00")],
            files=[
                ("null-row", "src/x.py", None),
                ("legacy-row", "src/x.py", LEGACY),
                ("canonical-row", "src/x.py", CANONICAL),
            ],
        )
        with db_connection(union_env.store) as conn:
            assert get_file_ids(conn, "src/x.py", None) == ["null-row"]
            assert sorted(get_file_ids(conn, "src/x.py", (CANONICAL,))) == [
                "canonical-row"
            ]
            assert sorted(get_file_ids(conn, "src/x.py", (CANONICAL, LEGACY))) == [
                "canonical-row",
                "legacy-row",
            ]


# ---------------------------------------------------------------------------
# The remaining read seams use the union
# ---------------------------------------------------------------------------

class TestPerSeamUnion:
    def test_a_legacy_only_prefix_resolves_and_a_foreign_prefix_does_not(
        self, union_env
    ):
        """`_resolve_id_or_full` must resolve a prefix through the WIDENED
        scope. Catches a prefix seam still bound to the canonical id alone,
        which would hide every legacy row from `get`/`update`/`delete` by
        prefix."""
        legacy_id = "a" * 32
        foreign_id = "b" * 32
        _seed(
            union_env,
            [
                (legacy_id, "legacy row", LEGACY, "2026-01-01T00:00:00+00:00"),
                (foreign_id, "foreign row", FOREIGN, "2026-01-02T00:00:00+00:00"),
            ],
        )
        memory = _memory(union_env)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            assert memory.get("a" * 8) is not None, (
                "a legacy-only prefix did not resolve; the prefix seam is "
                "reading the canonical id alone"
            )
            assert memory.get("b" * 8) is None, (
                "a foreign project's prefix resolved through this project"
            )

    def test_search_reaches_the_legacy_bucket(self, union_env):
        """The user-visible incident path: search under the canonical identity
        must return legacy rows."""
        _seed(
            union_env,
            [
                ("l1", "legacy searchable needle", LEGACY, "2026-01-01T00:00:00+00:00"),
                ("f1", "foreign needle", FOREIGN, "2026-01-02T00:00:00+00:00"),
            ],
        )
        memory = _memory(union_env)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            results = memory.search("needle", sync_to_claude=False)
        assert [item.id for item in results] == ["l1"], (
            "search did not use the widened scope, or a foreign row leaked"
        )

    def test_sync_projects_the_union(self, union_env):
        """A sync that reads only the canonical bucket writes an empty Working
        Memory while 2,000 legacy rows exist — the reported incident."""
        _seed(
            union_env,
            [("l1", "legacy row", LEGACY, "2026-01-01T00:00:00+00:00")],
        )
        memory = _memory(union_env)
        from fixtures.claude_md_seed import seed_claude_md

        seed_claude_md(union_env.repo)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            projected = memory.sync(claude_md_root=union_env.repo)
        assert projected == ["l1"]

# ---------------------------------------------------------------------------
# Review findings: the arms the review proved were missing
# ---------------------------------------------------------------------------

class TestReviewB1GateVerified:
    def test_the_gate_verifies_against_the_declared_identity_root(
        self, tmp_path, monkeypatch
    ):
        """The `verified` branch of the plausibility gate.

        The identity root is the DECLARED repo (A), while the process cwd is
        a different repo (B). A bucket with an absolute file row under A must
        be `verified`, not excluded; comparing against B's root (the
        pre-fix behaviour) excludes A's own bucket and tells the operator to
        rekey rows that are already theirs.

        RED WHEN: the gate compares against the process cwd's repo root, or
        the gate excludes every absolute-path bucket.
        """
        assert_outside_repository(tmp_path)
        declared = make_repo(
            tmp_path / "A-checkout", remote="https://github.com/o/a.git"
        )
        cwd_repo = make_repo(
            tmp_path / "B-checkout", remote="https://github.com/o/b.git"
        )
        store = tmp_path / "gate.db"
        monkeypatch.chdir(cwd_repo)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
        memory_api._LEGACY_SCOPE_CACHE.clear()

        with db_connection(store) as conn:
            ensure_initialized(conn)
            conn.execute(
                "INSERT INTO memories "
                "(id, context, project_id, session_id, created_at, updated_at) "
                "VALUES ('m1', 'declared', 'A-checkout', 's', "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES ('f1', ?, ?)",
                (str(declared / "src" / "x.py"), "A-checkout"),
            )
            conn.commit()

        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._project_id == "github.com/o/a"
        scopes = memory._read_project_scopes()
        state = memory._legacy_scope_state
        assert state["verified"] is True, (
            "the declared repo's own absolute-path bucket was not verified: "
            "the gate used a root other than the winning rung's"
        )
        assert state["excluded"] is False
        assert state["included"] is True
        assert "A-checkout" in scopes


class TestReviewB3EmptyScope:
    def test_scope_or_none_keeps_the_empty_sequence_empty(self):
        """`_scope_or_none` must not fold `()` to None.

        A truthiness implementation (`tuple(project_id) or None`) turns an
        empty scope into the whole store, which is the empty-set trap the
        plan names. The `""` arm is an empty scope too: no rows, never
        None.
        """
        assert _scope_or_none(()) == ()
        assert _scope_or_none([]) == ()
        assert _scope_or_none(None) is None
        assert _scope_or_none("") == ()
        assert _scope_or_none((CANONICAL, LEGACY)) == (CANONICAL, LEGACY)

    def test_get_graph_stats_empty_scope_is_all_zeros_not_the_store(self, union_env):
        """`get_graph_stats(conn, [])` must not fall through to the
        whole store. The populated `None` call is the positive control."""
        _seed(
            union_env,
            [("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00")],
            files=[("f1", "src/x.py", CANONICAL)],
        )
        with db_connection(union_env.store) as conn:
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id) VALUES ('c1', 'f1')"
            )
            conn.commit()
            assert get_graph_stats(conn, []) == {
                "files": 0,
                "memories": 0,
                "memory_file_links": 0,
                "file_relations": 0,
                # The distinct-path total joined the envelope with the
                # store fix; it is part of the zeros contract too.
                "file_paths": 0,
            }, "an empty graph scope returned the store"
            assert get_graph_stats(conn, None)["memories"] == 1
            # THE CLASS GUARD for exact `graph_stats` literals elsewhere: the
            # zeroed and populated envelopes must expose the SAME key set. A
            # key added to one branch only leaves an exact-literal assertion
            # in another file stale (the `file_paths` CI break), and this arm
            # reddens the moment that drift returns.
            assert set(get_graph_stats(conn, [])) == set(
                get_graph_stats(conn, None)
            ), (
                "the zero-scope graph_stats envelope dropped a key the "
                "populated envelope carries; exact-literal pins elsewhere "
                "will drift"
            )


class TestReviewB4FoldedCandidate:
    def test_an_exact_case_match_wins_over_a_case_variant(self):
        """EXACT case first, the fold only as a fallback.

        A folded-first implementation passes on a case-insensitive filesystem
        and loses the bucket on a case-sensitive one: with an exact bucket and
        a case variant both present, the fold sees two matches and returns
        None. This arm reddens on any OS if the precedence is dropped. RED
        WHEN the `if legacy in buckets` early return is removed.

        `allow_fold=True` IS EXPLICIT: this arm exercises the PURE-STRING
        matcher's precedence, not the filesystem gate (the gate is pinned by
        the behavioural arms in test_project_identity_name_provenance.py). The
        production default is fail-closed, so only a caller that has already
        established a folding filesystem may ask for the fold.
        """
        candidate, ambiguous = memory_api._match_legacy_candidate(
            "PACT-prompt", {"PACT-prompt": 1, "PACT-PROMPT": 2}, allow_fold=True
        )
        assert candidate == "PACT-prompt", (
            "the exact-case bucket lost to a case-folded ambiguity"
        )
        assert ambiguous == [], "an exact match must not report ambiguity"

    def test_a_unique_case_folded_candidate_is_the_raw_stored_spelling(self):
        """The unique folded fallback, as a PURE-STRING surrogate.

        The real filesystem shape is macOS-APFS-only and unreproducible
        locally; this arm reddens on any OS if the fold starts guessing or
        returns the input casing instead of the stored spelling.

        `allow_fold=True` IS EXPLICIT: the caller states the folding fact
        here, exactly as the production call site does after consulting
        `project_identity.filesystem_case_insensitive`. The production default
        is fail-closed, so this arm must not rely on it.
        """
        candidate, ambiguous = memory_api._match_legacy_candidate(
            "Pact-Prompt", {"PACT-PROMPT": 3, "other": 1}, allow_fold=True
        )
        assert candidate == "PACT-PROMPT", (
            "the fold did not return the RAW stored spelling"
        )
        assert ambiguous == []


class TestReviewB2Seams:
    def _vec_store(self, env):
        from helpers import create_test_schema

        conn = db_sqlite3.connect(str(env.store))
        conn.row_factory = db_sqlite3.Row
        create_test_schema(conn)
        conn.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute(
            "CREATE VIRTUAL TABLE vec_memories USING vec0("
            "memory_id TEXT PRIMARY KEY, project_id TEXT PARTITION KEY, "
            "embedding float[3])"
        )
        for memory_id, project in (
            ("m-legacy", LEGACY),
            ("m-canon", CANONICAL),
            ("m-foreign", FOREIGN),
        ):
            conn.execute(
                "INSERT INTO memories "
                "(id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, 'ctx', ?, 's', "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (memory_id, project),
            )
            conn.execute(
                "INSERT INTO vec_memories (memory_id, project_id, embedding) "
                "VALUES (?, ?, ?)",
                (memory_id, project, struct.pack("3f", 0.1, 0.2, 0.3)),
            )
        conn.commit()
        return conn

    def test_the_vector_seam_resolves_the_union_and_rejects_a_foreign_bucket(
        self, union_env, monkeypatch
    ):
        """One KNN per scope across the union.

        RED WHEN: the vector seam uses only the first/canonical id, drops a
        scope, or serves a foreign partition's hit.
        """
        conn = self._vec_store(union_env)
        monkeypatch.setattr(
            search_mod, "generate_embedding", lambda query: [0.1, 0.2, 0.3]
        )
        try:
            union = {
                memory_id
                for memory_id, _ in vector_search(
                    conn, "q", project_id=(CANONICAL, LEGACY), limit=10
                )
            }
            assert union == {"m-legacy", "m-canon"}, (
                f"the vector seam did not resolve the union: {union}"
            )
            assert "m-foreign" not in union
            canonical_only = {
                memory_id
                for memory_id, _ in vector_search(
                    conn, "q", project_id=(CANONICAL,), limit=10
                )
            }
            assert canonical_only == {"m-canon"}, (
                "the legacy hit survived a canonical-only scope; the arm is "
                "not sensitive to the scope it claims to test"
            )
        finally:
            conn.close()

    def test_two_graph_seams_resolve_the_union_and_reject_a_foreign_bucket(
        self, union_env
    ):
        """Two graph seams across the union (`get_files_for_memory` and
        `get_memories_for_file`), with a foreign
        negative. RED WHEN either seam ignores the sequence and binds one id.
        """
        _seed(
            union_env,
            [
                ("m-legacy", "legacy", LEGACY, "2026-01-01T00:00:00+00:00"),
                ("m-foreign", "foreign", FOREIGN, "2026-01-02T00:00:00+00:00"),
            ],
            files=[
                ("f-legacy", "src/x.py", LEGACY),
                ("f-foreign", "src/x.py", FOREIGN),
            ],
        )
        with db_connection(union_env.store) as conn:
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id) "
                "VALUES ('m-legacy', 'f-legacy')"
            )
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id) "
                "VALUES ('m-foreign', 'f-foreign')"
            )
            conn.commit()

            union_files = get_files_for_memory(
                conn, "m-legacy", project_id=(CANONICAL, LEGACY)
            )
            assert [row["id"] for row in union_files] == ["f-legacy"]
            assert (
                get_files_for_memory(conn, "m-legacy", project_id=(CANONICAL,))
                == []
            ), "get_files_for_memory did not apply the scope to the legacy file row"

            assert get_memories_for_file(
                conn, "f-legacy", project_id=(CANONICAL, LEGACY)
            ) == ["m-legacy"]
            assert (
                get_memories_for_file(conn, "f-legacy", project_id=(CANONICAL,))
                == []
            ), "get_memories_for_file did not apply the scope to the legacy memory row"

            assert (
                get_memories_for_file(
                    conn, "f-foreign", project_id=(CANONICAL, LEGACY)
                )
                == []
            ), "get_memories_for_file served a foreign bucket's memory"


    def test_search_by_file_resolves_the_union_and_rejects_a_foreign_bucket(
        self, union_env
    ):
        """The SECOND belt comparison (search.py's `search_by_file`).

        `graph_enhanced_search`'s belt is live through
        `test_search_reaches_the_legacy_bucket`; this sibling belt had no
        union arm, so downgrading its membership test to equality left the
        suite green. A legacy memory linked through the file graph would
        silently disappear from the file view. RED WHEN the belt compares to
        the first scope id instead of testing membership.
        """
        _seed(
            union_env,
            [
                ("m-legacy", "legacy", LEGACY, "2026-01-01T00:00:00+00:00"),
                ("m-foreign", "foreign", FOREIGN, "2026-01-02T00:00:00+00:00"),
            ],
            files=[
                ("f-legacy", "src/y.py", LEGACY),
                ("f-foreign", "src/y.py", FOREIGN),
            ],
        )
        with db_connection(union_env.store) as conn:
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id) "
                "VALUES ('m-legacy', 'f-legacy')"
            )
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id) "
                "VALUES ('m-foreign', 'f-foreign')"
            )
            conn.commit()

        memory = _memory(union_env)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            rows = memory.search_by_file("src/y.py", limit=10)

        assert [row.id for row in rows] == ["m-legacy"], (
            "search_by_file did not resolve the legacy bucket, or served a "
            f"foreign row: {[row.id for row in rows]}"
        )


class TestReviewDbConfirmationCap:
    """`vector_search` returns AT MOST `limit`, and the cap can make it short.

    Pinned so the documented trade cannot be "fixed" into an unbounded fetch
    or into a silent `limit` guarantee. The docstring states it; these arms
    hold the behaviour.
    """

    def _store(self, env, stale, valid):
        from helpers import create_test_schema

        conn = db_sqlite3.connect(str(env.store))
        conn.row_factory = db_sqlite3.Row
        create_test_schema(conn)
        conn.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE vec_memories USING vec0("
            "memory_id TEXT PRIMARY KEY, project_id TEXT PARTITION KEY, "
            "embedding float[3])"
        )

        def add(memory_id, stored_project, partition, embedding):
            conn.execute(
                "INSERT INTO memories (id, context, project_id, session_id, "
                "created_at, updated_at) VALUES (?, 'ctx', ?, 's', "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (memory_id, stored_project),
            )
            conn.execute(
                "INSERT INTO vec_memories (memory_id, project_id, embedding) "
                "VALUES (?, ?, ?)",
                (memory_id, partition, struct.pack("3f", *embedding)),
            )

        # STALE: the partition is in scope, the row's stored project is not.
        for index in range(stale):
            add(f"stale{index}", FOREIGN, CANONICAL, (0.1, 0.2, 0.3))
        # VALID: in scope, ranked worse than every stale row.
        for index in range(valid):
            add(f"valid{index}", CANONICAL, CANONICAL, (0.9, 0.9, 0.9))
        conn.commit()
        return conn

    def test_a_capped_window_returns_fewer_than_limit(
        self, union_env, monkeypatch
    ):
        """The measured construction: 200 stale rows (partition in scope,
        stored project FOREIGN) at distance ~0, and 10 valid in-scope rows
        behind the cap at a worse distance. The confirmation widens to
        CONFIRMATION_FETCH_CAP, never sees a confirmable hit, and returns
        FEWER THAN `limit` — here none — while the store holds ten valid
        matches. That is the documented cost bound, pinned so it cannot drift
        into either an unbounded fetch or a `limit` guarantee.

        RED WHEN: the widening stops honouring the cap, or the return claims
        rows it did not confirm.
        """
        monkeypatch.setattr(
            search_mod, "generate_embedding", lambda query: [0.1, 0.2, 0.3]
        )
        limit = 5
        conn = self._store(union_env, stale=200, valid=10)
        try:
            valid_in_store = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE project_id = ?",
                (CANONICAL,),
            ).fetchone()[0]
            rows = vector_search(
                conn, "q", project_id=(CANONICAL,), limit=limit
            )
        finally:
            conn.close()
        assert valid_in_store == 10, (
            "fixture control: the store must hold ten confirmable in-scope "
            f"rows, found {valid_in_store}"
        )
        assert len(rows) < limit, (
            "the capped call returned the full limit, so this arm did not "
            f"exercise the cap: {rows}"
        )
        assert rows == [], (
            "an unconfirmed id survived the cap exit: "
            f"{rows}"
        )

    def test_an_exhausted_store_returns_what_exists(
        self, union_env, monkeypatch
    ):
        """Positive control: no stale rows, so the same limit-5 call returns
        five of the ten valid rows. The short result above is the cap, not a
        broken fixture."""
        monkeypatch.setattr(
            search_mod, "generate_embedding", lambda query: [0.1, 0.2, 0.3]
        )
        conn = self._store(union_env, stale=0, valid=10)
        try:
            rows = vector_search(conn, "q", project_id=(CANONICAL,), limit=5)
        finally:
            conn.close()
        assert len(rows) == 5, f"an exhausted store returned {len(rows)} rows"
        assert all(memory_id.startswith("valid") for memory_id, _ in rows)


class TestReviewDbSearchByFileOrder:
    def test_the_subset_does_not_depend_on_set_order(self, union_env):
        """More related memories than `limit`, so WHICH ones the window
        returns is visible. `list(set)` made that subset change with the
        process hash seed; sorted ids make it reproducible.

        RED WHEN: the ordered list is `list(memory_ids)` again — this
        assertion then needs the hash order to be ascending, a 1-in-720
        accident for these six ids.
        """
        rows = [
            (
                f"m{index}",
                f"ctx {index}",
                CANONICAL if index % 2 == 0 else LEGACY,
                "2026-01-01T00:00:00+00:00",
            )
            for index in range(6)
        ]
        _seed(union_env, rows, files=[("f1", "src/x.py", CANONICAL)])
        with db_connection(union_env.store) as conn:
            for index in range(6):
                conn.execute(
                    "INSERT INTO memory_files (memory_id, file_id) "
                    "VALUES (?, 'f1')",
                    (f"m{index}",),
                )
            conn.commit()

        memory = _memory(union_env)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            limited = _ids(memory.search_by_file("src/x.py", limit=3))
            full = _ids(memory.search_by_file("src/x.py", limit=6))

        assert full == sorted(full), (
            "search_by_file returned a set-ordered list; the same store and "
            f"query answer differently per process: {full}"
        )
        assert limited == full[:3], (
            "the limit-3 window is not the deterministic prefix of the "
            f"relation set: {limited} vs {full}"
        )

    def test_the_subset_is_identical_across_hash_seeds(self, union_env, tmp_path):
        """The cross-process statement of the same contract: two child
        processes with different PYTHONHASHSEED values must return the same
        ids, and they must be the sorted prefix.

        RED WHEN: the window is sliced from `list(set)` — the children then
        disagree with each other and with the sorted prefix.
        """
        rows = [
            (
                f"m{index}",
                f"ctx {index}",
                CANONICAL if index % 2 == 0 else LEGACY,
                "2026-01-01T00:00:00+00:00",
            )
            for index in range(6)
        ]
        _seed(union_env, rows, files=[("f1", "src/x.py", CANONICAL)])
        with db_connection(union_env.store) as conn:
            for index in range(6):
                conn.execute(
                    "INSERT INTO memory_files (memory_id, file_id) "
                    "VALUES (?, 'f1')",
                    (f"m{index}",),
                )
            conn.commit()

        scripts_parent = str(
            Path(__file__).resolve().parent.parent / "skills" / "pact-memory"
        )
        script = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from scripts.config import store_scope\n"
            "from scripts.search import search_by_file\n"
            "with store_scope(sys.argv[2]):\n"
            "    found = search_by_file(\n"
            "        sys.argv[3], project_id=tuple(sys.argv[4].split(',')),\n"
            "        limit=int(sys.argv[5]))\n"
            "print(','.join(m.id for m in found))\n"
        )
        outputs = []
        for seed in ("0", "1"):
            out = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    scripts_parent,
                    str(union_env.store),
                    "src/x.py",
                    f"{CANONICAL},{LEGACY}",
                    "3",
                ],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PYTHONHASHSEED": seed,
                    "HOME": str(tmp_path / "home"),
                    "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
                },
                check=True,
            )
            outputs.append(out.stdout.strip())
        assert outputs == ["m0,m1,m2", "m0,m1,m2"], (
            "search_by_file's subset depends on the process hash seed: "
            f"{outputs}"
        )


class TestReviewDbGateWorktreeRoots:
    """The legacy gate must accept every checkout of the repository.

    `main_repo_root()` deliberately answers the MAIN checkout for a linked
    worktree, so a bucket whose only absolute rows were written from the
    worktree verified against the main root alone would be EXCLUDED — reads
    collapse to the canonical id and the operator is told to rekey their own
    rows. The caller builds the set from `git worktree list` run AT the
    identity root (`_listed_worktree_toplevels`), deliberately NOT from
    `backlog.checkout_roots()`: that helper resolves against the PROCESS, so
    a declared session would append another repository's checkouts.
    """

    def _seed_row(self, env, file_id, path, project):
        with db_connection(env.store) as conn:
            ensure_initialized(conn)
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
            conn.commit()

    def test_an_unreadable_path_is_evidence_and_fails_closed(self, union_env):
        """A `files.path` with an embedded NUL makes `os.path.realpath` raise
        ValueError. This gate runs on every read, so letting that escape fails
        every read for the project from a check that only decides whether to
        widen the scope. Swallowing it as non-evidence is the opposite
        failure: a row nobody can judge would buy the bucket an INCLUDE.

        Ruling: an unreadable absolute row counts as `absolute_rows` and can
        never count as `contained_rows`, so a bucket whose only absolute rows
        are unreadable lands in EXCLUDED (disclosed, with the rekey remedy).

        RED WHEN: the realpath call is unguarded (ValueError escapes) or the
        unreadable row is dropped from `absolute_rows` (the bucket then lands
        in `unverified` and its rows are read).
        """
        from scripts.database import (
            LEGACY_PLAUSIBLE_EXCLUDED,
            LEGACY_PLAUSIBLE_VERIFIED,
            legacy_candidate_plausibility,
        )

        nul_path = "/tmp/x\x00y.py"
        self._seed_row(union_env, "f-nul", nul_path, "legacy-nul")
        self._seed_row(union_env, "f-nul2", nul_path, "legacy-nul-plus")
        self._seed_row(
            union_env, "f-elsewhere",
            str(union_env.tmp_path / "elsewhere" / "z.py"),
            "legacy-nul-plus",
        )
        self._seed_row(union_env, "f-nul3", nul_path, "legacy-nul-contained")
        self._seed_row(
            union_env, "f-contained",
            str(union_env.repo / "src" / "kept.py"),
            "legacy-nul-contained",
        )

        with db_connection(union_env.store) as conn:
            only_nul = legacy_candidate_plausibility(
                conn, "legacy-nul", [union_env.repo]
            )
            nul_plus_real = legacy_candidate_plausibility(
                conn, "legacy-nul-plus", [union_env.repo]
            )
            nul_plus_contained = legacy_candidate_plausibility(
                conn, "legacy-nul-contained", [union_env.repo]
            )

        assert only_nul[0] == LEGACY_PLAUSIBLE_EXCLUDED, (
            "a bucket whose only absolute row is unreadable must FAIL CLOSED "
            f"(excluded, disclosed), not be read: {only_nul}"
        )
        assert only_nul[1]["file_rows"] == 1
        assert only_nul[1]["absolute_rows"] == 1, (
            "the unreadable row was dropped from absolute evidence, so it "
            f"bought the bucket an include: {only_nul[1]}"
        )
        assert only_nul[1]["contained_rows"] == 0
        assert nul_plus_real[0] == LEGACY_PLAUSIBLE_EXCLUDED, (
            "a judgeable row outside every root must still exclude: "
            f"{nul_plus_real}"
        )
        assert nul_plus_real[1]["absolute_rows"] == 2
        assert nul_plus_real[1]["contained_rows"] == 0
        assert nul_plus_contained[0] == LEGACY_PLAUSIBLE_VERIFIED, (
            "an unreadable row must not veto a bucket that also holds a "
            f"contained row: {nul_plus_contained}"
        )
        assert nul_plus_contained[1]["absolute_rows"] == 2
        assert nul_plus_contained[1]["contained_rows"] == 1

    def test_a_nul_path_row_does_not_break_the_read(self, union_env):
        """The user-visible shape: one corrupt `files.path` must not make
        `list` (or any read) raise for the WHOLE project, and the bucket it
        makes unjudgeable must be EXCLUDED and disclosed, so the project's
        canonical rows still come back.

        RED WHEN: the gate's realpath call is unguarded (this call raises
        ValueError) or the exclusion is not disclosed in `legacy_scope`.
        """
        _seed(
            union_env,
            [
                ("c1", "canon", CANONICAL, "2026-01-01T00:00:00+00:00"),
                ("l1", "legacy", LEGACY, "2026-01-02T00:00:00+00:00"),
            ],
        )
        self._seed_row(union_env, "f-nul", "/tmp/x\x00y.py", LEGACY)

        memory = _memory(union_env)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            ids = _ids(memory.list(limit=10))
        legacy_state = memory._legacy_scope_state

        assert ids == ["c1"], (
            "the read did not return exactly the canonical row; the legacy "
            f"bucket should be excluded and the call should not raise: {ids}"
        )
        assert legacy_state["excluded"] is True, (
            f"the exclusion was not disclosed: {legacy_state}"
        )
        assert legacy_state["memory_count"] == 1
        assert legacy_state["file_paths"] == 1
        assert legacy_state["rekey"] is None, (
            "an excluded bucket must carry no runnable command"
        )

    def test_the_gate_accepts_every_checkout_and_nothing_else(self, union_env):
        from scripts.database import (
            LEGACY_PLAUSIBLE_EXCLUDED,
            LEGACY_PLAUSIBLE_UNVERIFIED,
            LEGACY_PLAUSIBLE_VERIFIED,
            legacy_candidate_plausibility,
        )

        main_root = union_env.repo
        worktree = union_env.tmp_path / "feature-wt"
        linked = union_env.tmp_path / "linked"
        (worktree / "src").mkdir(parents=True, exist_ok=True)
        (main_root / "src").mkdir(parents=True, exist_ok=True)
        linked.symlink_to(worktree, target_is_directory=True)

        self._seed_row(
            union_env, "f-main", str(main_root / "src" / "m.py"),
            "legacy-main",
        )
        self._seed_row(
            union_env, "f-wt", str(worktree / "src" / "x.py"), LEGACY
        )
        self._seed_row(
            union_env, "f-foreign",
            str(union_env.tmp_path / "elsewhere" / "src" / "z.py"),
            "legacy-foreign",
        )
        self._seed_row(
            union_env, "f-relative", "src/rel.py", "legacy-relative"
        )
        # STORED through the symlink, VERIFIED against the resolved root.
        self._seed_row(
            union_env, "f-linked", str(linked / "src" / "y.py"),
            "legacy-linked",
        )

        with db_connection(union_env.store) as conn:
            main_only = legacy_candidate_plausibility(
                conn, "legacy-main", [main_root]
            )
            only_main_against_worktree_row = legacy_candidate_plausibility(
                conn, LEGACY, main_root
            )
            checkout_set = legacy_candidate_plausibility(
                conn, LEGACY, [main_root, worktree]
            )
            single_path = legacy_candidate_plausibility(conn, LEGACY, worktree)
            as_string = legacy_candidate_plausibility(
                conn, LEGACY, str(worktree)
            )
            foreign = legacy_candidate_plausibility(
                conn, "legacy-foreign", [main_root, worktree]
            )
            relative_only = legacy_candidate_plausibility(
                conn, "legacy-relative", [main_root, worktree]
            )
            through_link = legacy_candidate_plausibility(
                conn, "legacy-linked", [main_root, worktree]
            )

        assert main_only[0] == LEGACY_PLAUSIBLE_VERIFIED, (
            f"a row under the main root did not verify: {main_only}"
        )
        assert only_main_against_worktree_row[0] == LEGACY_PLAUSIBLE_EXCLUDED, (
            "the single-main-root control did not reproduce the linked-worktree exclusion: "
            f"{only_main_against_worktree_row}"
        )
        assert checkout_set[0] == LEGACY_PLAUSIBLE_VERIFIED, (
            "a row written from the linked worktree did not verify against "
            f"the checkout set: {checkout_set}"
        )
        assert single_path[0] == LEGACY_PLAUSIBLE_VERIFIED, (
            "a single Path root is no longer accepted as a one-element set"
        )
        assert as_string[0] == LEGACY_PLAUSIBLE_VERIFIED, (
            "a single STRING root was not treated as one root; it must not "
            "be iterated as characters"
        )
        assert foreign[0] == LEGACY_PLAUSIBLE_EXCLUDED, (
            f"a row outside every root was called plausible: {foreign}"
        )
        assert relative_only[0] == LEGACY_PLAUSIBLE_UNVERIFIED, (
            "a bucket with no absolute row must be unverified, never "
            f"excluded: {relative_only}"
        )
        assert through_link[0] == LEGACY_PLAUSIBLE_VERIFIED, (
            "a row written through a symlinked spelling did not resolve to "
            f"the real worktree root: {through_link}"
        )
        assert checkout_set[1]["contained_rows"] == 1
        assert checkout_set[1]["roots"] == 2


class TestReviewQuoting:
    def test_the_exclusion_notice_renders_no_command(
        self, tmp_path, monkeypatch, caplog
    ):
        """An EXCLUDED bucket's notice hands the reader nothing to paste.

        Its absolute rows lie in another repository, so a command moving it
        into this id would file that checkout's rows here. RED WHEN the
        exclusion notice renders a rekey command.
        """
        assert_outside_repository(tmp_path)
        hostile = "Legacy;echo PWNED"
        repo = make_repo(
            tmp_path / hostile,
            remote="https://github.com/syn-labs/pact-plugin.git",
        )
        store = tmp_path / "quoting.db"
        monkeypatch.chdir(repo)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        memory_api._LEGACY_SCOPE_CACHE.clear()
        memory_api._EXCLUSION_WARNED.clear()
        _seed_hostile(store, hostile, str(tmp_path / "elsewhere" / "x.py"))

        memory = PACTMemory(session_id="s", db_path=store)
        with caplog.at_level(logging.WARNING):
            memory._read_project_scopes()
        assert memory._legacy_scope_state["excluded"] is True
        assert "legacy project bucket" in caplog.text
        assert "project-id rekey" not in caplog.text

    def test_the_unproven_command_round_trips_a_hostile_bucket_id(
        self, tmp_path, monkeypatch, caplog
    ):
        """The unproven notice renders the command with `shlex.join`, so a
        hostile bucket id is ONE `--from=` argument, logged with repr so a
        newline cannot forge a log line. RED WHEN the renderer is unquoted or
        the id is split across arguments.
        """
        assert_outside_repository(tmp_path)
        hostile = "Legacy;echo PWNED"
        repo = make_repo(
            tmp_path / hostile,
            remote="https://github.com/syn-labs/pact-plugin.git",
        )
        store = tmp_path / "quoting.db"
        monkeypatch.chdir(repo)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        memory_api._LEGACY_SCOPE_CACHE.clear()
        memory_api._UNPROVEN_WARNED.clear()
        _seed_hostile(store, hostile, "src/relative.py")

        memory = PACTMemory(session_id="s", db_path=store)
        with caplog.at_level(logging.WARNING):
            memory._read_project_scopes()

        match = re.search(r"move them explicitly with: (.+)", caplog.text)
        assert match is not None, (
            f"the unproven notice did not render a rekey command: {caplog.text!r}"
        )
        argv = shlex.split(ast.literal_eval(match.group(1)))
        assert f"--from={hostile}" in argv, (
            f"the bucket id did not round-trip as ONE --from argument: {argv!r}"
        )
        assert f"--to={CANONICAL}" in argv, argv
        # The surface knows the store, so the remedy must name it.
        assert f"--db-path={store}" in argv, argv


# ---------------------------------------------------------------------------
# Reads never write, at first use: a ZERO-BYTE store must stay zero bytes on a read
# ---------------------------------------------------------------------------

class TestZeroByteStoreStaysEmptyOnRead:
    """The "reads never write" rule at FIRST USE, every shape.

    The existing spy arms SEED a store, so `ensure_initialized` finds its
    schema already present and sees no DDL; they cover an EXISTING store's
    resolution path, not the first-use case, where the CREATE would actually
    run. Each arm here runs ONE verb in a FRESH process
    (`memory_init._initialized` is per-process).

    THE CONTRACT, THREE SHAPES, matching `_schema_present`:
    - EXISTING store without a schema (0 bytes, truncated): never initialised
      by a read; the file stays 0.
    - ABSENT store, CALLER-NAMED: a read answers EMPTY and does not create it.
    - ABSENT store, DERIVED route: the first memory operation bootstraps the
      directory + file + schema — the pre-existing initialisation path, a
      separately asserted exception. This is a REDIRECT, not a refusal: it is
      what keeps every production caller that passes no path working.
    THIS CLASS MEASURES ALL THREE: the zero-byte and caller-named absent arms
    below, and the derived-absent control that proves the bootstrap survives.
    """

    VERBS = (
        "list",
        "search",
        "search_by_file",
        "sync",
        "get_status",
        "_resolve_id_or_full",
    )

    def _run(self, verb, target, union_env, *, derived=False):
        worker = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "zero_create_worker.py"
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key != "CLAUDE_PROJECT_DIR"
        }
        argv = [sys.executable, str(worker), verb, str(target)]
        if derived:
            argv.append("derived")
        return subprocess.run(
            argv,
            cwd=str(union_env.repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    @pytest.mark.parametrize("verb", VERBS)
    def test_a_zero_byte_store_is_not_initialised_by_a_read(self, verb, union_env):
        """RED before the fix for every verb that CREATEs on first read.

        The fix belongs to the read-path owner; this arm is the measurement,
        so a residual red here is the finding, not a flaky test.
        """
        store = union_env.tmp_path / f"empty-{verb}.db"
        store.write_bytes(b"")
        result = self._run(verb, store, union_env)
        assert result.returncode == 0, (verb, result.stderr)
        size = store.stat().st_size
        assert size == 0, (
            f"the {verb} read initialised a zero-byte store to {size} bytes; "
            "a read must not CREATE the schema"
        )

    @pytest.mark.parametrize("verb", VERBS)
    def test_an_absent_store_is_not_created_by_a_read(self, verb, union_env):
        """A CALLER-NAMED absent store answers empty; the file stays ABSENT.

        The worker passes an explicit `db_path`, so this is the caller-named
        shape only: the DERIVED route bootstraps by ruling and is pinned by
        `test_the_derived_route_still_creates_the_store`. `_resolve_id_or_full`
        goes through `get()`, its production surface, so the arm measures the
        read path's own guard rather than a worker-opened connection.
        """
        store = union_env.tmp_path / f"absent-{verb}.db"
        assert not store.exists()
        result = self._run(verb, store, union_env)
        assert result.returncode == 0, (verb, result.stderr)
        assert not store.exists(), (
            f"the {verb} read created {store}; a read on an absent store must "
            "answer empty, not initialise"
        )

    @pytest.mark.parametrize("verb", VERBS)
    def test_a_derived_absent_store_bootstraps_on_a_read(self, verb, union_env):
        """POSITIVE CONTROL: the derived route REDIRECTS, it does not refuse.

        The pre-existing initialisation path creates directory + file + schema
        on the first memory operation. Without this control a repair could
        answer empty for EVERY absent store and pass the caller-named arms
        while breaking every production caller that passes no path.
        """
        root = union_env.tmp_path / f"derived-{verb}"
        store = root / "memory.db"
        assert not store.exists(), "fixture must start with no derived store"
        result = self._run(verb, root, union_env, derived=True)
        assert result.returncode == 0, (verb, result.stderr)
        assert root.is_dir(), f"the derived read did not create {root}"
        assert store.exists(), (
            f"the {verb} read did not bootstrap an absent derived store; the "
            "derived route is a redirect, not a refusal"
        )
        with db_sqlite3.connect(str(store)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        assert "memories" in tables, (
            "the derived read created a file without the schema: "
            f"{sorted(tables)!r}"
        )
