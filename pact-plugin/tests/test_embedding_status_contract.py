"""
Location: pact-plugin/tests/test_embedding_status_contract.py

Summary: Pins the contract of `PACTMemory._store_embedding` — one test per KIND
of exit, plus the condition-keyed removal of a vector that would otherwise go
stale. These are the tests the existing suite could not provide: it was written
against the old `bool` return and cannot distinguish a correct reason code from
a wrong one.

Used by/with:
- skills/pact-memory/scripts/memory_api.py: the contract under test.
- skills/pact-memory/scripts/search.py: supplies the capability the reason code
  reports, so save and search cannot report different things.

THE THREE KINDS, and they are kinds rather than lines because keying on line
numbers would re-introduce the list that section 4.1 exists to remove:
  CAPABILITY — this process cannot embed at all. Reported, because a caller can
               act on it.
  INPUT      — this record has nothing to embed. NOT reported: it is a property
               of the record, not of the system, and its author just caused it.
  FAULT      — storing raised. Reported.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from scripts.memory_api import PACTMemory


@pytest.fixture
def mem():
    return PACTMemory(project_id="test-project", session_id="test-session")


@pytest.fixture
def conn():
    return MagicMock()


def _memory(text_field: str = "some embeddable context") -> dict:
    return {"context": text_field, "project_id": "test-project"}


class TestCapabilityExits:
    """CAPABILITY: the process cannot embed. Must be reported."""

    def test_extensions_unavailable_reports_degraded(self, mem, conn):
        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", False), \
             patch("scripts.memory_api.get_search_capabilities",
                   return_value={"search_mode": "keyword"}):
            result = mem._store_embedding(conn, "mem-1", _memory())

        assert result == "degraded:keyword"

    def test_embedding_generation_unavailable_reports_degraded(self, mem, conn):
        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding_text", return_value="text"), \
             patch("scripts.memory_api.generate_embedding", return_value=None), \
             patch("scripts.memory_api.get_search_capabilities",
                   return_value={"search_mode": "keyword"}):
            result = mem._store_embedding(conn, "mem-1", _memory())

        assert result == "degraded:keyword"

    def test_reason_code_carries_the_search_paths_own_mode(self, mem, conn):
        """The code must come from get_search_capabilities, not a second predicate.

        If a save-side predicate were ever introduced, this test would keep
        passing on the literal 'keyword' while the two drifted apart — so it
        pins an UNUSUAL mode value that only the real call can produce.
        """
        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", False), \
             patch("scripts.memory_api.get_search_capabilities",
                   return_value={"search_mode": "sentinel-mode"}):
            result = mem._store_embedding(conn, "mem-1", _memory())

        assert result == "degraded:sentinel-mode"


class TestInputExit:
    """INPUT: nothing to embed. Correct, and deliberately NOT reported."""

    def test_no_embeddable_text_reports_nothing(self, mem, conn):
        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding_text", return_value=""), \
             patch.object(PACTMemory, "_drop_existing_vector", return_value=True):
            result = mem._store_embedding(conn, "mem-1", _memory(""))

        assert result is None, (
            "an empty record is not a degraded system; reporting it would tell "
            "the caller the process cannot embed, which is false"
        )


class TestFaultExit:
    """FAULT: storing raised. Reported, and the row is left alone."""

    def test_storage_failure_reports_fault(self, mem, conn):
        conn.execute.side_effect = RuntimeError("disk went away")
        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding_text", return_value="text"), \
             patch("scripts.memory_api.generate_embedding", return_value=[0.1] * 256):
            result = mem._store_embedding(conn, "mem-1", _memory())

        assert result == "fault"

    def test_fault_does_not_drop_the_vector(self, mem, conn):
        """THE FAULT HANDLER ADDS NO DROP OF ITS OWN.

        WHY THE ASSERTION IS NOT `assert_not_called`, WHICH IS WHAT IT WAS.
        The replace path now drops before it inserts, so a drop DOES happen
        inside this method. The old spelling bound to "no drop anywhere in
        this method" when the property it protects is "no drop in the
        HANDLER". The two agreed while the write path performed no drop, and
        they stopped agreeing the moment a replace was possible.

        So the assertion below pins the EXACT call list: one drop, the
        deferred one that belongs to the replace. A drop added in the handler
        appends a second entry and this arm goes red. A drop that stops
        deferring its commit changes the recorded kwargs and this arm goes red
        too, which is the stronger property the old spelling could not state.
        """
        conn.commit.side_effect = RuntimeError("commit failed after write")
        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding_text", return_value="text"), \
             patch("scripts.memory_api.generate_embedding", return_value=[0.1] * 256), \
             patch.object(PACTMemory, "_drop_existing_vector") as drop:
            mem._store_embedding(conn, "mem-1", _memory())

        assert drop.call_args_list == [call(conn, "mem-1", commit=False)]


class TestSuccess:
    def test_stored_vector_reports_nothing(self, mem, conn):
        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding_text", return_value="text"), \
             patch("scripts.memory_api.generate_embedding", return_value=[0.1] * 256):
            result = mem._store_embedding(conn, "mem-1", _memory())

        assert result is None


class TestStaleVectorIsRemoved:
    """The condition-keyed remedy: no vector may survive describing old text.

    A missing vector makes a record invisible to semantic search. A STALE one
    makes it findable, confidently, for the wrong query — the worse failure,
    and the one an `update()` produces when re-embedding fails.

    RED ARM: against the pre-change code (`6b2d1b4c^`) `_store_embedding`
    returned a bare bool and never deleted, so both tests below fail — the
    first on the missing method, the second because no DELETE is issued.
    """

    def test_input_exit_drops_an_existing_vector(self, mem, conn):
        """An update that empties the text must not leave the old vector."""
        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding_text", return_value=""), \
             patch.object(PACTMemory, "_drop_existing_vector") as drop:
            mem._store_embedding(conn, "mem-42", _memory(""))

        drop.assert_called_once()
        assert drop.call_args[0][1] == "mem-42"

    def test_generation_failure_drops_an_existing_vector(self, mem, conn):
        with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding_text", return_value="text"), \
             patch("scripts.memory_api.generate_embedding", return_value=None), \
             patch("scripts.memory_api.get_search_capabilities",
                   return_value={"search_mode": "keyword"}), \
             patch.object(PACTMemory, "_drop_existing_vector") as drop:
            mem._store_embedding(conn, "mem-42", _memory())

        drop.assert_called_once()
        assert drop.call_args[0][1] == "mem-42"

    def test_drop_issues_a_delete_keyed_on_the_memory_id(self, mem, conn):
        """Pins the statement itself, so the helper cannot silently no-op."""
        with patch.dict("sys.modules", {"sqlite_vec": MagicMock()}):
            assert mem._drop_existing_vector(conn, "mem-42") is True

        sql, params = conn.execute.call_args[0]
        assert "DELETE" in sql.upper() and "vec_memories" in sql
        assert params == ("mem-42",)

    def test_drop_reports_false_when_the_vector_table_is_unreachable(self, mem, conn):
        """Without the extension a vec0 virtual table cannot be reached at all,
        so the drop is IMPOSSIBLE rather than skipped. The caller is told."""
        conn.enable_load_extension.side_effect = RuntimeError("no extension support")

        assert mem._drop_existing_vector(conn, "mem-42") is False


# The scope disclosure a real save attaches. `project_scope` is TOTAL like
# `sync_status` -- save() sets it on every branch -- so a clean save carries it
# and the exact-equality assertion below must include it.
_SCOPE = {
    "project_id": "proj",
    "source": "supplied",
    "cwd_repo": "proj",
    "location_divergence": False,
}


class TestCliSuccessEnvelopeCarriesTheStatus:
    """The CLI is the only consumer most callers have.

    If the API captures the reason and the envelope drops it, a command-line
    caller still receives unqualified success and R3 is unfinished where it
    matters most.
    """

    def _run_cmd_save(self, last_status, sync_status="wrote"):
        import json as _json
        from types import SimpleNamespace
        from scripts import cli

        fake = MagicMock()
        fake.save.return_value = "mem-1"
        fake.last_embedding_status = last_status
        # CONFIGURED, NOT LEFT TO THE MOCK. An unset attribute on a MagicMock is
        # not None -- it is a fresh child mock, which is never None, so the
        # envelope would carry a mock object and the key-set assertion below
        # would fail for a reason that has nothing to do with either status.
        # `wrote` is the honest default because a real save always reports one.
        fake.last_sync_status = sync_status
        # Same reason as `last_sync_status` above, and the same trap: an unset
        # MagicMock attribute is a child mock, never None, so the envelope would
        # carry a mock OBJECT and the exact-equality assertion would compare
        # mock identities instead of a shape. A real dict pins the shape.
        fake.last_project_scope = _SCOPE

        captured = {}
        with patch.object(cli, "PACTMemory", return_value=fake), \
             patch.object(cli, "_success", side_effect=lambda r: captured.setdefault("r", r)):
            cli.cmd_save(
                SimpleNamespace(stdin=False, json_data=_json.dumps({"context": "c"}),
                                no_sync=False),
                db_path=None,
            )
        return captured["r"]

    def test_degraded_save_reports_its_status(self):
        result = self._run_cmd_save("degraded:keyword")
        assert result["memory_id"] == "mem-1"
        assert result["embedding_status"] == "degraded:keyword"

    def test_fault_is_reported_too(self):
        assert self._run_cmd_save("fault")["embedding_status"] == "fault"

    def test_clean_save_omits_the_field_entirely(self):
        """Nothing to report must stay silent, so the common case is unchanged
        and no caller has to interpret a null.

        THE ENVELOPE'S TWO STATUS FIELDS FOLLOW OPPOSITE RULES, ON PURPOSE, and
        this assertion is where that shows. `embedding_status` is PARTIAL: it
        appears only when there is a problem, which is what this test pins.
        `sync_status` is TOTAL: a save always performed, suppressed or refused a
        sync, so it always says which. Its presence here is the contract, not
        noise -- the omission-based reading is correct for one field and would
        be a silent failure for the other.
        """
        result = self._run_cmd_save(None)
        assert result == {
            "memory_id": "mem-1",
            "sync_status": "wrote",
            "project_scope": _SCOPE,
        }
        assert "embedding_status" not in result


class TestCliStderrStaysCleanOnTheFaultPath:
    """The CLI's stderr is a structured JSON channel; the fault must not reach it.

    cli.py configures no logging, so `logging.lastResort` emits WARNING and
    above to stderr. A fault logged at WARNING therefore lands in the middle of
    a channel that callers parse, corrupting it.

    THE SUBPROCESS IS THE POINT. In-process tests cannot catch this: pytest
    installs its own handlers, so lastResort never engages. The CLI tests that
    exist mock the memory object, so the real fault branch never runs. This is
    the only arm that puts a REAL object on the REAL fault path in a process
    with no logging configured -- which is exactly how a user runs it.

    NON-VACUITY: an empty-stderr assertion passes whether the fix works or the
    fault never fired. So the positive arm reads `embedding_status` from stdout
    and requires it to be `fault` -- proving the branch under test executed.
    """

    def _run_cli_save_with_a_forced_fault(self, db_path):
        """`db_path` IS A STORE THAT IS PRESENT. `cli.main` refuses a
        `--db-path` naming a store that is absent, for each command other
        than `setup`, so callers pass the `memory_store` fixture result."""
        pkg_root = str(Path(__file__).parent.parent / "skills" / "pact-memory")
        db = str(db_path)
        child = (
            "import sys, json\n"
            # memory_api uses relative imports, so it must load as part of the
            # `scripts` package rather than as a bare module.
            f"sys.path.insert(0, {pkg_root!r})\n"
            "from scripts import memory_api\n"
            # Force the real except-branch from INSIDE its try block, so the
            # code under test runs rather than a stand-in for it.
            "class _Boom:\n"
            "    @staticmethod\n"
            "    def pack(*a, **k):\n"
            "        raise RuntimeError('forced storage fault')\n"
            "memory_api.struct = _Boom\n"
            "from scripts import cli\n"
            # --db-path is a hidden parent-parser flag and goes AFTER the
            # subcommand, so the child writes to a scratch database.
            # --no-sync IS REQUIRED, NOT TIDINESS. --db-path scopes the
            # DATABASE only; the save path still syncs to the developer's real
            # CLAUDE.md Working Memory. Without this flag every run of this
            # test writes an entry into the operator's live file -- a test
            # reaching into real user state, which is the exact defect class
            # this whole change set exists to close.
            "cli.main(['save', '--no-sync', '--db-path', " + repr(db) + ", "
            "json.dumps({'context': 'a record with embeddable text'})])\n"
        )
        # Suppress the model backend's own progress bar. huggingface_hub writes
        # "Fetching N files" to stderr even on a cache hit, which is THIRD-PARTY
        # pollution of the same channel and not what this test is measuring.
        # Controlling it out is what lets the assertion below be exact rather
        # than a substring match that a future emission could slip past.
        env = dict(os.environ)
        env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        # Same reason, second channel. huggingface_hub relays server-sent
        # `X-HF-Warning` response headers through logger.warning() -- e.g. the
        # unauthenticated-request notice -- which lastResort puts on stderr
        # exactly like the fault this test measures. The text is chosen by the
        # Hub, not by us, so it cannot be matched on; silencing the library's
        # own logger at source is what keeps the assertion below an exact
        # equality instead of a carve-out that would also hide our output.
        env["HF_HUB_VERBOSITY"] = "error"
        return subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True,
            timeout=120, env=env,
        )

    def test_fault_is_reported_on_stdout_and_stderr_stays_empty(self, memory_store):
        proc = self._run_cli_save_with_a_forced_fault(memory_store("probe.db"))

        payload = json.loads(proc.stdout)
        assert payload["ok"] is True

        # POSITIVE ARM: the fault branch demonstrably executed.
        assert payload["result"].get("embedding_status") == "fault", (
            "the forced fault did not reach the reason code, so this test "
            f"never exercised the fault path: {payload}"
        )

        assert proc.stderr == "", (
            "the CLI emitted free text on its structured JSON channel:\n"
            f"{proc.stderr!r}"
        )


class TestSqliteVecAbsenceIsReportedAsKeyword:
    """The one exit of six with no test: sqlite-vec missing.

    `_store_embedding` reaches its `except ImportError` when sqlite-vec cannot
    be imported, and reports the process capability. Before this was fixed the
    capability said `semantic` -- because it checked pysqlite3 and the embedding
    backend but never sqlite-vec -- so a save that could not store a vector
    reported that semantic search was available.

    The claim was FALSE ON THE SEARCH SIDE TOO, independently of any save:
    `vector_search` does the same import and returns [] on ImportError, so
    semantic search returns nothing on every query while the capability calls
    the mode `semantic`. One subject, one predicate, one correction.

    THE REAL PATH, NOT A STAND-IN: `get_search_capabilities` is deliberately
    NOT patched here. A single environmental fact -- sqlite_vec unimportable --
    drives both the exit under test and the capability that reports it.
    """

    def test_missing_sqlite_vec_reports_keyword_not_semantic(self, mem, conn):
        with patch.dict("sys.modules", {"sqlite_vec": None}), \
             patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding_text", return_value="text"), \
             patch("scripts.memory_api.generate_embedding", return_value=[0.1] * 256):
            result = mem._store_embedding(conn, "mem-1", _memory())

        assert result == "degraded:keyword", (
            "with sqlite-vec absent no vector can be stored and none can be "
            f"searched, so the capability must not claim semantic; got {result!r}"
        )


class TestAmbientWorkingMemorySyncIsRefusedUnderPytest:
    """The third path to live operator state, which had no guard at all.

    The database and the session marker both refuse a test process. The
    CLAUDE.md sync did not, so a CLI save from a test wrote into the operator's
    real Working Memory -- and a sandboxed HOME never covered it, because the
    target is resolved from CLAUDE_PROJECT_DIR and the working directory rather
    than from HOME.

    NON-VACUITY: asserting a file was not written passes whether the guard
    fired or the save never ran. So the child runs WITHOUT --no-sync against a
    SANDBOXED project dir, and the assertions prove BOTH halves -- that the
    save genuinely happened, and that the sync it would have performed did not
    reach the file. No operator state is touched: the ambient target is
    redirected into tmp for the duration.
    """

    def test_save_succeeds_while_the_ambient_sync_is_refused(self, tmp_path, memory_store):
        project = tmp_path / "proj"
        project.mkdir()
        claude_md = project / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n## Working Memory\n", encoding="utf-8"
        )
        before = claude_md.read_text(encoding="utf-8")

        pkg_root = str(Path(__file__).parent.parent / "skills" / "pact-memory")
        db = str(memory_store("probe.db"))
        child = (
            "import sys, json\n"
            f"sys.path.insert(0, {pkg_root!r})\n"
            "from scripts import cli\n"
            # NOTE: deliberately NO --no-sync. The guard, not the flag, is what
            # must stop this.
            "cli.main(['save', '--db-path', " + repr(db) + ", "
            "json.dumps({'context': 'ambient sync probe'})])\n"
        )
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        env["PYTEST_CURRENT_TEST"] = "test_save_succeeds_while_the_ambient_sync_is_refused (call)"
        proc = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True,
            timeout=120, env=env,
        )

        # POSITIVE ARM 1: the save actually ran and succeeded.
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True
        assert payload["result"]["memory_id"], "no save occurred, so nothing was refused"

        # POSITIVE ARM 2: the record really is in the database, so the sync is
        # the ONLY thing that did not happen.
        import sqlite3 as _sqlite3
        with _sqlite3.connect(db) as con:
            count = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert count == 1, f"expected the probe record to be stored, found {count}"

        # THE GUARD: the ambient target is untouched.
        assert claude_md.read_text(encoding="utf-8") == before, (
            "the working-memory sync reached an ambiently-resolved CLAUDE.md "
            "from a test process"
        )
