"""
Tests for pact-plugin/telegram/deps.py

Tests cover:
1. check_dependencies: detecting missing/present packages
2. install_dependencies: pip subprocess invocation, success/failure, timeout
3. get_dependency_status: per-package status reporting
4. get_optional_dependency_status: voice transcription availability
"""

import importlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram.deps import (
    check_dependencies,
    install_dependencies,
    get_dependency_status,
    get_optional_dependency_status,
    REQUIRED_PACKAGES,
    REQUIREMENTS_FILE,
)


# =============================================================================
# check_dependencies Tests
# =============================================================================

class TestCheckDependencies:
    """Tests for check_dependencies -- detecting missing packages."""

    def test_returns_empty_when_all_present(self):
        """Should return empty list when all dependencies are importable."""
        with patch("telegram.deps.importlib.import_module") as mock_import:
            mock_import.return_value = MagicMock()
            result = check_dependencies()

        assert result == []

    def test_returns_missing_packages(self):
        """Should return pip names of missing packages."""
        def side_effect(name):
            if name == "mcp":
                raise ImportError("No module named 'mcp'")
            return MagicMock()

        with patch("telegram.deps.importlib.import_module", side_effect=side_effect):
            result = check_dependencies()

        assert "mcp" in result

    def test_returns_all_missing_when_none_installed(self):
        """Should return all packages when none are importable."""
        with patch("telegram.deps.importlib.import_module", side_effect=ImportError):
            result = check_dependencies()

        assert len(result) == len(REQUIRED_PACKAGES)


# =============================================================================
# install_dependencies Tests
# =============================================================================

class TestInstallDependencies:
    """Tests for install_dependencies -- pip installation subprocess."""

    def test_success_with_requirements_file(self, tmp_path):
        """Should succeed when pip installs from requirements.txt."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("telegram.deps.subprocess.run", return_value=mock_result):
            with patch("telegram.deps.REQUIREMENTS_FILE", tmp_path / "requirements.txt"):
                # Create the file so it exists
                (tmp_path / "requirements.txt").write_text("mcp>=1.25\nhttpx>=0.27\n")
                with patch("telegram.deps.check_dependencies", return_value=[]):
                    success, msg = install_dependencies(quiet=True)

        assert success is True
        assert "successfully" in msg.lower()

    def test_failure_when_pip_fails(self):
        """Should return failure when pip returns non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ERROR: Could not install"
        mock_result.stdout = ""

        with patch("telegram.deps.subprocess.run", return_value=mock_result):
            success, msg = install_dependencies()

        assert success is False
        assert "pip install failed" in msg

    def test_failure_on_timeout(self):
        """Should handle pip timeout gracefully."""
        with patch("telegram.deps.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="pip", timeout=120)
            success, msg = install_dependencies()

        assert success is False
        assert "timed out" in msg

    def test_failure_when_pip_not_found(self):
        """Should handle missing pip gracefully."""
        with patch("telegram.deps.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("pip not found")
            success, msg = install_dependencies()

        assert success is False
        assert "pip not found" in msg

    def test_success_but_still_missing_warns(self):
        """Should warn when pip succeeds but packages still not importable."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("telegram.deps.subprocess.run", return_value=mock_result):
            with patch("telegram.deps.check_dependencies", return_value=["mcp"]):
                success, msg = install_dependencies()

        assert success is False
        assert "still not importable" in msg

    def test_fallback_to_direct_install_when_no_requirements(self, tmp_path):
        """Should install packages directly when requirements.txt not found."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("telegram.deps.REQUIREMENTS_FILE", tmp_path / "nonexistent.txt"):
            with patch("telegram.deps.check_dependencies", side_effect=[["mcp"], []]):
                with patch("telegram.deps.subprocess.run", return_value=mock_result) as mock_run:
                    success, msg = install_dependencies()

        assert success is True
        # Verify "mcp" was passed directly to pip
        call_args = mock_run.call_args[0][0]
        assert "mcp" in call_args

    def test_returns_success_when_already_installed(self, tmp_path):
        """Should return success immediately when all deps already installed."""
        with patch("telegram.deps.REQUIREMENTS_FILE", tmp_path / "nonexistent.txt"):
            with patch("telegram.deps.check_dependencies", return_value=[]):
                success, msg = install_dependencies()

        assert success is True
        assert "already installed" in msg.lower()


# =============================================================================
# get_dependency_status Tests
# =============================================================================

class TestGetDependencyStatus:
    """Tests for get_dependency_status -- per-package status check."""

    def test_reports_all_packages(self):
        """Should report status for all required packages."""
        with patch("telegram.deps.importlib.import_module") as mock_import:
            mock_import.return_value = MagicMock()
            status = get_dependency_status()

        assert len(status) == len(REQUIRED_PACKAGES)
        for import_name, _ in REQUIRED_PACKAGES:
            assert import_name in status

    def test_reports_missing_package(self):
        """Should report False for missing packages."""
        def side_effect(name):
            if name == "mcp":
                raise ImportError()
            return MagicMock()

        with patch("telegram.deps.importlib.import_module", side_effect=side_effect):
            status = get_dependency_status()

        assert status["mcp"] is False
        assert status["httpx"] is True


# =============================================================================
# get_optional_dependency_status Tests
# =============================================================================

class TestGetOptionalDependencyStatus:
    """Tests for get_optional_dependency_status."""

    def test_voice_available_with_api_key(self):
        """Should report voice_transcription=True when OpenAI key configured."""
        mock_config = {"openai_api_key": "sk-test"}
        with patch("telegram.config.load_config", return_value=mock_config):
            status = get_optional_dependency_status()

        assert status["voice_transcription"] is True

    def test_voice_unavailable_without_api_key(self):
        """Should report voice_transcription=False when no OpenAI key."""
        mock_config = {"openai_api_key": None}
        with patch("telegram.config.load_config", return_value=mock_config):
            status = get_optional_dependency_status()

        assert status["voice_transcription"] is False

    def test_voice_unavailable_on_config_error(self):
        """Should report voice_transcription=False when config fails."""
        with patch("telegram.config.load_config", side_effect=Exception("no config")):
            status = get_optional_dependency_status()

        assert status["voice_transcription"] is False
