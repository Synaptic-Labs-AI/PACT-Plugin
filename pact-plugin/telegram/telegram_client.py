"""
Location: pact-plugin/telegram/telegram_client.py
Summary: Async Telegram Bot API wrapper using httpx.
Used by: tools.py (MCP tool handlers), server.py (background polling loop).

Wraps the subset of Telegram Bot API endpoints needed by pact-telegram:
- sendMessage (with ReplyParameters and InlineKeyboardMarkup)
- getUpdates (long polling for replies)
- getFile (for voice note download)
- answerCallbackQuery (for inline keyboard button acknowledgement)

Design decisions:
- Uses httpx.AsyncClient for async-native HTTP (consistent with MCP server)
- Content filter applied to every outbound message at the transport layer
- Chat_id validation on every inbound message (security must-have)
- Uses modern ReplyParameters instead of deprecated reply_to_message_id
- Rate limit handling: respects 429 Retry-After header
- HTML parse_mode used by default for rich text formatting
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from telegram.content_filter import filter_and_truncate, sanitize_inbound

logger = logging.getLogger("pact-telegram.client")

# Telegram Bot API base URL
API_BASE = "https://api.telegram.org/bot{token}"

# Default long-polling timeout (seconds)
POLL_TIMEOUT = 30

# Maximum retries for transient errors
MAX_RETRIES = 3


class TelegramAPIError(Exception):
    """Raised when a Telegram Bot API call fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TelegramClient:
    """
    Async wrapper for the Telegram Bot API.

    Provides methods for sending messages, polling for updates, and
    downloading files. Applies content filtering on all outbound messages
    and chat_id validation on all inbound messages.

    Args:
        bot_token: Telegram bot token from BotFather.
        chat_id: Authorized chat ID. Messages from other chats are silently ignored.
        http_client: Optional shared httpx.AsyncClient. If None, creates one internally.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._base_url = API_BASE.format(token=bot_token)
        self._client = http_client
        self._owns_client = http_client is None
        self._update_offset: int = 0

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or lazily create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=POLL_TIMEOUT + 10)
            self._owns_client = True
        return self._client

    async def _api_call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        retries: int = MAX_RETRIES,
    ) -> dict[str, Any]:
        """
        Make a Telegram Bot API call with retry and rate-limit handling.

        Args:
            method: API method name (e.g., "sendMessage").
            params: JSON-serializable request parameters.
            retries: Number of retries for transient errors.

        Returns:
            The "result" field from the API response.

        Raises:
            TelegramAPIError: On API errors after exhausting retries.
        """
        client = await self._get_client()
        url = f"{self._base_url}/{method}"

        for attempt in range(retries + 1):
            try:
                response = await client.post(url, json=params or {})

                # Handle rate limiting (429)
                if response.status_code == 429:
                    data = response.json()
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    if attempt < retries:
                        logger.warning(
                            "Rate limited, retrying after %ds", retry_after
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    raise TelegramAPIError(
                        f"Rate limited after {retries} retries",
                        status_code=429,
                    )

                response.raise_for_status()
                data = response.json()

                if not data.get("ok"):
                    description = data.get("description", "Unknown error")
                    error_code = data.get("error_code", 0)
                    raise TelegramAPIError(description, status_code=error_code)

                return data.get("result", {})

            except httpx.HTTPStatusError as e:
                if attempt < retries and e.response.status_code >= 500:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise TelegramAPIError(
                    f"HTTP {e.response.status_code}",
                    status_code=e.response.status_code,
                ) from e
            except httpx.RequestError as e:
                if attempt < retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise TelegramAPIError(
                    f"Request failed: {type(e).__name__}"
                ) from e

        raise TelegramAPIError("Exhausted retries")

    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send a message to the authorized chat.

        Content filter is applied to the text before sending. The message
        is also truncated to Telegram's 4096-character limit.

        Args:
            text: Message text (HTML formatting supported).
            parse_mode: Formatting mode ("HTML", "Markdown", or "plain").
            reply_to_message_id: Message ID to reply to (uses ReplyParameters).
            reply_markup: Inline keyboard markup or other reply markup.

        Returns:
            The sent Message object from Telegram API.

        Raises:
            TelegramAPIError: On API errors.
        """
        # Security: filter outbound content
        filtered_text = filter_and_truncate(text)

        params: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": filtered_text,
        }

        if parse_mode and parse_mode.lower() != "plain":
            params["parse_mode"] = parse_mode

        # Use modern ReplyParameters instead of deprecated reply_to_message_id
        if reply_to_message_id is not None:
            params["reply_parameters"] = {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": True,
            }

        if reply_markup is not None:
            params["reply_markup"] = reply_markup

        result = await self._api_call("sendMessage", params)
        logger.info(
            "Message sent",
            extra={"message_id": result.get("message_id"), "chat_id": self._chat_id},
        )
        return result

    async def send_message_with_buttons(
        self,
        text: str,
        buttons: list[str],
        parse_mode: str = "HTML",
    ) -> dict[str, Any]:
        """
        Send a message with inline keyboard buttons.

        Creates a single row of buttons from the provided labels. Each button's
        callback_data is the button label text.

        Args:
            text: Message text (HTML formatting supported).
            buttons: List of button labels for the inline keyboard.
            parse_mode: Formatting mode.

        Returns:
            The sent Message object from Telegram API.
        """
        inline_keyboard = [
            [{"text": label, "callback_data": label} for label in buttons]
        ]
        reply_markup = {"inline_keyboard": inline_keyboard}

        return await self.send_message(
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
    ) -> bool:
        """
        Acknowledge an inline keyboard button press.

        Args:
            callback_query_id: ID from the callback_query update.
            text: Optional notification text shown to the user.

        Returns:
            True if successful.
        """
        params: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text

        try:
            await self._api_call("answerCallbackQuery", params)
            return True
        except TelegramAPIError:
            return False

    async def get_updates(
        self,
        timeout: int = POLL_TIMEOUT,
    ) -> list[dict[str, Any]]:
        """
        Long-poll for new updates from Telegram.

        Filters updates to only include messages from the authorized chat_id.
        This is a security control: messages from unauthorized chats are
        silently discarded.

        Args:
            timeout: Long-polling timeout in seconds.

        Returns:
            List of update objects from the authorized chat only.
        """
        params = {
            "offset": self._update_offset,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }

        try:
            # Use a longer HTTP timeout than the poll timeout
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}/getUpdates",
                json=params,
                timeout=timeout + 10,
            )

            # Handle rate limiting (429) — back off and return empty
            if response.status_code == 429:
                try:
                    retry_data = response.json()
                    retry_after = retry_data.get("parameters", {}).get("retry_after", 5)
                except Exception:
                    retry_after = 5
                logger.warning("getUpdates rate limited, backing off %ds", retry_after)
                await asyncio.sleep(retry_after)
                return []

            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                return []

            updates = data.get("result", [])
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.debug("getUpdates error: %s", type(e).__name__)
            return []

        if not updates:
            return []

        # Advance offset past the last update
        self._update_offset = updates[-1]["update_id"] + 1

        # Security: filter to authorized chat_id only
        authorized: list[dict[str, Any]] = []
        for update in updates:
            chat_id = self._extract_chat_id(update)
            if chat_id is not None and str(chat_id) == self._chat_id:
                authorized.append(update)
            elif chat_id is not None:
                logger.debug("Rejected update from unauthorized chat")

        return authorized

    async def get_file(self, file_id: str) -> dict[str, Any]:
        """
        Get file metadata from Telegram (for voice note downloads).

        Args:
            file_id: Telegram file_id from a message.

        Returns:
            File object with file_path for download.

        Raises:
            TelegramAPIError: On API errors.
        """
        return await self._api_call("getFile", {"file_id": file_id})

    @staticmethod
    def _extract_chat_id(update: dict[str, Any]) -> int | None:
        """
        Extract chat_id from various update types.

        Handles both regular messages and callback queries.

        Args:
            update: A Telegram update object.

        Returns:
            The chat_id as integer, or None if not extractable.
        """
        # Regular message
        message = update.get("message")
        if message:
            chat = message.get("chat", {})
            return chat.get("id")

        # Callback query (inline keyboard button press)
        callback_query = update.get("callback_query")
        if callback_query:
            message = callback_query.get("message", {})
            chat = message.get("chat", {})
            return chat.get("id")

        return None

    @staticmethod
    def extract_text(update: dict[str, Any]) -> str | None:
        """
        Extract and sanitize text content from an update.

        Handles text messages, voice note indicators, and callback query data.
        Applies inbound sanitization (control char stripping, length limit).

        Args:
            update: A Telegram update object.

        Returns:
            Sanitized text content, or None if no text found.
        """
        # Callback query (button press)
        callback_query = update.get("callback_query")
        if callback_query:
            data = callback_query.get("data", "")
            return sanitize_inbound(data) if data else None

        # Regular message
        message = update.get("message", {})

        # Text message
        text = message.get("text")
        if text:
            return sanitize_inbound(text)

        # Voice note (return marker; caller handles transcription)
        if message.get("voice"):
            return None  # Caller should check for voice separately

        return None

    @staticmethod
    def extract_voice(update: dict[str, Any]) -> dict[str, Any] | None:
        """
        Extract voice note metadata from an update.

        Args:
            update: A Telegram update object.

        Returns:
            Voice object with file_id and duration, or None if not a voice message.
        """
        message = update.get("message", {})
        return message.get("voice")

    @staticmethod
    def extract_callback_query_id(update: dict[str, Any]) -> str | None:
        """
        Extract callback_query_id for answering inline keyboard presses.

        Args:
            update: A Telegram update object.

        Returns:
            The callback_query ID string, or None.
        """
        callback_query = update.get("callback_query")
        if callback_query:
            return callback_query.get("id")
        return None

    @staticmethod
    def extract_reply_to_message_id(update: dict[str, Any]) -> int | None:
        """
        Extract the message_id that this message is replying to.

        Used for session routing: matching replies to the original question.

        Args:
            update: A Telegram update object.

        Returns:
            The replied-to message_id, or None.
        """
        # Callback query: the message field IS the original message
        callback_query = update.get("callback_query")
        if callback_query:
            message = callback_query.get("message", {})
            return message.get("message_id")

        # Regular message reply
        message = update.get("message", {})
        reply = message.get("reply_to_message")
        if reply:
            return reply.get("message_id")

        return None

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
