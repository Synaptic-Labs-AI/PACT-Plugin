"""
Tests for pact-plugin/telegram/notify.py

Tests cover:
1. _filter_message: inline credential redaction (mirrors content_filter.py)
2. _parse_env: stdlib .env parser
3. _build_session_summary: message formatting from Stop hook input
4. send_notification: HTTP POST to Telegram with content filter, truncation, timeouts
5. main: full Stop hook entry point behavior
6. Security: content filter applied, 5-second timeout, always exits 0
"""

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram.notify import (
    _filter_message,
    _get_project_name,
    _parse_env,
    _build_session_summary,
    send_notification,
    main,
    ENV_FILE,
    HTTP_TIMEOUT,
)


# =============================================================================
# _filter_message Tests
# =============================================================================

class TestFilterMessage:
    """Tests for _filter_message -- inline credential redaction."""

    def test_safe_text_unchanged(self):
        """Should not modify safe text."""
        assert _filter_message("Session ended normally.") == "Session ended normally."

    def test_redacts_openai_key(self):
        """Should redact OpenAI API keys."""
        text = "Using sk-abcdefghijklmnopqrstuvwxyz"
        result = _filter_message(text)
        assert "sk-" not in result
        assert "[REDACTED]" in result

    def test_redacts_telegram_bot_token(self):
        """Should redact Telegram bot tokens."""
        text = "Token: 123456789:ABCdefGHI-JKLmnoPQRstuVWXyz123456"
        result = _filter_message(text)
        assert "123456789:" not in result

    def test_redacts_aws_key(self):
        """Should redact AWS access keys."""
        text = "AWS: AKIAIOSFODNN7EXAMPLE"
        result = _filter_message(text)
        assert "AKIA" not in result

    def test_redacts_github_token(self):
        """Should redact GitHub tokens."""
        text = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
        result = _filter_message(text)
        assert "ghp_" not in result

    def test_redacts_jwt(self):
        """Should redact JWT tokens."""
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123def456"
        result = _filter_message(jwt)
        assert "eyJ" not in result

    def test_redacts_password_pattern(self):
        """Should redact password=value patterns."""
        text = "password=mysecretpassword123"
        result = _filter_message(text)
        assert "mysecretpassword" not in result


# =============================================================================
# _parse_env Tests
# =============================================================================

class TestParseEnv:
    """Tests for _parse_env -- stdlib .env parser."""

    def test_parses_basic_env(self, tmp_path):
        """Should parse KEY=VALUE pairs."""
        env_file = tmp_path / ".env"
        env_file.write_text("BOT_TOKEN=abc\nCHAT_ID=123\n")

        with patch("telegram.notify.ENV_FILE", env_file):
            result = _parse_env()

        assert result == {"BOT_TOKEN": "abc", "CHAT_ID": "123"}

    def test_returns_empty_for_nonexistent_file(self, tmp_path):
        """Should return empty dict when file doesn't exist."""
        with patch("telegram.notify.ENV_FILE", tmp_path / "nonexistent"):
            result = _parse_env()

        assert result == {}

    def test_skips_comments_and_blank_lines(self, tmp_path):
        """Should skip comments and blank lines."""
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nKEY=value\n")

        with patch("telegram.notify.ENV_FILE", env_file):
            result = _parse_env()

        assert result == {"KEY": "value"}

    def test_strips_quotes(self, tmp_path):
        """Should strip surrounding quotes from values."""
        env_file = tmp_path / ".env"
        env_file.write_text('KEY="quoted value"\n')

        with patch("telegram.notify.ENV_FILE", env_file):
            result = _parse_env()

        assert result["KEY"] == "quoted value"

    def test_handles_read_error(self, tmp_path):
        """Should return empty dict on file read errors."""
        env_file = tmp_path / ".env"
        env_file.write_text("data")
        env_file.chmod(0o000)

        try:
            with patch("telegram.notify.ENV_FILE", env_file):
                result = _parse_env()
            assert result == {}
        finally:
            env_file.chmod(0o644)


# =============================================================================
# _build_session_summary Tests
# =============================================================================

class TestGetProjectNameNotify:
    """Tests for _get_project_name in notify.py (stdlib-only version)."""

    def test_returns_basename_from_env(self):
        """Should return basename of CLAUDE_PROJECT_DIR."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/home/user/my-project"}):
            assert _get_project_name() == "my-project"

    def test_returns_unknown_when_not_set(self):
        """Should return 'unknown' when env var is missing."""
        with patch.dict("os.environ", {}, clear=True):
            assert _get_project_name() == "unknown"


class TestBuildSessionSummary:
    """Tests for _build_session_summary -- notification message formatting."""

    def test_includes_session_id(self):
        """Should include truncated session ID."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/path/to/Proj"}):
            result = _build_session_summary({"session_id": "abcdef1234567890"})
        assert "abcdef12" in result
        assert "<b>Session ended</b>" in result

    def test_includes_project_name_prefix(self):
        """Should include bold project name prefix."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/path/to/MyApp"}):
            result = _build_session_summary({"session_id": "test"})
        assert result.startswith("<b>[MyApp]</b>\n")

    def test_handles_missing_session_id(self):
        """Should use 'unknown' when session_id is missing."""
        result = _build_session_summary({})
        assert "unknown" in result

    def test_includes_last_activity(self):
        """Should include the last line of the transcript."""
        result = _build_session_summary({
            "session_id": "test",
            "transcript": "Line 1\nLine 2\nLast line here",
        })
        assert "Last activity: Last line here" in result

    def test_truncates_long_last_activity(self):
        """Should cap last activity at 200 chars."""
        long_line = "x" * 300
        result = _build_session_summary({
            "session_id": "test",
            "transcript": long_line,
        })
        # Find the activity part and check length
        assert len(result) < 350  # prefix + truncated content

    def test_handles_empty_transcript(self):
        """Should handle empty transcript gracefully."""
        result = _build_session_summary({
            "session_id": "test",
            "transcript": "",
        })
        assert "Session ended" in result
        assert "Last activity" not in result

    def test_handles_transcript_with_only_blank_lines(self):
        """Should handle transcript with only whitespace lines."""
        result = _build_session_summary({
            "session_id": "test",
            "transcript": "\n\n  \n\n",
        })
        assert "Session ended" in result


# =============================================================================
# send_notification Tests
# =============================================================================

class TestSendNotification:
    """Tests for send_notification -- HTTP POST to Telegram."""

    def test_applies_content_filter(self):
        """Should filter message content before sending."""
        with patch("telegram.notify.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            send_notification("token", "123", "Key: sk-abcdefghijklmnopqrstuvwxyz")

            # Extract the payload that was sent
            call_args = mock_urlopen.call_args
            req = call_args[0][0]
            payload = json.loads(req.data.decode())
            assert "sk-" not in payload["text"]

    def test_truncates_long_messages(self):
        """Should truncate messages exceeding 4096 chars."""
        with patch("telegram.notify.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            long_msg = "x" * 5000
            send_notification("token", "123", long_msg)

            call_args = mock_urlopen.call_args
            req = call_args[0][0]
            payload = json.loads(req.data.decode())
            assert len(payload["text"]) <= 4096

    def test_returns_true_on_success(self):
        """Should return True on successful send."""
        with patch("telegram.notify.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = send_notification("token", "123", "test")

        assert result is True

    def test_returns_false_on_http_error(self):
        """Should return False on HTTP errors (no crash)."""
        with patch("telegram.notify.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                "url", 400, "Bad Request", {}, None
            )

            result = send_notification("token", "123", "test")

        assert result is False

    def test_returns_false_on_timeout(self):
        """Should return False on timeout (no crash)."""
        with patch("telegram.notify.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError()

            result = send_notification("token", "123", "test")

        assert result is False

    def test_returns_false_on_network_error(self):
        """Should return False on URL/network errors."""
        with patch("telegram.notify.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            result = send_notification("token", "123", "test")

        assert result is False

    def test_uses_html_parse_mode(self):
        """Should send with HTML parse_mode."""
        with patch("telegram.notify.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            send_notification("token", "123", "test")

            call_args = mock_urlopen.call_args
            req = call_args[0][0]
            payload = json.loads(req.data.decode())
            assert payload["parse_mode"] == "HTML"


# =============================================================================
# main Tests
# =============================================================================

class TestMain:
    """Tests for main -- Stop hook entry point."""

    def test_exits_zero_when_not_configured(self, tmp_path):
        """Should exit 0 when config is missing (never block shutdown)."""
        with patch("telegram.notify.ENV_FILE", tmp_path / "nonexistent"):
            with patch("sys.stdin", io.StringIO(json.dumps({}))):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0

    def test_exits_zero_on_exception(self):
        """Should always exit 0 even on unexpected exceptions."""
        with patch("sys.stdin", side_effect=Exception("unexpected")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_sends_notification_when_configured(self, tmp_path):
        """Should send notification when config is valid."""
        env_file = tmp_path / ".env"
        env_file.write_text("TELEGRAM_BOT_TOKEN=test_token\nTELEGRAM_CHAT_ID=12345\n")

        input_data = {"session_id": "test-session", "transcript": "last line"}

        with patch("telegram.notify.ENV_FILE", env_file):
            with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
                with patch("telegram.notify.send_notification", return_value=True) as mock_send:
                    with pytest.raises(SystemExit) as exc_info:
                        main()

        assert exc_info.value.code == 0
        mock_send.assert_called_once()
        # Verify it was called with the right token and chat_id
        args = mock_send.call_args[0]
        assert args[0] == "test_token"
        assert args[1] == "12345"

    def test_handles_malformed_json_input(self, tmp_path):
        """Should handle malformed JSON from stdin without crashing."""
        with patch("telegram.notify.ENV_FILE", tmp_path / "nonexistent"):
            with patch("sys.stdin", io.StringIO("not json")):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
