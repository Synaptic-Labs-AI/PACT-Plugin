"""One expression yields the store file path, and each consumer reaches it.

WHAT THIS FILE PINS, and why the sibling witness suite does not cover it.
`test_memory_store_isolation.py` proves the store resolves AWAY from the
operator's real store under test. It says nothing about a caller that names its
own store, and that was the open hole: the tree held THREE expressions that
derived the store file, and they disagreed inside a single invocation.

  1. `config.compute_db_path` composed the file name.
  2. `database.get_db_path` composed it a second time.
  3. `database.get_connection` had a third rule, `db_path or get_db_path()`,
     which made the caller path a RIVAL of the resolver rather than an input.

With `--db-path /tmp/x.db`, rule 3 opened `/tmp/x.db` while every layer that
asked the resolver got `<default>/memory.db`. The connection and the resolver
named different files in one command. Two of the three derivations are gone,
and a `ContextVar` store scope carries the caller path THROUGH the survivor.

WHICH ARMS CAN GO RED, stated because an arm that cannot fail retires the
question it appears to answer:

  RED ON THE UNPATCHED TREE, by behaviour a reader can reproduce:
    - the scoped search arm, the scoped status arm, the three setup arms,
      the two single-derivation arms, the guard-order arm.
  RED AGAINST A MUTANT ONLY, and each such arm names its mutant in its own
  docstring:
    - the inheritance arms (the unconditional `set()` spelling),
    - the connection-factory arm (a added `isolation_level`),
    - the refusal arm (a `raise` added to the resolver).
  NON-REGRESSION, green before and after, and LABELLED AS SUCH in place:
    - the default-resolution arm and the creates-nothing arm.

WHAT OUTRANKS EVERYTHING HERE. The scope is a REDIRECT and never a REFUSAL.
`archive_pin --index N` is a production command that passes no path on purpose
and must reach the real store. An isolation that works by making the default
unreachable would refuse an honest user command, so those arms rank above the
isolation arms and `TestTheScopeIsARedirectAndNotARefusal` carries them.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

SCRIPTS_PARENT = Path(__file__).parent.parent / "skills" / "pact-memory"

from scripts import cli as cli_module  # noqa: E402
from scripts import memory_api as memory_api_module  # noqa: E402
from scripts.config import (  # noqa: E402
    DB_FILENAME,
    get_memory_dir,
    resolve_db_path,
    store_scope,
)
from scripts import search as memory_search  # noqa: E402
from scripts.database import db_connection  # noqa: E402
from scripts.database import (  # noqa: E402
    ensure_initialized,
    list_memories,
    search_memories_by_text,
)
from scripts.graph import add_file_relation, get_file_id  # noqa: E402
from scripts.graph import track_file  # noqa: E402
from scripts.memory_api import PACTMemory  # noqa: E402
from scripts.working_memory import SyncResult  # noqa: E402

_TOKEN = "REMOVED-WORKTREE-ROW-TOKEN"
_REKEY_TOKEN = "REKEY-CROSS-PROJECT-TOKEN"

_SCRIPTS_DIR = SCRIPTS_PARENT / "scripts"


# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------


def _package_modules():
    """Every module of the store package. The population for the AST arms.

    The population is the PACKAGE, not the repository. Test files and skill
    documentation mention the store file name in prose and are out of scope: a
    count taken over the tree would report a failure on a correct fix, which is
    as useless as an arm that cannot fail and worse in one respect, because
    somebody would change the code to satisfy it.
    """
    return sorted(_SCRIPTS_DIR.rglob("*.py"))


def _parse(module: Path) -> ast.Module:
    return ast.parse(module.read_text(encoding="utf-8"), filename=str(module))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"no function named {name!r}; the instrument is aimed wrong")


def _enclosing_function(tree: ast.Module, lineno: int) -> str:
    """Return the innermost function holding `lineno`, or "<module>"."""
    best = ("<module>", -1)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if node.lineno > best[1]:
                    best = (node.name, node.lineno)
    return best[0]


def _connection_file(conn) -> Path:
    """Return the file the connection has open, read from SQLite itself."""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row["name"] == "main":
            return Path(row["file"])
    raise AssertionError("no main database on this connection")


# ---------------------------------------------------------------------------
# Inheritance: the one line the whole design turns on
# ---------------------------------------------------------------------------


class TestAPathlessCallInheritsTheScope:
    """`store_scope(None)` must INHERIT. It must not reset to the default.

    THE MUTANT THESE ARMS EXIST FOR, and it is the natural spelling. Replace the
    body of `store_scope` with one unconditional
    `token = _STORE_DB_PATH.set(db_path)`. That writes None, which IS the
    default, so an inner pathless call clears an outer binding for the length of
    its block.

    NO ARM IN THE SHIPPED SUITE CATCHES THAT, and the reason is worth holding:
    those arms set no OUTER scope, so a reset from None to None changes nothing
    they can observe. Only a NESTED arm separates the two spellings.
    """

    def test_the_resolver_inherits_through_a_pathless_scope(self, tmp_path):
        """RED against the unconditional-set mutant."""
        outer = tmp_path / "outer" / "outer.db"

        with store_scope(outer):
            # CONTROL, POSITIVE: the outer binding took effect at all, so a
            # failure below is inheritance and not a dead scope.
            assert resolve_db_path() == outer

            with store_scope(None):
                assert resolve_db_path() == outer, (
                    "a pathless inner call cleared the enclosing store. The "
                    "search and catch-up layers open db_connection() with no "
                    "argument, so this sends every scoped read back to the "
                    "default store with nothing raised"
                )

        # CONTROL, NEGATIVE: the binding is released at the end of the block,
        # so the arm above cannot pass through a leaked global.
        assert resolve_db_path() != outer

    def test_the_directory_inherits_through_a_pathless_scope(self, tmp_path):
        """RED against the unconditional-set mutant, on the directory leg."""
        outer = tmp_path / "outer" / "outer.db"

        with store_scope(outer):
            with store_scope(None):
                assert get_memory_dir() == outer.parent

    def test_a_pathless_connection_opens_the_enclosing_store(self, tmp_path):
        """The behavioural form of the arm above, read from SQLite itself.

        RED against the unconditional-set mutant. `db_connection()` with no
        argument is exactly what `search.py`, `graph.py` and
        `embedding_catchup.py` issue, and not one of them accepts a path.
        """
        outer = tmp_path / "outer" / "outer.db"
        outer.parent.mkdir(parents=True)

        with store_scope(outer):
            with db_connection() as conn:
                assert _connection_file(conn) == outer

    def test_an_inner_path_wins_and_the_outer_one_returns(self, tmp_path):
        """A real inner path overrides, and the outer binding is restored."""
        outer = tmp_path / "outer" / "outer.db"
        inner = tmp_path / "inner" / "inner.db"

        with store_scope(outer):
            with store_scope(inner):
                assert resolve_db_path() == inner
            assert resolve_db_path() == outer


# ---------------------------------------------------------------------------
# The scoped API reads the scoped store
# ---------------------------------------------------------------------------


class TestTheScopedApiReadsTheScopedStore:
    """`PACTMemory(db_path=X)` must READ X, and not only write to it.

    NO ARM HERE DRIVES THE SAVE PATH, deliberately. `__init__` stored the path
    and six of the eight methods forwarded it to their own connection, so a save
    ISOLATED BEFORE THIS CHANGE. An arm built on it passes against the unpatched
    tree, proves nothing, and retires the question. The leak lived in the layers
    that accept no path: search, the lazy-init side path, and the status report.
    """

    def test_search_reads_the_scoped_store(self, tmp_path):
        """RED ON THE UNPATCHED TREE. `search` returned default-store rows.

        `PACTMemory.search` calls `graph_enhanced_search`, which opens
        `db_connection()` with no argument, and no function in `search.py`,
        `graph.py`, `embedding_catchup.py` or `memory_init.py` accepts a
        `db_path` to forward. So the query ran against the default store and
        returned plausible rows from it. Nothing raised.

        The embedding backend is stubbed out on purpose: this arm is about WHICH
        FILE the read opens, and a live model would add a download to it. With
        no vector the search falls through to the keyword route, which reads the
        same connection.
        """
        scoped = tmp_path / "scoped-store" / "scoped.db"
        scoped.parent.mkdir(parents=True)
        token = "quatrefoil" + uuid4().hex
        project = "store-scope-arm"

        with patch.object(memory_api_module, "_ensure_ready"), patch.object(
            memory_api_module, "generate_embedding", return_value=None
        ):
            api = memory_api_module.PACTMemory(project_id=project, db_path=scoped)
            memory_id = api.save(
                {"context": f"a record about {token}"}, sync_to_claude=False
            )
            results = api.search(token, sync_to_claude=False)

        # CONTROL, POSITIVE: the record landed in the scoped store, so an empty
        # result below is a read fault and not a write fault.
        with db_connection(scoped) as conn:
            rows = conn.execute(
                "SELECT id FROM memories WHERE id = ?", (memory_id,)
            ).fetchall()
        assert len(rows) == 1, "the save did not reach the scoped store"

        # CONTROL, NEGATIVE: the token is unique to this run, so no other store
        # can supply it. This is what makes the assertion below discriminating
        # rather than merely non-empty.
        default_store = get_memory_dir() / DB_FILENAME
        if default_store.exists():
            with db_connection(default_store) as conn:
                stray = conn.execute(
                    "SELECT id FROM memories WHERE context LIKE ?", (f"%{token}%",)
                ).fetchall()
            assert stray == [], "the token leaked into the default store"

        assert [m.context for m in results] == [f"a record about {token}"], (
            "the search read a store other than the one the caller named"
        )

    def test_status_reports_the_scoped_store(self, tmp_path):
        """RED ON THE UNPATCHED TREE. The report named the default store.

        The counts came from the caller store and the `db_path` field came from
        the default one, so the envelope disagreed with itself.
        """
        scoped = tmp_path / "status-store" / "status.db"
        scoped.parent.mkdir(parents=True)

        with patch.object(memory_api_module, "_ensure_ready"):
            status = memory_api_module.PACTMemory(
                project_id="store-scope-arm", db_path=scoped
            ).get_status()

        assert status["db_path"] == str(scoped)


# ---------------------------------------------------------------------------
# setup: three legs, one scope
# ---------------------------------------------------------------------------


class TestSetupIsolatesOnAllThreeLegs:
    """`setup --db-path X` honoured X on ONE leg of three.

    It wrote the schema to X, CREATED the default directory, and then REPORTED
    the default directory. That is worse than a plain failure to isolate,
    because the report agreed with the incorrect leg. All three legs reach
    `get_memory_dir()`, so one scope repairs the three and `setup_memory.py`
    needs no edit at all.
    """

    def _run_setup(self, db_path: Path):
        with pytest.raises(SystemExit) as exc:
            cli_module.main(["setup", "--db-path", str(db_path)])
        return exc.value.code

    def test_setup_creates_the_scoped_directory(self, tmp_path, capsys):
        """RED ON THE UNPATCHED TREE, and it fails outright there.

        The directory leg took no argument, so it created the default directory
        and left the parent of X absent. The schema leg then opened X against a
        parent that did not exist and raised, which `ensure_initialized` caught
        and reported as a setup failure.
        """
        scoped = tmp_path / "absent-parent" / "setup.db"
        assert not scoped.parent.exists()

        code = self._run_setup(scoped)
        capsys.readouterr()

        assert code == 0
        assert scoped.parent.is_dir()
        assert scoped.exists()

    def test_setup_does_not_create_the_default_directory(self, tmp_path, capsys):
        """RED ON THE UNPATCHED TREE. The directory leg created the default one.

        The parent of X exists here on purpose, so the schema leg succeeds
        either way and this arm isolates the DIRECTORY leg by itself.
        """
        scoped = tmp_path / "present-parent" / "setup.db"
        scoped.parent.mkdir(parents=True)
        default_dir = get_memory_dir()

        # CONTROL: the default directory is absent before the call, so its
        # presence afterwards can only come from this command.
        assert not default_dir.exists()

        code = self._run_setup(scoped)
        capsys.readouterr()

        assert code == 0
        assert not default_dir.exists(), (
            "setup created the default store directory while the caller named "
            "another store"
        )

    def test_setup_reports_the_scoped_directory(self, tmp_path, capsys):
        """RED ON THE UNPATCHED TREE. The report leg named the default store."""
        scoped = tmp_path / "reported" / "setup.db"
        scoped.parent.mkdir(parents=True)

        code = self._run_setup(scoped)
        envelope = json.loads(capsys.readouterr().out)

        assert code == 0
        assert envelope["ok"] is True
        assert envelope["result"]["details"]["paths"]["memory_dir"] == str(
            scoped.parent
        )


# ---------------------------------------------------------------------------
# The cardinal constraint: redirect, never refuse
# ---------------------------------------------------------------------------


class TestTheScopeIsARedirectAndNotARefusal:
    """CARDINAL. A refused good-faith command damages work immediately.

    `archive_pin --index N` passes no path on purpose and must reach the real
    store. These arms rank above every isolation arm in this file.
    """

    def test_an_unscoped_call_resolves_the_default_store(self, tmp_path):
        """NON-REGRESSION ARM. Green before and after. DO NOT DELETE IT.

        It cannot go red against the store scope, because an unbound resolution
        takes the same route it always took. It is here because the property it
        states outranks the rest of the file: with no scope bound, resolution
        falls through to the environment variable and then to the home
        directory, and a production caller keeps reaching its store.
        """
        assert resolve_db_path() == get_memory_dir() / DB_FILENAME

    def test_resolution_creates_no_directory(self, tmp_path):
        """NON-REGRESSION ARM for the behaviour, new for the name.

        `compute_db_path` already carried this property and still does. The
        assertion moves to `resolve_db_path` because that is now the composing
        expression, and because the status report calls it: a read-shaped method
        must not leave a directory tree behind.
        """
        target = tmp_path / "never-created" / "never.db"

        with store_scope(target):
            assert resolve_db_path() == target

        assert not target.parent.exists()

    def test_the_resolver_holds_no_refusal(self):
        """MUTATION-BOUND ARM. Red only against a `raise` added to the resolver.

        A refusal is what an isolation fix reaches for when it is written the
        wrong way round: block the default and every isolation arm goes green
        while production breaks. This asserts the refusal has no expression to
        live in.
        """
        config_tree = _parse(_SCRIPTS_DIR / "config.py")

        for name in ("resolve_db_path", "get_memory_dir", "store_scope"):
            raises = [
                node
                for node in ast.walk(_function(config_tree, name))
                if isinstance(node, ast.Raise)
            ]
            assert raises == [], f"{name} refuses a store instead of redirecting it"

        # CONTROL, POSITIVE: the detector finds a `raise` where one is present,
        # so the empty results above are absence and not a blind instrument.
        database_tree = _parse(_SCRIPTS_DIR / "database.py")
        control = [
            node
            for node in ast.walk(_function(database_tree, "db_connection"))
            if isinstance(node, ast.Raise)
        ]
        assert control, "the raise detector found nothing where a raise exists"


# ---------------------------------------------------------------------------
# The refusal guard must keep reading the caller's own argument
# ---------------------------------------------------------------------------


class TestTheGuardStillReadsTheCallerArgument:
    """The scope must enter AFTER `_refuse_live_db_under_pytest`.

    That guard keys on `db_path is None`. Bind the store before it and a later
    reader of the resolver sees a path where the caller supplied none, which is
    how a guard gets disarmed by a change that looks unrelated to it.
    """

    def test_the_scope_entry_follows_the_refusal_guard(self):
        """RED ON THE UNPATCHED TREE, where no scope entry exists to order.

        The mutation it exists for is the reordering: move the `with
        store_scope(...)` above the guard call and this arm fails.
        """
        tree = _parse(_SCRIPTS_DIR / "cli.py")
        main_fn = _function(tree, "main")

        guard_lines = [
            node.lineno
            for node in ast.walk(main_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_refuse_live_db_under_pytest"
        ]
        scope_lines = [
            node.lineno
            for node in ast.walk(main_fn)
            if isinstance(node, ast.With)
            for item in node.items
            if isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "store_scope"
        ]

        assert len(guard_lines) == 1, guard_lines
        assert len(scope_lines) == 1, scope_lines
        assert guard_lines[0] < scope_lines[0], (
            "the store scope is bound before the refusal guard reads the "
            "caller's argument"
        )


# ---------------------------------------------------------------------------
# One expression, in two parts, because the two counts are not one property
# ---------------------------------------------------------------------------


class TestOnlyOneExpressionYieldsTheStorePath:
    """The store file path has ONE derivation in this package.

    THE TWO ARMS BELOW MEASURE DIFFERENT THINGS, and the first is a PROXY.
    Counting the literal catches a second spelling of the name. It does NOT
    catch a second derivation that REUSES the constant: that keeps the literal
    count at one while putting the derivation count back to two. The second arm
    is what closes that case, so do not read a green first arm as proof on its
    own.
    """

    def test_the_store_file_name_is_written_one_time(self):
        """RED ON THE UNPATCHED TREE, where the name is written two times.

        THE COUNT IS TAKEN OVER SYNTAX, NOT TEXT, and that is load-bearing. The
        name also appears inside prose in a module docstring and in a function
        docstring. A raw text search finds those, reports three on a correct
        fix, and reads as a failure. A docstring is ONE syntax node holding its
        whole text, so an equality test against the name cannot match it.
        """
        modules = _package_modules()

        # CONTROL, POSITIVE: the population is real and holds the module the
        # constant lives in.
        assert len(modules) > 5, modules
        assert any(m.name == "config.py" for m in modules)

        hits = [
            (module.name, node.lineno)
            for module in modules
            for node in ast.walk(_parse(module))
            if isinstance(node, ast.Constant) and node.value == DB_FILENAME
        ]
        assert len(hits) == 1, f"the store file name is written more than once: {hits}"
        assert hits[0][0] == "config.py"

        # CONTROL, NEGATIVE: a name that cannot be present returns nothing, so
        # the matcher discriminates rather than matching every string.
        impossible = [
            module.name
            for module in modules
            for node in ast.walk(_parse(module))
            if isinstance(node, ast.Constant) and node.value == "no-such-store.db"
        ]
        assert impossible == []

        # CONTROL: the text instrument and the syntax instrument DISAGREE, and
        # the disagreement is the reason this arm is written over syntax. If
        # these two ever agree, the prose mentions have gone and a reviewer
        # should re-read this arm rather than trust it.
        text_hits = [m.name for m in modules if DB_FILENAME in m.read_text(encoding="utf-8")]
        assert len(text_hits) > len(hits), text_hits

    def test_one_site_composes_a_path_from_the_name(self):
        """RED ON THE UNPATCHED TREE, where the constant does not exist.

        THIS IS THE ARM THAT CATCHES A REUSE. A second derivation written from
        the shared constant leaves the literal count at one and is invisible to
        the arm above.
        """
        modules = _package_modules()

        loads = [
            (module.name, node.lineno, _enclosing_function(tree, node.lineno))
            for module in modules
            for tree in [_parse(module)]
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id == "DB_FILENAME"
            and isinstance(node.ctx, ast.Load)
        ]

        assert len(loads) == 1, f"more than one site derives the store path: {loads}"
        assert loads[0][0] == "config.py"
        assert loads[0][2] == "resolve_db_path"

        # CONTROL, POSITIVE: the constant IS defined, so the count of one above
        # is a real single reader and not a package where the name is absent.
        stores = [
            module.name
            for module in modules
            for node in ast.walk(_parse(module))
            if isinstance(node, ast.Name)
            and node.id == "DB_FILENAME"
            and isinstance(node.ctx, ast.Store)
        ]
        assert stores == ["config.py"]


# ---------------------------------------------------------------------------
# The connection factory
# ---------------------------------------------------------------------------


class TestTheConnectionFactoryContract:
    """The factory that every scoped read and write goes through."""

    def test_the_factory_passes_no_isolation_level(self):
        """MUTATION-BOUND ARM. Green before and after this change.

        It is here because the property had NO test at all: `isolation_level`
        appeared in zero test files while the repair work depends on it. Leaving
        it unset keeps the driver's implicit transaction handling, so a delete
        and its replacement insert sit in one transaction and a failure between
        them rolls back. Pass `isolation_level=None` and each statement
        autocommits, which turns that pair into two independent writes.
        """
        tree = _parse(_SCRIPTS_DIR / "database.py")
        factory = _function(tree, "get_connection")

        connects = [
            node
            for node in ast.walk(factory)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
        ]
        assert len(connects) == 1, connects

        keywords = {kw.arg for kw in connects[0].keywords}

        # CONTROL, POSITIVE: the instrument reads keyword arguments at all, so
        # the absence asserted below is an absence and not a blind read.
        assert "check_same_thread" in keywords

        assert "isolation_level" not in keywords, (
            "the factory sets isolation_level, which changes when a statement "
            "commits and breaks the drop-then-insert pairing"
        )


# ---------------------------------------------------------------------------
# An unresolved identity scopes every store read to nothing, never to all
# (relocated from the worktree-record module).
# ---------------------------------------------------------------------------

class TestUnresolvedIdentityScopesReads:
    """R1: None means NO records, never every project's records."""

    def _seed(self, db_path: Path):
        memory = PACTMemory(project_id="other-proj-a", session_id="s", db_path=db_path)
        linked = db_path.parent / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        target = db_path.parent / "target.py"
        target.write_text("y = 2\n", encoding="utf-8")
        memory_id = memory.save(
            {"context": _TOKEN}, files=[str(linked)], sync_to_claude=False
        )
        with db_connection(db_path) as conn:
            add_file_relation(
                conn, str(linked), str(target), "imports", project_id="other-proj-a"
            )
        return memory_id, linked

    def _unresolved(self, monkeypatch, db_path: Path) -> PACTMemory:
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: memory_api_module._DetectedIdentity(
                    None, "unresolved", None, None
                )
            ),
        )
        memory = PACTMemory(session_id="s", db_path=db_path)
        assert memory.project_id is None
        return memory

    def test_r1_control_a_scoped_instance_finds_the_seeded_rows(self, tmp_path):
        """The positive control the other R1 arms rest on.

        Without it an arm can pass because the fixture has nothing findable —
        the same vacuous-pass class as I2.
        """
        db_path = tmp_path / "store.db"
        memory_id, linked = self._seed(db_path)

        scoped = PACTMemory(project_id="other-proj-a", session_id="s", db_path=db_path)
        assert [m.id for m in scoped.list()] == [memory_id]
        assert [m.id for m in scoped.search(_TOKEN, sync_to_claude=False)] == [memory_id]
        assert [m.id for m in scoped.search_by_file(str(linked))] == [memory_id]
        status = scoped.get_status()
        assert status["memory_count"] == 1
        assert all(
            status["graph_stats"][key] > 0
            for key in ("files", "memories", "memory_file_links", "file_relations")
        ), status["graph_stats"]

    def test_r1_an_unresolved_identity_returns_no_records_from_any_surface(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "store.db"
        self._seed(db_path)
        empty = self._unresolved(monkeypatch, db_path)

        assert empty.list() == []
        assert empty.search(_TOKEN, sync_to_claude=False) == []
        assert empty.search_by_file(str(db_path.parent / "linked.py")) == []

        status = empty.get_status()
        assert status["project_id"] is None
        assert status["memory_count"] == 0
        assert status["graph_stats"] == {
            "files": 0,
            "memories": 0,
            "memory_file_links": 0,
            "file_relations": 0,
        }, status["graph_stats"]

        scoped = PACTMemory(project_id="other-proj-a", session_id="s", db_path=db_path)
        assert set(status) == set(scoped.get_status()), (
            "the zeroed envelope hides a key the populated one carries"
        )

        # The store really holds another project's rows, so [] above is the
        # guard's doing rather than an empty store.
        with db_connection(db_path) as conn:
            assert list_memories(conn, project_id=None)
            assert search_memories_by_text(conn, _TOKEN, project_id=None)

    def test_r1c_the_file_search_would_surface_a_NULL_keyed_row_unguarded(
        self, tmp_path, monkeypatch
    ):
        """`search_by_file` has a different causal shape: NULL, not all."""
        db_path = tmp_path / "store.db"
        linked = db_path.parent / "linked.py"
        self._seed(db_path)
        with db_connection(db_path) as conn:
            file_id = get_file_id(conn, str(linked), "other-proj-a")
            assert file_id
            conn.execute("UPDATE files SET project_id = NULL WHERE id = ?", (file_id,))
            conn.commit()
        with db_connection(db_path) as conn:
            assert get_file_id(conn, str(linked), None) == file_id, (
                "the fixture does not exercise the NULL-keyed shape"
            )

        empty = self._unresolved(monkeypatch, db_path)
        assert empty.search_by_file(str(linked)) == []

    def test_unresolved_search_clears_the_previous_sync_status(
        self, tmp_path, monkeypatch
    ):
        """The identity early return must not inherit a save's `wrote`."""
        empty = self._unresolved(monkeypatch, tmp_path / "store.db")
        with patch(
            "scripts.memory_api.sync_to_claude_md",
            return_value=SyncResult(SyncResult.WROTE),
        ):
            empty.save({"context": "PREVIOUS", "project_id": "chosen"})
        assert empty.last_sync_status == SyncResult.WROTE

        assert empty.search("NO-RECORDS") == []
        assert empty.last_sync_status is None

    def test_empty_result_search_clears_the_previous_sync_status(self, tmp_path):
        """An ordinary no-result search also attempts no sync."""
        memory = PACTMemory(
            project_id="detected-b", session_id="s", db_path=tmp_path / "store.db"
        )
        with patch(
            "scripts.memory_api.sync_to_claude_md",
            return_value=SyncResult(SyncResult.WROTE),
        ):
            memory.save({"context": "PREVIOUS"})
        assert memory.last_sync_status == SyncResult.WROTE

        with patch("scripts.memory_api.graph_enhanced_search", return_value=[]):
            assert memory.search("NO-MATCH") == []
        assert memory.last_sync_status is None


# ---------------------------------------------------------------------------
# The graph-boosted leg must confirm a memory's stored project before it
# becomes a result (relocated by placement rule; see F16).
# ---------------------------------------------------------------------------

class TestTheGraphLegConfirmsTheStoredProject:
    def test_a_stale_link_does_not_serve_another_projects_memory(self, tmp_path):
        """The graph-boosted leg adds ids from file links with no project
        check, so the loop's stored-project confirmation is the only filter
        between such an id and a returned memory."""
        db_path = tmp_path / "graph-leg.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory = PACTMemory(project_id="projB", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": _REKEY_TOKEN}, files=[str(linked)], sync_to_claude=False
        )
        assert [m.id for m in memory.search_by_file(str(linked))] == [memory_id], (
            "the live fixture is not addressable; the arms below prove nothing"
        )

        # A stale link on the OLD project's file row.
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            file_id = track_file(conn, str(linked), "projA")
            conn.execute(
                "INSERT OR IGNORE INTO memory_files "
                "(memory_id, file_id, relationship) VALUES (?, ?, 'modified')",
                (memory_id, file_id),
            )
            conn.commit()

        with store_scope(db_path):
            leaked = memory_search.graph_enhanced_search(
                _REKEY_TOKEN, current_file=str(linked), project_id="projA"
            )
            owned = memory_search.graph_enhanced_search(
                _REKEY_TOKEN, current_file=str(linked), project_id="projB"
            )
            whole = memory_search.graph_enhanced_search(
                _REKEY_TOKEN, current_file=str(linked), project_id=None
            )

        assert [m.id for m in leaked] == [], (
            "the graph leg served a projB memory to projA"
        )
        # CONTROLS on the same fixture: the memory IS reachable from its own
        # project and unscoped, so [] above is the confirmation's doing.
        assert [m.id for m in owned] == [memory_id]
        assert [m.id for m in whole] == [memory_id]
