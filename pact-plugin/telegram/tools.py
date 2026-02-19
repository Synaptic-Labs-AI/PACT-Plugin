"""
Location: pact-plugin/telegram/tools.py
Summary: MCP tool definitions for the pact-telegram bridge.
Used by: server.py (registers these tools with the FastMCP server).

Defines three MCP tools:
- telegram_notify: One-way notification to the user's Telegram
- telegram_ask: Blocking question that waits for user reply via Telegram
- telegram_status: Bridge status and health check

The telegram_ask tool uses an asyncio.Future pattern:
1. Send question with optional InlineKeyboardMarkup buttons
2. Register {message_id: Future} in the pending replies dict
3. Background polling loop (in server.py) resolves the Future when a
   reply matching that message_id arrives
4. Tool returns the user's reply text (or timeout error)

Voice note replies: When the user replies with a voice note, the polling
loop transcribes it via voice.py and resolves the Future with the
transcribed text.

Interface contracts follow the plan's MCP Tool Schemas exactly.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import time
from typing import Any

from telegram.config import load_config_safe
from telegram.telegram_client import TelegramClient, TelegramAPIError
from telegram.voice import VoiceTranscriber, VoiceTranscriptionError

logger = logging.getLogger("pact-telegram.tools")

# Default timeout for telegram_ask (seconds)
DEFAULT_ASK_TIMEOUT = 300

# Maximum number of inline keyboard buttons
MAX_BUTTONS = 10

# Rate limiting: max notifications per minute
NOTIFY_RATE_LIMIT = 20
NOTIFY_RATE_WINDOW = 60  # seconds

# Maximum concurrent pending telegram_ask questions
MAX_PENDING_REPLIES = 10

# Buffer beyond timeout before considering a pending Future stale (seconds)
STALE_FUTURE_BUFFER = 60

# Reply hint appended to telegram_ask messages
ASK_REPLY_HINT = "\n\n↩️ Reply to this message to respond"


def _get_project_name() -> str:
    """
    Get the project name from CLAUDE_PROJECT_DIR environment variable.

    Returns the basename of the project directory (e.g. 'PACT-prompt'),
    falling back to 'unknown' if the variable is not set.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project_dir:
        return os.path.basename(project_dir)
    return "unknown"


def _prepend_session_prefix(text: str) -> str:
    """
    Prepend a bold session identifier header line to a message.

    Format: **[ProjectName]** as the first line, then the original content.
    """
    project_name = _get_project_name()
    return f"<b>[{project_name}]</b>\n{text}"


class ToolContext:
    """
    Shared context for MCP tool handlers.

    Holds the TelegramClient, VoiceTranscriber, configuration, and the
    pending replies registry (message_id -> asyncio.Future). Created once
    at server startup and passed to all tool handlers.
    """

    def __init__(self) -> None:
        self.client: TelegramClient | None = None
        self.voice: VoiceTranscriber | None = None
        self.config: dict[str, Any] | None = None
        self.configured: bool = False
        self.start_time: float = time.time()

        # Pending telegram_ask replies: {message_id: asyncio.Future}
        self.pending_replies: dict[int, asyncio.Future[str]] = {}

        # Track when each pending reply was registered (for stale cleanup)
        self._pending_timestamps: dict[int, float] = {}

        # Rate limiter: deque of timestamps for recent notify calls
        self._notify_timestamps: collections.deque[float] = collections.deque()

    def initialize(self, config: dict[str, Any]) -> None:
        """
        Initialize the tool context with loaded configuration.

        Args:
            config: Configuration dict from config.load_config().
        """
        self.config = config
        self.configured = True

        self.client = TelegramClient(
            bot_token=config["bot_token"],
            chat_id=config["chat_id"],
        )

        self.voice = VoiceTranscriber(
            bot_token=config["bot_token"],
            openai_api_key=config.get("openai_api_key"),
        )

    def check_notify_rate_limit(self) -> bool:
        """
        Check if sending a notification would exceed the rate limit.

        Returns:
            True if the call is allowed, False if rate limited.
        """
        now = time.time()
        # Evict timestamps older than the rate window
        while self._notify_timestamps and self._notify_timestamps[0] < now - NOTIFY_RATE_WINDOW:
            self._notify_timestamps.popleft()

        if len(self._notify_timestamps) >= NOTIFY_RATE_LIMIT:
            return False

        self._notify_timestamps.append(now)
        return True

    def cleanup_stale_futures(self) -> int:
        """
        Remove pending Futures that have exceeded timeout + buffer.

        Prevents memory leaks from Futures that were never resolved
        (e.g., if the polling loop missed a reply).

        Returns:
            Number of stale entries removed.
        """
        now = time.time()
        stale_ids = []
        for msg_id, timestamp in self._pending_timestamps.items():
            # Use DEFAULT_ASK_TIMEOUT as max expected lifetime + buffer
            if now - timestamp > DEFAULT_ASK_TIMEOUT + STALE_FUTURE_BUFFER:
                stale_ids.append(msg_id)

        for msg_id in stale_ids:
            future = self.pending_replies.pop(msg_id, None)
            if future is not None and not future.done():
                future.cancel()
            self._pending_timestamps.pop(msg_id, None)

        if stale_ids:
            logger.info("Cleaned up %d stale pending replies", len(stale_ids))

        return len(stale_ids)

    def resolve_reply(self, message_id: int, text: str) -> bool:
        """
        Resolve a pending telegram_ask reply.

        Called by the background polling loop when a reply matching a
        pending question's message_id arrives.

        Args:
            message_id: The message_id of the original question.
            text: The reply text (or transcribed voice note).

        Returns:
            True if a pending Future was found and resolved.
        """
        future = self.pending_replies.get(message_id)
        if future is not None and not future.done():
            future.set_result(text)
            self._pending_timestamps.pop(message_id, None)
            return True
        return False

    async def close(self) -> None:
        """Clean up resources."""
        if self.client:
            await self.client.close()
        if self.voice:
            await self.voice.close()

        # Cancel any pending futures
        for msg_id, future in self.pending_replies.items():
            if not future.done():
                future.cancel()
        self.pending_replies.clear()
        self._pending_timestamps.clear()


# Global tool context (initialized by server.py)
_ctx = ToolContext()


def get_context() -> ToolContext:
    """Get the global tool context. Used by server.py for initialization."""
    return _ctx


async def tool_telegram_notify(
    message: str,
    parse_mode: str = "HTML",
) -> str:
    """
    Send a one-way notification to the user's Telegram.

    Args:
        message: Message text (supports HTML formatting).
        parse_mode: Formatting mode - "HTML" (default), "Markdown", or "plain".

    Returns:
        Success confirmation or error message.
    """
    if not _ctx.configured or _ctx.client is None:
        return (
            "pact-telegram is not configured. "
            "Run /PACT:telegram-setup to set up the Telegram bridge."
        )

    # Rate limiting: max NOTIFY_RATE_LIMIT messages per minute
    if not _ctx.check_notify_rate_limit():
        return (
            f"Rate limited: max {NOTIFY_RATE_LIMIT} notifications per minute. "
            f"Please wait before sending more."
        )

    # Prepend session identifier so user knows which instance sent this
    prefixed_message = _prepend_session_prefix(message)

    try:
        result = await _ctx.client.send_message(
            text=prefixed_message,
            parse_mode=parse_mode,
        )
        msg_id = result.get("message_id", "?")
        return f"Notification sent (message_id: {msg_id})"

    except TelegramAPIError as e:
        logger.error("telegram_notify failed: %s", e)
        return f"Failed to send notification: {e}"


async def tool_telegram_ask(
    question: str,
    options: list[str] | None = None,
    timeout_seconds: int = DEFAULT_ASK_TIMEOUT,
) -> str:
    """
    Send a question to Telegram and BLOCK until user replies.

    Use when the user is away from the terminal and you need their input.
    Optionally provides inline keyboard buttons for quick-reply options.

    The tool blocks the MCP call until:
    - The user replies to the question message (text or voice note)
    - The user taps an inline keyboard button
    - The timeout expires

    Args:
        question: The question to ask the user.
        options: Optional list of quick-reply button labels (max 10).
        timeout_seconds: Maximum seconds to wait (default: 300).

    Returns:
        The user's reply text, or a timeout/error message.
    """
    if not _ctx.configured or _ctx.client is None:
        return (
            "pact-telegram is not configured. "
            "Run /PACT:telegram-setup to set up the Telegram bridge."
        )

    # Validate options
    if options and len(options) > MAX_BUTTONS:
        options = options[:MAX_BUTTONS]
        logger.warning("Truncated options to %d buttons", MAX_BUTTONS)

    # Clamp timeout
    timeout_seconds = max(10, min(timeout_seconds, 600))

    # Clean up stale futures before checking cap
    _ctx.cleanup_stale_futures()

    # Enforce concurrent pending replies cap
    if len(_ctx.pending_replies) >= MAX_PENDING_REPLIES:
        return (
            f"Too many pending questions ({MAX_PENDING_REPLIES}). "
            f"Wait for existing questions to be answered first."
        )

    # Prepend session identifier and append reply hint
    prefixed_question = _prepend_session_prefix(question) + ASK_REPLY_HINT

    try:
        # Send the question
        if options:
            result = await _ctx.client.send_message_with_buttons(
                text=prefixed_question,
                buttons=options,
            )
        else:
            result = await _ctx.client.send_message(text=prefixed_question)

        sent_message_id = result.get("message_id")
        if sent_message_id is None:
            return "Failed to send question: no message_id in response"

        # Register a Future for this question
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        _ctx.pending_replies[sent_message_id] = future
        _ctx._pending_timestamps[sent_message_id] = time.time()

        try:
            # Block until reply received or timeout
            reply_text = await asyncio.wait_for(future, timeout=timeout_seconds)
            return reply_text
        except asyncio.TimeoutError:
            return (
                f"No reply received within {timeout_seconds} seconds. "
                f"The user may not be available on Telegram."
            )
        finally:
            # Clean up the pending entry and its timestamp
            _ctx.pending_replies.pop(sent_message_id, None)
            _ctx._pending_timestamps.pop(sent_message_id, None)

    except TelegramAPIError as e:
        logger.error("telegram_ask failed: %s", e)
        return f"Failed to send question: {e}"


async def tool_telegram_status() -> str:
    """
    Get pact-telegram bridge status and health.

    Returns bridge configuration status, mode, uptime, and feature
    availability. Always works even when not configured.

    Returns:
        Formatted status string.
    """
    if not _ctx.configured:
        return (
            "pact-telegram status: NOT CONFIGURED\n"
            "Run /PACT:telegram-setup to configure the Telegram bridge."
        )

    config = _ctx.config or {}
    uptime = int(time.time() - _ctx.start_time)
    uptime_str = f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s"

    voice_available = _ctx.voice.is_available() if _ctx.voice else False
    pending_count = len(_ctx.pending_replies)

    lines = [
        "pact-telegram status: CONNECTED",
        f"  Mode: {config.get('mode', 'unknown')}",
        f"  Uptime: {uptime_str}",
        f"  Voice transcription: {'available' if voice_available else 'not configured (no OpenAI key)'}",
        f"  Pending questions: {pending_count}",
    ]

    warnings = config.get("warnings", [])
    if warnings:
        lines.append("  Warnings:")
        for warning in warnings:
            lines.append(f"    - {warning}")

    return "\n".join(lines)
