"""
Tests for pact-plugin/telegram/telegram_client.py

Tests cover:
1. TelegramClient construction and HTTP client lifecycle
2. send_message: content filtering, parse_mode, ReplyParameters, reply_markup
3. send_message_with_buttons: inline keyboard construction
4. answer_callback_query: acknowledgement and error handling
5. get_updates: long-polling, chat_id filtering (security), offset advancement
6. get_file: file metadata retrieval
7. Static extractors: _extract_chat_id, extract_text, extract_voice,
   extract_callback_query_id, extract_reply_to_message_id
8. _api_call: retry logic, rate limiting (429), error handling
9. Security: unauthorized chat_id rejection, inbound sanitization
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram.telegram_client import (
    TelegramClient,
    TelegramAPIError,
    API_BASE,
    POLL_TIMEOUT,
    MAX_RETRIES,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_http_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
def telegram_client(mock_http_client):
    """Create a TelegramClient with a mock HTTP client."""
    return TelegramClient(
        bot_token="123456789:TESTTOKEN",
        chat_id="999888777",
        http_client=mock_http_client,
    )


def make_ok_response(result=None, status_code=200):
    """Create a mock successful Telegram API response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {"ok": True, "result": result or {}}
    mock_response.raise_for_status = MagicMock()
    return mock_response


def make_error_response(description="Error", error_code=400, status_code=400):
    """Create a mock error Telegram API response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {
        "ok": False,
        "description": description,
        "error_code": error_code,
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


def make_rate_limit_response(retry_after=5):
    """Create a mock 429 rate-limit response."""
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.json.return_value = {
        "ok": False,
        "description": "Too Many Requests",
        "parameters": {"retry_after": retry_after},
    }
    return mock_response


# =============================================================================
# Constructor and Client Lifecycle Tests
# =============================================================================

class TestClientConstruction:
    """Tests for TelegramClient constructor and HTTP client management."""

    def test_stores_bot_token_and_chat_id(self):
        """Should store bot_token and chat_id."""
        client = TelegramClient(bot_token="token", chat_id="123")
        assert client._chat_id == "123"

    def test_builds_base_url_from_token(self):
        """Should construct base URL using the bot token."""
        client = TelegramClient(bot_token="123:ABC", chat_id="456")
        assert client._base_url == "https://api.telegram.org/bot123:ABC"

    def test_uses_provided_http_client(self, mock_http_client):
        """Should use externally provided HTTP client."""
        client = TelegramClient(
            bot_token="token", chat_id="123", http_client=mock_http_client
        )
        assert client._client is mock_http_client
        assert client._owns_client is False

    def test_creates_client_lazily_when_none_provided(self):
        """Should mark _owns_client=True when no client provided."""
        client = TelegramClient(bot_token="token", chat_id="123")
        assert client._client is None
        assert client._owns_client is True

    @pytest.mark.asyncio
    async def test_close_closes_owned_client(self, mock_http_client):
        """Should close HTTP client when we own it."""
        client = TelegramClient(
            bot_token="token", chat_id="123", http_client=mock_http_client
        )
        client._owns_client = True
        await client.close()
        mock_http_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_does_not_close_external_client(self, mock_http_client):
        """Should not close HTTP client when externally provided."""
        client = TelegramClient(
            bot_token="token", chat_id="123", http_client=mock_http_client
        )
        # _owns_client is False when http_client is provided
        await client.close()
        mock_http_client.aclose.assert_not_awaited()


# =============================================================================
# _api_call Tests
# =============================================================================

class TestApiCall:
    """Tests for _api_call -- retry logic and error handling."""

    @pytest.mark.asyncio
    async def test_successful_api_call(self, telegram_client, mock_http_client):
        """Should return result from successful API response."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        result = await telegram_client._api_call("sendMessage", {"text": "hi"})

        assert result == {"message_id": 42}

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, telegram_client, mock_http_client):
        """Should raise TelegramAPIError on non-ok API response."""
        mock_http_client.post.return_value = make_error_response("Bad Request", 400)

        with pytest.raises(TelegramAPIError, match="Bad Request"):
            await telegram_client._api_call("sendMessage", {"text": "hi"})

    @pytest.mark.asyncio
    async def test_rate_limit_retry(self, telegram_client, mock_http_client):
        """Should retry on 429 and succeed after rate limit clears."""
        mock_http_client.post.side_effect = [
            make_rate_limit_response(retry_after=0),
            make_ok_response({"message_id": 1}),
        ]

        with patch("telegram.telegram_client.asyncio.sleep", new_callable=AsyncMock):
            result = await telegram_client._api_call("sendMessage", retries=1)

        assert result == {"message_id": 1}

    @pytest.mark.asyncio
    async def test_rate_limit_exhausted_raises(self, telegram_client, mock_http_client):
        """Should raise after exhausting retries on rate limit."""
        mock_http_client.post.return_value = make_rate_limit_response(retry_after=0)

        with patch("telegram.telegram_client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(TelegramAPIError, match="Rate limited"):
                await telegram_client._api_call("sendMessage", retries=1)

    @pytest.mark.asyncio
    async def test_retries_on_server_error(self, telegram_client, mock_http_client):
        """Should retry on 5xx server errors."""
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=error_response
        )

        ok_response = make_ok_response({"message_id": 1})

        mock_http_client.post.side_effect = [error_response, ok_response]

        with patch("telegram.telegram_client.asyncio.sleep", new_callable=AsyncMock):
            result = await telegram_client._api_call("sendMessage", retries=1)

        assert result == {"message_id": 1}

    @pytest.mark.asyncio
    async def test_retries_on_request_error(self, telegram_client, mock_http_client):
        """Should retry on network-level request errors."""
        mock_http_client.post.side_effect = [
            httpx.ConnectError("Connection refused"),
            make_ok_response({"message_id": 1}),
        ]

        with patch("telegram.telegram_client.asyncio.sleep", new_callable=AsyncMock):
            result = await telegram_client._api_call("sendMessage", retries=1)

        assert result == {"message_id": 1}


# =============================================================================
# send_message Tests
# =============================================================================

class TestSendMessage:
    """Tests for send_message -- outbound message sending."""

    @pytest.mark.asyncio
    async def test_sends_message_to_authorized_chat(self, telegram_client, mock_http_client):
        """Should send message to the configured chat_id."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        await telegram_client.send_message("Hello")

        call_args = mock_http_client.post.call_args
        params = call_args.kwargs.get("json") or call_args[1].get("json")
        assert params["chat_id"] == "999888777"
        assert params["text"] == "Hello"

    @pytest.mark.asyncio
    async def test_applies_content_filter(self, telegram_client, mock_http_client):
        """Should filter outbound messages for credentials."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        await telegram_client.send_message("Key: sk-abcdefghijklmnopqrstuvwxyz")

        call_args = mock_http_client.post.call_args
        params = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "sk-" not in params["text"]

    @pytest.mark.asyncio
    async def test_default_html_parse_mode(self, telegram_client, mock_http_client):
        """Should use HTML parse_mode by default."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        await telegram_client.send_message("Hello")

        call_args = mock_http_client.post.call_args
        params = call_args.kwargs.get("json") or call_args[1].get("json")
        assert params["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_plain_parse_mode_omits_param(self, telegram_client, mock_http_client):
        """Should omit parse_mode when set to 'plain'."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        await telegram_client.send_message("Hello", parse_mode="plain")

        call_args = mock_http_client.post.call_args
        params = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "parse_mode" not in params

    @pytest.mark.asyncio
    async def test_reply_parameters_when_reply_to_set(self, telegram_client, mock_http_client):
        """Should use modern ReplyParameters when reply_to_message_id set."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        await telegram_client.send_message("Reply", reply_to_message_id=10)

        call_args = mock_http_client.post.call_args
        params = call_args.kwargs.get("json") or call_args[1].get("json")
        assert params["reply_parameters"] == {
            "message_id": 10,
            "allow_sending_without_reply": True,
        }

    @pytest.mark.asyncio
    async def test_includes_reply_markup(self, telegram_client, mock_http_client):
        """Should include reply_markup when provided."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})
        markup = {"inline_keyboard": [[{"text": "Yes", "callback_data": "yes"}]]}

        await telegram_client.send_message("Choose:", reply_markup=markup)

        call_args = mock_http_client.post.call_args
        params = call_args.kwargs.get("json") or call_args[1].get("json")
        assert params["reply_markup"] == markup


# =============================================================================
# send_message_with_buttons Tests
# =============================================================================

class TestSendMessageWithButtons:
    """Tests for send_message_with_buttons -- inline keyboard construction."""

    @pytest.mark.asyncio
    async def test_creates_inline_keyboard(self, telegram_client, mock_http_client):
        """Should create inline keyboard from button labels."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        await telegram_client.send_message_with_buttons(
            "Choose:", buttons=["Yes", "No"]
        )

        call_args = mock_http_client.post.call_args
        params = call_args.kwargs.get("json") or call_args[1].get("json")
        keyboard = params["reply_markup"]["inline_keyboard"]
        assert len(keyboard) == 1  # single row
        assert len(keyboard[0]) == 2  # two buttons
        assert keyboard[0][0] == {"text": "Yes", "callback_data": "Yes"}
        assert keyboard[0][1] == {"text": "No", "callback_data": "No"}


# =============================================================================
# answer_callback_query Tests
# =============================================================================

class TestAnswerCallbackQuery:
    """Tests for answer_callback_query -- inline keyboard button acknowledgement."""

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, telegram_client, mock_http_client):
        """Should return True when API call succeeds."""
        mock_http_client.post.return_value = make_ok_response(True)

        result = await telegram_client.answer_callback_query("query_123")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self, telegram_client, mock_http_client):
        """Should return False when API call fails."""
        mock_http_client.post.return_value = make_error_response("Query expired", 400)

        result = await telegram_client.answer_callback_query("query_123")

        assert result is False


# =============================================================================
# get_updates Tests
# =============================================================================

class TestGetUpdates:
    """Tests for get_updates -- long-polling with chat_id security filtering."""

    @pytest.mark.asyncio
    async def test_returns_authorized_updates_only(self, telegram_client, mock_http_client):
        """Should filter updates to authorized chat_id only (security control)."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "message": {"chat": {"id": 999888777}, "text": "hello"},
                },
                {
                    "update_id": 2,
                    "message": {"chat": {"id": 111222333}, "text": "intruder"},
                },
            ],
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        updates = await telegram_client.get_updates(timeout=1)

        assert len(updates) == 1
        assert updates[0]["update_id"] == 1

    @pytest.mark.asyncio
    async def test_rejects_all_unauthorized_updates(self, telegram_client, mock_http_client):
        """Should return empty when all updates are from unauthorized chats."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "message": {"chat": {"id": 111111}, "text": "bad"},
                },
            ],
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        updates = await telegram_client.get_updates(timeout=1)

        assert updates == []

    @pytest.mark.asyncio
    async def test_advances_offset_after_updates(self, telegram_client, mock_http_client):
        """Should advance _update_offset past the last received update_id."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "ok": True,
            "result": [
                {"update_id": 100, "message": {"chat": {"id": 999888777}, "text": "a"}},
                {"update_id": 101, "message": {"chat": {"id": 999888777}, "text": "b"}},
            ],
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        await telegram_client.get_updates(timeout=1)

        assert telegram_client._update_offset == 102

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_error(self, telegram_client, mock_http_client):
        """Should return empty list on network errors (no crash)."""
        mock_http_client.post.side_effect = httpx.ConnectError("timeout")

        updates = await telegram_client.get_updates(timeout=1)

        assert updates == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_api_not_ok(self, telegram_client, mock_http_client):
        """Should return empty list when API returns ok=false."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": False}
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        updates = await telegram_client.get_updates(timeout=1)

        assert updates == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_rate_limit(self, telegram_client, mock_http_client):
        """Should return empty and back off when getUpdates gets 429."""
        response = MagicMock()
        response.status_code = 429
        response.json.return_value = {
            "ok": False,
            "parameters": {"retry_after": 1},
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        with patch("telegram.telegram_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            updates = await telegram_client.get_updates(timeout=1)

        assert updates == []
        mock_sleep.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_filters_callback_query_by_chat_id(self, telegram_client, mock_http_client):
        """Should filter callback queries by chat_id too (security)."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "callback_query": {
                        "id": "cq1",
                        "message": {"chat": {"id": 999888777}, "message_id": 10},
                        "data": "Yes",
                    },
                },
                {
                    "update_id": 2,
                    "callback_query": {
                        "id": "cq2",
                        "message": {"chat": {"id": 666}, "message_id": 20},
                        "data": "No",
                    },
                },
            ],
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        updates = await telegram_client.get_updates(timeout=1)

        assert len(updates) == 1
        assert updates[0]["callback_query"]["data"] == "Yes"


# =============================================================================
# get_file Tests
# =============================================================================

class TestGetFile:
    """Tests for get_file -- file metadata retrieval for voice note downloads."""

    @pytest.mark.asyncio
    async def test_returns_file_metadata(self, telegram_client, mock_http_client):
        """Should return file metadata from Telegram API."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "ok": True,
            "result": {
                "file_id": "voice_abc",
                "file_unique_id": "unique123",
                "file_size": 12345,
                "file_path": "voice/file_0.oga",
            },
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        result = await telegram_client.get_file("voice_abc")

        assert result["file_path"] == "voice/file_0.oga"
        assert result["file_size"] == 12345
        mock_http_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, telegram_client, mock_http_client):
        """Should raise TelegramAPIError on API failure."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: invalid file_id",
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        with pytest.raises(TelegramAPIError, match="invalid file_id"):
            await telegram_client.get_file("bad_file_id")


# =============================================================================
# Static Extractor Tests
# =============================================================================

class TestExtractChatId:
    """Tests for _extract_chat_id -- extracting chat_id from different update types."""

    def test_extracts_from_regular_message(self):
        """Should extract chat_id from a regular message update."""
        update = {"message": {"chat": {"id": 12345}, "text": "hello"}}
        assert TelegramClient._extract_chat_id(update) == 12345

    def test_extracts_from_callback_query(self):
        """Should extract chat_id from callback_query."""
        update = {
            "callback_query": {
                "id": "cq1",
                "message": {"chat": {"id": 67890}},
                "data": "Yes",
            }
        }
        assert TelegramClient._extract_chat_id(update) == 67890

    def test_returns_none_for_unknown_update_type(self):
        """Should return None when update has no message or callback_query."""
        update = {"channel_post": {"chat": {"id": 111}}}
        assert TelegramClient._extract_chat_id(update) is None


class TestExtractText:
    """Tests for extract_text -- extracting and sanitizing text from updates."""

    def test_extracts_text_message(self):
        """Should extract text from a regular text message."""
        update = {"message": {"text": "Hello"}}
        assert TelegramClient.extract_text(update) == "Hello"

    def test_sanitizes_inbound_text(self):
        """Should strip control characters from inbound text."""
        update = {"message": {"text": "Hello\x00World"}}
        assert TelegramClient.extract_text(update) == "HelloWorld"

    def test_extracts_callback_query_data(self):
        """Should extract data from callback_query."""
        update = {"callback_query": {"id": "cq1", "data": "Yes"}}
        assert TelegramClient.extract_text(update) == "Yes"

    def test_returns_none_for_voice_message(self):
        """Should return None for voice messages (caller handles separately)."""
        update = {"message": {"voice": {"file_id": "abc", "duration": 5}}}
        assert TelegramClient.extract_text(update) is None

    def test_returns_none_for_empty_update(self):
        """Should return None when no text content is found."""
        update = {"message": {}}
        assert TelegramClient.extract_text(update) is None

    def test_returns_none_for_empty_callback_data(self):
        """Should return None when callback data is empty."""
        update = {"callback_query": {"id": "cq1", "data": ""}}
        assert TelegramClient.extract_text(update) is None


class TestExtractVoice:
    """Tests for extract_voice -- extracting voice note metadata."""

    def test_extracts_voice_metadata(self):
        """Should extract voice object from voice message."""
        update = {"message": {"voice": {"file_id": "abc123", "duration": 10}}}
        voice = TelegramClient.extract_voice(update)
        assert voice == {"file_id": "abc123", "duration": 10}

    def test_returns_none_for_non_voice(self):
        """Should return None for non-voice messages."""
        update = {"message": {"text": "hello"}}
        assert TelegramClient.extract_voice(update) is None


class TestExtractCallbackQueryId:
    """Tests for extract_callback_query_id."""

    def test_extracts_callback_query_id(self):
        """Should extract callback_query_id."""
        update = {"callback_query": {"id": "cq_12345", "data": "Yes"}}
        assert TelegramClient.extract_callback_query_id(update) == "cq_12345"

    def test_returns_none_for_regular_message(self):
        """Should return None for non-callback updates."""
        update = {"message": {"text": "hello"}}
        assert TelegramClient.extract_callback_query_id(update) is None


class TestExtractReplyToMessageId:
    """Tests for extract_reply_to_message_id -- reply routing."""

    def test_extracts_reply_to_from_regular_message(self):
        """Should extract reply_to_message_id from message reply."""
        update = {
            "message": {
                "text": "reply",
                "reply_to_message": {"message_id": 42},
            }
        }
        assert TelegramClient.extract_reply_to_message_id(update) == 42

    def test_extracts_message_id_from_callback_query(self):
        """Should extract original message_id from callback query."""
        update = {
            "callback_query": {
                "id": "cq1",
                "message": {"message_id": 99, "chat": {"id": 123}},
                "data": "Yes",
            }
        }
        assert TelegramClient.extract_reply_to_message_id(update) == 99

    def test_returns_none_for_non_reply(self):
        """Should return None when message is not a reply."""
        update = {"message": {"text": "standalone message"}}
        assert TelegramClient.extract_reply_to_message_id(update) is None
