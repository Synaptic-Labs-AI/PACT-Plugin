#!/usr/bin/env python3
"""
Location: pact-plugin/telegram/notify.py
Summary: Standalone Stop hook that sends session-end notifications to Telegram.
Used by: Claude Code hooks.json (Stop event) — fires on session end.

This script is intentionally standalone with ZERO external dependencies.
It uses only Python stdlib (urllib.request, json, os, sys, re) so it can
run reliably even if the MCP server's dependencies (mcp, httpx) are not
installed.

Contract:
    Input: JSON from stdin with { "session_id": "...", "transcript": "...",
           "hook_event_name": "Stop" }
    Output: HTTP POST to Telegram sendMessage (fire-and-forget)
    Exit: Always 0 (never block shutdown)

Security controls:
- Content filter applied to outbound messages (inline regex, same patterns
  as content_filter.py but self-contained for zero-dep independence)
- Reads config from ~/.claude/pact-telegram/.env
- 5-second HTTP timeout (never blocks shutdown)
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Config location
ENV_FILE = Path.home() / ".claude" / "pact-telegram" / ".env"

# Telegram API
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# HTTP timeout (seconds) — must be short to not block shutdown
HTTP_TIMEOUT = 5

# Credential patterns for inline content filter (self-contained, no imports).
# Mirrors content_filter.py patterns but kept here for zero-dep independence.
_REDACT_PATTERNS = [
    # AWS access keys
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED]"),

    # OpenAI API keys
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),

    # Telegram bot tokens
    (re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}"), "[REDACTED]"),

    # GitHub tokens
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"), "[REDACTED]"),

    # Generic API key/secret/token assignments
    (re.compile(
        r"(?i)(?:api[_-]?key|apikey|api[_-]?secret|api[_-]?token)"
        r"[\s]*[=:]\s*['\"]?([A-Za-z0-9_\-/.+=]{16,})['\"]?"
    ), "[REDACTED]"),

    # Bearer tokens
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-/.+=]{16,}"), "[REDACTED]"),

    # Authorization headers
    (re.compile(r"(?i)authorization[\s]*[=:]\s*['\"]?[A-Za-z0-9_\-/.+=\s]{16,}['\"]?"), "[REDACTED]"),

    # Password/secret/token assignments
    (re.compile(
        r"(?i)(?:password|passwd|pwd|secret|token)"
        r"[\s]*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?"
    ), "[REDACTED]"),

    # Connection strings with credentials
    (re.compile(
        r"(?i)(?:postgres|mysql|mongodb|redis|amqp)(?:ql)?://"
        r"[^:]+:[^@]+@"
    ), "[REDACTED]://"),

    # Private key blocks
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"), "[REDACTED]"),

    # .env file content patterns
    (re.compile(
        r"(?i)^(?:TELEGRAM_BOT_TOKEN|OPENAI_API_KEY|AWS_SECRET|DB_PASSWORD|"
        r"SECRET_KEY|PRIVATE_KEY|AUTH_TOKEN)"
        r"\s*=\s*.+$",
        re.MULTILINE,
    ), "[REDACTED]"),

    # JWT tokens
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_\-+=]+"), "[REDACTED]"),

    # Hex strings that look like secrets (48+ hex chars, avoids git SHAs)
    (re.compile(r"(?<![A-Fa-f0-9])[0-9a-f]{48,}(?![A-Fa-f0-9])"), "[REDACTED]"),
]


def _filter_message(text: str) -> str:
    """Apply inline content filter to redact credential patterns."""
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _parse_env() -> dict[str, str]:
    """
    Parse the .env file using stdlib only.

    Returns empty dict if file doesn't exist or can't be read.
    """
    if not ENV_FILE.exists():
        return {}

    try:
        content = ENV_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    result: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value

    return result


def _get_project_name() -> str:
    """
    Get the project name from CLAUDE_PROJECT_DIR environment variable.

    Returns the basename of the project directory (e.g. 'PACT-prompt'),
    falling back to 'unknown' if the variable is not set.
    Stdlib-only implementation (mirrors tools._get_project_name).
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project_dir:
        return os.path.basename(project_dir)
    return "unknown"


def _build_session_summary(input_data: dict) -> str:
    """
    Build a notification message from the Stop hook input data.

    Includes a session identifier prefix so the user knows which project
    instance sent the notification.

    Args:
        input_data: Parsed JSON from stdin.

    Returns:
        Formatted notification message with project name prefix.
    """
    project_name = _get_project_name()
    session_id = input_data.get("session_id", "unknown")
    # Show only first 8 chars of session ID for brevity
    short_id = str(session_id)[:8] if session_id else "unknown"

    parts = [f"<b>[{project_name}]</b>\n<b>Session ended</b> ({short_id})"]

    # Extract a brief summary from transcript if available
    transcript = input_data.get("transcript", "")
    if transcript:
        # Take last meaningful line (skip empty lines from end)
        lines = [l.strip() for l in transcript.strip().splitlines() if l.strip()]
        if lines:
            last_line = lines[-1][:200]  # Cap at 200 chars
            parts.append(f"\nLast activity: {last_line}")

    return "\n".join(parts)


def send_notification(bot_token: str, chat_id: str, message: str) -> bool:
    """
    Send a notification to Telegram via urllib.request.

    Fire-and-forget with 5-second timeout. Returns True on success,
    False on any error (never raises).

    Args:
        bot_token: Telegram bot token.
        chat_id: Target chat ID.
        message: Message text (will be filtered for credentials).

    Returns:
        True if message was sent successfully.
    """
    # Apply content filter
    filtered = _filter_message(message)

    # Truncate to Telegram limit
    if len(filtered) > 4096:
        filtered = filtered[:4060] + "\n\n[...truncated]"

    url = TELEGRAM_API.format(token=bot_token)
    payload = json.dumps({
        "chat_id": chat_id,
        "text": filtered,
        "parse_mode": "HTML",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
            return response.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
        return False


def main() -> None:
    """
    Stop hook entry point.

    Reads session data from stdin, loads config, and sends a session-end
    notification to Telegram. Always exits 0 (never blocks shutdown).
    """
    try:
        # Read input
        try:
            input_data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            input_data = {}

        # Load config
        env = _parse_env()
        bot_token = env.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = env.get("TELEGRAM_CHAT_ID", "")

        if not bot_token or not chat_id:
            # Not configured — silent exit
            sys.exit(0)

        # Build and send notification
        message = _build_session_summary(input_data)
        send_notification(bot_token, chat_id, message)

    except Exception:
        # Never block shutdown
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
