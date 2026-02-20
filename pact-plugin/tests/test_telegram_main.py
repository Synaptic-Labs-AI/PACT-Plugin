"""
Tests for pact-plugin/telegram/__main__.py

Tests cover:
1. main() with missing dependencies: prints friendly error and exits 1
2. main() with all deps present: calls create_server and runs it
3. Error message format: includes missing package names and setup instructions
"""

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock the mcp module tree before importing anything that touches server.py
if "mcp" not in sys.modules:
    _mcp = ModuleType("mcp")
    _mcp_server = ModuleType("mcp.server")
    _mcp_server_fastmcp = ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        """Minimal stub for FastMCP."""
        def __init__(self, name, **kwargs):
            self.name = name
            self._lifespan = kwargs.get("lifespan")
            self.instructions = kwargs.get("instructions")
        def tool(self, **kwargs):
            def decorator(fn):
                return fn
            return decorator
        def run(self, **kwargs):
            pass

    _mcp_server_fastmcp.FastMCP = _FakeFastMCP
    _mcp.server = _mcp_server
    _mcp_server.fastmcp = _mcp_server_fastmcp
    sys.modules["mcp"] = _mcp
    sys.modules["mcp.server"] = _mcp_server
    sys.modules["mcp.server.fastmcp"] = _mcp_server_fastmcp

from telegram.__main__ import main


# =============================================================================
# Missing Dependencies Tests
# =============================================================================

class TestMainMissingDeps:
    """Tests for main() when required dependencies are missing."""

    def test_exits_with_code_1_when_deps_missing(self):
        """Should exit with code 1 when check_dependencies returns missing packages."""
        with patch("telegram.deps.check_dependencies", return_value=["mcp", "httpx"]), \
             pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_prints_missing_package_names_to_stderr(self, capsys):
        """Should print missing package names in the error message."""
        with patch("telegram.deps.check_dependencies", return_value=["mcp"]), \
             pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "mcp" in captured.err

    def test_prints_setup_instructions_to_stderr(self, capsys):
        """Should print setup command instructions in the error message."""
        with patch("telegram.deps.check_dependencies", return_value=["httpx"]), \
             pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "/PACT:telegram-setup" in captured.err
        assert "pip install" in captured.err

    def test_prints_all_missing_packages(self, capsys):
        """Should list all missing packages, not just the first."""
        with patch("telegram.deps.check_dependencies", return_value=["mcp", "httpx"]), \
             pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "mcp" in captured.err
        assert "httpx" in captured.err


# =============================================================================
# Successful Start Tests
# =============================================================================

class TestMainSuccessfulStart:
    """Tests for main() when all dependencies are present."""

    def test_calls_create_server_and_runs(self):
        """Should create server and run with stdio transport when deps are satisfied."""
        mock_server = MagicMock()

        with patch("telegram.deps.check_dependencies", return_value=[]), \
             patch("telegram.server.create_server", return_value=mock_server) as mock_create:
            main()

        mock_create.assert_called_once()
        mock_server.run.assert_called_once_with(transport="stdio")

    def test_does_not_exit_on_success(self):
        """Should not call sys.exit when dependencies are present."""
        mock_server = MagicMock()

        with patch("telegram.deps.check_dependencies", return_value=[]), \
             patch("telegram.server.create_server", return_value=mock_server):
            # Should return normally, not raise SystemExit
            main()
