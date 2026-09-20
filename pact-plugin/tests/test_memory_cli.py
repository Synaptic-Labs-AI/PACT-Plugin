"""
Tests for pact-memory/scripts/cli.py — CLI entry point.

Tests cover:
1. Arg parsing: subcommand dispatch, required arguments, defaults
2. Save command: JSON arg, --stdin, invalid JSON, non-dict input
3. Save verification: CLI-level propagation of API-layer verification (RuntimeError → SYSTEM_ERROR)
4. Search command: query dispatch, --limit
5. List command: default and custom --limit
6. Get command: existing and missing memory IDs
7. Status command: status dict output
8. Setup command: success and failure paths
9. Output format: JSON envelope consistency, stdout/stderr routing
10. Error handling: exit codes, error types, unknown commands
11. Subprocess E2E: true black-box tests via subprocess.run
12. E2E save verification: subprocess roundtrip confirming verification, error paths
13. Agent configuration: model frontmatter verification
"""
import json
import os
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fixtures.hf_cache import hf_cache_env

from helpers import create_test_schema, make_cli_memory_dict

# Reused rather than re-implemented: the canonical minimal-CLAUDE.md seeder.
# The isolation fixture below needs a real file at the env-var root, because
# the display resolver treats "no CLAUDE.md here" as "keep looking".
from test_working_memory_concurrency_comprehensive import _seed_claude_md

from scripts.cli import build_parser, cmd_save, cmd_search, cmd_list, cmd_get, cmd_status, cmd_setup, cmd_update, cmd_delete, main, _COMMANDS, _refuse_live_db_under_pytest
from scripts.memory_api import PACTMemory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Stable 32-char fake memory ID used by mock_pact_memory defaults so tests
# asserting on the success envelope's `memory_id` field have a known value
# to compare against. Distinct from any prefix used in test args so that
# assertions catch regressions where the user-supplied prefix is echoed
# instead of the resolved full ID.
_FAKE_RESOLVED_ID = "fa1ce1d" + "0" * 25  # 32 chars, lowercase hex pattern


@pytest.fixture
def mock_pact_memory():
    """Create a mock PACTMemory instance with standard return values."""
    mock = MagicMock()
    mock.save.return_value = "abc123def456"
    mock.search.return_value = []
    mock.list.return_value = []
    # Non-None default so save verification in PACTMemory.save() passes;
    # override to None in tests that need NOT_FOUND behavior.
    mock.get.return_value = MagicMock()
    # update/delete return Optional[str] (resolved full ID). Explicit defaults
    # match the API contract; tests for not-found paths override to None.
    mock.update.return_value = _FAKE_RESOLVED_ID
    mock.delete.return_value = _FAKE_RESOLVED_ID
    mock.get_status.return_value = {
        "project_id": "test-project",
        "memory_count": 5,
        "db_path": "/tmp/test.db",
    }
    return mock


@pytest.fixture
def cli_db(tmp_path):
    """Create a temporary database for subprocess tests."""
    # Use the same sqlite3 module as the codebase
    try:
        import pysqlite3 as sqlite3
    except ImportError:
        import sqlite3

    db_path = tmp_path / "cli_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_test_schema(conn)
    conn.close()
    return db_path


@pytest.fixture
def cli_script_path():
    """Return the absolute path to cli.py."""
    return str(
        Path(__file__).resolve().parent.parent
        / "skills" / "pact-memory" / "scripts" / "cli.py"
    )


@pytest.fixture(autouse=True)
def _isolate_claude_md_target(tmp_path, monkeypatch):
    """Redirect every CLAUDE.md resolution in this module to a per-test tmp tree.

    The subprocess sites in this file pass neither ``env=`` nor ``cwd=``, so each
    child inherits this process's ``os.environ`` and cwd. Without this fixture the
    display resolver walks git from the inherited cwd and lands on the DEVELOPER'S
    REAL project CLAUDE.md, and the ``save`` and ``search`` sites then write
    Working Memory entries into it. That file is gitignored and untracked, so the
    damage is invisible to ``git status`` and has no git recovery path.

    BOTH halves are required, and this is the part that makes the obvious fix a
    silent no-op: setting ``CLAUDE_PROJECT_DIR`` alone does NOT isolate, because
    ``_resolve_display_claude_md_path`` probes the env dir and CONTINUES when no
    CLAUDE.md is found there, falling through to the git walk and back onto the
    live file. Seeding a CLAUDE.md at the env dir is what makes the first probe
    succeed and terminate resolution before the walk.

    Autouse rather than opt-in: a test that simply does not request the fixture
    would be unprotected with nothing to see in review, and invisible non-coverage
    is the exact failure mode this fixture exists to prevent. Scoped to this module
    by placement -- a global autouse would break files that legitimately exercise
    the resolver's fallback branches.
    """
    _seed_claude_md(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # THE MODEL CACHE RIDES THE SAME INHERITANCE. Because the sites here pass
    # no `env=`, the only way to reach their children is this process's
    # environment — so the cache pin belongs beside the project-dir pin rather
    # than at 60 call sites. Measured without it: 59 model-load attempts from
    # children plus 1 in this process, each a cold-cache download wherever no
    # warm cache exists (i.e. CI).
    for _k, _v in hf_cache_env().items():
        monkeypatch.setenv(_k, _v)


class TestSubprocessClaudeMdIsolation:
    """Proof that the autouse isolation reaches a SPAWNED CHILD, not just this process."""

    def test_spawned_subprocess_resolves_claude_md_under_tmp(self, tmp_path):
        """A child spawned the way every E2E site spawns one must resolve the
        display CLAUDE.md inside the per-test tmp tree.

        Asserts the resolved path REPORTED BY THE CHILD rather than that the
        fixture ran. The chain under test is monkeypatch.setenv -> os.environ ->
        subprocess inheritance -> resolver, and only a child process can witness
        all four links; asserting on the parent's environ would prove none of them.
        Spawned with no ``env=`` and no ``cwd=`` so it inherits exactly what the
        other subprocess sites in this module inherit.
        """
        scripts_dir = (
            Path(__file__).resolve().parent.parent
            / "skills" / "pact-memory" / "scripts"
        )
        code = (
            "import sys; sys.path.insert(0, {!r});"
            "import working_memory as wm;"
            "print(wm._resolve_display_claude_md_path())".format(str(scripts_dir))
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        resolved = Path(result.stdout.strip())
        assert resolved.resolve() == (tmp_path / ".claude" / "CLAUDE.md").resolve()
        # Containment is the property that matters: anything under tmp_path is
        # disposable, anything outside it is somebody's real file.
        resolved.resolve().relative_to(tmp_path.resolve())


# ---------------------------------------------------------------------------
# Arg Parsing
# ---------------------------------------------------------------------------

class TestCliArgParsing:
    """Test argparse configuration and subcommand routing."""

    def test_build_parser_returns_parser(self):
        parser = build_parser()
        assert parser is not None
        assert parser.prog == sys.argv[0]

    def test_build_parser_prog_keeps_invoked_directory(self, monkeypatch):
        invoked = "/abs/path/to/cli.py"
        monkeypatch.setattr(sys, "argv", [invoked])
        parser = build_parser()
        assert parser.prog == invoked
        help_text = parser.format_help()
        assert invoked in help_text
        assert "Examples:" in help_text
        # The example line carries the executable shape: interpreter prefix
        # and the quoted invoked path.
        assert f'python3 "{invoked}" save --stdin' in help_text

    def test_save_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["save", '{"context": "test"}'])
        assert args.command == "save"
        assert args.json_data == '{"context": "test"}'
        assert args.stdin is False

    def test_save_stdin_flag(self):
        parser = build_parser()
        args = parser.parse_args(["save", "--stdin"])
        assert args.command == "save"
        assert args.stdin is True
        assert args.json_data is None

    def test_search_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["search", "auth tokens"])
        assert args.command == "search"
        assert args.query == "auth tokens"

    def test_search_limit_default(self):
        parser = build_parser()
        args = parser.parse_args(["search", "query"])
        assert args.limit == 5

    def test_search_limit_custom(self):
        parser = build_parser()
        args = parser.parse_args(["search", "query", "--limit", "20"])
        assert args.limit == 20

    def test_list_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"

    def test_list_limit_default(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.limit == 20

    def test_list_limit_custom(self):
        parser = build_parser()
        args = parser.parse_args(["list", "--limit", "3"])
        assert args.limit == 3

    def test_get_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["get", "abc123"])
        assert args.command == "get"
        assert args.memory_id == "abc123"

    def test_status_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_setup_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"

    def test_db_path_hidden_flag(self):
        parser = build_parser()
        args = parser.parse_args(["list", "--db-path", "/tmp/test.db"])
        assert args.db_path == "/tmp/test.db"

    def test_db_path_default_is_none(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.db_path is None

    def test_no_command_sets_none(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_update_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", '{"context": "updated"}'])
        assert args.command == "update"
        assert args.memory_id == "abc123"
        assert args.json_data == '{"context": "updated"}'
        assert args.stdin is False

    def test_update_stdin_flag(self):
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", "--stdin"])
        assert args.command == "update"
        assert args.memory_id == "abc123"
        assert args.stdin is True

    def test_delete_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["delete", "abc123"])
        assert args.command == "delete"
        assert args.memory_id == "abc123"

    def test_search_current_file_flag(self):
        parser = build_parser()
        args = parser.parse_args(["search", "query", "--current-file", "/path/to/file.py"])
        assert args.current_file == "/path/to/file.py"

    def test_search_current_file_default_is_none(self):
        parser = build_parser()
        args = parser.parse_args(["search", "query"])
        assert args.current_file is None

    def test_limit_zero_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["list", "--limit", "0"])
        assert exc_info.value.code == 2

    def test_limit_negative_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["search", "query", "--limit", "-1"])
        assert exc_info.value.code == 2

    def test_dispatch_table_covers_all_subcommands(self):
        expected = {"save", "search", "list", "get", "status", "setup", "update", "delete", "sync"}
        assert set(_COMMANDS.keys()) == expected


# ---------------------------------------------------------------------------
# Save Command
# ---------------------------------------------------------------------------

class TestCliSaveCommand:
    """Test the save subcommand handler."""

    def test_save_with_json_arg(self, mock_pact_memory):
        memory_dict = make_cli_memory_dict()
        json_str = json.dumps(memory_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with(memory_dict)

    def test_save_with_stdin(self, mock_pact_memory, monkeypatch):
        memory_dict = make_cli_memory_dict()
        json_str = json.dumps(memory_dict)
        monkeypatch.setattr("sys.stdin", StringIO(json_str))
        parser = build_parser()
        args = parser.parse_args(["save", "--stdin"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with(memory_dict)

    def test_save_outputs_memory_id(self, mock_pact_memory, capsys):
        memory_dict = make_cli_memory_dict()
        json_str = json.dumps(memory_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_save(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["memory_id"] == "abc123def456"

    def test_save_invalid_json(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["save", "not valid json{"])

        with pytest.raises(SystemExit) as exc_info:
            cmd_save(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "INVALID_JSON"

    def test_save_non_dict_json(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["save", '["a list"]'])

        with pytest.raises(SystemExit) as exc_info:
            cmd_save(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "INVALID_INPUT"

    def test_save_no_input(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["save"])

        with pytest.raises(SystemExit) as exc_info:
            cmd_save(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "MISSING_INPUT"

    def test_save_passes_db_path(self, mock_pact_memory):
        memory_dict = make_cli_memory_dict()
        json_str = json.dumps(memory_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str, "--db-path", "/tmp/test.db"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory) as mock_cls:
            with pytest.raises(SystemExit):
                cmd_save(args, db_path=Path("/tmp/test.db"))
        mock_cls.assert_called_once_with(db_path=Path("/tmp/test.db"))


# ---------------------------------------------------------------------------
# Save Verification
# ---------------------------------------------------------------------------

class TestCliSaveVerification:
    """Test save verification behavior at the CLI layer (#245).

    Verification (save-then-get) lives in PACTMemory.save() so all callers
    benefit.  When verification fails, save() raises RuntimeError which
    main()'s try/except catches as SYSTEM_ERROR (exit 2).
    """

    def test_save_success_when_save_returns_id(self, mock_pact_memory, capsys):
        """Normal save succeeds when PACTMemory.save() returns an ID."""
        memory_dict = make_cli_memory_dict()
        parser = build_parser()
        args = parser.parse_args(["save", json.dumps(memory_dict)])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["memory_id"] == "abc123def456"

    def test_save_verification_failure_exits_2(self, capsys):
        """When save() raises RuntimeError (verification failure), exits 2."""
        mock = MagicMock()
        mock.save.side_effect = RuntimeError(
            "Save verification failed — memory_id abc123 not found after save"
        )
        memory_dict = make_cli_memory_dict()

        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit) as exc_info:
                main(["save", json.dumps(memory_dict)])
        assert exc_info.value.code == 2

    def test_save_verification_failure_error_format(self, capsys):
        """Verification failure outputs SYSTEM_ERROR JSON envelope to stderr."""
        mock = MagicMock()
        mock.save.side_effect = RuntimeError(
            "Save verification failed — memory_id abc123def456 not found after save"
        )
        memory_dict = make_cli_memory_dict()

        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit):
                main(["save", json.dumps(memory_dict)])
        captured = capsys.readouterr()
        assert captured.out == ""
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "SYSTEM_ERROR"
        assert "abc123def456" in err_output["message"]

    def test_save_verification_failure_message_contains_id(self, capsys):
        """Error message includes the memory_id that failed verification."""
        mock = MagicMock()
        mock.save.side_effect = RuntimeError(
            "Save verification failed — memory_id custom_id_xyz not found after save"
        )
        memory_dict = make_cli_memory_dict()

        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit):
                main(["save", json.dumps(memory_dict)])
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert "custom_id_xyz" in err_output["message"]

    def test_save_exception_via_main_exits_2(self, capsys):
        """Any exception from save() is caught by main() as SYSTEM_ERROR."""
        mock = MagicMock()
        mock.save.side_effect = RuntimeError("DB connection lost")
        memory_dict = make_cli_memory_dict()

        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit) as exc_info:
                main(["save", json.dumps(memory_dict)])
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "SYSTEM_ERROR"
        assert "DB connection lost" in err_output["message"]

    def test_save_verification_stdin_path(self, monkeypatch, capsys):
        """Verification failure propagates when input comes via --stdin."""
        mock = MagicMock()
        mock.save.side_effect = RuntimeError(
            "Save verification failed — memory_id stdin_id not found after save"
        )
        memory_dict = make_cli_memory_dict()
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(memory_dict)))

        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit) as exc_info:
                main(["save", "--stdin"])
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "SYSTEM_ERROR"

    def test_save_calls_save_with_dict(self, mock_pact_memory):
        """cmd_save passes the parsed dict to PACTMemory.save()."""
        memory_dict = make_cli_memory_dict(context="verification neutral")
        parser = build_parser()
        args = parser.parse_args(["save", json.dumps(memory_dict)])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_save(args)
        mock_pact_memory.save.assert_called_once_with(memory_dict)


# ---------------------------------------------------------------------------
# Save Verification (API Layer)
# ---------------------------------------------------------------------------

class TestApiSaveVerification:
    """Test save-then-get verification in PACTMemory.save() (#245).

    Verification lives in the API layer so all callers benefit.
    PACTMemory.save() calls self.get(memory_id) after saving and raises
    RuntimeError if the result is None or if memory_id itself is None.

    Tests use a properly initialized PACTMemory with a real temp database
    where possible, mocking only _ensure_ready (to skip dependency checks)
    and sync_to_claude_md (to skip file writes). Failure-path tests mock
    the specific internal that needs to fail.
    """

    @pytest.fixture
    def api_memory(self, tmp_path):
        """Create a PACTMemory instance with a real temp database."""
        import sqlite3
        db_path = tmp_path / "verify_test.db"
        conn = sqlite3.connect(str(db_path))
        create_test_schema(conn)
        conn.close()
        with patch("scripts.memory_api._ensure_ready"), \
             patch("scripts.memory_api.sync_to_claude_md"):
            memory = PACTMemory(
                project_id="test-project",
                session_id="test-session",
                db_path=db_path,
            )
            yield memory

    def test_save_succeeds_with_real_db(self, api_memory):
        """save() returns a memory_id and verification passes against real DB."""
        memory_id = api_memory.save({"context": "verification test"})
        assert memory_id is not None
        assert len(memory_id) > 0
        # Confirm the memory is retrievable (verification already passed internally)
        result = api_memory.get(memory_id)
        assert result is not None

    def test_save_verification_calls_get_with_returned_id(self, api_memory):
        """Verification calls get() with the exact ID from the save."""
        with patch.object(api_memory, "get", wraps=api_memory.get) as spy_get:
            memory_id = api_memory.save({"context": "spy test"})
        spy_get.assert_called_once_with(memory_id)

    def test_save_raises_on_none_memory_id(self, api_memory):
        """save() raises RuntimeError if create_memory returns None."""
        with patch("scripts.memory_api.create_memory", return_value=None):
            with pytest.raises(RuntimeError, match="no memory_id returned"):
                api_memory.save({"context": "test"})

    def test_save_raises_on_verification_failure(self, api_memory):
        """save() raises RuntimeError if get() returns None after save."""
        with patch.object(api_memory, "get", return_value=None):
            with pytest.raises(RuntimeError, match="not found after save"):
                api_memory.save({"context": "test"})

    def test_save_verifies_before_syncing_to_claude_md(self, tmp_path):
        """get() (verification) is called BEFORE sync_to_claude_md().

        Ensures we never write a phantom memory reference to CLAUDE.md.
        Uses a call-order recording pattern to assert ordering.
        """
        import sqlite3
        db_path = tmp_path / "order_test.db"
        conn = sqlite3.connect(str(db_path))
        create_test_schema(conn)
        conn.close()

        call_order = []

        original_get = PACTMemory.get

        def recording_get(self_inner, *args, **kwargs):
            call_order.append("get")
            return original_get(self_inner, *args, **kwargs)

        def recording_sync(*args, **kwargs):
            call_order.append("sync")

        with patch("scripts.memory_api._ensure_ready"), \
             patch("scripts.memory_api.sync_to_claude_md", side_effect=recording_sync), \
             patch.object(PACTMemory, "get", recording_get):
            memory = PACTMemory(
                project_id="test-project",
                session_id="test-session",
                db_path=db_path,
            )
            memory.save({"context": "ordering test"})

        assert call_order == ["get", "sync"]


# ---------------------------------------------------------------------------
# Search Command
# ---------------------------------------------------------------------------

class TestCliSearchCommand:
    """Test the search subcommand handler."""

    def test_search_calls_api(self, mock_pact_memory):
        parser = build_parser()
        args = parser.parse_args(["search", "auth tokens"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_search(args)
        assert exc_info.value.code == 0
        mock_pact_memory.search.assert_called_once_with(
            "auth tokens", current_file=None, limit=5, sync_to_claude=False
        )

    def test_search_with_limit(self, mock_pact_memory):
        parser = build_parser()
        args = parser.parse_args(["search", "query", "--limit", "3"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_search(args)
        mock_pact_memory.search.assert_called_once_with(
            "query", current_file=None, limit=3, sync_to_claude=False
        )

    def test_search_empty_results(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["search", "nonexistent"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_search(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"] == []

    def test_search_passes_current_file(self, mock_pact_memory):
        parser = build_parser()
        args = parser.parse_args(["search", "auth", "--current-file", "/src/auth.py"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_search(args)
        assert exc_info.value.code == 0
        mock_pact_memory.search.assert_called_once_with(
            "auth", current_file="/src/auth.py", limit=5, sync_to_claude=False
        )

    def test_search_with_results(self, mock_pact_memory, capsys):
        mock_memory_obj = MagicMock()
        mock_memory_obj.to_dict.return_value = {
            "id": "mem1",
            "context": "auth work",
        }
        mock_pact_memory.search.return_value = [mock_memory_obj]
        parser = build_parser()
        args = parser.parse_args(["search", "auth"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_search(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert len(output["result"]) == 1
        assert output["result"][0]["id"] == "mem1"


# ---------------------------------------------------------------------------
# List Command
# ---------------------------------------------------------------------------

class TestCliListCommand:
    """Test the list subcommand handler."""

    def test_list_calls_api_with_default_limit(self, mock_pact_memory):
        parser = build_parser()
        args = parser.parse_args(["list"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_list(args)
        mock_pact_memory.list.assert_called_once_with(limit=20)

    def test_list_custom_limit(self, mock_pact_memory):
        parser = build_parser()
        args = parser.parse_args(["list", "--limit", "25"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_list(args)
        mock_pact_memory.list.assert_called_once_with(limit=25)

    def test_list_empty_results(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["list"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_list(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"] == []

    def test_list_with_results(self, mock_pact_memory, capsys):
        mock_obj = MagicMock()
        mock_obj.to_dict.return_value = {"id": "mem1", "context": "test"}
        mock_pact_memory.list.return_value = [mock_obj]
        parser = build_parser()
        args = parser.parse_args(["list"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_list(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert len(output["result"]) == 1


# ---------------------------------------------------------------------------
# Get Command
# ---------------------------------------------------------------------------

class TestCliGetCommand:
    """Test the get subcommand handler."""

    def test_get_existing_memory(self, mock_pact_memory, capsys):
        mock_obj = MagicMock()
        mock_obj.to_dict.return_value = {"id": "abc123", "context": "found"}
        mock_pact_memory.get.return_value = mock_obj
        parser = build_parser()
        args = parser.parse_args(["get", "abc123"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["id"] == "abc123"

    def test_get_not_found(self, mock_pact_memory, capsys):
        mock_pact_memory.get.return_value = None
        parser = build_parser()
        args = parser.parse_args(["get", "nonexistent"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "NOT_FOUND"

    def test_get_passes_db_path(self, mock_pact_memory):
        mock_obj = MagicMock()
        mock_obj.to_dict.return_value = {"id": "abc123"}
        mock_pact_memory.get.return_value = mock_obj
        parser = build_parser()
        args = parser.parse_args(["get", "abc123", "--db-path", "/tmp/t.db"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory) as mock_cls:
            with pytest.raises(SystemExit):
                cmd_get(args, db_path=Path("/tmp/t.db"))
        mock_cls.assert_called_once_with(db_path=Path("/tmp/t.db"))


class TestCliGetPrefixResolution:
    """CLI-layer tests for git-style prefix resolution on `get`."""

    def test_get_unique_prefix_returns_memory(self, mock_pact_memory, capsys):
        from scripts.database import MEMORY_ID_LENGTH
        full_id = "a" * MEMORY_ID_LENGTH
        mock_obj = MagicMock()
        mock_obj.to_dict.return_value = {"id": full_id, "context": "match"}
        # Simulate API-layer prefix resolution: short input still returns the obj
        mock_pact_memory.get.return_value = mock_obj
        parser = build_parser()
        args = parser.parse_args(["get", "aaaa1234"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["id"] == full_id
        # CLI passed the prefix through to the API layer unchanged
        mock_pact_memory.get.assert_called_once_with("aaaa1234")

    def test_get_ambiguous_prefix_returns_match_list(self, mock_pact_memory, capsys):
        from scripts.database import AmbiguousPrefixError
        matches = [
            {"id": "abcd123" + "1" + "0" * 24, "context": "first"},
            {"id": "abcd123" + "2" + "0" * 24, "context": "second"},
        ]
        mock_pact_memory.get.side_effect = AmbiguousPrefixError("abcd123", matches)
        parser = build_parser()
        args = parser.parse_args(["get", "abcd123"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "AMBIGUOUS_PREFIX"
        assert err_output["prefix"] == "abcd123"
        assert err_output["matches"] == matches

    def test_get_too_short_prefix_returns_error(self, mock_pact_memory, capsys):
        from scripts.database import PrefixTooShortError, MIN_PREFIX_LENGTH
        mock_pact_memory.get.side_effect = PrefixTooShortError(
            "abc123", minimum=MIN_PREFIX_LENGTH
        )
        parser = build_parser()
        args = parser.parse_args(["get", "abc123"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "PREFIX_TOO_SHORT"
        assert err_output["minimum"] == MIN_PREFIX_LENGTH

    def test_get_full_hash_unchanged(self, mock_pact_memory, capsys):
        """Full 32-char IDs continue to work unchanged."""
        from scripts.database import MEMORY_ID_LENGTH
        full_id = "a" * MEMORY_ID_LENGTH
        mock_obj = MagicMock()
        mock_obj.to_dict.return_value = {"id": full_id, "context": "found"}
        mock_pact_memory.get.return_value = mock_obj
        parser = build_parser()
        args = parser.parse_args(["get", full_id])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["result"]["id"] == full_id
        mock_pact_memory.get.assert_called_once_with(full_id)


# ---------------------------------------------------------------------------
# Status Command
# ---------------------------------------------------------------------------

class TestCliStatusCommand:
    """Test the status subcommand handler."""

    def test_status_returns_dict(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["status"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_status(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["memory_count"] == 5

    def test_status_includes_project_id(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["status"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_status(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["result"]["project_id"] == "test-project"


# ---------------------------------------------------------------------------
# Setup Command
# ---------------------------------------------------------------------------

class TestCliSetupCommand:
    """Test the setup subcommand handler."""

    def test_setup_success(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["setup"])

        with patch("scripts.cli.ensure_initialized", return_value=True), \
             patch("scripts.cli.get_setup_status", return_value={"initialized": True}):
            with pytest.raises(SystemExit) as exc_info:
                cmd_setup(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["status"] == "ready"

    def test_setup_failure(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["setup"])

        with patch("scripts.cli.ensure_initialized", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                cmd_setup(args)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "SETUP_FAILED"


# ---------------------------------------------------------------------------
# Update Command
# ---------------------------------------------------------------------------

class TestCliUpdateCommand:
    """Test the update subcommand handler."""

    def test_update_existing_memory(self, mock_pact_memory, capsys):
        mock_pact_memory.update.return_value = _FAKE_RESOLVED_ID
        parser = build_parser()
        args = parser.parse_args(["update", "abc1234", '{"context": "updated"}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 0
        mock_pact_memory.update.assert_called_once_with(
            "abc1234", {"context": "updated"}, replace=False
        )
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        # Envelope echoes the RESOLVED full ID returned by the API, not the
        # user-supplied prefix — locks the cycle-2 echo fix.
        assert output["result"]["memory_id"] == _FAKE_RESOLVED_ID

    def test_update_not_found(self, mock_pact_memory, capsys):
        mock_pact_memory.update.return_value = None
        parser = build_parser()
        args = parser.parse_args(["update", "nonexistent", '{"context": "x"}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "NOT_FOUND"

    def test_update_too_short_prefix_returns_error(self, mock_pact_memory, capsys):
        """Locks the PrefixTooShortError-IS-A-ValueError except-clause precedence.

        cmd_update catches PrefixTooShortError BEFORE the generic ValueError
        handler that surfaces field-validation failures. Since
        PrefixTooShortError is a ValueError subclass, the order matters:
        a swap would silently route prefix-too-short to the ValueError
        envelope (with allowed_fields list and exit_code=2) instead of the
        intended PREFIX_TOO_SHORT envelope.
        """
        from scripts.database import PrefixTooShortError
        mock_pact_memory.update.side_effect = PrefixTooShortError("abc", minimum=7)
        parser = build_parser()
        args = parser.parse_args(["update", "abc", '{"context": "x"}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "PREFIX_TOO_SHORT"
        assert err_output["minimum"] == 7
        # Negative assertion: the ValueError envelope shape would have
        # this key; PREFIX_TOO_SHORT must NOT.
        assert "allowed_fields" not in err_output

    def test_update_with_stdin(self, mock_pact_memory, monkeypatch):
        mock_pact_memory.update.return_value = _FAKE_RESOLVED_ID
        monkeypatch.setattr("sys.stdin", StringIO('{"context": "from stdin"}'))
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", "--stdin"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 0
        mock_pact_memory.update.assert_called_once_with(
            "abc123", {"context": "from stdin"}, replace=False
        )

    def test_update_invalid_json(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", "not{valid"])

        with pytest.raises(SystemExit) as exc_info:
            cmd_update(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "INVALID_JSON"

    def test_update_non_dict_json(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", '["a list"]'])

        with pytest.raises(SystemExit) as exc_info:
            cmd_update(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "INVALID_INPUT"

    def test_update_no_input(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["update", "abc123"])

        with pytest.raises(SystemExit) as exc_info:
            cmd_update(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "MISSING_INPUT"

    def test_update_passes_db_path(self, mock_pact_memory):
        mock_pact_memory.update.return_value = _FAKE_RESOLVED_ID
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", '{"context": "x"}', "--db-path", "/tmp/t.db"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory) as mock_cls:
            with pytest.raises(SystemExit):
                cmd_update(args, db_path=Path("/tmp/t.db"))
        mock_cls.assert_called_once_with(db_path=Path("/tmp/t.db"))


class TestCliUpdateReplaceFlag:
    """Test the --replace flag and ValueError envelope on the update subcommand."""

    def test_replace_flag_forwards_true(self, mock_pact_memory):
        mock_pact_memory.update.return_value = _FAKE_RESOLVED_ID
        parser = build_parser()
        args = parser.parse_args(
            ["update", "abc123", '{"lessons": ["x"]}', "--replace"]
        )

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 0
        mock_pact_memory.update.assert_called_once_with(
            "abc123", {"lessons": ["x"]}, replace=True
        )

    def test_replace_default_is_false(self, mock_pact_memory):
        mock_pact_memory.update.return_value = _FAKE_RESOLVED_ID
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", '{"lessons": ["x"]}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_update(args)
        _, kwargs = mock_pact_memory.update.call_args
        assert kwargs == {"replace": False}

    def test_value_error_envelope_exit_code_2(self, mock_pact_memory, capsys):
        mock_pact_memory.update.side_effect = ValueError(
            "Unknown memory field(s) for update: 'bogus'. Allowed fields: context, goal"
        )
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", '{"bogus": 1}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 2
        err_output = json.loads(capsys.readouterr().err)
        assert err_output["ok"] is False
        assert err_output["error"] == "ValueError"
        assert "Unknown memory field" in err_output["message"]
        assert "allowed_fields" in err_output
        assert isinstance(err_output["allowed_fields"], list)
        assert "context" in err_output["allowed_fields"]

    def test_value_error_from_subobject(self, mock_pact_memory, capsys):
        mock_pact_memory.update.side_effect = ValueError(
            "Unknown key(s) for Entity: 'description'. Allowed: name, type, notes"
        )
        parser = build_parser()
        args = parser.parse_args(
            ["update", "abc123", '{"entities": [{"description": "x"}]}']
        )

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 2
        err_output = json.loads(capsys.readouterr().err)
        assert err_output["error"] == "ValueError"
        assert "Entity" in err_output["message"]


class TestCliSaveValueError:
    """Test ValueError envelope on the save subcommand."""

    def test_save_value_error_envelope(self, mock_pact_memory, capsys):
        mock_pact_memory.save.side_effect = ValueError(
            "Unknown memory field(s) for save: 'bogus'. Allowed fields: context, goal"
        )
        parser = build_parser()
        args = parser.parse_args(["save", '{"bogus": 1}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 2
        err_output = json.loads(capsys.readouterr().err)
        assert err_output["error"] == "ValueError"
        assert "allowed_fields" in err_output

    def test_save_subobject_value_error(self, mock_pact_memory, capsys):
        """Bug 3 part 2 (#374): unknown sub-object keys on the save path
        route through the same cmd_save ValueError handler as top-level
        key errors. Exit code 2, allowed_fields present in envelope.
        Before the fix, create_memory silently accepted junk sub-object
        keys so this handler was unreachable for sub-object errors."""
        mock_pact_memory.save.side_effect = ValueError(
            "Unknown keys for TaskItem: ['id', 'subject']. "
            "Allowed keys: notes, priority, status, task"
        )
        parser = build_parser()
        args = parser.parse_args([
            "save",
            '{"context":"x","active_tasks":[{"id":"a","subject":"b"}]}',
        ])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 2
        err_output = json.loads(capsys.readouterr().err)
        assert err_output["error"] == "ValueError"
        assert "TaskItem" in err_output["message"]
        assert "allowed_fields" in err_output


class TestCliSystemErrorPathScrubbing:
    """SYSTEM_ERROR envelope scrubs $HOME from exception messages (#374 R2-3a).

    main() wraps handler exceptions and rewrites str(exc) with
    os.path.expanduser("~") replaced by "~" before emitting the SYSTEM_ERROR
    envelope (cli.py:326). Without scrubbing, absolute paths from internal
    exception messages would leak user home paths into stderr where JSON
    envelopes are commonly piped into shared logs.

    Regression-guards commit f4c0d7d: "fix(pact-memory): SYSTEM_ERROR
    envelope path scrubbing".
    """

    def test_system_error_scrubs_home_path(self, capsys):
        """str(exc) containing expanduser('~') is rewritten to '~' in envelope."""
        home = os.path.expanduser("~")
        # Exception message embeds the expanded home path inside a plausible
        # internal path — this is what a raw sqlite/OSError would contain.
        leaky_path = os.path.join(home, ".claude", "pact-memory", "memory.db")
        leaky_message = f"unable to open database file: {leaky_path}"

        mock = MagicMock()
        mock.save.side_effect = RuntimeError(leaky_message)
        memory_dict = make_cli_memory_dict()

        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit) as exc_info:
                main(["save", json.dumps(memory_dict)])
        assert exc_info.value.code == 2

        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "SYSTEM_ERROR"

        msg = err_output["message"]
        # Positive: the scrubbed "~" marker is present where the home used to be.
        assert "~/.claude/pact-memory/memory.db" in msg
        # Negative: the expanded absolute home path must NOT appear.
        assert home not in msg, (
            f"SYSTEM_ERROR envelope leaked absolute home path: {msg!r}"
        )

    def test_system_error_scrubs_realpath_home_form(self, capsys):
        """macOS symlink-resolved $HOME form is also scrubbed.

        On macOS, os.path.expanduser('~') may differ from the raw HOME env var
        due to /var → /private/var or /tmp → /private/tmp symlinks. cli.py
        scrubs by replacing os.path.expanduser('~') with '~' — this test asserts
        that same form is what gets scrubbed when the exception message came
        from a call site that used expanduser('~') as its path base.
        """
        expanded_home = os.path.expanduser("~")
        # Build a leaky path using the expanduser form — this is the form
        # cli.py's str(exc).replace(expanduser('~'), '~') will rewrite.
        leaky_path = os.path.join(expanded_home, "Library", "logs", "memory.log")
        leaky_message = f"permission denied writing {leaky_path}"

        mock = MagicMock()
        mock.save.side_effect = OSError(leaky_message)
        memory_dict = make_cli_memory_dict()

        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit) as exc_info:
                main(["save", json.dumps(memory_dict)])
        assert exc_info.value.code == 2

        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        msg = err_output["message"]
        assert "~/Library/logs/memory.log" in msg
        assert expanded_home not in msg, (
            f"SYSTEM_ERROR envelope leaked expanded home path: {msg!r}"
        )


# ---------------------------------------------------------------------------
# Delete Command
# ---------------------------------------------------------------------------

class TestCliDeleteCommand:
    """Test the delete subcommand handler."""

    def test_delete_existing_memory(self, mock_pact_memory, capsys):
        mock_pact_memory.delete.return_value = _FAKE_RESOLVED_ID
        parser = build_parser()
        args = parser.parse_args(["delete", "abc1234"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_delete(args)
        assert exc_info.value.code == 0
        mock_pact_memory.delete.assert_called_once_with("abc1234")
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["deleted"] is True
        # Envelope echoes the RESOLVED full ID, not the user-supplied prefix.
        assert output["result"]["memory_id"] == _FAKE_RESOLVED_ID

    def test_delete_not_found(self, mock_pact_memory, capsys):
        mock_pact_memory.delete.return_value = None
        parser = build_parser()
        args = parser.parse_args(["delete", "nonexistent"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_delete(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "NOT_FOUND"

    def test_delete_too_short_prefix_returns_error(self, mock_pact_memory, capsys):
        """cmd_delete surfaces PREFIX_TOO_SHORT envelope, not a generic error.

        cmd_delete has no field-validation ValueError path, but the
        explicit `except PrefixTooShortError` clause still matters: it
        carries the `minimum` field through to the envelope rather than
        falling through to a generic exception handler.
        """
        from scripts.database import PrefixTooShortError
        mock_pact_memory.delete.side_effect = PrefixTooShortError("abc", minimum=7)
        parser = build_parser()
        args = parser.parse_args(["delete", "abc"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_delete(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "PREFIX_TOO_SHORT"
        assert err_output["minimum"] == 7

    def test_delete_passes_db_path(self, mock_pact_memory):
        mock_pact_memory.delete.return_value = _FAKE_RESOLVED_ID
        parser = build_parser()
        args = parser.parse_args(["delete", "abc123", "--db-path", "/tmp/t.db"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory) as mock_cls:
            with pytest.raises(SystemExit):
                cmd_delete(args, db_path=Path("/tmp/t.db"))
        mock_cls.assert_called_once_with(db_path=Path("/tmp/t.db"))


# ---------------------------------------------------------------------------
# Output Format
# ---------------------------------------------------------------------------

class TestCliOutputFormat:
    """Test JSON output envelope consistency."""

    def test_success_envelope_structure(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["list"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_list(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "ok" in output
        assert "result" in output
        assert output["ok"] is True

    def test_error_envelope_structure(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["save", "bad json{"])

        with pytest.raises(SystemExit):
            cmd_save(args)
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert "ok" in err_output
        assert "error" in err_output
        assert "message" in err_output
        assert err_output["ok"] is False

    def test_success_output_is_indented(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["list"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_list(args)
        captured = capsys.readouterr()
        # Indented JSON has newlines
        assert "\n" in captured.out

    def test_error_output_goes_to_stderr(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["save", "bad"])

        with pytest.raises(SystemExit):
            cmd_save(args)
        captured = capsys.readouterr()
        assert captured.out == ""  # Nothing on stdout
        assert captured.err != ""  # Error on stderr

    def test_success_output_goes_to_stdout(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["list"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_list(args)
        captured = capsys.readouterr()
        assert captured.out != ""
        assert captured.err == ""

    def test_unicode_in_output(self, mock_pact_memory, capsys):
        mock_obj = MagicMock()
        mock_obj.to_dict.return_value = {
            "id": "mem1",
            "context": "Unicode: \u00e9\u00e0\u00fc \u4e16\u754c \ud83d\ude80",
        }
        mock_pact_memory.search.return_value = [mock_obj]
        parser = build_parser()
        args = parser.parse_args(["search", "unicode"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_search(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "\u00e9" in output["result"][0]["context"]


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------

class TestLiveDbGuardScope:
    """The live-DB guard is scoped to SPAWNED CHILDREN, deliberately.

    `_refuse_live_db_under_pytest` refuses the real store when a test
    process spawned the CLI with no `--db-path`. It must NOT fire for the
    in-process `main()` calls this file makes: they patch `PACTMemory` and
    open no store, and `test_main_db_path_none_when_not_specified` exists
    precisely to assert that an omitted `--db-path` yields `db_path=None`.
    A guard firing there would refuse a contract the CLI is meant to have.

    The separation is available because the same fact that FORCES the env
    signal for spawned children also identifies them: a child is a fresh
    interpreter with no `pytest` in `sys.modules`.

    This is pinned rather than left implicit because the ten in-process tests
    below pass for this reason, and a maintainer who removed the `sys.modules`
    check would see ten failures with no statement of why the check was there.

    RESIDUAL, stated: an in-process `main()` call with a REAL `PACTMemory` and
    no `--db-path` still reaches the live store. Nothing catches that; nothing
    currently does it.
    """

    def test_in_process_callers_are_exempt(self):
        assert "pytest" in sys.modules, (
            "pytest is absent from this process, so this test cannot "
            "distinguish the in-process case from the spawned-child case"
        )
        # Returns instead of raising SystemExit — the in-process branch.
        assert _refuse_live_db_under_pytest(None) is None

    def test_an_explicit_db_path_is_never_refused(self, tmp_path, monkeypatch):
        """Non-vacuity: the db_path check must not be MASKED by the exemption.

        Asserting this from an ordinary in-process test measures nothing. Both
        early returns fire here — `db_path is not None` AND `pytest in
        sys.modules` — so the call returns None whichever one is doing the
        work, and it keeps returning None when the db_path check is deleted
        outright. Verified by mutation: with that check removed the assertion
        still passed, while the child-side spawn test in test_archive_pin.py
        caught it. A control that cannot fail is not a control.

        So the in-process exemption is SUPPRESSED first — pytest is hidden from
        `sys.modules` and the inherited child signal is set, which is exactly
        the state a spawned child is in. The db_path check is then the ONLY
        thing that can prevent a refusal, and the assertion is coupled to it.
        """
        monkeypatch.delitem(sys.modules, "pytest", raising=False)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "sentinel::test (call)")
        assert "pytest" not in sys.modules, (
            "precondition: the in-process exemption is still live, so this "
            "test cannot show the db_path check is what prevents the refusal"
        )
        assert _refuse_live_db_under_pytest(tmp_path / "x.db") is None


class TestCliErrorHandling:
    """Test error paths, exit codes, and the main() entry point."""

    def test_no_command_exits_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    def test_system_error_exits_2(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["list"])

        mock = MagicMock()
        mock.list.side_effect = RuntimeError("DB connection failed")
        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit) as exc_info:
                main(["list"])
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "SYSTEM_ERROR"
        assert "DB connection failed" in err_output["message"]

    def test_main_dispatches_to_correct_command(self, mock_pact_memory, capsys):
        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                main(["list"])
        assert exc_info.value.code == 0
        mock_pact_memory.list.assert_called_once()

    def test_main_passes_db_path(self, mock_pact_memory, memory_store):
        """THE STORE MUST BE PRESENT, AND THAT IS THE SUBJECT AND NOT SETUP.

        `main` refuses a `--db-path` naming a store that is absent, BEFORE it
        reaches a handler. A patched `PACTMemory` does not change that: the
        refusal precedes the handler, so an absent path would end this arm at
        the boundary and the forwarding assertion below would never run.

        An arm that names an absent path here does not fail loudly. It reports
        that `PACTMemory` was called 0 times, which reads as a forwarding
        defect rather than as its own precondition.
        """
        store = memory_store("test.db")
        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory) as mock_cls:
            with pytest.raises(SystemExit):
                main(["list", "--db-path", str(store)])
        mock_cls.assert_called_once_with(db_path=Path(str(store)))

    def test_main_db_path_none_when_not_specified(self, mock_pact_memory):
        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory) as mock_cls:
            with pytest.raises(SystemExit):
                main(["list"])
        mock_cls.assert_called_once_with(db_path=None)

    def test_save_api_exception_exits_2(self, capsys):
        mock = MagicMock()
        mock.save.side_effect = Exception("Disk full")
        memory_dict = make_cli_memory_dict()

        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit) as exc_info:
                main(["save", json.dumps(memory_dict)])
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "SYSTEM_ERROR"


# ---------------------------------------------------------------------------
# Subprocess E2E Tests
# ---------------------------------------------------------------------------

class TestCliSubprocess:
    """True black-box E2E tests via subprocess.run."""

    def test_no_command_exits_1(self, cli_script_path):
        result = subprocess.run(
            [sys.executable, cli_script_path],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1

    def test_save_and_get_roundtrip(self, cli_script_path, cli_db, tmp_path):
        """E2E roundtrip, and THE ONLY E2E ARM THAT DECLARES A ROOT.

        Its eight siblings in this class suppress with `--no-sync`, which is
        correct for them but means the declared-anchor path had no coverage
        through a real subprocess -- only through the archive wiring, which
        supplies the flag itself. This arm passes `--claude-md-root` explicitly,
        the way any caller can, and asserts BOTH that the sync was permitted
        and that the write landed UNDER the declared root.

        A DECLARED ROOT IS WHY THIS SAVE MAY SYNC AT ALL. The child inherits
        PYTEST_CURRENT_TEST and has no pytest module, so the ambient guard would
        refuse it; declaring a root exempts that refusal, and the write is then
        bounded by containment instead of by the guard.
        """
        memory_dict = make_cli_memory_dict()
        json_str = json.dumps(memory_dict)

        # A sandbox project the write is allowed to land in.
        declared_root = tmp_path / "declared-project"
        declared_root.mkdir()
        claude_md = declared_root / "CLAUDE.md"
        claude_md.write_text(
            "# Declared\n\n## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. -->\n",
            encoding="utf-8",
        )
        before = claude_md.read_bytes()

        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(declared_root)

        # Save
        save_result = subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--claude-md-root", str(declared_root), "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60, env=env,
        )
        assert save_result.returncode == 0, f"save stderr: {save_result.stderr}"
        save_output = json.loads(save_result.stdout)
        assert save_output["ok"] is True
        memory_id = save_output["result"]["memory_id"]

        # THE VERDICT, and then WHERE IT LANDED. Asserting only the verdict
        # would pass for a sync that reported success without writing.
        assert save_output["result"]["sync_status"] == "wrote", (
            f"a declared root did not permit the sync: {save_output}"
        )
        assert claude_md.read_bytes() != before, (
            "the sync reported `wrote` but the file under the declared root is "
            "unchanged"
        )

        # Get
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", memory_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 0, f"get stderr: {get_result.stderr}"
        get_output = json.loads(get_result.stdout)
        assert get_output["ok"] is True
        assert get_output["result"]["context"] == memory_dict["context"]

    def test_get_unique_prefix_resolves_via_subprocess(self, cli_script_path, cli_db):
        """E2E: real cli.py → memory_api → database → SQL roundtrip on a unique prefix."""
        from scripts.database import create_memory
        try:
            import pysqlite3 as sqlite3
        except ImportError:
            import sqlite3

        full_id = "fa11ce5" + "1" + "0" * 24  # 32 chars; prefix "fa11ce5" is 7 chars
        conn = sqlite3.connect(str(cli_db))
        conn.row_factory = sqlite3.Row
        with patch("scripts.database.ensure_initialized"):
            create_memory(conn, {"id": full_id, "context": "unique-prefix-roundtrip"})
        conn.commit()
        conn.close()

        result = subprocess.run(
            [sys.executable, cli_script_path, "get", "fa11ce5",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert output["result"]["id"] == full_id
        assert output["result"]["context"] == "unique-prefix-roundtrip"

    def test_get_ambiguous_prefix_envelope_via_subprocess(self, cli_script_path, cli_db):
        """E2E: ambiguous prefix returns AMBIGUOUS_PREFIX envelope with full match list."""
        from scripts.database import create_memory
        try:
            import pysqlite3 as sqlite3
        except ImportError:
            import sqlite3

        id_a = "ambig00" + "a" + "0" * 24
        id_b = "ambig00" + "b" + "0" * 24
        conn = sqlite3.connect(str(cli_db))
        conn.row_factory = sqlite3.Row
        with patch("scripts.database.ensure_initialized"):
            create_memory(conn, {"id": id_a, "context": "first"})
            create_memory(conn, {"id": id_b, "context": "second"})
        conn.commit()
        conn.close()

        result = subprocess.run(
            [sys.executable, cli_script_path, "get", "ambig00",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1, f"stdout: {result.stdout}, stderr: {result.stderr}"
        err_output = json.loads(result.stderr)
        assert err_output["ok"] is False
        assert err_output["error"] == "AMBIGUOUS_PREFIX"
        assert err_output["prefix"] == "ambig00"
        match_ids = sorted(m["id"] for m in err_output["matches"])
        assert match_ids == sorted([id_a, id_b])

    def test_get_too_short_prefix_envelope_via_subprocess(self, cli_script_path, cli_db):
        """E2E: prefix shorter than minimum returns PREFIX_TOO_SHORT envelope.

        Uses a 6-char prefix — exactly MIN_PREFIX_LENGTH - 1 — to exercise
        the gate at its sharpest boundary rather than a trivially-short input.
        """
        from scripts.database import MIN_PREFIX_LENGTH
        too_short = "abcdef"  # exactly MIN_PREFIX_LENGTH - 1
        assert len(too_short) == MIN_PREFIX_LENGTH - 1

        result = subprocess.run(
            [sys.executable, cli_script_path, "get", too_short,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1, f"stdout: {result.stdout}, stderr: {result.stderr}"
        err_output = json.loads(result.stderr)
        assert err_output["ok"] is False
        assert err_output["error"] == "PREFIX_TOO_SHORT"
        assert err_output["minimum"] == MIN_PREFIX_LENGTH

    # ----- F7: update/delete prefix resolution (subprocess E2E) -----

    def _seed_memory(self, db_path, memory_id, context):
        """Helper: insert one memory via storage primitive (bypasses CLI ingress strip)."""
        from scripts.database import create_memory
        try:
            import pysqlite3 as sqlite3
        except ImportError:
            import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        with patch("scripts.database.ensure_initialized"):
            create_memory(conn, {"id": memory_id, "context": context})
        conn.commit()
        conn.close()

    def test_update_unique_prefix_resolves_and_mutates(self, cli_script_path, cli_db):
        """E2E: update by unique prefix resolves to full ID and mutates the memory."""
        full_id = "upd1nfo" + "1" + "0" * 24
        self._seed_memory(cli_db, full_id, "before-update")

        update_payload = json.dumps({"context": "after-update"})
        update_result = subprocess.run(
            [sys.executable, cli_script_path, "update", "upd1nfo", update_payload,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert update_result.returncode == 0, f"stderr: {update_result.stderr}"
        assert json.loads(update_result.stdout)["ok"] is True

        # Verify the mutation landed by reading the FULL ID
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", full_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 0
        assert json.loads(get_result.stdout)["result"]["context"] == "after-update"

    def test_update_ambiguous_prefix_refuses_with_envelope(self, cli_script_path, cli_db):
        """E2E: ambiguous prefix on update refuses with AMBIGUOUS_PREFIX, leaves both rows untouched."""
        id_a = "updambi" + "a" + "0" * 24
        id_b = "updambi" + "b" + "0" * 24
        self._seed_memory(cli_db, id_a, "original-a")
        self._seed_memory(cli_db, id_b, "original-b")

        update_result = subprocess.run(
            [sys.executable, cli_script_path, "update", "updambi",
             json.dumps({"context": "should-not-land"}),
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert update_result.returncode == 1, f"stdout: {update_result.stdout}"
        err = json.loads(update_result.stderr)
        assert err["error"] == "AMBIGUOUS_PREFIX"
        assert err["prefix"] == "updambi"
        assert err["matches_capped"] is False
        assert err["total_matches"] == 2
        match_ids = sorted(m["id"] for m in err["matches"])
        assert match_ids == sorted([id_a, id_b])

        # Verify NEITHER row was mutated
        for fid, original in [(id_a, "original-a"), (id_b, "original-b")]:
            get_result = subprocess.run(
                [sys.executable, cli_script_path, "get", fid,
                 "--db-path", str(cli_db)],
                capture_output=True, text=True, timeout=60,
            )
            assert json.loads(get_result.stdout)["result"]["context"] == original

    def test_update_full_hash_unchanged(self, cli_script_path, cli_db):
        """E2E: full 32-char ID on update bypasses resolver and works as before."""
        full_id = "fu11upd0" + "0" * 24
        self._seed_memory(cli_db, full_id, "pre")

        update_result = subprocess.run(
            [sys.executable, cli_script_path, "update", full_id,
             json.dumps({"context": "post"}),
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert update_result.returncode == 0, f"stderr: {update_result.stderr}"

        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", full_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert json.loads(get_result.stdout)["result"]["context"] == "post"

    def test_delete_unique_prefix_resolves_and_removes(self, cli_script_path, cli_db):
        """E2E: delete by unique prefix resolves to full ID and removes the memory."""
        full_id = "del1nfo" + "1" + "0" * 24
        self._seed_memory(cli_db, full_id, "to-be-deleted")

        delete_result = subprocess.run(
            [sys.executable, cli_script_path, "delete", "del1nfo",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert delete_result.returncode == 0, f"stderr: {delete_result.stderr}"
        out = json.loads(delete_result.stdout)
        assert out["ok"] is True
        assert out["result"]["deleted"] is True

        # Verify it's actually gone via full ID
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", full_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 1
        assert json.loads(get_result.stderr)["error"] == "NOT_FOUND"

    def test_delete_ambiguous_prefix_refuses_with_envelope(self, cli_script_path, cli_db):
        """E2E: ambiguous prefix on delete refuses, leaves both rows present."""
        id_a = "delambi" + "a" + "0" * 24
        id_b = "delambi" + "b" + "0" * 24
        self._seed_memory(cli_db, id_a, "stays-a")
        self._seed_memory(cli_db, id_b, "stays-b")

        delete_result = subprocess.run(
            [sys.executable, cli_script_path, "delete", "delambi",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert delete_result.returncode == 1, f"stdout: {delete_result.stdout}"
        err = json.loads(delete_result.stderr)
        assert err["error"] == "AMBIGUOUS_PREFIX"
        assert err["prefix"] == "delambi"
        assert err["matches_capped"] is False
        assert err["total_matches"] == 2

        # Verify BOTH rows survive
        for fid in (id_a, id_b):
            get_result = subprocess.run(
                [sys.executable, cli_script_path, "get", fid,
                 "--db-path", str(cli_db)],
                capture_output=True, text=True, timeout=60,
            )
            assert get_result.returncode == 0, f"row {fid} was unexpectedly deleted"

    def test_delete_full_hash_unchanged(self, cli_script_path, cli_db):
        """E2E: full 32-char ID on delete bypasses resolver and works as before."""
        full_id = "fu11del0" + "0" * 24
        self._seed_memory(cli_db, full_id, "doomed")

        delete_result = subprocess.run(
            [sys.executable, cli_script_path, "delete", full_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert delete_result.returncode == 0, f"stderr: {delete_result.stderr}"

        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", full_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 1
        assert json.loads(get_result.stderr)["error"] == "NOT_FOUND"

    # ----- Cycle 2: echo-fix regression — envelope must echo resolved full ID -----

    def test_update_success_envelope_returns_resolved_full_id(self, cli_script_path, cli_db):
        """E2E: update success envelope echoes the resolved full ID, not the user prefix.

        Calling `pact-memory update <prefix>` and then chaining a follow-up
        operation against the returned ID must work without re-running prefix
        resolution. Locks the cycle-2 echo fix.
        """
        full_id = "ech0upd" + "1" + "0" * 24  # 32 chars
        self._seed_memory(cli_db, full_id, "before")

        prefix = "ech0upd"
        assert prefix != full_id  # The whole point: caller passed a prefix
        update_result = subprocess.run(
            [sys.executable, cli_script_path, "update", prefix,
             json.dumps({"context": "after"}),
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert update_result.returncode == 0, f"stderr: {update_result.stderr}"
        out = json.loads(update_result.stdout)
        assert out["ok"] is True
        # Regression assertion: envelope must surface the RESOLVED full ID,
        # not the user-supplied prefix.
        assert out["result"]["memory_id"] == full_id
        assert out["result"]["memory_id"] != prefix

    def test_delete_success_envelope_returns_resolved_full_id(self, cli_script_path, cli_db):
        """E2E: delete success envelope echoes the resolved full ID, not the user prefix."""
        full_id = "ech0del" + "1" + "0" * 24
        self._seed_memory(cli_db, full_id, "to-go")

        prefix = "ech0del"
        assert prefix != full_id
        delete_result = subprocess.run(
            [sys.executable, cli_script_path, "delete", prefix,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert delete_result.returncode == 0, f"stderr: {delete_result.stderr}"
        out = json.loads(delete_result.stdout)
        assert out["ok"] is True
        assert out["result"]["deleted"] is True
        assert out["result"]["memory_id"] == full_id
        assert out["result"]["memory_id"] != prefix

    # ----- Cycle 3 H1: case-insensitive matching extends to FULL-32-char branch -----

    def test_uppercase_full_id_resolves_to_lowercase_stored_get(self, cli_script_path, cli_db):
        """E2E: passing the full ID in UPPERCASE to `get` still resolves the lowercase-stored row.

        The 32-char branch bypasses prefix resolution entirely; case-normalization
        must apply on this branch too, otherwise an uppercase full ID misses
        despite being byte-identical-after-lower() to the stored ID.
        """
        full_id_lower = "abcdef0" + "1" + "0" * 24
        self._seed_memory(cli_db, full_id_lower, "case-roundtrip")

        full_id_upper = full_id_lower.upper()
        assert full_id_upper != full_id_lower  # confirm the input differs

        result = subprocess.run(
            [sys.executable, cli_script_path, "get", full_id_upper,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        out = json.loads(result.stdout)
        assert out["ok"] is True
        assert out["result"]["id"] == full_id_lower
        assert out["result"]["context"] == "case-roundtrip"

    def test_uppercase_full_id_resolves_to_lowercase_stored_update(self, cli_script_path, cli_db):
        """E2E: passing the full ID in UPPERCASE to `update` mutates the lowercase-stored row."""
        full_id_lower = "abcdef0" + "2" + "0" * 24
        self._seed_memory(cli_db, full_id_lower, "before-case")

        update_result = subprocess.run(
            [sys.executable, cli_script_path, "update", full_id_lower.upper(),
             json.dumps({"context": "after-case"}),
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert update_result.returncode == 0, f"stderr: {update_result.stderr}"
        out = json.loads(update_result.stdout)
        assert out["ok"] is True
        # Resolved-id echo must be the lowercase storage form, not the uppercase input.
        assert out["result"]["memory_id"] == full_id_lower

        # Verify mutation actually landed via lowercase GET
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", full_id_lower,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert json.loads(get_result.stdout)["result"]["context"] == "after-case"

    def test_uppercase_full_id_resolves_to_lowercase_stored_delete(self, cli_script_path, cli_db):
        """E2E: passing the full ID in UPPERCASE to `delete` removes the lowercase-stored row."""
        full_id_lower = "abcdef0" + "3" + "0" * 24
        self._seed_memory(cli_db, full_id_lower, "case-doomed")

        delete_result = subprocess.run(
            [sys.executable, cli_script_path, "delete", full_id_lower.upper(),
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert delete_result.returncode == 0, f"stderr: {delete_result.stderr}"
        out = json.loads(delete_result.stdout)
        assert out["ok"] is True
        assert out["result"]["memory_id"] == full_id_lower

        # Verify removal via lowercase GET → NOT_FOUND
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", full_id_lower,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 1
        assert json.loads(get_result.stderr)["error"] == "NOT_FOUND"

    # ----- Cycle 3 H3: AMBIGUOUS_PREFIX envelope scrubs $HOME from match contexts -----

    def test_ambiguous_prefix_scrubs_context_in_envelope(self, monkeypatch, cli_script_path, cli_db):
        """E2E: AMBIGUOUS_PREFIX envelope replaces user $HOME path with '~' in each match context.

        Contexts may carry absolute paths from agent notes; piping the envelope
        to a log file would otherwise leak the operating user's home directory.
        Scrub is applied per-match in cli.py at all 3 ambiguous-prefix call
        sites (cmd_get, cmd_update, cmd_delete); this test exercises cmd_get
        as representative.
        """
        # Subprocess + truncation isolation: the autouse conftest fixture
        # redirects Path.home() -> tmp_path IN-PROCESS only (setattr does not
        # cross to the child), and this test verifies $HOME scrubbing, so the
        # subprocess must read the SAME home the parent captures. Two reasons a
        # SHORT controlled home is needed here (not the fixture's tmp_path):
        #  (a) SUBPROCESS: the child reads $HOME, not the setattr — override
        #      Path.home() AND set $HOME together so parent and child agree.
        #  (b) TRUNCATION: the envelope's per-match context snippet is capped
        #      at descriptor_chars=60 (database.py). The fixture's tmp_path is
        #      ~98 chars on macOS (/private/var/folders/...), so the snippet
        #      cuts the home path mid-way, before "/Sites/" — _scrub can never
        #      match the full home and "~/Sites/..." never appears. A short
        #      home keeps "Note about <home>/Sites/secret-project/file.py"
        #      (52 chars) inside the 60-char window (same shape as the original
        #      /Users/mj assumption). This test writes via --db-path (never
        #      under ~/.claude), so a controlled fake home re-opens no #1186
        #      leak. See conftest _isolate_config_root_to_tmp "WHY NOT ALSO SET
        #      HOME ENV".
        short_home = "/tmp/pmscrub"
        monkeypatch.setattr(Path, "home", lambda: Path(short_home))
        monkeypatch.setenv("HOME", short_home)
        home = str(Path.home())
        # Guard against an unresolved tilde — _scrub no-ops if HOME is unset
        # or returns the literal '~', and this test would not be exercising
        # the scrub path in that case.
        assert home and home != "~", f"HOME must be a real path for this test (got {home!r})"

        sensitive_path = f"{home}/Sites/secret-project/file.py"
        id_a = "scrub00" + "a" + "0" * 24
        id_b = "scrub00" + "b" + "0" * 24
        self._seed_memory(cli_db, id_a, f"Note about {sensitive_path}")
        self._seed_memory(cli_db, id_b, f"Other note also at {sensitive_path}")

        result = subprocess.run(
            [sys.executable, cli_script_path, "get", "scrub00",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1, f"stdout: {result.stdout}"
        err = json.loads(result.stderr)
        assert err["error"] == "AMBIGUOUS_PREFIX"
        assert err["prefix"] == "scrub00"

        # Per-match contexts must NOT contain the literal $HOME expansion.
        for match in err["matches"]:
            assert home not in match["context"], (
                f"$HOME ({home!r}) leaked into match context: {match['context']!r}"
            )
            # Positive assertion: the substituted form is present.
            assert "~/Sites/secret-project/file.py" in match["context"]
            # IDs are hex and must NOT be scrubbed; they should be the
            # untouched 32-char storage form. Per backend H3 contract.
            assert match["id"] in (id_a, id_b)
            assert len(match["id"]) == 32

    def test_save_via_stdin(self, cli_script_path, cli_db):
        memory_dict = make_cli_memory_dict(context="stdin test")
        json_str = json.dumps(memory_dict)

        result = subprocess.run(
            [sys.executable, cli_script_path, "save", "--stdin",
             "--no-sync", "--db-path", str(cli_db)],
            input=json_str,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert "memory_id" in output["result"]

    def test_save_invalid_json_exits_1(self, cli_script_path, cli_db):
        result = subprocess.run(
            [sys.executable, cli_script_path, "save", "not{valid",
             "--no-sync", "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1
        err_output = json.loads(result.stderr)
        assert err_output["ok"] is False
        assert err_output["error"] == "INVALID_JSON"

    def test_list_returns_saved_memory(self, cli_script_path, cli_db):
        memory_dict = make_cli_memory_dict(context="list test memory")
        json_str = json.dumps(memory_dict)

        # Save first
        subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--no-sync", "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )

        # List
        result = subprocess.run(
            [sys.executable, cli_script_path, "list",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert len(output["result"]) >= 1
        contexts = [m["context"] for m in output["result"]]
        assert "list test memory" in contexts

    def test_search_returns_results(self, cli_script_path, cli_db):
        memory_dict = make_cli_memory_dict(context="searchable authentication test")
        json_str = json.dumps(memory_dict)

        # Save first
        subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--no-sync", "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )

        # Search
        result = subprocess.run(
            [sys.executable, cli_script_path, "search", "authentication",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["ok"] is True
        # THE CONTENT ASSERTION, and it is the point of this arm now.
        #
        # This used to assert the SHAPE only, because `graph_enhanced_search`
        # opened its own connection with no argument and read the default store
        # rather than `--db-path`. The comment here recorded that as a known
        # limitation. The store scope closed it: the search layer accepts no
        # path and needs none, because it inherits the scope the CLI binds.
        #
        # A SHAPE ASSERTION CANNOT GO RED HERE. An empty list is a list, so the
        # old arm passed while the search read another store entirely.
        assert isinstance(output["result"], list)
        contexts = [m["context"] for m in output["result"]]
        assert "searchable authentication test" in contexts, (
            f"the search did not read --db-path; got {contexts}"
        )

    def test_get_not_found_exits_1(self, cli_script_path, cli_db):
        result = subprocess.run(
            [sys.executable, cli_script_path, "get", "nonexistent_id",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1
        err_output = json.loads(result.stderr)
        assert err_output["error"] == "NOT_FOUND"

    def test_list_limit_flag(self, cli_script_path, cli_db):
        # Save 3 memories (each subprocess spin-up takes ~10-15s)
        for i in range(3):
            memory_dict = make_cli_memory_dict(context=f"limit test {i}")
            subprocess.run(
                [sys.executable, cli_script_path, "save", json.dumps(memory_dict),
                 "--no-sync", "--db-path", str(cli_db)],
                capture_output=True, text=True, timeout=60,
            )

        # List with limit=2
        result = subprocess.run(
            [sys.executable, cli_script_path, "list", "--limit", "2",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["result"]) == 2

    def test_save_non_dict_exits_1(self, cli_script_path, cli_db):
        result = subprocess.run(
            [sys.executable, cli_script_path, "save", '"just a string"',
             "--no-sync", "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1
        err_output = json.loads(result.stderr)
        assert err_output["error"] == "INVALID_INPUT"

    def test_output_is_valid_json(self, cli_script_path, cli_db):
        result = subprocess.run(
            [sys.executable, cli_script_path, "list",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        # Should parse without error
        output = json.loads(result.stdout)
        assert isinstance(output, dict)

    def test_update_and_verify(self, cli_script_path, cli_db):
        memory_dict = make_cli_memory_dict(context="original context")
        json_str = json.dumps(memory_dict)

        # Save
        save_result = subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--no-sync", "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert save_result.returncode == 0
        memory_id = json.loads(save_result.stdout)["result"]["memory_id"]

        # Update
        update_result = subprocess.run(
            [sys.executable, cli_script_path, "update", memory_id,
             '{"context": "updated context"}',
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert update_result.returncode == 0, f"stderr: {update_result.stderr}"
        update_output = json.loads(update_result.stdout)
        assert update_output["ok"] is True
        assert update_output["result"]["memory_id"] == memory_id

        # Verify via get
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", memory_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 0
        get_output = json.loads(get_result.stdout)
        assert get_output["result"]["context"] == "updated context"

    def test_update_not_found_exits_1(self, cli_script_path, cli_db):
        result = subprocess.run(
            [sys.executable, cli_script_path, "update", "nonexistent_id",
             '{"context": "x"}', "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1
        err_output = json.loads(result.stderr)
        assert err_output["error"] == "NOT_FOUND"

    def test_delete_and_verify(self, cli_script_path, cli_db):
        memory_dict = make_cli_memory_dict(context="to be deleted")
        json_str = json.dumps(memory_dict)

        # Save
        save_result = subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--no-sync", "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert save_result.returncode == 0
        memory_id = json.loads(save_result.stdout)["result"]["memory_id"]

        # Delete
        delete_result = subprocess.run(
            [sys.executable, cli_script_path, "delete", memory_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert delete_result.returncode == 0, f"stderr: {delete_result.stderr}"
        delete_output = json.loads(delete_result.stdout)
        assert delete_output["ok"] is True
        assert delete_output["result"]["deleted"] is True

        # Verify deleted via get
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", memory_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 1
        err_output = json.loads(get_result.stderr)
        assert err_output["error"] == "NOT_FOUND"

    def test_delete_not_found_exits_1(self, cli_script_path, cli_db):
        result = subprocess.run(
            [sys.executable, cli_script_path, "delete", "nonexistent_id",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1
        err_output = json.loads(result.stderr)
        assert err_output["error"] == "NOT_FOUND"

    def test_status_returns_system_info(self, cli_script_path, cli_db):
        result = subprocess.run(
            [sys.executable, cli_script_path, "status",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert isinstance(output["result"], dict)
        # Status should include core fields from get_status()
        assert "memory_count" in output["result"]
        assert "project_id" in output["result"]


# ---------------------------------------------------------------------------
# E2E Save Verification
# ---------------------------------------------------------------------------

class TestCliSaveVerificationE2E:
    """E2E subprocess tests for save verification (#245).

    The save-then-get verification lives in PACTMemory.save(). When it
    fails, save() raises RuntimeError, which main()'s try/except catches
    as SYSTEM_ERROR (exit 2). These tests exercise the full CLI binary
    against real SQLite databases.
    """

    def test_save_roundtrip_confirms_verification_passed(self, cli_script_path, cli_db):
        """Save succeeds (exit 0) only if internal verification passed.

        Because PACTMemory.save() now verifies via get() before returning,
        a successful save (exit 0) implies verification succeeded. We then
        confirm the data is actually retrievable via a separate get call.
        """
        memory_dict = make_cli_memory_dict(context="verification roundtrip test")
        json_str = json.dumps(memory_dict)

        # Save — exit 0 means internal verification passed
        save_result = subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert save_result.returncode == 0, f"save stderr: {save_result.stderr}"
        save_output = json.loads(save_result.stdout)
        assert save_output["ok"] is True
        memory_id = save_output["result"]["memory_id"]
        assert memory_id  # Non-empty ID

        # Confirm the memory is actually retrievable
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", memory_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 0, f"get stderr: {get_result.stderr}"
        get_output = json.loads(get_result.stdout)
        assert get_output["ok"] is True
        assert get_output["result"]["context"] == "verification roundtrip test"

    def test_save_via_stdin_confirms_verification_passed(self, cli_script_path, cli_db):
        """Save via --stdin also exercises the verification path."""
        memory_dict = make_cli_memory_dict(context="stdin verification test")
        json_str = json.dumps(memory_dict)

        save_result = subprocess.run(
            [sys.executable, cli_script_path, "save", "--stdin",
             "--db-path", str(cli_db)],
            input=json_str,
            capture_output=True, text=True, timeout=60,
        )
        assert save_result.returncode == 0, f"save stderr: {save_result.stderr}"
        save_output = json.loads(save_result.stdout)
        memory_id = save_output["result"]["memory_id"]

        # Verify the saved memory is retrievable
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", memory_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 0
        get_output = json.loads(get_result.stdout)
        assert get_output["result"]["context"] == "stdin verification test"

    def test_save_exits_2_on_unwritable_db(self, cli_script_path, tmp_path):
        """Save exits 2 with SYSTEM_ERROR when DB is inaccessible.

        This exercises the same error-handling path that a verification
        failure would take: PACTMemory.save() raises an exception, main()
        catches it as SYSTEM_ERROR with exit code 2.
        """
        # Create a directory where a file is expected — SQLite can't open it
        bad_db = tmp_path / "not_a_file"
        bad_db.mkdir()

        memory_dict = make_cli_memory_dict(context="should fail")
        json_str = json.dumps(memory_dict)

        result = subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--db-path", str(bad_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 2
        assert result.stdout == ""
        err_output = json.loads(result.stderr)
        assert err_output["ok"] is False
        assert err_output["error"] == "SYSTEM_ERROR"

    def test_save_exits_2_on_readonly_db(self, cli_script_path, cli_db):
        """Save exits 2 when the database file is read-only.

        A read-only DB prevents the INSERT in save() from succeeding,
        triggering the SYSTEM_ERROR path (exit 2).
        """
        import stat
        # Make the DB file read-only
        cli_db.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        try:
            memory_dict = make_cli_memory_dict(context="should fail readonly")
            json_str = json.dumps(memory_dict)

            result = subprocess.run(
                [sys.executable, cli_script_path, "save", json_str,
                 "--db-path", str(cli_db)],
                capture_output=True, text=True, timeout=60,
            )
            assert result.returncode == 2
            err_output = json.loads(result.stderr)
            assert err_output["ok"] is False
            assert err_output["error"] == "SYSTEM_ERROR"
        finally:
            # Restore write permission for cleanup
            cli_db.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_save_error_envelope_format(self, cli_script_path, tmp_path):
        """SYSTEM_ERROR envelope has correct JSON structure."""
        bad_db = tmp_path / "bad_dir"
        bad_db.mkdir()

        memory_dict = make_cli_memory_dict()
        json_str = json.dumps(memory_dict)

        result = subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--db-path", str(bad_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 2
        err_output = json.loads(result.stderr)
        # Verify full envelope structure
        assert set(err_output.keys()) == {"ok", "error", "message"}
        assert err_output["ok"] is False
        assert err_output["error"] == "SYSTEM_ERROR"
        assert isinstance(err_output["message"], str)
        assert len(err_output["message"]) > 0


# ---------------------------------------------------------------------------
# Adversarial Save Input Tests
# ---------------------------------------------------------------------------

class TestCliSaveAdversarial:
    """Test edge cases and adversarial inputs for the save command."""

    def test_save_empty_dict(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["save", "{}"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with({})

    def test_save_deeply_nested_json(self, mock_pact_memory, capsys):
        # Build a 50-level nested dict
        nested = {"value": "leaf"}
        for _ in range(50):
            nested = {"nested": nested}
        json_str = json.dumps(nested)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with(nested)

    def test_save_large_json_payload(self, mock_pact_memory, capsys):
        # ~100KB payload
        large_dict = {"context": "x" * 100_000}
        json_str = json.dumps(large_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with(large_dict)

    def test_save_unicode_emoji(self, mock_pact_memory, capsys):
        memory_dict = {"context": "Testing emoji \U0001f680\U0001f525\U0001f4a5 support"}
        json_str = json.dumps(memory_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True

    def test_save_unicode_cjk(self, mock_pact_memory, capsys):
        memory_dict = {"context": "\u4e16\u754c\u3053\u3093\u306b\u3061\u306f\uc548\ub155\ud558\uc138\uc694"}
        json_str = json.dumps(memory_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with(memory_dict)

    def test_save_unicode_rtl(self, mock_pact_memory, capsys):
        memory_dict = {"context": "\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645"}
        json_str = json.dumps(memory_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with(memory_dict)

    def test_save_special_chars_in_keys(self, mock_pact_memory, capsys):
        memory_dict = {"key with spaces": "val", "key/with/slashes": "val", "key.with.dots": "val"}
        json_str = json.dumps(memory_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with(memory_dict)

    def test_save_null_values(self, mock_pact_memory, capsys):
        memory_dict = {"context": None, "goal": None}
        json_str = json.dumps(memory_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with(memory_dict)

    def test_save_numeric_scalar_json(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["save", "42"])

        with pytest.raises(SystemExit) as exc_info:
            cmd_save(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "INVALID_INPUT"

    def test_save_boolean_scalar_json(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["save", "true"])

        with pytest.raises(SystemExit) as exc_info:
            cmd_save(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "INVALID_INPUT"

    def test_save_null_scalar_json(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["save", "null"])

        with pytest.raises(SystemExit) as exc_info:
            cmd_save(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# Help Output Tests
# ---------------------------------------------------------------------------

class TestCliHelpOutput:
    """Test argparse --help output renders correctly."""

    def test_main_help_includes_program_name(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "PACT Memory" in captured.out
        assert "Examples:" in captured.out

    def test_main_help_lists_subcommands(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        for cmd in ["save", "search", "list", "get", "status", "setup"]:
            assert cmd in captured.out

    def test_save_help_shows_options(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["save", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--stdin" in captured.out
        assert "json_data" in captured.out.lower() or "json" in captured.out.lower()

    def test_search_help_shows_options(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["search", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--limit" in captured.out
        assert "query" in captured.out.lower()

    def test_main_help_contains_examples(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # Full example lines, content-pinned. In-process the examples are
        # argv[0]-relative: the parser is built inside main() with the test
        # process's argv[0], so the known constant is sys.argv[0]. The epilog
        # is unwrapped (RawDescriptionHelpFormatter), so full-line assertions
        # are width-safe at any COLUMNS.
        for line in self._example_lines().values():
            assert line in captured.out, line

    def test_every_subcommand_help_contains_examples(self, capsys):
        for verb in self._example_lines():
            with pytest.raises(SystemExit) as exc_info:
                main([verb, "--help"])
            assert exc_info.value.code == 0, verb
            captured = capsys.readouterr()
            assert self._example_lines()[verb] in captured.out, verb

    def test_get_without_id_is_argparse_usage_with_example(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["get"])
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        # One blank line between the error line and the example.
        assert (
            f"\n\nExamples:\n  {self._example_lines()['get']}\n"
            in captured.err
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(captured.err)

    def test_bare_invocation_prints_help_with_examples_exit_one(self, capsys):
        # This CLI's pre-existing bare contract: subparsers are not required,
        # so main() prints the full help (epilog Examples included) to stderr
        # and exits 1 — not the argparse usage-error path.
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert self._example_lines()["save"] in captured.err

    def test_unknown_top_level_flag_is_usage_error_with_one_example(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--nope"])
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err
        assert self._example_lines()["save"] in captured.err

    @staticmethod
    def _example_lines():
        # Interpreter-prefixed, quoted script path, concrete flags: the exact
        # strings build_parser() renders as pasteable examples.
        prog = sys.argv[0]
        return {
            "save": f'python3 "{prog}" save --stdin',
            "search": f'python3 "{prog}" search "query"',
            "list": f'python3 "{prog}" list',
            "get": f'python3 "{prog}" get <memory-id>',
            "status": f'python3 "{prog}" status',
            "setup": f'python3 "{prog}" setup',
            "update": f'python3 "{prog}" update <memory-id> --stdin',
            "delete": f'python3 "{prog}" delete <memory-id>',
            "sync": f'python3 "{prog}" sync',
        }


# ---------------------------------------------------------------------------
# save(sync_to_claude=...) — the Working Memory projection, made optional
# ---------------------------------------------------------------------------


class TestSaveSyncSuppression:
    """`save()` gained `sync_to_claude`, completing a half-built symmetry.

    `search()` has carried this parameter all along and the CLI's search path
    already passes False; `save()` never got it and called the sync
    unconditionally. That asymmetry is why the pin-archival path could not
    avoid writing the archived block straight back into the CLAUDE.md it was
    removing it from — freeing the pin SLOT while leaving the bytes.

    THE DEFAULT DIRECTION IS THE ONE THAT MATTERS HERE. Suppressing the sync
    for a caller that did not ask is the damaging failure: it presents as a
    projection quietly not appearing, which nobody notices. So the default
    path is tested by asserting the sync ACTUALLY FIRES, not merely that the
    save returned an id.
    """

    def _spy(self, monkeypatch):
        calls = []
        import scripts.memory_api as memory_api
        monkeypatch.setattr(
            memory_api, "sync_to_claude_md",
            lambda *a, **k: calls.append((a, k)),
        )
        return calls

    def test_default_still_syncs(self, tmp_path, monkeypatch):
        """A caller that omits the parameter must be byte-identical to before."""
        calls = self._spy(monkeypatch)
        memory = PACTMemory(db_path=tmp_path / "d.db")
        memory_id = memory.save(make_cli_memory_dict())
        assert memory_id, "save must still return an id"
        assert memory.get(memory_id) is not None, (
            "save must still persist — otherwise the sync assertion below is "
            "measuring a crash rather than a default"
        )
        assert len(calls) == 1, (
            "the default path MUST still sync; silently suppressing it for "
            "every existing caller is the damaging direction of this change"
        )

    def test_explicit_true_syncs(self, tmp_path, monkeypatch):
        calls = self._spy(monkeypatch)
        memory = PACTMemory(db_path=tmp_path / "d.db")
        memory.save(make_cli_memory_dict(), sync_to_claude=True)
        assert len(calls) == 1

    def test_false_suppresses(self, tmp_path, monkeypatch):
        calls = self._spy(monkeypatch)
        memory = PACTMemory(db_path=tmp_path / "d.db")
        memory_id = memory.save(make_cli_memory_dict(), sync_to_claude=False)
        assert memory.get(memory_id) is not None, (
            "the save must PERSIST — a crashed save also produces zero sync "
            "calls, and the two are indistinguishable without this assertion"
        )
        assert calls == [], "sync_to_claude=False must not project"

    def test_signature_mirrors_search(self):
        """The parameter exists on both, with the same name and default."""
        import inspect
        save_p = inspect.signature(PACTMemory.save).parameters
        search_p = inspect.signature(PACTMemory.search).parameters
        assert "sync_to_claude" in save_p
        assert save_p["sync_to_claude"].default is True
        assert search_p["sync_to_claude"].default is True

    def test_claude_md_untouched_when_suppressed(self, tmp_path):
        """End-to-end against the REAL file the autouse fixture seeded.

        Byte-identity rather than a content check: it fails if ANYTHING
        writes, including a writer nobody has thought of, whereas a check for
        one specific string only fails for the writer you already suspected.
        """
        # The autouse fixture seeds the PREFERRED layout, .claude/CLAUDE.md,
        # not the legacy sibling — asserted rather than assumed, because a
        # wrong path here would make every byte comparison below vacuous.
        claude_md = tmp_path / ".claude" / "CLAUDE.md"
        assert claude_md.exists(), "isolation fixture must have seeded a file"

        before = claude_md.read_bytes()
        memory = PACTMemory(db_path=tmp_path / "s.db")
        memory_id = memory.save(make_cli_memory_dict(), sync_to_claude=False)
        assert memory.get(memory_id) is not None
        assert claude_md.read_bytes() == before, (
            "suppressed save wrote to CLAUDE.md"
        )

        # CONTROL: the unsuppressed path MUST change the file, or the
        # assertion above passes for a harness that cannot detect a write.
        memory.save(make_cli_memory_dict(), sync_to_claude=True)
        assert claude_md.read_bytes() != before, (
            "the unsuppressed control did not write — this test cannot "
            "distinguish suppression from an inert sync"
        )


class TestCliNoSyncFlag:
    """`--no-sync` on the save subcommand, threading to the parameter."""

    def test_flag_parses_and_defaults_false(self):
        parser = build_parser()
        assert parser.parse_args(["save", "{}"]).no_sync is False
        assert parser.parse_args(["save", "{}", "--no-sync"]).no_sync is True

    def test_flag_threads_to_the_api(self, tmp_path, monkeypatch):
        seen = {}

        def _capture(self, memory, files=None, include_tracked=True,
                     sync_to_claude=True):
            seen["sync_to_claude"] = sync_to_claude
            return "a" * 32

        monkeypatch.setattr(PACTMemory, "save", _capture)
        parser = build_parser()

        with pytest.raises(SystemExit):
            cmd_save(parser.parse_args(
                ["save", json.dumps(make_cli_memory_dict()), "--no-sync"]
            ), db_path=str(tmp_path / "x.db"))
        assert seen["sync_to_claude"] is False

        seen.clear()
        with pytest.raises(SystemExit):
            cmd_save(parser.parse_args(
                ["save", json.dumps(make_cli_memory_dict())]
            ), db_path=str(tmp_path / "x.db"))
        assert seen["sync_to_claude"] is True, (
            "omitting --no-sync must reach the API as True, not as absent"
        )


# ---------------------------------------------------------------------------
# The error envelope must survive a stderr a dependency also writes to
# ---------------------------------------------------------------------------


class TestErrorEnvelopeSurvivesASharedStderr:
    """Both envelopes are machine-readable, and stderr is not the CLI's alone.

    THE DEFECT. The embedding catch-up runs against the caller store, so it
    finds pending work there, loads the backend, and the backend writes a
    download progress bar to stderr. The error envelope is written to the same
    stream, so a caller that parses stderr meets the progress bytes first and
    the parse fails on output that reads correctly to a human.

    A SECOND EMITTER REACHES THE SAME STREAM and a progress-bar switch does not
    touch it: the standard-library logging last-resort handler writes WARNING
    and above to stderr when no handler is configured, and the memory layer
    calls `logger.warning` in several places. That is why the guard bounds the
    STREAM rather than one library.

    THE THREE ARMS BELOW MEASURE DIFFERENT THINGS. Read the labels before you
    delete one. One is red on the tree before the fix, one cannot go vacuous,
    and one guards against the WRONG fix rather than against the defect.
    """

    def _store_with_pending_embeddings(self, cli_db):
        """Put rows carrying no vector into the caller store.

        THE PRECONDITION THE INTEGRATION ARM NEEDS. The catch-up loads the
        backend only when it finds work, so a store with no pending row leaves
        the noisy path unexercised.
        """
        from scripts.database import create_memory
        try:
            import pysqlite3 as sqlite3
        except ImportError:
            import sqlite3

        id_a = "ambig00" + "a" + "0" * 24
        id_b = "ambig00" + "b" + "0" * 24
        conn = sqlite3.connect(str(cli_db))
        conn.row_factory = sqlite3.Row
        with patch("scripts.database.ensure_initialized"):
            create_memory(conn, {"id": id_a, "context": "first pending record"})
            create_memory(conn, {"id": id_b, "context": "second pending record"})
        conn.commit()
        pending = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
        conn.close()
        return pending

    def test_stderr_parses_as_json_when_a_dependency_writes_there(
        self, cli_script_path, cli_db
    ):
        """RED ON THE TREE BEFORE THIS FIX. INTEGRATION ARM.

        Drives the real CLI as a subprocess against a store carrying pending
        embeddings, which is the condition that puts a dependency's output on
        stderr. Asserts the whole stream parses as one JSON envelope.

        BOUND, STATED RATHER THAN IMPLIED: the noise source here is the REAL
        backend. On a machine where that backend is absent, nothing writes to
        stderr and this arm passes without exercising the guard. It is green
        for the right reason only where the backend is installed. The hermetic
        arm below is what covers that gap, and it is why two arms exist rather
        than one.
        """
        pending = self._store_with_pending_embeddings(cli_db)
        # CONTROL: the precondition really holds, so an empty stderr below
        # cannot come from a store that had no work in it.
        assert pending == 2

        result = subprocess.run(
            [sys.executable, cli_script_path, "get", "ambig00",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=120,
        )

        assert result.returncode == 1, f"stdout: {result.stdout}"
        envelope = json.loads(result.stderr)
        assert envelope["ok"] is False
        assert envelope["error"] == "AMBIGUOUS_PREFIX"

    def test_stdout_stays_clean_json_when_a_dependency_writes_to_stderr(
        self, cli_script_path, cli_db
    ):
        """GREEN BEFORE AND AFTER THIS FIX. IT GUARDS THE WRONG FIX, NOT THE DEFECT.

        DO NOT DELETE IT AS A PASSING ARM THAT PROVES NOTHING. It cannot go red
        against the defect, because the success envelope was always clean. Its
        mutant is a plausible REPAIR: a guard that quiets stderr by sending the
        dependency output to stdout passes the integration arm above and
        corrupts the success envelope, which is the more common path. This arm
        is the only thing that separates the two.
        """
        pending = self._store_with_pending_embeddings(cli_db)
        assert pending == 2

        result = subprocess.run(
            [sys.executable, cli_script_path, "list", "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=120,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is True


class TestTheStderrGuardItself:
    """HERMETIC ARMS. The noise is a write this test issues, not a dependency.

    WHY THESE EXIST AT ALL. The integration arm above takes its noise from the
    embedding backend, so it passes without exercising the guard wherever that
    backend is absent, which is a green for the wrong reason. A guard that
    works at the file-descriptor level is testable at that level with no
    dependency present, so these arms cannot go vacuous.

    They also pin the DISPOSAL, which differs by exit path on purpose.
    """

    def test_captured_bytes_do_not_reach_stderr_on_the_error_path(self, capfd):
        """The whole point: noise in, envelope out, nothing else."""
        from scripts.cli import _own_stderr_for_envelope, _error

        capfd.readouterr()
        with pytest.raises(SystemExit) as exc:
            with _own_stderr_for_envelope():
                os.write(2, b"NOISE-FROM-A-DEPENDENCY\n")
                _error("AMBIGUOUS_PREFIX", "two matches")

        assert exc.value.code == 1
        captured = capfd.readouterr()
        assert "NOISE-FROM-A-DEPENDENCY" not in captured.err
        envelope = json.loads(captured.err)
        assert envelope["error"] == "AMBIGUOUS_PREFIX"
        # CONTROL: the guard did not divert the payload to the other channel.
        assert captured.out == ""

    def test_captured_bytes_are_replayed_on_the_success_path(self, capfd):
        """DISPOSAL, DECIDED PER PATH. Success replays, failure discards.

        Nothing parses stderr on success, so the guard protects nothing there,
        and discarding would remove the only sign that a first-run model
        download is happening. A silent command that runs for 30 seconds looks
        hung. Replay does NOT restore the timing, and the guard's own comment
        records that cost.
        """
        from scripts.cli import _own_stderr_for_envelope

        capfd.readouterr()
        with _own_stderr_for_envelope():
            os.write(2, b"PROGRESS-FROM-A-DEPENDENCY\n")

        captured = capfd.readouterr()
        assert "PROGRESS-FROM-A-DEPENDENCY" in captured.err

    def test_the_descriptor_is_restored_after_the_window(self, capfd):
        """A guard that leaks its redirect would silence the whole process."""
        from scripts.cli import _own_stderr_for_envelope

        capfd.readouterr()
        with _own_stderr_for_envelope():
            os.write(2, b"inside\n")
        os.write(2, b"AFTER-THE-WINDOW\n")

        captured = capfd.readouterr()
        assert "AFTER-THE-WINDOW" in captured.err

    def test_the_descriptor_is_restored_when_the_handler_raises(self, capfd):
        """Restoration sits in a `finally`, so an exit through an exception
        cannot strand file descriptor 2 on the capture buffer."""
        from scripts.cli import _own_stderr_for_envelope

        capfd.readouterr()
        with pytest.raises(RuntimeError):
            with _own_stderr_for_envelope():
                raise RuntimeError("handler blew up")
        os.write(2, b"AFTER-THE-RAISE\n")

        captured = capfd.readouterr()
        assert "AFTER-THE-RAISE" in captured.err


# ---------------------------------------------------------------------------
# A caller-supplied store path is opened, not created
# ---------------------------------------------------------------------------


class TestACallerSuppliedPathIsNotCreated:
    """A typo on `--db-path` must fail loudly, as it did before the resolver.

    THE REGRESSION THESE ARMS CLOSE. The directory side effect used to aim at
    the DEFAULT memory directory. When the resolver began returning a caller
    path, the side effect began building the tree for THAT path, so a mistyped
    `--db-path` stopped raising and started to succeed against an empty store.
    A write lands in a file the caller does not know about, and the command
    reports success.

    LOUD TO SILENT IS THE WRONG DIRECTION ON A CALLER-CONTROLLED VALUE. The
    derived route keeps its create, because production passes no path and must
    reach and build the real store.

    ⚠️ WHICH LAYER EACH ARM NOW MEASURES, BECAUSE ONE OF THEM MOVED. The CLI
    boundary in `cli.main` refuses an absent caller `--db-path` BEFORE the
    resolver runs, so the first arm below measures THE BOUNDARY REFUSAL and no
    longer reaches the directory-half guard it was written for. It stays here
    as a CLI-level statement of the same rule. The directory-half guard in
    `database.get_db_path` is pinned at the layer that still reaches it, in
    `tests/test_caller_path_is_not_created.py`, by the class named for the
    directory half below the boundary, through a library caller. Do NOT read a
    green result here as cover for that guard.
    """


    def test_an_absent_caller_parent_is_not_created(self, cli_script_path, tmp_path):
        """The CLI refuses a caller path that is absent, and creates nothing.

        THIS ARM MEASURES THE BOUNDARY REFUSAL, NOT THE RESOLVER. It reads the
        error NAME for that reason: a status alone cannot say WHICH layer
        answered, and this arm went green for a different layer once already.
        """
        absent = tmp_path / "typo-dir"
        db = absent / "x.db"
        assert not absent.exists()

        result = subprocess.run(
            [sys.executable, cli_script_path, "get", "abc1234", "--db-path", str(db)],
            capture_output=True, text=True, timeout=120,
        )

        assert not absent.exists(), (
            "a mistyped --db-path built its own directory tree, so the typo "
            "succeeded against an empty store instead of failing"
        )
        assert not db.exists()
        assert result.returncode != 0
        assert json.loads(result.stderr)["error"] == "DB_PATH_NOT_FOUND", (
            f"a non-zero status came from somewhere other than the boundary "
            f"refusal, so this arm no longer measures what it names. "
            f"stderr={result.stderr[:300]}"
        )

    def test_the_derived_route_still_creates_the_store(self, cli_script_path, tmp_path):
        """THE OTHER HALF, AND IT OUTRANKS THE ARM ABOVE.

        The scope must REDIRECT and never REFUSE. A production caller passes no
        `--db-path`, so the store comes from the derived route and the create
        must still happen. An arm that only proved the refusal would pass
        against a fix that broke production.
        """
        derived_root = tmp_path / "derived-home" / "pact-memory"
        assert not derived_root.exists()

        env = dict(os.environ)
        env["PACT_TEST_MEMORY_DIR"] = str(derived_root)
        env.pop("PYTEST_CURRENT_TEST", None)

        result = subprocess.run(
            [sys.executable, cli_script_path, "list"],
            capture_output=True, text=True, timeout=120, env=env,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert derived_root.is_dir(), (
            "the derived route stopped creating the store directory, which "
            "breaks every production caller that passes no --db-path"
        )


# ---------------------------------------------------------------------------
# A failed diagnostic write must not change the outcome
# ---------------------------------------------------------------------------


class TestAFailedReportDoesNotChangeTheOutcome:
    """`cmd 2>&1 | head` is an ordinary command line, not a constructed one.

    Once `head` stops reading, stderr is a pipe with no reader. The guard's
    replay then raised, the error envelope could not reach the same broken
    stream, and the process died with a status describing the REPORTING
    failure rather than the OPERATION. A successful command printed a success
    envelope on stdout and exited non-zero.
    """

    def test_a_broken_stderr_does_not_replace_a_success_exit(self):
        """RED BEFORE THE FIX: the replay raised and took the exit code with it.

        DETERMINISTIC BY CONSTRUCTION, and that is deliberate. The reviewer met
        this through a live dependency writing a progress bar into the capture.
        A noise source that depends on a backend being installed makes the arm
        pass for the wrong reason wherever that backend is absent, so this arm
        writes the captured bytes itself and points file descriptor 2 at a
        reader-less pipe by hand. It reproduces the mechanism rather than the
        occasion.
        """
        from scripts.cli import _own_stderr_for_envelope

        read_end, write_end = os.pipe()
        saved = os.dup(2)
        try:
            os.dup2(write_end, 2)
            # No reader from here on, so any write to descriptor 2 raises.
            os.close(read_end)

            with pytest.raises(SystemExit) as exc:
                with _own_stderr_for_envelope():
                    # Lands in the CAPTURE, because the guard holds descriptor
                    # 2 while the block runs. The replay meets the broken pipe
                    # after the guard restores it.
                    os.write(2, b"captured diagnostic line\n")
                    sys.exit(0)

            assert exc.value.code == 0, (
                "a failure in the reporting path replaced the outcome of an "
                "operation that succeeded"
            )
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            os.close(write_end)

    def test_a_broken_stdout_does_not_replace_a_success_exit(self, monkeypatch):
        """RED BEFORE THE FIX: the success write raised and took the exit code.

        THE OTHER STREAM, AND THE SAME MECHANISM. The reported occasion was
        `cmd 2>&1 | head`, which breaks stderr. `cmd | head` breaks STDOUT, and
        it is as ordinary a command line. On the unfixed path the success
        envelope write raised, `main` caught it as an unexpected exception and
        reported SYSTEM_ERROR, so a command that did what it was asked exited
        non-zero.

        A FIX THAT CURED ONLY THE REPORTED STREAM WOULD CURE THE OCCASION AND
        LEAVE THE MECHANISM, which is the standard this suite refuses
        elsewhere. So this arm exists rather than the symmetry of the argument.

        DETERMINISTIC BY CONSTRUCTION, for the same reason the stderr arm is:
        it points the stream at a reader-less pipe by hand rather than wait for
        a payload large enough to fill one.

        ⚠️ WHAT THIS ARM DOES AND DOES NOT COVER, MEASURED RATHER THAN ARGUED.
        The replacement stream here is LINE buffered, so the write raises inside
        `_success` and this arm sees it. Reverting `_success` to its unguarded
        form turns this arm RED, so it does separate guarded from unguarded, and
        it is worth keeping for that.
        IT CANNOT REACH THE REPORTED SCENARIO. A stdout that is a real pipe is
        BLOCK buffered, so the write raises nothing here and the failure lands
        in the flush the interpreter performs at shutdown. This arm catches
        `SystemExit` inside the test process, so it never reaches finalization
        at all: an in-process arm sees the SystemExit CODE, and only a child
        process sees the exit STATUS. Those differ exactly where finalization
        fails.
        `TestTheExitStatusSurvivesAReaderLessPipe` below holds the child-process
        arms that cover that case. Do not delete this arm in favour of them:
        the two answer different questions.
        """
        # IMPORTS `_success` ALONE, DELIBERATELY. An arm that also imported the
        # helper the fix introduces would fail on the unfixed tree with an
        # ImportError, which is red by ABSENCE OF A NAME rather than by
        # behaviour. The cleanup below is therefore inline.
        from scripts.cli import _success

        read_end, write_end = os.pipe()
        broken = os.fdopen(write_end, "w", buffering=1)
        os.close(read_end)
        monkeypatch.setattr(sys, "stdout", broken)
        try:
            with pytest.raises(SystemExit) as exc:
                _success({"context": "an operation that succeeded"})

            assert exc.value.code == 0, (
                "a command that did what it was asked reported failure "
                "because its own success envelope could not be written"
            )
        finally:
            try:
                broken.close()
            except (OSError, ValueError):
                pass

    def test_a_best_effort_report_swallows_both_failure_classes(self):
        """THE TWO CLASSES ARE MEASURED HERE, not assumed.

        A write to a reader-less pipe raises BrokenPipeError, which IS an
        OSError. A flush on a CLOSED file object raises ValueError, which is
        NOT. A clause naming OSError alone leaves the second class live.
        """
        from scripts.cli import _best_effort_report

        r, w = os.pipe()
        broken = os.fdopen(w, "w", buffering=1)
        os.close(r)
        try:
            assert _best_effort_report(broken.write, "x\n") in (True, False)
            assert _best_effort_report(broken.flush) is False
        finally:
            # Close through the helper too. A bare close on a broken pipe
            # raises in the finalizer and pytest reports it as unraisable.
            _best_effort_report(broken.close)

        r2, w2 = os.pipe()
        closed = os.fdopen(w2, "w", buffering=1)
        _best_effort_report(closed.close)
        os.close(r2)
        assert _best_effort_report(closed.flush) is False

        # CONTROL: a write that CAN land reports True, so the two results above
        # are a bounded failure and not a helper that reports False always.
        import io
        assert _best_effort_report(io.StringIO().write, "ok") is True

    def test_the_private_envelope_handle_leaves_no_unflushed_residue(self):
        """THE IN-PROCESS CELL, WHICH THE NEUTRALISE STEP DOES NOT COVER.

        `_neutralise_unwritable_std_streams` runs from the `__main__` block
        alone, so an IN-PROCESS caller of `main()`, which the unit tests of this
        CLI are, gets no protection from it. That is deliberate: the rebind is
        safe only for a process about to end.

        WHAT IS LEFT FOR THIS ARM. When the envelope write meets a reader-less
        stream, the bytes stay pending inside the PRIVATE handle. The window
        then closes the descriptor beneath that handle. Nothing closes the
        handle, so its finalizer attempts one more flush against a descriptor
        that has gone, and the interpreter reports an unraisable exception into
        the test runner. This arm makes that residue a failure rather than
        noise.
        """
        import gc

        from scripts.cli import _error, _own_stderr_for_envelope

        seen = []
        previous_hook = sys.unraisablehook
        sys.unraisablehook = seen.append

        read_end, write_end = os.pipe()
        saved = os.dup(2)
        try:
            os.dup2(write_end, 2)
            os.close(read_end)   # no reader, so a write to descriptor 2 fails
            with pytest.raises(SystemExit):
                with _own_stderr_for_envelope():
                    _error("SOME_ERROR", "e" * 64, exit_code=2)
            gc.collect()
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            os.close(write_end)
            sys.unraisablehook = previous_hook

        assert not seen, (
            "the private envelope handle kept unflushed bytes over a descriptor "
            "that was closed, so its finalizer reported an unraisable exception "
            f"into the test runner: {[str(x.exc_value) for x in seen]}"
        )


# ---------------------------------------------------------------------------
# The exit STATUS a shell sees, measured in a child process
# ---------------------------------------------------------------------------


def _spawn_with_readerless_pipes(argv, env, break_stdout, break_stderr):
    """Run cli.py as a CHILD and return (returncode, stdout_text, stderr_text).

    THE CONDITION IS A MISSING READER, NOT A NUMBER OF BYTES. The parent closes
    the read end of each pipe immediately after the spawn, so the reader is gone
    BEFORE the child writes anything. That is what a consumer which stops early
    leaves behind.

    NO SIZE THRESHOLD APPEARS HERE, DELIBERATELY. Whether a given write raises
    at the call or survives in a buffer until interpreter shutdown depends on
    the stdio buffer size and on the pipe capacity of the system. Those move
    with the platform and the interpreter, and a number written here would decay
    with no event to show it. Removing the reader makes the condition hold at
    every size.

    A BROKEN STREAM RETURNS A SENTINEL rather than text, because its bytes went
    into a pipe nobody reads. Do not assert on a sentinel: a probe that merges
    a stream into the one it discards cannot report on that stream.
    """
    read_ends, write_ends, files = [], [], {}

    def target(broken, name):
        if broken:
            r, w = os.pipe()
            read_ends.append(r)
            write_ends.append(w)
            return w
        files[name] = tempfile.TemporaryFile()
        return files[name].fileno()

    out_target = target(break_stdout, "out")
    err_target = target(break_stderr, "err")

    proc = subprocess.Popen(
        [sys.executable] + argv,
        stdout=out_target,
        stderr=err_target,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    # The reader is gone from here on, and the child has not written yet.
    for fd in read_ends + write_ends:
        os.close(fd)
    returncode = proc.wait(timeout=120)

    def read_back(name):
        handle = files.get(name)
        if handle is None:
            return "<discarded: this stream was the reader-less pipe>"
        handle.seek(0)
        text = handle.read().decode("utf-8", "replace")
        handle.close()
        return text

    return returncode, read_back("out"), read_back("err")


class TestTheExitStatusSurvivesAReaderLessPipe:
    """A shell must read the OUTCOME of the operation, not of the report.

    WHY A CHILD PROCESS AND NOT A MONKEYPATCHED STREAM. An in-process arm can
    observe the ``SystemExit`` code, and only a child observes the exit STATUS.
    The two differ where interpreter finalization fails, which is where this
    defect lives, so an in-process arm cannot reach it by construction.

    ⚠️ 120 IS A SHARED STATUS. CPython uses it for any finalization failure, so
    a number alone cannot name a cause. Each arm below asserts the shutdown TEXT
    beside the number, and carries a control with no broken stream.
    """

    def test_a_broken_stdout_leaves_a_successful_run_at_zero(self, cli_script_path, tmp_path):
        """RED BEFORE THE FIX: exit 120, with the shutdown text on stderr.

        A success envelope is a REPORT. Once it is written the operation is
        finished, so a reader that has gone away costs the caller the text and
        must not change the outcome.
        """
        store = tmp_path / "derived" / "pact-memory"
        store.mkdir(parents=True)          # a redirect target that is present
        env = dict(os.environ)
        env["PACT_TEST_MEMORY_DIR"] = str(store)
        env.pop("PYTEST_CURRENT_TEST", None)

        rc, _out, err = _spawn_with_readerless_pipes(
            [cli_script_path, "list"], env, break_stdout=True, break_stderr=False
        )

        assert "Exception ignored" not in err, (
            "the interpreter reported a shutdown failure on the terminal of "
            f"the caller; stderr was: {err!r}"
        )
        assert rc == 0, (
            "a command that did what it was asked reported failure, because "
            f"the reader of its own success envelope had gone away (exit {rc})"
        )

    def test_a_broken_stderr_keeps_the_refusal_code_distinct(self, cli_script_path, tmp_path):
        """RED BEFORE THE FIX: exit 120, which erases the refusal code.

        THE CONSUMER IS REAL. This refusal exits 2, and `/PACT:prune-memory`
        reads that code to choose between refuse and proceed. Exit 120 is the
        finalization status of the interpreter and carries no exit code of ours,
        so a caller cannot separate a REFUSAL from a generic failure.

        THIS COMMAND OPENS NO STORE. The refusal fires before any store is
        reached, which is why it is safe to run here.
        """
        env = dict(os.environ)
        env["PYTEST_CURRENT_TEST"] = "test_a_broken_stderr_keeps_the_refusal_code_distinct"

        rc, out, _err = _spawn_with_readerless_pipes(
            [cli_script_path, "get", "abc1234"], env,
            break_stdout=False, break_stderr=True,
        )

        assert rc != 120, (
            "the exit code became the finalization status of the interpreter, "
            "so the refusal is no longer distinguishable from a generic failure"
        )
        assert rc == 2, f"expected the refusal code 2, got {rc}; stdout: {out!r}"

    def test_the_neutralise_step_is_reachable_only_from_the_main_block(
        self, cli_script_path
    ):
        """THE GATE ON THE REBIND, PINNED WHERE A BEHAVIOURAL ARM CANNOT REACH.

        `_neutralise_unwritable_std_streams` rebinds a PROCESS-GLOBAL
        descriptor. That is safe for a spawned CLI, which is about to end, and
        it is NOT safe for an in-process caller of `main()`, which the unit
        tests of this CLI are: there the same rebind would point a descriptor of
        the TEST RUNNER at the null device and lose the rest of that run.

        WHY A SYNTAX ARM AND NOT A BEHAVIOURAL ONE. To show the hazard by
        behaviour, a test would have to CAUSE it, and a test that silences the
        runner cannot then report. So the placement is pinned instead: the call
        must sit in the `__main__` block and in no function body.
        """
        import ast

        tree = ast.parse(Path(cli_script_path).read_text())
        name = "_neutralise_unwritable_std_streams"

        # CONTROL FIRST. A rename would empty every set below and read as a
        # clean pass, so the arm asserts its own subject is present.
        defined = [
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        assert defined == [name], (
            f"{name} is not defined in cli.py, so this arm has no subject and "
            "its other assertions are vacuous"
        )

        def is_the_call(node):
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
            )

        def is_the_main_guard(test):
            """True for `__name__ == "__main__"` in either operand order."""
            if not isinstance(test, ast.Compare):
                return False
            if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
                return False
            if len(test.comparators) != 1:
                return False
            ends = (test.left, test.comparators[0])
            names = {n.id for n in ends if isinstance(n, ast.Name)}
            texts = {
                c.value for c in ends
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            }
            return names == {"__name__"} and texts == {"__main__"}

        # ⚠️ COUNT WHAT THE PREDICATE ADMITS, NOT ONLY WHAT IT FORBIDS.
        #
        # An earlier spelling of this arm asked two questions: is the call
        # inside a function, and is there a call below SOME module-level `if`.
        # An independent reviewer defeated it by ADDING a module-level
        # `if True:` call while KEEPING the correct one. Every arm passed, and
        # the measured harm was worse than the case the arm was built for: a
        # plain `import scripts.cli` left descriptor 1 on the null device, so an
        # import-time rebind reaches EVERY IMPORTER rather than one caller, and
        # this suite imports that module.
        #
        # THE REPAIR IS A COMPLEMENT OVER PLACEMENT. Collect the calls the
        # `__main__` guard admits, then require that no other call by this NAME
        # is present anywhere in the module. A list of bad placements has to
        # name each one, and the list was short by at least one.
        #
        # ⚠️ WHAT THIS CLOSES AND WHAT IT DOES NOT, AND AN EARLIER WORDING HERE
        # CLAIMED THE WHOLE OF IT. The complement is CLOSED OVER PLACEMENT and
        # OPEN OVER SPELLING. It removes the need to name each position, so a
        # bare module-level call and a call below `if True:` are covered without
        # being listed. It does NOT remove the need to name each spelling: the
        # predicate accepts a Call whose func is a Name with this exact id, so a
        # call through an ALIAS enters neither the admitted set nor the stray
        # set, and an independent reviewer defeated this arm that way. MEASURED,
        # not argued: with the alias in place all syntax arms pass, and
        # importing the module leaves descriptor 1 on the null device.
        #
        # THE ALPHABET IS DELIBERATELY NOT WIDENED. Adding attribute calls, then
        # `globals()`, then `getattr` keeps two spellings in step while the next
        # one stays free, which is the generator of the defect rather than its
        # cure. The behavioural arm below covers the spelling axis instead: it
        # asks what the DESCRIPTOR is after an import, which no spelling can
        # hide. Read the two arms as one pair. This one reports the LINE NUMBER,
        # which the behavioural arm cannot, and the behavioural arm reaches the
        # spellings this one cannot.
        admitted = {
            id(call)
            for node in tree.body
            if isinstance(node, ast.If) and is_the_main_guard(node.test)
            for call in ast.walk(node)
            if is_the_call(call)
        }
        every_call = [node for node in ast.walk(tree) if is_the_call(node)]

        assert every_call, (
            f"{name} is never called, so the exit status of a spawned command "
            "is no longer protected"
        )
        assert admitted, (
            f"{name} is called, but never below `if __name__ == \"__main__\"`. "
            "That guard is the only caller which ends the process, and it is "
            "what makes a rebind of a process-global descriptor safe"
        )
        stray = sorted(node.lineno for node in every_call if id(node) not in admitted)
        assert not stray, (
            f"{name} is also called outside the `__main__` guard, at line(s) "
            f"{stray}. Each such call rebinds a process-global descriptor for a "
            "caller that does not end the process. At module level that reaches "
            "every importer of this module, and this suite imports it"
        )

    def test_an_import_does_not_rebind_the_standard_output_descriptor(
        self, cli_script_path
    ):
        """THE SPELLING AXIS, WHICH THE SYNTAX ARM ABOVE CANNOT REACH.

        The syntax arm is closed over PLACEMENT and open over SPELLING: it keys
        on a call by NAME, so a call through an alias escapes it. This arm asks
        the question no spelling can answer around: AFTER AN IMPORT, WHAT IS
        DESCRIPTOR 1. An import must not rebind it, because a rebind at import
        time reaches every importer of this module, and this suite is one.

        WHY A CHILD PROCESS. The harm under test is a descriptor of the RUNNING
        process pointed at the null device. An in-process version would silence
        the runner that has to report the result, so the measurement would
        destroy its own report. The child reports through its EXIT STATUS and
        through stderr, which this arm leaves intact.

        ⚠️ TWO CONDITIONS ARE NEEDED, AND THE FIRST SPELLING OF THIS ARM HAD
        ONLY ONE. A reader-less pipe on descriptor 1 is not sufficient. The
        neutralise step rebinds ONLY when a flush FAILS, and a flush of an EMPTY
        buffer succeeds even against a reader-less pipe. So the child must also
        leave BYTES PENDING before the import. Without them the step runs,
        finds a writable stream and rebinds nothing, and this arm passes against
        the very mutants it exists to catch. MEASURED: with the pending write
        absent, all four variants of the acceptance matrix passed.

        THE CHILD CLEARS THE HAZARD FOR ITSELF BEFORE IT EXITS, after the
        measurement and never before it. Otherwise the pending bytes meet the
        reader-less pipe during interpreter shutdown, the process exits 120, and
        the status this arm reads stops naming what it measured.
        """
        skill_root = str(Path(cli_script_path).resolve().parent.parent)
        child = (
            "import os, stat, sys\n"
            f"sys.path.insert(0, {skill_root!r})\n"
            # CONDITION 1: bytes pending on a block-buffered stdout, so a flush
            # during the import has something to fail on.
            "sys.stdout.write('pending')\n"
            "import scripts.cli\n"
            "st = os.fstat(1)\n"
            "null = os.stat(os.devnull)\n"
            "on_null = stat.S_ISCHR(st.st_mode) and st.st_rdev == null.st_rdev\n"
            "sys.stderr.write('ON_NULL=%s MODE=%s\\n' % (on_null, oct(st.st_mode)))\n"
            # Measurement is done. Now make this child's own exit clean, so the
            # status reports the CHECK and not a shutdown failure of the probe.
            "fd = os.open(os.devnull, os.O_WRONLY)\n"
            "os.dup2(fd, 1)\n"
            "os.close(fd)\n"
            "sys.exit(3 if on_null else 0)\n"
        )
        env = dict(os.environ)

        rc, _out, err = _spawn_with_readerless_pipes(
            ["-c", child], env, break_stdout=True, break_stderr=False
        )

        assert "ON_NULL=" in err, (
            "the child did not reach its own check, so this arm proves nothing; "
            f"stderr: {err[:400]!r}"
        )
        assert rc != 3, (
            "importing the module rebound descriptor 1 to the null device. An "
            "import-time rebind reaches EVERY importer of this module, and this "
            f"suite imports it; child reported: {err.strip()!r}"
        )
        assert rc == 0, f"the child failed for another reason; stderr: {err[:400]!r}"

    def test_the_controls_with_no_broken_stream_agree(self, cli_script_path, tmp_path):
        """NON-VACUITY, AND IT MUST CERTIFY THE CAUSE RATHER THAN THE NUMBER.

        Without a broken stream the same two commands give the same two codes,
        so the arms above measure the broken pipe and not the commands.

        ⚠️ A BARE EXIT CODE DOES NOT NAME ITS CAUSE, AND AN EARLIER SPELLING OF
        THIS CONTROL DID NOT REACH PAST IT. An independent reviewer renamed the
        `get` subcommand to `getx`. `cli.py get abc1234` then became an argparse
        usage error, which ALSO exits 2. The refusal arm passed and this control
        passed, with the subcommand gone.

        WHY THE PAIR FAILED TOGETHER, which is the part worth keeping: this
        control ran the same command and asserted the same bare number as the
        arm it guards, so it moved in LOCKSTEP with it. A pair whose halves
        share one weakness is a single point of failure presented as two.

        THIS CONTROL HOLDS THE UNBROKEN STDERR, so it is the half that CAN read
        the envelope. The refusal arm cannot, because its stderr is the
        reader-less pipe, which is the condition it exists to test. So the cause
        is asserted here and the status is asserted there, and the two halves
        now fail for different reasons.
        """
        store = tmp_path / "derived" / "pact-memory"
        store.mkdir(parents=True)
        ok_env = dict(os.environ)
        ok_env["PACT_TEST_MEMORY_DIR"] = str(store)
        ok_env.pop("PYTEST_CURRENT_TEST", None)
        rc_ok, out_ok, _e = _spawn_with_readerless_pipes(
            [cli_script_path, "list"], ok_env, break_stdout=False, break_stderr=False
        )
        assert rc_ok == 0
        assert '"ok": true' in out_ok.lower(), (
            "the success leg exited 0 without a success envelope on stdout, so "
            f"the 0 above does not name its cause; stdout: {out_ok[:200]!r}"
        )

        refuse_env = dict(os.environ)
        refuse_env["PYTEST_CURRENT_TEST"] = "test_the_controls_with_no_broken_stream_agree"
        rc_refuse, _o2, err_refuse = _spawn_with_readerless_pipes(
            [cli_script_path, "get", "abc1234"], refuse_env,
            break_stdout=False, break_stderr=False,
        )
        assert rc_refuse == 2
        assert "UNSCOPED_TEST_DB" in err_refuse, (
            "the refusal leg exited 2 for some OTHER cause than the store-path "
            "refusal. An argparse usage error exits 2 as well, so a bare 2 "
            f"cannot separate the two; stderr: {err_refuse[:300]!r}"
        )


# ---------------------------------------------------------------------------
# Sync Command
# ---------------------------------------------------------------------------

_WORKING_MEMORY_SCAFFOLD = (
    "# Probe\n\n"
    "## Working Memory\n"
    "<!-- Auto-managed by pact-memory skill. -->\n\n"
    "## Pinned Context\n\nkeep me\n"
)


def _backdate(db_path, memory_id, stamp):
    """Set a record's `created_at` directly, because a save cannot.

    The writer ignores a caller-supplied `created_at` and stamps its own,
    microsecond ISO, so saves land in an order these arms cannot choose and
    never tie. Writing the stamp here fixes the ordering in the store, which
    is the record of truth the projection reads. Note that the stamps written
    here are the space form, which is what the schema DEFAULT emits and not
    what the writer emits -- an arm that needs the production form must say
    so explicitly."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE memories SET created_at = ? WHERE id = ?", (stamp, memory_id))
    conn.commit()
    conn.close()


def _working_memory_headers(text):
    section = text.split("## Working Memory\n", 1)[1].split("\n## ", 1)[0]
    return [line[4:] for line in section.splitlines() if line.startswith("### ")]


class TestCliSyncSubprocess:
    """`sync` end to end: saves with `--no-sync`, then one `sync` writes the
    section from the store. Every `sync` carries `--claude-md-root`, and the
    child's `CLAUDE_PROJECT_DIR` is the same tmp project, so the anchored
    write resolves inside its anchor."""

    @pytest.fixture
    def project(self, tmp_path):
        root = tmp_path / "sync-project"
        root.mkdir()
        (root / "CLAUDE.md").write_text(_WORKING_MEMORY_SCAFFOLD, encoding="utf-8")
        return root

    def _env(self, project):
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        return env

    def _run(self, cli_script_path, project, *args):
        proc = subprocess.run(
            [sys.executable, cli_script_path, *args],
            capture_output=True, text=True, timeout=120, env=self._env(project),
        )
        assert proc.returncode == 0, f"rc={proc.returncode}\n{proc.stderr[:600]}"
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True, payload
        return payload["result"]

    def _save(self, cli_script_path, project, cli_db, context, stamp=None):
        result = self._run(
            cli_script_path, project, "save", json.dumps({"context": context}),
            "--no-sync", "--db-path", str(cli_db),
        )
        assert result["sync_status"] == "suppressed", result
        if stamp is not None:
            _backdate(cli_db, result["memory_id"], stamp)
        return result["memory_id"]

    def _sync(self, cli_script_path, project, cli_db):
        return self._run(
            cli_script_path, project, "sync",
            "--claude-md-root", str(project), "--db-path", str(cli_db),
        )

    def _project_id(self, cli_script_path, project, cli_db):
        """The id `status` reports under the same env: the envelope's
        `project_id` is pinned by agreement with it, not by a literal."""
        return self._run(cli_script_path, project, "status", "--db-path", str(cli_db))["project_id"]

    def test_a_project_with_no_records_is_empty_and_untouched(
        self, cli_script_path, cli_db, project
    ):
        before = (project / "CLAUDE.md").read_bytes()
        result = self._sync(cli_script_path, project, cli_db)
        expected_id = self._project_id(cli_script_path, project, cli_db)
        assert expected_id, "status reported no project id; the agreement pin below is vacuous"
        assert result == {"sync_status": "empty", "projected": 0, "memory_ids": [],
                          "project_id": expected_id}
        assert (project / "CLAUDE.md").read_bytes() == before

    def test_sync_projects_the_records_under_their_own_dates(
        self, cli_script_path, cli_db, project
    ):
        older = self._save(cli_script_path, project, cli_db, "older save",
                           "2026-09-01 08:00:00")
        newer = self._save(cli_script_path, project, cli_db, "newer save",
                           "2026-09-02 09:30:00")

        result = self._sync(cli_script_path, project, cli_db)

        assert result["sync_status"] == "wrote" and result["projected"] == 2
        assert result["memory_ids"] == [newer, older]
        assert result["project_id"] == self._project_id(cli_script_path, project, cli_db)
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert _working_memory_headers(text) == ["2026-09-02 09:30", "2026-09-01 08:00"]
        assert "newer save" in text and "older save" in text
        assert text.endswith("## Pinned Context\n\nkeep me\n")

    def test_sync_twice_writes_the_same_bytes(
        self, cli_script_path, cli_db, project
    ):
        self._save(cli_script_path, project, cli_db, "same", "2026-09-01 08:00:00")
        first = self._sync(cli_script_path, project, cli_db)
        after_first = (project / "CLAUDE.md").read_bytes()
        second = self._sync(cli_script_path, project, cli_db)
        assert first == second and second["sync_status"] == "wrote"
        assert (project / "CLAUDE.md").read_bytes() == after_first

    def test_sync_keeps_the_newest_three(self, cli_script_path, cli_db, project):
        ids = [
            self._save(cli_script_path, project, cli_db, f"save {i}",
                       f"2026-09-0{i} 00:00:00")
            for i in (1, 2, 3, 4)
        ]
        result = self._sync(cli_script_path, project, cli_db)
        assert result["projected"] == 3
        assert result["memory_ids"] == [ids[3], ids[2], ids[1]]
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert ids[0] not in text and "save 1" not in text
        assert _working_memory_headers(text) == [
            "2026-09-04 00:00", "2026-09-03 00:00", "2026-09-02 00:00",
        ]

    def test_update_then_sync_shows_the_corrected_record(
        self, cli_script_path, cli_db, project
    ):
        memory_id = self._save(cli_script_path, project, cli_db, "wrong text",
                               "2026-09-01 08:00:00")
        self._sync(cli_script_path, project, cli_db)
        assert "wrong text" in (project / "CLAUDE.md").read_text(encoding="utf-8")

        self._run(cli_script_path, project, "update", memory_id,
                  json.dumps({"context": "corrected text"}), "--db-path", str(cli_db))
        result = self._sync(cli_script_path, project, cli_db)

        assert result["memory_ids"] == [memory_id]
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "corrected text" in text and "wrong text" not in text


class TestApiSync:
    """`PACTMemory.sync()`: the outcome channel and the returned ids."""

    @pytest.fixture
    def api_memory(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "sync_test.db"
        conn = sqlite3.connect(str(db_path))
        create_test_schema(conn)
        conn.close()
        with patch("scripts.memory_api._ensure_ready"), \
             patch("scripts.memory_api.sync_to_claude_md"):
            yield PACTMemory(
                project_id="test-project", session_id="test-session",
                db_path=db_path,
            ), db_path

    def test_no_records_is_empty_with_no_ids(self, api_memory):
        memory, _ = api_memory
        assert memory.last_sync_status is None
        assert memory.sync() == []
        assert memory.last_sync_status == "empty"

    def test_wrote_returns_the_ids_newest_first(self, api_memory):
        from scripts.working_memory import SyncResult
        memory, db_path = api_memory
        older = memory.save({"context": "older"})
        newer = memory.save({"context": "newer"})
        _backdate(db_path, older, "2026-01-01 00:00:00")
        _backdate(db_path, newer, "2026-01-02 00:00:00")

        with patch("scripts.memory_api.project_memories_to_claude_md",
                   return_value=SyncResult(SyncResult.WROTE)) as projector:
            ids = memory.sync(claude_md_root=Path("/anchor"))

        assert ids == [newer, older]
        assert memory.last_sync_status == "wrote"
        payload = projector.call_args.args[0]
        assert [m["id"] for m in payload] == [newer, older]
        assert payload[0]["context"] == "newer"
        assert projector.call_args.kwargs == {"claude_md_root": Path("/anchor")}

    def test_a_refusal_is_refused_with_no_ids(self, api_memory):
        from scripts.working_memory import AmbientSyncRefused
        memory, _ = api_memory
        memory.save({"context": "present"})
        with patch("scripts.memory_api.project_memories_to_claude_md",
                   side_effect=AmbientSyncRefused("guard")):
            assert memory.sync() == []
        assert memory.last_sync_status == "refused"

    def test_any_other_failure_is_failed_with_no_ids(self, api_memory):
        memory, _ = api_memory
        memory.save({"context": "present"})
        with patch("scripts.memory_api.project_memories_to_claude_md",
                   side_effect=RuntimeError("disk")):
            assert memory.sync() == []
        assert memory.last_sync_status == "failed"
