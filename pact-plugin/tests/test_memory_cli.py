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
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from helpers import create_test_schema, make_cli_memory_dict

# Add pact-memory skill root to path so `from scripts.cli import ...` works
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))

from scripts.cli import build_parser, cmd_save, cmd_search, cmd_list, cmd_get, cmd_status, cmd_setup, cmd_update, cmd_delete, main, _COMMANDS
from scripts.memory_api import PACTMemory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Arg Parsing
# ---------------------------------------------------------------------------

class TestCliArgParsing:
    """Test argparse configuration and subcommand routing."""

    def test_build_parser_returns_parser(self):
        parser = build_parser()
        assert parser is not None
        assert parser.prog == "pact-memory"

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
        expected = {"save", "search", "list", "get", "status", "setup", "update", "delete"}
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
            {"id": "abcd0001" + "0" * 24, "context": "first"},
            {"id": "abcd0002" + "0" * 24, "context": "second"},
        ]
        mock_pact_memory.get.side_effect = AmbiguousPrefixError("abcd", matches)
        parser = build_parser()
        args = parser.parse_args(["get", "abcd"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "AMBIGUOUS_PREFIX"
        assert err_output["prefix"] == "abcd"
        assert err_output["matches"] == matches

    def test_get_too_short_prefix_returns_error(self, mock_pact_memory, capsys):
        from scripts.database import PrefixTooShortError
        mock_pact_memory.get.side_effect = PrefixTooShortError("abc", minimum=4)
        parser = build_parser()
        args = parser.parse_args(["get", "abc"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "PREFIX_TOO_SHORT"
        assert err_output["minimum"] == 4

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
        mock_pact_memory.update.return_value = True
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", '{"context": "updated"}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 0
        mock_pact_memory.update.assert_called_once_with(
            "abc123", {"context": "updated"}, replace=False
        )
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["memory_id"] == "abc123"

    def test_update_not_found(self, mock_pact_memory, capsys):
        mock_pact_memory.update.return_value = False
        parser = build_parser()
        args = parser.parse_args(["update", "nonexistent", '{"context": "x"}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "NOT_FOUND"

    def test_update_with_stdin(self, mock_pact_memory, monkeypatch):
        mock_pact_memory.update.return_value = True
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
        mock_pact_memory.update.return_value = True
        parser = build_parser()
        args = parser.parse_args(["update", "abc123", '{"context": "x"}', "--db-path", "/tmp/t.db"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory) as mock_cls:
            with pytest.raises(SystemExit):
                cmd_update(args, db_path=Path("/tmp/t.db"))
        mock_cls.assert_called_once_with(db_path=Path("/tmp/t.db"))


class TestCliUpdateReplaceFlag:
    """Test the --replace flag and ValueError envelope on the update subcommand."""

    def test_replace_flag_forwards_true(self, mock_pact_memory):
        mock_pact_memory.update.return_value = True
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
        mock_pact_memory.update.return_value = True
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
        mock_pact_memory.delete.return_value = True
        parser = build_parser()
        args = parser.parse_args(["delete", "abc123"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_delete(args)
        assert exc_info.value.code == 0
        mock_pact_memory.delete.assert_called_once_with("abc123")
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["deleted"] is True
        assert output["result"]["memory_id"] == "abc123"

    def test_delete_not_found(self, mock_pact_memory, capsys):
        mock_pact_memory.delete.return_value = False
        parser = build_parser()
        args = parser.parse_args(["delete", "nonexistent"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_delete(args)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["error"] == "NOT_FOUND"

    def test_delete_passes_db_path(self, mock_pact_memory):
        mock_pact_memory.delete.return_value = True
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

    def test_main_passes_db_path(self, mock_pact_memory):
        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory) as mock_cls:
            with pytest.raises(SystemExit):
                main(["list", "--db-path", "/tmp/test.db"])
        mock_cls.assert_called_once_with(db_path=Path("/tmp/test.db"))

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

    def test_save_and_get_roundtrip(self, cli_script_path, cli_db):
        memory_dict = make_cli_memory_dict()
        json_str = json.dumps(memory_dict)

        # Save
        save_result = subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert save_result.returncode == 0, f"save stderr: {save_result.stderr}"
        save_output = json.loads(save_result.stdout)
        assert save_output["ok"] is True
        memory_id = save_output["result"]["memory_id"]

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

    def test_save_via_stdin(self, cli_script_path, cli_db):
        memory_dict = make_cli_memory_dict(context="stdin test")
        json_str = json.dumps(memory_dict)

        result = subprocess.run(
            [sys.executable, cli_script_path, "save", "--stdin",
             "--db-path", str(cli_db)],
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
             "--db-path", str(cli_db)],
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
             "--db-path", str(cli_db)],
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
             "--db-path", str(cli_db)],
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
        # Note: graph_enhanced_search opens its own DB connection to the global
        # database, bypassing --db-path. The subprocess search returns results
        # from the global DB, not cli_db. We verify the envelope is correct and
        # the result is a list — content assertion requires the search backend
        # to honor --db-path, which is tracked as a known limitation.
        assert isinstance(output["result"], list)

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
                 "--db-path", str(cli_db)],
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
             "--db-path", str(cli_db)],
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
             "--db-path", str(cli_db)],
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
             "--db-path", str(cli_db)],
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
        assert "pact-memory" in captured.out

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
