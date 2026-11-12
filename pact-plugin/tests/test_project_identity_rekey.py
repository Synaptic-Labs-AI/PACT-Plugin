"""The one-shot rekey: atomicity, collisions, vectors and CLI (P0 C1-C12).

The rekey is the ONLY row-rewriting surface. Its dangerous states are a
PARTIAL move (k rows moved, N-k left) and a committed move whose auxiliary
rows (files, links, relations, vectors) did not follow.

Each arm names the defect it would catch.
"""

from __future__ import annotations

import json
import shlex
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
    seed_session_evidence,
)
from scripts.database import (
    db_connection,
    ensure_initialized,
    rekey_project,
)
from scripts.database import (
    sqlite3 as db_sqlite3,
)
from scripts.memory_api import PACTMemory

from scripts import cli, memory_api
from scripts import database as db

CANONICAL = "github.com/syn-labs/pact-plugin"
LEGACY = "PACT-prompt"


# ---------------------------------------------------------------------------
# Fixtures and seeding
# ---------------------------------------------------------------------------

@pytest.fixture
def rekey_env(tmp_path, monkeypatch):
    assert_outside_repository(tmp_path)
    repo = make_repo(
        tmp_path / LEGACY, remote="https://github.com/syn-labs/pact-plugin.git"
    )
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    yield SimpleNamespace(repo=repo, store=tmp_path / "rekey.db", tmp_path=tmp_path)


def _seed(env, memories, files=(), links=(), relations=()):
    with db_connection(env.store) as conn:
        ensure_initialized(conn)
        for memory_id, project in memories:
            conn.execute(
                "INSERT INTO memories "
                "(id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    f"context {memory_id}",
                    project,
                    "s",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        for file_id, path, project in files:
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
        for memory_id, file_id in links:
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id) VALUES (?, ?)",
                (memory_id, file_id),
            )
        for source, target in relations:
            conn.execute(
                "INSERT INTO file_relations (source_file, target_file, relationship) "
                "VALUES (?, ?, 'imports')",
                (source, target),
            )
        conn.commit()


def _rows(store):
    """A comparable snapshot of the row sets the rekey touches."""
    with db_connection(store) as conn:
        ensure_initialized(conn)
        return {
            "memories": sorted(
                (row["id"], row["project_id"])
                for row in conn.execute("SELECT id, project_id FROM memories")
            ),
            "files": sorted(
                (row["id"], row["path"], row["project_id"])
                for row in conn.execute("SELECT id, path, project_id FROM files")
            ),
            "links": sorted(
                (row["memory_id"], row["file_id"])
                for row in conn.execute("SELECT memory_id, file_id FROM memory_files")
            ),
        }


# ---------------------------------------------------------------------------
# C1-C3: dry-run, apply, no-op
# ---------------------------------------------------------------------------

class TestRekeyLifecycle:
    def test_dry_run_changes_no_row_and_reports_the_apply_counters(self, rekey_env):
        """Catches a dry run that either writes or estimates differently from
        the apply it claims to preview."""
        _seed(
            rekey_env,
            [("m1", LEGACY), ("m2", LEGACY)],
            files=[("f1", "src/a.py", LEGACY)],
        )
        before = _rows(rekey_env.store)
        with db_connection(rekey_env.store) as conn:
            planned = rekey_project(conn, LEGACY, CANONICAL, dry_run=True)
        assert _rows(rekey_env.store) == before, "the dry run wrote rows"
        assert planned["memories"] == 2
        assert planned["dry_run"] is True

        with db_connection(rekey_env.store) as conn:
            applied = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
        assert {k: v for k, v in applied.items() if k != "dry_run"} == {
            k: v for k, v in planned.items() if k != "dry_run"
        }, "the dry-run counters differ from the apply"

    def test_apply_moves_the_whole_partition_and_a_second_apply_is_a_noop(
        self, rekey_env
    ):
        """Catches a partially-moving rekey and a non-idempotent one."""
        _seed(
            rekey_env,
            [("m1", LEGACY), ("m2", LEGACY)],
            files=[("f1", "src/a.py", LEGACY)],
        )
        with db_connection(rekey_env.store) as conn:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
        assert counters["memories"] == 2
        with db_connection(rekey_env.store) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM memories WHERE project_id = ?", (LEGACY,)
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT COUNT(*) FROM memories WHERE project_id = ?", (CANONICAL,)
            ).fetchone()[0] == 2

        with db_connection(rekey_env.store) as conn:
            second = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
        assert second["memories"] == 0
        assert second["files_in_place"] == 0
        assert second["files_conflicted"] == 0

    def test_rekeying_into_a_populated_target_is_not_an_error(self, rekey_env):
        """F10: both buckets populated is a NORMAL state, not a conflict.

        The union serves a store holding canonical rows beside a legacy bucket
        every day; the rekey must sum them, not refuse. RED WHEN the target's
        existing rows make the command error or discard either side.
        """
        _seed(
            rekey_env,
            [("l1", LEGACY), ("l2", LEGACY), ("c1", CANONICAL)],
        )
        with db_connection(rekey_env.store) as conn:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
        assert counters["memories"] == 2
        with db_connection(rekey_env.store) as conn:
            canonical = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE project_id = ?", (CANONICAL,)
            ).fetchone()[0]
            legacy = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE project_id = ?", (LEGACY,)
            ).fetchone()[0]
        assert (legacy, canonical) == (0, 3), (
            f"the merge lost or refused rows: legacy={legacy}, canonical={canonical}"
        )

    def test_from_equals_to_is_a_zero_noop(self, rekey_env):
        _seed(rekey_env, [("m1", LEGACY)])
        before = _rows(rekey_env.store)
        with db_connection(rekey_env.store) as conn:
            counters = rekey_project(conn, LEGACY, LEGACY, dry_run=False)
        assert counters["memories"] == 0
        assert _rows(rekey_env.store) == before

    def test_null_project_rows_are_never_moved_and_are_reported(self, rekey_env):
        """Catches a rekey that sweeps the NULL bucket into a project, and a
        report that sums NULL memories and NULL files into one number."""
        _seed(
            rekey_env,
            [("m1", LEGACY), ("n1", None)],
            files=[("f-null", "src/x.py", None)],
        )
        with db_connection(rekey_env.store) as conn:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            assert counters["null_project_memories"] == 1
            assert counters["null_project_files"] == 1
            assert conn.execute(
                "SELECT project_id FROM memories WHERE id = 'n1'"
            ).fetchone()[0] is None


# ---------------------------------------------------------------------------
# C12: one transaction, never a partial move
# ---------------------------------------------------------------------------

class TestRekeyAtomicity:
    def test_a_failure_mid_move_leaves_the_old_partition_intact(self, rekey_env):
        """THE EITHER/OR ARM. A trigger aborts the files UPDATE after the
        memories UPDATE has run; the transaction must roll BOTH back.

        A test that only asserts 'an exception was raised' passes while the
        memories update stays committed — the partial move this guards.
        """
        _seed(
            rekey_env,
            [("m1", LEGACY), ("m2", LEGACY)],
            files=[("f1", "src/a.py", LEGACY)],
        )
        with db_connection(rekey_env.store) as conn:
            conn.execute(
                "CREATE TRIGGER abort_file_update BEFORE UPDATE ON files "
                "BEGIN SELECT RAISE(ABORT, 'injected-mid-rekey'); END"
            )
            conn.commit()

        # ROW IDS, NOT ONLY COUNTS: a move that swapped which rows moved, or
        # duplicated an id across buckets, keeps the counts and would pass a
        # count-only arm. The snapshot is taken AFTER the trigger exists so
        # the comparison is of the data the failed rekey touched.
        before = _rows(rekey_env.store)
        with db_connection(rekey_env.store) as conn, pytest.raises(
            db_sqlite3.IntegrityError
        ):
            rekey_project(conn, LEGACY, CANONICAL, dry_run=False)

        assert _rows(rekey_env.store) == before, (
            "partial move: the row-id snapshot changed across a rolled-back "
            "apply; the crash must leave the old partition byte-identical OR "
            "the new one complete"
        )

    def test_a_failure_in_apply_can_be_retried_to_a_complete_move(self, rekey_env):
        """The other half of the either/or: after removing the fault the same
        call completes, so the failed run left no poisoned state."""
        _seed(
            rekey_env,
            [("m1", LEGACY)],
            files=[("f1", "src/a.py", LEGACY)],
        )
        with db_connection(rekey_env.store) as conn:
            conn.execute(
                "CREATE TRIGGER abort_file_update BEFORE UPDATE ON files "
                "BEGIN SELECT RAISE(ABORT, 'injected'); END"
            )
            conn.commit()
        with db_connection(rekey_env.store) as conn, pytest.raises(
            db_sqlite3.IntegrityError
        ):
            rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
        with db_connection(rekey_env.store) as conn:
            conn.execute("DROP TRIGGER abort_file_update")
            conn.commit()
        with db_connection(rekey_env.store) as conn:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            assert counters["memories"] == 1


# ---------------------------------------------------------------------------
# C7: the UNIQUE(path, project_id) collision
# ---------------------------------------------------------------------------

class TestFilesCollision:
    def test_a_path_on_both_sides_is_merged_and_links_are_repointed(self, rekey_env):
        """Catches the naive UPDATE that violates UNIQUE(path, project_id), and
        a merge that deletes the old row before moving its links."""
        _seed(
            rekey_env,
            [("m1", LEGACY)],
            files=[
                ("f-old", "src/x.py", LEGACY),
                ("f-new", "src/x.py", CANONICAL),
                ("f-other", "src/other.py", CANONICAL),
            ],
            links=[("m1", "f-old")],
            relations=[("f-old", "f-other")],
        )
        with db_connection(rekey_env.store) as conn:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            assert counters["files_conflicted"] == 1
            assert conn.execute(
                "SELECT COUNT(*) FROM files WHERE path = 'src/x.py'"
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT file_id FROM memory_files WHERE memory_id = 'm1'"
            ).fetchone()[0] == "f-new"
            assert conn.execute(
                "SELECT target_file FROM file_relations WHERE source_file = 'f-new'"
            ).fetchone()[0] == "f-other"
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# ---------------------------------------------------------------------------
# C8: vectors follow the rekey, or are reported pending
# ---------------------------------------------------------------------------

def _vec_store(env, monkeypatch_query_only=False):
    """A store with a REAL vec0 table (dimension 3, no embedding model needed).

    `_init_vector_table` is neutralised so `ensure_initialized` cannot size the
    table from the embedding service and drop this fixture's table. The code
    under test is the rekey's delete+insert, not schema sizing.
    """
    conn = db_sqlite3.connect(str(env.store))
    conn.row_factory = db_sqlite3.Row
    from helpers import create_test_schema

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
    conn.execute(
        "INSERT INTO memories "
        "(id, context, project_id, session_id, created_at, updated_at) "
        "VALUES ('m1', 'ctx', ?, 's', '2026-01-01T00:00:00+00:00', "
        "'2026-01-01T00:00:00+00:00')",
        (LEGACY,),
    )
    blob = struct.pack("3f", 0.1, 0.2, 0.3)
    conn.execute(
        "INSERT INTO vec_memories (memory_id, project_id, embedding) VALUES (?, ?, ?)",
        ("m1", LEGACY, blob),
    )
    conn.commit()
    return conn, blob


class TestVectorMove:
    def test_the_embedding_blob_moves_to_the_target_partition(
        self, rekey_env, monkeypatch
    ):
        """Catches a rekey that drops vectors (semantic search silently loses
        the memory) or re-embeds them (wasted work and possible drift)."""
        conn, blob = _vec_store(rekey_env)
        monkeypatch.setattr(db, "_init_vector_table", lambda connection: True)
        try:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            row = conn.execute(
                "SELECT project_id, embedding FROM vec_memories WHERE memory_id = 'm1'"
            ).fetchone()
            assert counters["vectors_moved"] == 1
            assert row["project_id"] == CANONICAL
            assert bytes(row["embedding"]) == blob
        finally:
            conn.close()

    def test_a_memory_without_a_vector_row_is_skipped_not_pending(
        self, rekey_env, monkeypatch
    ):
        """F3: a MISSING vector is a skip, never a pending repair.

        The catch-up repairs a row that exists but could not be moved; it
        cannot repair one that never existed. Counting a skip as pending
        reports a permanent backlog. RED WHEN the `vector is None` branch
        increments `vectors_pending` (or fails the move).
        """
        conn, _blob = _vec_store(rekey_env)
        # A second memory under the same bucket with NO vec row.
        conn.execute(
            "INSERT INTO memories "
            "(id, context, project_id, session_id, created_at, updated_at) "
            "VALUES ('m2', 'ctx2', ?, 's', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (LEGACY,),
        )
        conn.commit()
        monkeypatch.setattr(db, "_init_vector_table", lambda connection: True)
        try:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            assert counters["memories"] == 2
            assert counters["vectors_moved"] == 1
            assert counters["vectors_pending"] == 0, (
                "a memory with no vector row was reported as a pending repair"
            )
        finally:
            conn.close()

    def test_an_unopenable_vector_table_reports_pending_not_success(
        self, rekey_env, monkeypatch
    ):
        """Catches a silent claim that vectors followed when the extension
        could not be loaded; catch-up repairs pending rows."""
        conn, _ = _vec_store(rekey_env)
        monkeypatch.setattr(db, "_init_vector_table", lambda connection: True)
        monkeypatch.setattr(db, "SQLITE_EXTENSIONS_ENABLED", False)
        try:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            assert counters["vectors_moved"] == 0
            assert counters["vectors_pending"] == 1
            assert conn.execute(
                "SELECT project_id FROM vec_memories WHERE memory_id = 'm1'"
            ).fetchone()[0] == LEGACY
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI: show, candidates, dry-run, apply, exit codes
# ---------------------------------------------------------------------------

def _cli(argv, capfd):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)
    captured = capfd.readouterr()
    # Success envelopes go to stdout; error envelopes go to stderr (the
    # envelope discipline keeps diagnostics off the channel a caller parses).
    text = captured.out.strip() or captured.err.strip()
    payload = json.loads(text) if text else None
    return excinfo.value.code, payload


class TestCliSurface:
    def test_show_reports_identity_shape_source_scope_and_counts(
        self, rekey_env, capfd
    ):
        """Catches a show that reports only the canonical count or omits the
        provenance fields a mis-scope diagnosis needs."""
        _seed(
            rekey_env,
            [("l1", LEGACY), ("c1", CANONICAL)],
        )
        # The legacy row is this checkout's own: a recorded session of it
        # wrote it, which is what admits an unverified bucket to the scope.
        # The config root is conftest's per-test sandbox (`tmp_path/home/
        # .claude`); naming it makes the helper refuse to write anywhere else.
        seed_session_evidence(
            rekey_env.tmp_path / "home" / ".claude", rekey_env.repo, "s"
        )
        code, payload = _cli(
            ["project-id", "show", "--db-path", str(rekey_env.store)], capfd
        )
        assert code == 0
        result = payload["result"]
        assert result["project_id"] == CANONICAL
        assert result["id_shape"] == "canonical"
        assert result["source"] == "origin remote"
        assert result["read_scope"] == [CANONICAL, LEGACY]
        # The disclosure shape must carry BOTH ambiguity and exclusion: the
        # operator deciding whether to move a bucket needs to see that the
        # gate did not merge two case-variant ids and that nothing was silently
        # excluded.
        assert {"ambiguous", "excluded", "included", "verified"} <= set(
            result["legacy_scope"]
        )
        assert result["legacy_scope"]["ambiguous"] == []
        assert result["legacy_scope"]["excluded"] is False
        counts = {item["id"]: item["memory_count"] for item in result["candidates"]}
        assert counts == {CANONICAL: 1, LEGACY: 1}

    def test_candidates_lists_every_bucket_with_the_literal_command(
        self, rekey_env, capfd
    ):
        _seed(rekey_env, [("l1", LEGACY), ("f1", "foreign")])
        code, payload = _cli(
            [
                "project-id",
                "rekey",
                "--candidates",
                "--db-path",
                str(rekey_env.store),
            ],
            capfd,
        )
        assert code == 0
        candidates = {item["id"]: item for item in payload["result"]["candidates"]}
        assert set(candidates) == {LEGACY, "foreign"}
        # A command only for this checkout's own candidate; another bucket
        # gets none, because a printed command is read as advice to run it.
        assert candidates[LEGACY]["relation"] == "legacy_candidate"
        assert candidates["foreign"]["relation"] == "other"
        assert candidates["foreign"]["rekey"] is None
        # THE RUNNABLE FORM, against the store the candidates were read from,
        # naming its target.
        argv = shlex.split(candidates[LEGACY]["rekey"])
        assert argv[0] == "python3", argv
        assert argv[1].endswith("cli.py") and Path(argv[1]).is_absolute(), argv
        assert argv[2:6] == [
            "project-id", "rekey", f"--from={LEGACY}", f"--to={CANONICAL}",
        ], argv
        assert f"--db-path={rekey_env.store}" in argv, argv

    def test_rekey_dry_run_then_apply_then_noop(self, rekey_env, capfd):
        _seed(rekey_env, [("l1", LEGACY)])
        code, payload = _cli(
            [
                "project-id",
                "rekey",
                "--from",
                LEGACY,
                "--db-path",
                str(rekey_env.store),
            ],
            capfd,
        )
        assert code == 0 and payload["result"]["dry_run"] is True
        assert payload["result"]["memories"] == 1
        with db_connection(rekey_env.store) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM memories WHERE project_id = ?", (LEGACY,)
            ).fetchone()[0] == 1

        code, payload = _cli(
            [
                "project-id",
                "rekey",
                "--from",
                LEGACY,
                "--apply",
                "--db-path",
                str(rekey_env.store),
            ],
            capfd,
        )
        assert code == 0 and payload["result"]["memories"] == 1
        with db_connection(rekey_env.store) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM memories WHERE project_id = ?", (LEGACY,)
            ).fetchone()[0] == 0

        code, payload = _cli(
            [
                "project-id",
                "rekey",
                "--from",
                LEGACY,
                "--apply",
                "--db-path",
                str(rekey_env.store),
            ],
            capfd,
        )
        assert code == 0 and payload["result"]["memories"] == 0

    def test_a_non_canonical_target_is_bad_input(self, rekey_env, capfd):
        """Catches a rekey that moves rows into a basename bucket."""
        _seed(rekey_env, [("l1", LEGACY)])
        code, payload = _cli(
            [
                "project-id",
                "rekey",
                "--from",
                LEGACY,
                "--to",
                "just-a-folder",
                "--apply",
                "--db-path",
                str(rekey_env.store),
            ],
            capfd,
        )
        assert code == 2
        assert payload["ok"] is False

    def test_no_canonical_target_refuses_with_exit_1(self, rekey_env, capfd, monkeypatch):
        """A detected BASENAME is the bucket to leave, never a target."""
        _seed(rekey_env, [("l1", LEGACY)])
        umbrella = rekey_env.tmp_path / "umbrella"
        umbrella.mkdir()
        monkeypatch.chdir(umbrella)
        code, payload = _cli(
            [
                "project-id",
                "rekey",
                "--from",
                LEGACY,
                "--db-path",
                str(rekey_env.store),
            ],
            capfd,
        )
        assert code == 1
        assert payload["ok"] is False

    def test_dry_run_and_apply_are_mutually_exclusive(self, rekey_env, capfd):
        _seed(rekey_env, [("l1", LEGACY)])
        code, _payload = _cli(
            [
                "project-id",
                "rekey",
                "--from",
                LEGACY,
                "--dry-run",
                "--apply",
                "--db-path",
                str(rekey_env.store),
            ],
            capfd,
        )
        assert code == 2

# ---------------------------------------------------------------------------
# Review finding B6-T: the incident flow, end to end
# ---------------------------------------------------------------------------

class TestReviewB6IncidentFlow:
    def test_show_candidates_then_rekey_makes_the_old_name_rows_reachable(
        self, tmp_path, monkeypatch, capfd
    ):
        """B6-T: the reported incident as an end-to-end flow.

        Rows live under the OLD folder name (`PACT-prompt`) while the current
        checkout is `PACT-Plugin`, so the derived read union cannot reach
        them. `show --candidates` must name the bucket, `rekey --apply` must
        move it, and the canonical read must then return the rows with the
        scope collapsed.

        RED WHEN: `show` omits the bucket, the rekey is a no-op, or the
        post-rekey read still needs the union (the last would mean the rows
        did not move).
        """
        assert_outside_repository(tmp_path)
        repo = make_repo(
            tmp_path / "PACT-Plugin",
            remote="https://github.com/syn-labs/pact-plugin.git",
        )
        monkeypatch.chdir(repo)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        memory_api._LEGACY_SCOPE_CACHE.clear()
        env = SimpleNamespace(
            repo=repo, store=tmp_path / "incident.db", tmp_path=tmp_path
        )
        _seed(env, [("old1", "PACT-prompt"), ("old2", "PACT-prompt")])

        memory = PACTMemory(session_id="s", db_path=env.store)
        assert memory.list(limit=10) == [], (
            "the pre-rekey read reached the old-name bucket; this fixture is "
            "not the incident shape"
        )

        code, payload = _cli(
            ["project-id", "show", "--db-path", str(env.store)], capfd
        )
        assert code == 0
        candidates = {item["id"]: item for item in payload["result"]["candidates"]}
        assert "PACT-prompt" in candidates, (
            f"show --candidates did not name the old bucket: {candidates}"
        )
        assert candidates["PACT-prompt"]["memory_count"] == 2

        # The bucket is NOT this checkout's own folder name, so the operator
        # names the target: a defaulted --to is refused for a foreign bucket.
        code, payload = _cli(
            [
                "project-id",
                "rekey",
                "--from",
                "PACT-prompt",
                "--to",
                CANONICAL,
                "--apply",
                "--db-path",
                str(env.store),
            ],
            capfd,
        )
        assert code == 0
        assert payload["result"]["memories"] == 2

        memory_api._LEGACY_SCOPE_CACHE.clear()
        memory = PACTMemory(session_id="s", db_path=env.store)
        assert sorted(item.id for item in memory.list(limit=10)) == [
            "old1",
            "old2",
        ], "the canonical read did not return the moved rows"
        assert memory._read_project_scopes() == (CANONICAL,), (
            "the scope did not collapse after the rekey"
        )


# ---------------------------------------------------------------------------
# C13: the two counters no other arm asserts
# ---------------------------------------------------------------------------

class TestRekeyCounterMeaning:
    """`links_repointed` and `relations_repointed` have documented meanings.

    A counter nobody asserts is free to drift, and a drifted counter is worse
    than an absent one: it reports a move that did not happen, or hides one
    that did.
    """

    def test_links_repointed_counts_every_examined_memory_link(self, rekey_env):
        """Two memories linked to the retiring file row.

        `links_repointed` is the number of old links EXAMINED (the documented
        meaning since `links_conflicted` was added), so with no conflicting
        target rows both are examined and both are re-pointed. RED WHEN the
        counter reports the surviving link rows instead of the links moved
        (e.g. `+= 1`).
        """
        _seed(
            rekey_env,
            [("m1", LEGACY), ("m2", LEGACY)],
            files=[
                ("f-old", "src/x.py", LEGACY),
                ("f-new", "src/x.py", CANONICAL),
            ],
            links=[("m1", "f-old"), ("m2", "f-old")],
        )
        with db_connection(rekey_env.store) as conn:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            assert counters["links_repointed"] == 2, counters
            linked = [
                row[0]
                for row in conn.execute("SELECT file_id FROM memory_files")
            ]
        assert linked == ["f-new", "f-new"], (
            "the counter claimed two repointed links but the rows did not "
            f"follow: {linked!r}"
        )

    def test_links_conflicted_counts_a_dropped_relationship(self, rekey_env):
        """The documented loss: the target already names the pair, differently.

        `memory_files` is keyed by `(memory_id, file_id)`, so the target's row
        wins and the retiring link's RELATIONSHIP is dropped. `INSERT OR
        IGNORE` used to hide that; `links_conflicted` makes it visible while
        `links_repointed` still counts the link as examined. RED WHEN the
        conflict count stays 0 or the target's relationship is overwritten.
        """
        _seed(
            rekey_env,
            [("m1", LEGACY)],
            files=[
                ("f-old", "src/x.py", LEGACY),
                ("f-new", "src/x.py", CANONICAL),
            ],
            links=[("m1", "f-old")],  # default relationship: 'modified'
        )
        with db_connection(rekey_env.store) as conn:
            # The target already names the same pair with a DIFFERENT type.
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id, relationship) "
                "VALUES ('m1', 'f-new', 'created')"
            )
            conn.commit()
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            assert counters["links_repointed"] == 1, counters
            assert counters["links_conflicted"] == 1, counters
            rows = [
                tuple(row)
                for row in conn.execute(
                    "SELECT file_id, relationship FROM memory_files "
                    "WHERE memory_id = 'm1'"
                )
            ]
        assert rows == [("f-new", "created")], (
            "the target's relationship did not win, or the dropped link "
            f"survived: {rows!r}"
        )

    def test_relations_repointed_counts_incident_edges_not_insert_rowcounts(
        self, rekey_env
    ):
        """The documented case: two edges COLLAPSE into one row and report 2.

        `f-old` and `f-new` each carry an edge to the other. Re-pointing both
        ends onto `f-new` yields ONE self-loop row, but TWO edges moved, and
        the counter must report the edges. Summing the two INSERT rowcounts
        would report 3 (the second INSERT is `INSERT OR IGNORE`), which is the
        drift this arm exists to catch.
        """
        _seed(
            rekey_env,
            [("m1", LEGACY)],
            files=[
                ("f-old", "src/x.py", LEGACY),
                ("f-new", "src/x.py", CANONICAL),
            ],
            relations=[("f-old", "f-new"), ("f-new", "f-old")],
        )
        with db_connection(rekey_env.store) as conn:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            assert counters["relations_repointed"] == 2, counters
            rows = [
                tuple(row)
                for row in conn.execute(
                    "SELECT source_file, target_file, relationship "
                    "FROM file_relations"
                )
            ]
        assert rows == [("f-new", "f-new", "imports")], (
            "the two edges did not collapse to the single documented self-"
            f"loop: {rows!r}"
        )
