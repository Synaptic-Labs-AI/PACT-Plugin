"""
Location: pact-plugin/telegram/config.py
Summary: Configuration loader for pact-telegram using stdlib .env parsing.
Used by: All other telegram modules (server.py, tools.py, telegram_client.py,
         voice.py, notify.py) to access bot token, chat ID, and optional settings.

Reads configuration from ~/.claude/pact-telegram/.env using a pure-stdlib
parser (no python-dotenv dependency). Validates required fields and provides
typed access to configuration values.

Config schema (~/.claude/pact-telegram/.env):
    TELEGRAM_BOT_TOKEN=123456:ABC-DEF...    # Required
    TELEGRAM_CHAT_ID=987654321              # Required
    OPENAI_API_KEY=sk-...                   # Optional (voice notes)
    PACT_TELEGRAM_MODE=passive              # passive|active (default: passive)

Security controls:
- .env file permissions checked (should be 600)
- Verifies .env is not inside a git working tree
- Bot token and API keys never appear in logs or error messages
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("pact-telegram.config")

# Default config directory
CONFIG_DIR = Path.home() / ".claude" / "pact-telegram"
ENV_FILE = CONFIG_DIR / ".env"

# Valid modes
VALID_MODES = {"passive", "active"}
DEFAULT_MODE = "passive"


class ConfigError(Exception):
    """Raised when configuration is invalid or missing required fields."""


def parse_env_file(path: Path) -> dict[str, str]:
    """
    Parse a .env file using stdlib only.

    Handles:
    - KEY=VALUE pairs (one per line)
    - Quoted values (single and double quotes, stripped)
    - Comments (lines starting with #)
    - Blank lines
    - Inline comments after unquoted values

    Does NOT handle:
    - Variable expansion ($VAR or ${VAR})
    - Multi-line values
    - Export prefixes

    Args:
        path: Path to the .env file.

    Returns:
        Dict of parsed key-value pairs.

    Raises:
        ConfigError: If the file cannot be read.
    """
    if not path.exists():
        return {}

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"Cannot read config file: {type(e).__name__}") from e

    result: dict[str, str] = {}

    for line_num, line in enumerate(content.splitlines(), start=1):
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue

        # Split on first '='
        if "=" not in line:
            logger.debug("Skipping malformed line %d (no '=' found)", line_num)
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        # Strip quotes from value
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            # Remove inline comments for unquoted values
            for comment_char in (" #", "\t#"):
                if comment_char in value:
                    value = value[: value.index(comment_char)].rstrip()
                    break

        result[key] = value

    return result


def check_file_permissions(path: Path) -> list[str]:
    """
    Check .env file permissions for security.

    Warns if the file is readable by group or others (should be 600).

    Args:
        path: Path to the .env file.

    Returns:
        List of warning messages (empty if permissions are secure).
    """
    warnings: list[str] = []

    if not path.exists():
        return warnings

    try:
        file_stat = path.stat()
        mode = file_stat.st_mode

        if mode & stat.S_IRGRP:
            warnings.append("Config file is group-readable (recommend chmod 600)")
        if mode & stat.S_IROTH:
            warnings.append("Config file is world-readable (recommend chmod 600)")
        if mode & stat.S_IWGRP or mode & stat.S_IWOTH:
            warnings.append("Config file is writable by others (recommend chmod 600)")
    except OSError:
        pass

    return warnings


def check_not_in_git(path: Path) -> bool:
    """
    Verify .env file is not inside a git working tree.

    This prevents accidental commits of credentials.

    Args:
        path: Path to the .env file.

    Returns:
        True if the file is safely outside any git repo, False if inside one.
    """
    if not path.exists():
        return True

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=str(path.parent),
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return True


def load_config(
    env_path: Path | None = None,
    check_security: bool = True,
) -> dict[str, Any]:
    """
    Load and validate pact-telegram configuration.

    Reads the .env file at the given path (or default location), validates
    required fields, and returns a typed configuration dict.

    Args:
        env_path: Optional override for the .env file path. Defaults to
                  ~/.claude/pact-telegram/.env.
        check_security: Whether to perform security checks (permissions,
                        git location). Set to False in tests.

    Returns:
        Configuration dict with keys:
            - bot_token (str): Telegram bot token (required)
            - chat_id (str): Telegram chat ID (required)
            - openai_api_key (str | None): OpenAI API key (optional)
            - mode (str): "passive" or "active"
            - config_dir (Path): Directory containing .env
            - warnings (list[str]): Security warnings

    Raises:
        ConfigError: If required fields are missing or invalid.
    """
    path = env_path or ENV_FILE
    warnings: list[str] = []

    # Parse the .env file
    raw = parse_env_file(path)

    # Security checks
    if check_security and path.exists():
        perm_warnings = check_file_permissions(path)
        warnings.extend(perm_warnings)

        if not check_not_in_git(path):
            warnings.append(
                "WARNING: Config file is inside a git working tree. "
                "Credentials may be accidentally committed."
            )

    # Extract and validate fields
    bot_token = raw.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = raw.get("TELEGRAM_CHAT_ID", "").strip()
    openai_api_key = raw.get("OPENAI_API_KEY", "").strip() or None
    mode = raw.get("PACT_TELEGRAM_MODE", DEFAULT_MODE).strip().lower()

    # Validate required fields
    if not bot_token:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN is required. "
            "Run /PACT:telegram-setup to configure."
        )

    if not chat_id:
        raise ConfigError(
            "TELEGRAM_CHAT_ID is required. "
            "Run /PACT:telegram-setup to configure."
        )

    # Validate chat_id is numeric
    if not chat_id.lstrip("-").isdigit():
        raise ConfigError(
            f"TELEGRAM_CHAT_ID must be numeric, got: {chat_id[:10]}..."
        )

    # Validate mode
    if mode not in VALID_MODES:
        logger.warning(
            "Invalid PACT_TELEGRAM_MODE '%s', falling back to '%s'",
            mode,
            DEFAULT_MODE,
        )
        mode = DEFAULT_MODE

    # Log warnings (no credentials in logs)
    for warning in warnings:
        logger.warning(warning)

    return {
        "bot_token": bot_token,
        "chat_id": chat_id,
        "openai_api_key": openai_api_key,
        "mode": mode,
        "config_dir": path.parent,
        "warnings": warnings,
    }


def load_config_safe(
    env_path: Path | None = None,
) -> dict[str, Any] | None:
    """
    Load configuration without raising exceptions.

    Returns None if configuration is missing or invalid. Used by the MCP
    server to implement graceful no-op behavior when unconfigured.

    Args:
        env_path: Optional override for the .env file path.

    Returns:
        Configuration dict or None if unavailable.
    """
    try:
        return load_config(env_path=env_path)
    except ConfigError:
        return None


def ensure_config_dir() -> Path:
    """
    Ensure the config directory exists with secure permissions.

    Creates ~/.claude/pact-telegram/ with mode 700 if it doesn't exist.

    Returns:
        Path to the config directory.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass

    return CONFIG_DIR
