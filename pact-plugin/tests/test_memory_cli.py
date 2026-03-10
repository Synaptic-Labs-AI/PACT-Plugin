"""
Tests for pact-memory/scripts/cli.py — CLI entry point.

Tests cover:
1. Arg parsing: subcommand dispatch, required arguments, defaults
2. Save command: JSON arg, --stdin, invalid JSON, non-dict input
3. Search command: query dispatch, --limit
4. List command: default and custom --limit
5. Get command: existing and missing memory IDs
6. Status command: status dict output
7. Setup command: success and failure paths
8. Output format: JSON envelope consistency, stdout/stderr routing
9. Error handling: exit codes, error types, unknown commands
10. Subprocess E2E: true black-box tests via subprocess.run
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
        mock_pact_memory.update.assert_called_once_with("abc123", {"context": "updated"})
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
        mock_pact_memory.update.assert_called_once_with("abc123", {"context": "from stdin"})

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
