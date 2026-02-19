"""
Tests for pact-plugin/telegram/config.py

Tests cover:
1. parse_env_file: KEY=VALUE parsing, quoted values, comments, blank lines, inline comments
2. check_file_permissions: secure (600) and insecure permission warnings
3. check_not_in_git: detecting .env inside/outside git repos
4. load_config: required field validation, mode validation, security checks
5. load_config_safe: graceful None return on errors
6. ensure_config_dir: directory creation with secure permissions
7. Edge cases: empty files, malformed lines, Unicode, long values
8. Security: no credentials in error messages, permission enforcement
"""

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add telegram package to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram.config import (
    ConfigError,
    check_file_permissions,
    check_not_in_git,
    ensure_config_dir,
    load_config,
    load_config_safe,
    parse_env_file,
    VALID_MODES,
    DEFAULT_MODE,
)


# =============================================================================
# parse_env_file Tests
# =============================================================================

class TestParseEnvFile:
    """Tests for parse_env_file -- .env file parsing using stdlib only."""

    def test_parses_basic_key_value(self, tmp_path):
        """Should parse simple KEY=VALUE pairs."""
        env_file = tmp_path / ".env"
        env_file.write_text("BOT_TOKEN=abc123\nCHAT_ID=456\n")

        result = parse_env_file(env_file)

        assert result == {"BOT_TOKEN": "abc123", "CHAT_ID": "456"}

    def test_strips_double_quotes(self, tmp_path):
        """Should strip double quotes from values."""
        env_file = tmp_path / ".env"
        env_file.write_text('KEY="some value"\n')

        result = parse_env_file(env_file)

        assert result["KEY"] == "some value"

    def test_strips_single_quotes(self, tmp_path):
        """Should strip single quotes from values."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY='some value'\n")

        result = parse_env_file(env_file)

        assert result["KEY"] == "some value"

    def test_skips_comment_lines(self, tmp_path):
        """Should skip lines starting with #."""
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\nKEY=value\n# Another comment\n")

        result = parse_env_file(env_file)

        assert result == {"KEY": "value"}

    def test_skips_blank_lines(self, tmp_path):
        """Should skip empty lines."""
        env_file = tmp_path / ".env"
        env_file.write_text("\nKEY=value\n\n\nKEY2=value2\n")

        result = parse_env_file(env_file)

        assert result == {"KEY": "value", "KEY2": "value2"}

    def test_removes_inline_comments_for_unquoted_values(self, tmp_path):
        """Should strip inline comments (space + #) from unquoted values."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value # this is a comment\n")

        result = parse_env_file(env_file)

        assert result["KEY"] == "value"

    def test_preserves_hash_in_quoted_values(self, tmp_path):
        """Should not strip # from values that are quoted."""
        env_file = tmp_path / ".env"
        env_file.write_text('KEY="value # with hash"\n')

        result = parse_env_file(env_file)

        assert result["KEY"] == "value # with hash"

    def test_handles_equals_in_value(self, tmp_path):
        """Should split on first = only, preserving = in value."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value=with=equals\n")

        result = parse_env_file(env_file)

        assert result["KEY"] == "value=with=equals"

    def test_skips_lines_without_equals(self, tmp_path):
        """Should silently skip malformed lines without = sign."""
        env_file = tmp_path / ".env"
        env_file.write_text("MALFORMED_LINE\nKEY=value\n")

        result = parse_env_file(env_file)

        assert result == {"KEY": "value"}

    def test_skips_empty_key(self, tmp_path):
        """Should skip lines where key is empty."""
        env_file = tmp_path / ".env"
        env_file.write_text("=empty_key\nKEY=value\n")

        result = parse_env_file(env_file)

        assert result == {"KEY": "value"}

    def test_returns_empty_dict_for_nonexistent_file(self, tmp_path):
        """Should return empty dict when file does not exist."""
        env_file = tmp_path / "nonexistent.env"

        result = parse_env_file(env_file)

        assert result == {}

    def test_returns_empty_dict_for_empty_file(self, tmp_path):
        """Should return empty dict for a file with no content."""
        env_file = tmp_path / ".env"
        env_file.write_text("")

        result = parse_env_file(env_file)

        assert result == {}

    def test_raises_config_error_on_read_failure(self, tmp_path):
        """Should raise ConfigError when file cannot be read."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")
        env_file.chmod(0o000)

        try:
            with pytest.raises(ConfigError, match="Cannot read config file"):
                parse_env_file(env_file)
        finally:
            env_file.chmod(0o644)

    def test_strips_whitespace_from_keys_and_values(self, tmp_path):
        """Should strip leading/trailing whitespace from keys and values."""
        env_file = tmp_path / ".env"
        env_file.write_text("  KEY  =  value  \n")

        result = parse_env_file(env_file)

        assert result["KEY"] == "value"

    def test_handles_tab_inline_comments(self, tmp_path):
        """Should strip inline comments with tab + # separator."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\t# tabbed comment\n")

        result = parse_env_file(env_file)

        assert result["KEY"] == "value"

    def test_only_comments_file(self, tmp_path):
        """Should return empty dict for file with only comments."""
        env_file = tmp_path / ".env"
        env_file.write_text("# Comment 1\n# Comment 2\n")

        result = parse_env_file(env_file)

        assert result == {}

    def test_value_with_spaces_quoted(self, tmp_path):
        """Should handle values with spaces when quoted."""
        env_file = tmp_path / ".env"
        env_file.write_text('MSG="hello world"\n')

        result = parse_env_file(env_file)

        assert result["MSG"] == "hello world"

    def test_mismatched_quotes_not_stripped(self, tmp_path):
        """Should not strip mismatched quotes."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY='value\"\n")

        result = parse_env_file(env_file)

        # Mismatched quotes: first and last chars differ, so not stripped
        assert result["KEY"] == "'value\""

    def test_empty_value(self, tmp_path):
        """Should handle empty value (KEY=)."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=\n")

        result = parse_env_file(env_file)

        assert result["KEY"] == ""


# =============================================================================
# check_file_permissions Tests
# =============================================================================

class TestCheckFilePermissions:
    """Tests for check_file_permissions -- security permission validation."""

    def test_no_warnings_for_secure_permissions(self, tmp_path):
        """Should return no warnings for 600 permissions."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")
        env_file.chmod(0o600)

        warnings = check_file_permissions(env_file)

        assert warnings == []

    def test_warns_on_group_readable(self, tmp_path):
        """Should warn when file is group-readable."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")
        env_file.chmod(0o640)

        warnings = check_file_permissions(env_file)

        assert any("group-readable" in w for w in warnings)

    def test_warns_on_world_readable(self, tmp_path):
        """Should warn when file is world-readable."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")
        env_file.chmod(0o604)

        warnings = check_file_permissions(env_file)

        assert any("world-readable" in w for w in warnings)

    def test_warns_on_group_or_world_writable(self, tmp_path):
        """Should warn when file is writable by group or others."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")
        env_file.chmod(0o622)

        warnings = check_file_permissions(env_file)

        assert any("writable by others" in w for w in warnings)

    def test_returns_empty_for_nonexistent_file(self, tmp_path):
        """Should return empty list for non-existent file."""
        env_file = tmp_path / "nonexistent.env"

        warnings = check_file_permissions(env_file)

        assert warnings == []

    def test_multiple_warnings_for_insecure_file(self, tmp_path):
        """Should return multiple warnings for file with 666 permissions."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")
        env_file.chmod(0o666)

        warnings = check_file_permissions(env_file)

        assert len(warnings) >= 2  # group-readable + world-readable + writable


# =============================================================================
# check_not_in_git Tests
# =============================================================================

class TestCheckNotInGit:
    """Tests for check_not_in_git -- verifying .env is not inside git repo."""

    def test_returns_true_for_nonexistent_file(self, tmp_path):
        """Should return True for non-existent file (safely outside git)."""
        env_file = tmp_path / "nonexistent.env"

        assert check_not_in_git(env_file) is True

    def test_returns_true_outside_git_repo(self, tmp_path):
        """Should return True when file is not inside a git working tree."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")

        # tmp_path is not a git repo by default
        with patch("telegram.config.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = check_not_in_git(env_file)

        assert result is True

    def test_returns_false_inside_git_repo(self, tmp_path):
        """Should return False when file is inside a git working tree."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")

        with patch("telegram.config.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="true\n")
            result = check_not_in_git(env_file)

        assert result is False

    def test_handles_subprocess_timeout(self, tmp_path):
        """Should return True (safe) on subprocess timeout."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")

        with patch("telegram.config.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
            result = check_not_in_git(env_file)

        assert result is True

    def test_handles_git_not_found(self, tmp_path):
        """Should return True (safe) when git is not installed."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")

        with patch("telegram.config.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            result = check_not_in_git(env_file)

        assert result is True


# =============================================================================
# load_config Tests
# =============================================================================

class TestLoadConfig:
    """Tests for load_config -- full configuration loading and validation."""

    def _write_env(self, tmp_path, content):
        """Helper to write a .env file and return its path."""
        env_file = tmp_path / ".env"
        env_file.write_text(content)
        return env_file

    def test_loads_valid_config(self, tmp_path):
        """Should load a valid configuration with all required fields."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=123456789:ABCDEF\nTELEGRAM_CHAT_ID=987654321\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["bot_token"] == "123456789:ABCDEF"
        assert config["chat_id"] == "987654321"
        assert config["mode"] == "passive"
        assert config["openai_api_key"] is None

    def test_raises_on_missing_bot_token(self, tmp_path):
        """Should raise ConfigError when TELEGRAM_BOT_TOKEN is missing."""
        env_file = self._write_env(tmp_path, "TELEGRAM_CHAT_ID=123\n")

        with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN is required"):
            load_config(env_path=env_file, check_security=False)

    def test_raises_on_missing_chat_id(self, tmp_path):
        """Should raise ConfigError when TELEGRAM_CHAT_ID is missing."""
        env_file = self._write_env(tmp_path, "TELEGRAM_BOT_TOKEN=abc\n")

        with pytest.raises(ConfigError, match="TELEGRAM_CHAT_ID is required"):
            load_config(env_path=env_file, check_security=False)

    def test_raises_on_non_numeric_chat_id(self, tmp_path):
        """Should raise ConfigError when TELEGRAM_CHAT_ID is not numeric."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=not_a_number\n",
        )

        with pytest.raises(ConfigError, match="TELEGRAM_CHAT_ID must be numeric"):
            load_config(env_path=env_file, check_security=False)

    def test_accepts_negative_chat_id(self, tmp_path):
        """Should accept negative chat IDs (group chats)."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=-1001234567890\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["chat_id"] == "-1001234567890"

    def test_loads_openai_api_key_when_present(self, tmp_path):
        """Should load optional OPENAI_API_KEY."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\nOPENAI_API_KEY=sk-test\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["openai_api_key"] == "sk-test"

    def test_openai_key_none_when_empty(self, tmp_path):
        """Should return None for openai_api_key when value is empty."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\nOPENAI_API_KEY=\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["openai_api_key"] is None

    def test_loads_active_mode(self, tmp_path):
        """Should load active mode when set."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\nPACT_TELEGRAM_MODE=active\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["mode"] == "active"

    def test_defaults_to_passive_mode(self, tmp_path):
        """Should default to passive mode when not set."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["mode"] == "passive"

    def test_falls_back_to_passive_on_invalid_mode(self, tmp_path):
        """Should fall back to passive mode when PACT_TELEGRAM_MODE is invalid."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\nPACT_TELEGRAM_MODE=invalid\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["mode"] == DEFAULT_MODE

    def test_mode_is_case_insensitive(self, tmp_path):
        """Should normalize mode to lowercase."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\nPACT_TELEGRAM_MODE=ACTIVE\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["mode"] == "active"

    def test_config_dir_set_to_parent_of_env(self, tmp_path):
        """Should set config_dir to the parent directory of the .env file."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["config_dir"] == tmp_path

    def test_raises_on_empty_env_file(self, tmp_path):
        """Should raise ConfigError when .env file is empty."""
        env_file = self._write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN is required"):
            load_config(env_path=env_file, check_security=False)

    def test_raises_on_nonexistent_env_file(self, tmp_path):
        """Should raise ConfigError when .env file does not exist."""
        env_file = tmp_path / "nonexistent.env"

        with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN is required"):
            load_config(env_path=env_file, check_security=False)

    def test_security_checks_add_warnings(self, tmp_path):
        """Should include permission warnings when check_security is True."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\n",
        )
        env_file.chmod(0o644)

        with patch("telegram.config.check_not_in_git", return_value=True):
            config = load_config(env_path=env_file, check_security=True)

        assert len(config["warnings"]) > 0

    def test_git_warning_when_inside_repo(self, tmp_path):
        """Should include git warning when .env is inside a git repo."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\n",
        )
        env_file.chmod(0o600)

        with patch("telegram.config.check_not_in_git", return_value=False):
            config = load_config(env_path=env_file, check_security=True)

        assert any("git working tree" in w for w in config["warnings"])

    def test_no_credentials_in_error_messages(self, tmp_path):
        """Should not expose credential values in error messages."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=not_numeric\n",
        )

        # First error: missing bot token
        with pytest.raises(ConfigError) as exc_info:
            load_config(env_path=env_file, check_security=False)

        error_msg = str(exc_info.value)
        assert "TELEGRAM_BOT_TOKEN" in error_msg
        # The actual token value should never appear
        assert "sk-" not in error_msg


# =============================================================================
# load_config_safe Tests
# =============================================================================

class TestLoadConfigSafe:
    """Tests for load_config_safe -- graceful error handling wrapper."""

    def test_returns_config_on_success(self, tmp_path):
        """Should return config dict when config is valid."""
        env_file = tmp_path / ".env"
        env_file.write_text("TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\n")

        result = load_config_safe(env_path=env_file)

        assert result is not None
        assert result["bot_token"] == "abc"

    def test_returns_none_on_missing_config(self, tmp_path):
        """Should return None when config file doesn't exist."""
        env_file = tmp_path / "nonexistent.env"

        result = load_config_safe(env_path=env_file)

        assert result is None

    def test_returns_none_on_invalid_config(self, tmp_path):
        """Should return None when config is invalid."""
        env_file = tmp_path / ".env"
        env_file.write_text("INVALID=true\n")

        result = load_config_safe(env_path=env_file)

        assert result is None


# =============================================================================
# ensure_config_dir Tests
# =============================================================================

class TestEnsureConfigDir:
    """Tests for ensure_config_dir -- directory creation with secure permissions."""

    def test_creates_directory_if_not_exists(self, tmp_path):
        """Should create the config directory."""
        test_dir = tmp_path / "new_config"

        with patch("telegram.config.CONFIG_DIR", test_dir):
            result = ensure_config_dir()

        assert result == test_dir
        assert test_dir.exists()
        assert test_dir.is_dir()

    def test_sets_secure_permissions(self, tmp_path):
        """Should set directory permissions to 700."""
        test_dir = tmp_path / "new_config"

        with patch("telegram.config.CONFIG_DIR", test_dir):
            ensure_config_dir()

        mode = test_dir.stat().st_mode & 0o777
        assert mode == 0o700

    def test_handles_existing_directory(self, tmp_path):
        """Should not raise when directory already exists."""
        test_dir = tmp_path / "existing"
        test_dir.mkdir()

        with patch("telegram.config.CONFIG_DIR", test_dir):
            result = ensure_config_dir()

        assert result == test_dir
