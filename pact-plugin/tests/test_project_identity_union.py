"""Derived read union, disclosure and read purity (P0 matrix B1-B19).

The model: read scope starts at `(canonical,)` and widens to
`(canonical, legacy_basename)` when the legacy bucket has rows; writes bind
canonical; nothing is persisted; only the rekey command moves rows.

Each arm names the defect it would catch. The highest-risk failure is a
scope that is silently NOT applied (an empty or None scope reaching the
query), which returns the whole store instead of one project.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo
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
from scripts.graph import get_file_ids
from scripts.memory_api import PACTMemory

from scripts import database as db
from scripts import memory_api

CANONICAL = "github.com/syn-labs/pact-plugin"
LEGACY = "PACT-prompt"
FOREIGN = "someone-elses-checkout"


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


# ---------------------------------------------------------------------------
# B1-B4: the widening and its negative controls
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
        """B3: both populated is NORMAL under the union, not a conflict."""
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
        """B8: a save in a widened session must not add a legacy row.

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
# B13, B16, B17: merged limit, deterministic order, post-rekey collapse
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
        """B16: ties are broken by the table-global rowid, so two buckets cannot
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
        """B17: once the legacy bucket is empty, the scope must be canonical
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
# B18: the empty-scope trap
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
        )
        with db_connection(union_env.store) as conn:
            assert list_memories(conn, project_id=[]) == []
            assert list_memories(conn, project_id="") == []
            assert search_memories_by_text(conn, "canonical", project_id=()) == []
            assert get_memory_count(conn, []) == 0
            assert get_file_ids(conn, "any/path.py", []) == []
            # Positive control: None keeps its no-filter meaning.
            assert len(list_memories(conn, project_id=None)) == 2


# ---------------------------------------------------------------------------
# B4/B7/B12: the plausibility gate and the disclosure
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
        assert "project-id rekey" in caplog.text

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
        assert str(state["candidate"]) == LEGACY

    def test_an_ambiguous_folded_match_is_not_guessed(self, union_env, caplog):
        """Two stored buckets differing only by case must not be merged by a
        case-insensitive filesystem accident."""
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
        """R7: an empty/partial status is the user-visible incident shape."""
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
        assert "project-id rekey --from PACT-prompt" in candidates[LEGACY]["rekey"]


# ---------------------------------------------------------------------------
# B14: reads never write
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
        back in). Schema DDL is allowed only with IF NOT EXISTS."""
        _seed(
            union_env,
            [("c1", "canonical", CANONICAL, "2026-01-01T00:00:00+00:00")],
        )
        writes = []

        class SpyConnection:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *args, **kwargs):
                head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
                if head in {"INSERT", "UPDATE", "DELETE", "DROP", "REPLACE"} or head == "CREATE" and "IF NOT EXISTS" not in sql.upper():
                    writes.append(sql)
                # ALTER is deliberately NOT recorded: `_migrate_ct_fields`
                # issues three swallowed ADD COLUMN attempts per connection,
                # pre-existing behaviour outside this change. They are no-ops
                # on an initialised store; the contract under test is no DATA
                # write and no unconditional CREATE/DROP on a read.
                return self._inner.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        real_get_connection = db.get_connection
        memory = _memory(union_env)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            patcher.setattr(
                db,
                "get_connection",
                lambda *a, **k: SpyConnection(real_get_connection(*a, **k)),
            )
            memory.list(limit=5)
            memory.get_status()
            memory.search("canonical", sync_to_claude=False)
            memory.search_by_file("src/x.py", limit=5)
            memory.get("c" * 8)
            memory.sync()
        assert writes == [], f"a read path wrote: {writes}"


# ---------------------------------------------------------------------------
# File seams: the NULL-project exception and scoped reads
# ---------------------------------------------------------------------------

class TestFileSeams:
    def test_get_file_ids_none_is_the_null_project_row_only(self, union_env):
        """G2's explicit exception. Catches a 'cleanup' that routes None
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
# B5: the remaining read seams use the union
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