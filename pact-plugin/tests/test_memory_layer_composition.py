"""
Location: pact-plugin/tests/test_memory_layer_composition.py

Summary: Arms for what the memory-layer changes do TOGETHER. Each individual
fix carries arms its own author proved by mutation, so nothing here re-tests
one fix alone. Every arm below needs TWO OR MORE of the changes to be present
before it can pass, and each one names the mutants that redden it.

WHY A SEPARATE MODULE. The four fixes landed in four commits by two authors,
and each author tested one side of every pair. A pair is therefore untested by
construction rather than by oversight. The store scope in particular did not
exist when the arms for the write path and the create guard were written, and
those arms hand in their own connection, so none of them resolves a store at
all.

THE FOUR PAIRS THIS MODULE CLOSES.

  1. THE WINDOW AND THE WRITE PATH. The window author was FORBIDDEN from
     writing an arm that re-embeds a record which already holds a vector: at
     that time the write path issued `INSERT OR REPLACE` against a `vec0`
     table, which honours no conflict clause, so a second write raised. Their
     arms therefore assert at the CALL BOUNDARY against a recording stand-in,
     and the write-path arms patch the generator, so NO arm anywhere drove the
     encoder and the store together. `TestTheWindowReachesTheStoredVector` is
     that arm. It is the composition the ordering constraint existed to
     protect.

  2. THE STORE SCOPE AND THE VECTOR WRITE. The row and the vector are written
     through two different statements inside one call. Before the resolver
     change, six sites passed `self._db_path` explicitly; they now pass
     nothing and resolve through a `ContextVar` that the method decorator
     binds. If a later edit drops that decorator from `save` or `update`, the
     ROW lands in the caller store and the VECTOR lands in the default one,
     and the two halves of one record separate with nothing to report it.

  3. THE CREATE GUARD AND THE VECTOR WRITE. The guard arms prove no ROW is
     written when the guard raises. Nothing proves no VECTOR is written. A
     guard that refused after the embedding step would leave a vector with no
     record, which is invisible to every row-level assertion.

  4. THE STDERR GUARD AND THE STORE SCOPE. The CLI nests them, stderr guard
     outermost. Their orders are pinned by an AST assertion, which cannot see
     whether the two unwind correctly. A scope that outlives its block leaks
     into the next call in the same interpreter.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers import create_test_schema

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

import sqlite_vec  # noqa: E402

from scripts import cli  # noqa: E402
from scripts.config import _STORE_DB_PATH, resolve_db_path  # noqa: E402
from scripts.embeddings import (  # noqa: E402
    EMBEDDING_DIM,
    EMBEDDING_MAX_TOKENS,
    MODEL_NAME,
)
from scripts.memory_api import PACTMemory  # noqa: E402


# The encoder default this feature ended. It is written here, and ONLY here,
# as the value the arm must separate the configured window FROM. It is not a
# second copy of the window: the window comes from the imported constant.
ENCODER_DEFAULT_WINDOW = 512


def make_vec_table(conn, dim=EMBEDDING_DIM):
    """Build the production vector table shape at the REAL embedding width.

    The write-path arms use an 8-wide table because their vectors are chosen
    by hand. An arm that drives the encoder must use the width the encoder
    emits, or the insert fails on the width rather than on the behaviour
    under test.
    """
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0("
        "  memory_id TEXT PRIMARY KEY,"
        "  project_id TEXT PARTITION KEY,"
        "  embedding float[{0}]"
        ")".format(dim)
    )
    conn.commit()


def stored_floats(conn, memory_id):
    """Read a stored vector back as floats, or None when no row is present."""
    row = conn.execute(
        "SELECT embedding FROM vec_memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    if row is None:
        return None
    raw = bytes(row[0])
    return list(struct.unpack("{0}f".format(len(raw) // 4), raw))


def long_record(marker="alpha"):
    """A record whose text is long enough to separate the two windows.

    THE LENGTH IS THE WHOLE POINT AND AN ARM ASSERTS IT RATHER THAN TRUSTS IT.
    A short record encodes identically at 512 tokens and at 2048, so an arm
    built on one would pass whether or not the window reached the encoder.
    That is the vacuous green this module exists to avoid, so
    `test_the_two_windows_separate_on_this_text` is a required companion and
    not a nicety.
    """
    sentence = (
        "The semantic index truncated each record at the encoder default "
        "window and reported nothing about the loss, so a median record "
        "reached the index at roughly its first quarter. "
    )
    return {
        "context": "{0} {1}".format(marker, sentence * 120),
        "project_id": "test-project",
    }


@pytest.fixture
def encoder():
    """The real encoder, loaded once per test that asks for it.

    A stand-in cannot answer this module's question. The subject is what the
    ENCODER returns for a given window, so a recording double would assert
    only that the test passed its own argument to itself.
    """
    from model2vec import StaticModel

    # force_download=False for the reason given at the production call site in
    # embeddings.py: model2vec's default of True makes ten metadata round-trips
    # to revalidate a model already on disk, and each is a network call that can
    # block in an unbounded SSL read. This fixture hung a full suite doing
    # exactly that. It still loads the REAL encoder -- which is the whole point
    # of the docstring above -- it simply stops revalidating first.
    return StaticModel.from_pretrained(MODEL_NAME, force_download=False)


@pytest.fixture
def vec_conn(tmp_path):
    """A store with the memories table and a real vec0 table at the real width."""
    connection = sqlite3.connect(str(tmp_path / "composition.db"))
    connection.row_factory = sqlite3.Row
    create_test_schema(connection)
    make_vec_table(connection)
    yield connection
    connection.close()


@pytest.fixture
def mem():
    return PACTMemory(project_id="test-project", session_id="test-session")


# ---------------------------------------------------------------------------
# PAIR 1. The window and the write path. THE ARM THAT WAS DEFERRED.
# ---------------------------------------------------------------------------

@pytest.mark.requires_embedding_backend
class TestTheWindowReachesTheStoredVector:
    """The composition the ordering constraint protected.

    TWO CHANGES MUST BOTH BE PRESENT FOR THIS TO PASS, and that is what makes
    it a composition arm rather than a third copy of an existing one:

      MUTANT A, the window keyword dropped from the encode call. The stored
      vector then equals the encoding at the encoder default, and
      `test_a_re_embed_stores_the_windowed_encoding` goes RED on the equality
      it asserts LAST.

      MUTANT B, the write path returned to `INSERT OR REPLACE` with no drop.
      The second write raises on a row that is present, the handler reports
      `fault`, and the same arm goes RED on the status it asserts FIRST.

    NEITHER EXISTING ARM CATCHES BOTH. The window arms assert at the call
    boundary and reach no store at all. The write-path arms patch the
    generator, so the value they store carries no window.
    """

    def test_the_two_windows_separate_on_this_text(self, encoder):
        """NON-VACUITY GATE for the arm below. Run it first and read it first.

        If this record encoded identically at the two windows, the arm below
        would pass against a dropped keyword and certify the defect as fixed.
        The gate makes the fixture length a measured property rather than an
        assumption about how many characters a token is worth.
        """
        from scripts.embeddings import generate_embedding_text
        text = generate_embedding_text(long_record())
        at_window = encoder.encode(
            [text], max_length=EMBEDDING_MAX_TOKENS
        )[0].tolist()
        at_default = encoder.encode(
            [text], max_length=ENCODER_DEFAULT_WINDOW
        )[0].tolist()
        assert at_window != at_default, (
            "the fixture text is too short to separate a window of "
            "{0} from the encoder default of {1}, so every arm built on it "
            "is vacuous".format(EMBEDDING_MAX_TOKENS, ENCODER_DEFAULT_WINDOW)
        )

    def test_a_re_embed_stores_the_windowed_encoding(self, mem, vec_conn, encoder):
        """A record that HOLDS a vector, re-embedded, through the write path."""
        from scripts.embeddings import generate_embedding_text

        first = long_record("alpha")
        second = long_record("beta")

        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True):
            assert mem._store_embedding(vec_conn, "mem-1", first) is None
            seeded = stored_floats(vec_conn, "mem-1")
            # CONTROL. Without a first vector this is not a REPLACE, and the
            # arm below would prove only that a first write works, which the
            # write-path module tests already.
            assert seeded is not None, "the record must hold a vector first"

            # THE RE-EMBED. Against the unfixed write path this raises inside
            # the handler and returns "fault".
            assert mem._store_embedding(vec_conn, "mem-1", second) is None

        replaced = stored_floats(vec_conn, "mem-1")
        assert replaced is not None
        assert replaced != seeded, "the re-embed did not replace the vector"

        text = generate_embedding_text(second)
        at_window = encoder.encode(
            [text], max_length=EMBEDDING_MAX_TOKENS
        )[0].tolist()
        at_default = encoder.encode(
            [text], max_length=ENCODER_DEFAULT_WINDOW
        )[0].tolist()

        # THE WINDOW ASSERTION. It takes its value from the module constant,
        # so a deliberate change to the window moves both sides together and
        # this arm stays green. The FLOOR arm in the window module is what
        # guards the value; this one guards that the value reaches the store.
        assert replaced == pytest.approx(at_window, abs=1e-6)
        assert replaced != pytest.approx(at_default, abs=1e-6), (
            "the stored vector equals the encoding at the encoder default, "
            "so the window did not reach the encoder on the write path"
        )


# ---------------------------------------------------------------------------
# PAIR 2. The store scope and the vector write.
# ---------------------------------------------------------------------------

class TestTheScopeCarriesTheRowAndTheVectorTogether:
    """The row and the vector must land in the SAME store.

    MUTANT: remove `@_with_store_scope` from `save`. The row then goes to the
    store the caller named and the vector goes to the default one, or the
    save fails outright. Either way this arm reddens.

    WHY THE DEFAULT STORE IS THE CONTROL AND NOT AN AFTERTHOUGHT. Under the
    harness `PACT_TEST_MEMORY_DIR` points the default at a tmp_path child, so
    a split record is written somewhere real and stays invisible to any arm
    that reads only the store it asked for. This arm reads BOTH.
    """

    def test_a_scoped_save_writes_the_two_halves_to_the_named_store(self, tmp_path):
        scoped = tmp_path / "scoped" / "named.db"
        scoped.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(scoped))
        conn.row_factory = sqlite3.Row
        create_test_schema(conn)
        make_vec_table(conn)
        conn.close()

        default_dir = Path(os.environ["PACT_TEST_MEMORY_DIR"])
        default_db = default_dir / "memory.db"

        memory = {"context": "a short embeddable record", "project_id": "test-project"}
        vector = [0.125] * EMBEDDING_DIM

        mem = PACTMemory(
            project_id="test-project", session_id="s", db_path=scoped
        )
        with patch("scripts.database.ensure_initialized"), \
             patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding", return_value=vector):
            memory_id = mem.save(memory, include_tracked=False, sync_to_claude=False)

        check = sqlite3.connect(str(scoped))
        try:
            rows = check.execute(
                "SELECT COUNT(*) FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()[0]
            assert rows == 1, "the row did not reach the named store"
            check.enable_load_extension(True)
            sqlite_vec.load(check)
            vectors = check.execute(
                "SELECT COUNT(*) FROM vec_memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()[0]
            assert vectors == 1, (
                "the vector did not reach the named store, so the row and its "
                "vector were split across two stores"
            )
        finally:
            check.close()

        # THE OTHER HALF OF THE PROPERTY. A vector in the named store does not
        # by itself prove none was written to the default one.
        assert not default_db.exists(), (
            "the default store file was created by a scoped save, at "
            "{0}".format(default_db)
        )


# ---------------------------------------------------------------------------
# PAIR 3. The create guard and the vector write.
# ---------------------------------------------------------------------------

class TestARefusedSaveLeavesNoOrphanVector:
    """A refused save must leave NO row AND NO vector.

    The guard arms in the ingress module assert the absence of a ROW. A guard
    that refused AFTER the embedding step would satisfy every one of them and
    leave a vector behind with no record to explain it, which no row-level
    assertion can see.

    MUTANT: move the `_normalize_list_field` call in `create_memory` to after
    the insert and the embedding step. The row arms stay green on the
    rollback and this arm reddens on the orphan.
    """

    def test_a_guarded_refusal_writes_neither_half(self, tmp_path):
        store = tmp_path / "refusal.db"
        conn = sqlite3.connect(str(store))
        conn.row_factory = sqlite3.Row
        create_test_schema(conn)
        make_vec_table(conn)
        conn.close()

        vector = [0.25] * EMBEDDING_DIM
        mem = PACTMemory(project_id="test-project", session_id="s", db_path=store)

        def counts():
            c = sqlite3.connect(str(store))
            try:
                c.enable_load_extension(True)
                sqlite_vec.load(c)
                return (
                    c.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
                    c.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0],
                )
            finally:
                c.close()

        with patch("scripts.database.ensure_initialized"), \
             patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding", return_value=vector):

            # POSITIVE CONTROL. A well-formed save must write BOTH halves, or
            # the refusal below proves nothing: a store where no save ever
            # writes a vector reports zero orphans for the wrong reason.
            mem.save(
                {"context": "a control record", "reasoning_chains": ["a proper item"]},
                include_tracked=False,
                sync_to_claude=False,
            )
            assert counts() == (1, 1), "the control save wrote no vector"

            before = counts()
            with pytest.raises(ValueError):
                mem.save(
                    {"context": "a refused record", "reasoning_chains": "a bare string"},
                    include_tracked=False,
                    sync_to_claude=False,
                )
            assert counts() == before, (
                "a refused save changed the store: it wrote a row, a vector, "
                "or both"
            )


# ---------------------------------------------------------------------------
# PAIR 4. The stderr guard and the store scope.
# ---------------------------------------------------------------------------

class TestTheStderrGuardAndStoreScopeUnwindWithoutLeaking:
    """The nested guards must each release, on the success and the raise path.

    THE AST ORDER ASSERTION CANNOT SEE THIS. It proves the refusal guard is
    written before the scope entry. It says nothing about whether the scope
    releases, because a leak is a runtime property of the unwind.

    MUTANT: replace the body of `store_scope` with a bare
    `_STORE_DB_PATH.set(Path(db_path))` and no reset. Both arms redden.

    A LEAK IS NOT COSMETIC. The CLI runs one command per process, so nothing
    in production reads a leaked binding. An in-process caller does, and the
    suite is the largest one: a scope surviving one test binds the store for
    the next.
    """

    def _run_main(self, argv, handler):
        commands = dict(cli._COMMANDS)
        commands["status"] = handler
        with patch.object(cli, "_COMMANDS", commands):
            cli.main(argv)

    def test_the_scope_is_released_after_a_successful_command(self, memory_store):
        # THE STORE MUST BE PRESENT OR THIS ARM STOPS AT THE BOUNDARY. `main`
        # refuses a `--db-path` naming a store that is absent, BEFORE it reaches
        # a handler, so an absent path would end the call before the probe below
        # ever runs and this arm would measure the refusal rather than the scope.
        target = memory_store("unwind.db")
        seen = {}

        def probe(args, db_path=None):
            # INSIDE the handler the stderr guard and the store scope are active,
            # so the resolver must
            # report the caller file. A green here with a red assertion below
            # would mean the scope binds and does not release.
            seen["resolved"] = resolve_db_path()

        assert _STORE_DB_PATH.get() is None, "a previous test leaked a scope"
        # The probe returns rather than calling `_success`, so `main` returns
        # normally. That is the SUCCESS path of the stderr guard and the store
        # scope, and it is the
        # path on which a missing reset leaks quietly rather than loudly.
        self._run_main(["status", "--db-path", str(target)], probe)

        assert seen["resolved"] == target, (
            "the scope did not reach the handler through the stderr guard"
        )
        assert _STORE_DB_PATH.get() is None, (
            "the store scope outlived the command"
        )

    def test_the_scope_is_released_when_the_handler_raises(self, memory_store):
        # THE STORE MUST BE PRESENT, AND HERE THAT IS A VACUITY GUARD RATHER
        # THAN A PRECONDITION. This arm asserts a SystemExit, and the boundary
        # refusal raises SystemExit too. With an absent path the arm passes
        # GREEN while the exploding handler below never runs, so it would prove
        # nothing about unwinding. The `raised` flag makes that visible.
        target = memory_store("unwind-raise.db")
        raised = {}

        def exploding(args, db_path=None):
            raised["yes"] = True
            raise RuntimeError("the handler failed inside the stderr guard and the store scope")

        assert _STORE_DB_PATH.get() is None, "a previous test leaked a scope"
        # `main` converts the exception into the error envelope and exits 2.
        with pytest.raises(SystemExit):
            self._run_main(["status", "--db-path", str(target)], exploding)

        assert raised.get("yes"), (
            "the handler never ran, so the SystemExit above came from somewhere "
            "else and this arm did not measure the unwind it claims to"
        )

        assert _STORE_DB_PATH.get() is None, (
            "the store scope outlived a failing command, so a later call in "
            "this interpreter resolves to the wrong store"
        )
