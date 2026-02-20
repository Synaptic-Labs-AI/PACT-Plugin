"""
Location: pact-plugin/telegram/server.py
Summary: FastMCP server setup for the pact-telegram bridge (stdio transport).
Used by: __main__.py (entry point spawned by Claude Code via .mcp.json).

Creates the MCP server with four tools (telegram_notify, telegram_ask,
telegram_check_replies, telegram_status) and manages the background
Telegram polling loop.

Architecture:
- Server starts and loads config (graceful no-op if unconfigured)
- Background asyncio task polls Telegram via getUpdates
- Incoming replies are routed to pending telegram_ask Futures
- Unmatched replies (e.g., to notifications) are queued for telegram_check_replies
- Voice note replies are transcribed before resolving Futures or queuing

Graceful no-op behavior:
- If config is missing, server starts but only telegram_status is functional
- telegram_notify and telegram_ask return "not configured" messages
- This allows the MCP server to be registered without breaking on startup
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP

from telegram.config import get_or_create_session_id, load_config_safe
from telegram.routing import (
    FileBasedRouter,
    UpdateRouter,
    count_active_sessions,
    register_session,
    unregister_session,
)
from telegram.telegram_client import TelegramClient
from telegram.tools import (
    get_context,
    tool_telegram_notify,
    tool_telegram_ask,
    tool_telegram_check_replies,
    tool_telegram_status,
)
from telegram.voice import VoiceTranscriptionError

logger = logging.getLogger("pact-telegram.server")

# Polling interval when no updates received (seconds)
POLL_INTERVAL = 1

# Minimum delay between poll errors to prevent tight loops
ERROR_BACKOFF = 5


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """
    MCP server lifespan manager.

    Handles startup (config loading, router creation, polling loop start)
    and shutdown (polling loop cancellation, router cleanup, resource cleanup).

    Always uses FileBasedRouter for update routing. This ensures that even the
    first session can detect and coordinate with sessions that appear later,
    avoiding a race condition where DirectRouter would consume all updates.

    Yields:
        Empty dict (no lifespan state needed by tools).
    """
    ctx = get_context()
    polling_task: asyncio.Task | None = None
    router: UpdateRouter | None = None
    session_id: str | None = None

    try:
        # Load configuration (graceful no-op if missing)
        config = load_config_safe()

        if config is not None:
            ctx.initialize(config)

            # Generate a session ID for this MCP server instance
            session_id = get_or_create_session_id()

            # Register session BEFORE counting so this session is visible
            register_session(session_id)

            # Always use FileBasedRouter for coordination.
            # Even with a single session, FileBasedRouter allows detecting and
            # coordinating with new sessions that appear later.
            active_sessions = count_active_sessions()
            router = FileBasedRouter(ctx.client, session_id=session_id)
            logger.info(
                "pact-telegram initialized with FileBasedRouter "
                "(mode=%s, voice=%s, active_sessions=%d)",
                config.get("mode", "unknown"),
                "yes" if config.get("openai_api_key") else "no",
                active_sessions,
            )

            # Store router in context for tools.py to access
            ctx.router = router

            # Start router
            await router.start(session_id)

            # Start background polling loop
            polling_task = asyncio.create_task(
                _polling_loop(ctx, router),
                name="telegram-polling",
            )
        else:
            logger.info("pact-telegram: not configured (graceful no-op mode)")

        yield {}

    finally:
        # Shutdown: cancel polling and clean up
        if polling_task is not None:
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass

        if router is not None:
            await router.stop()

        # Unregister session regardless of router type
        if session_id is not None:
            unregister_session(session_id)

        await ctx.close()
        logger.info("pact-telegram shut down")


async def _polling_loop(ctx, router: UpdateRouter) -> None:
    """
    Background task that polls Telegram for incoming messages.

    Uses the provided UpdateRouter to get updates. Updates are
    coordinated across sessions via the FileBasedRouter.

    Routes replies to pending telegram_ask Futures based on
    reply_to_message_id matching. Handles voice note transcription
    for voice replies.

    Args:
        ctx: The shared ToolContext from tools.py.
        router: The UpdateRouter instance for retrieving updates.
    """
    logger.info("Telegram polling loop started")

    while True:
        try:
            updates = await router.get_updates(timeout=30)

            for update in updates:
                await _process_update(ctx, update)

        except asyncio.CancelledError:
            logger.info("Polling loop cancelled")
            raise
        except Exception as e:
            logger.error("Polling error: %s", e)
            await asyncio.sleep(ERROR_BACKOFF)


async def _process_update(ctx, update: dict) -> None:
    """
    Process a single Telegram update.

    Routes the update to a pending telegram_ask Future if applicable.
    Handles callback queries (inline keyboard button presses) and
    voice note transcription.

    Args:
        ctx: The shared ToolContext.
        update: A Telegram update object (already filtered by chat_id).
    """
    client: TelegramClient = ctx.client

    # Handle callback queries (inline keyboard button presses)
    callback_query_id = client.extract_callback_query_id(update)
    if callback_query_id:
        await client.answer_callback_query(callback_query_id)

    # Determine which question this reply is for
    reply_to_id = client.extract_reply_to_message_id(update)
    if reply_to_id is None:
        # Not a reply to a known question; route to first pending if any
        if ctx.pending_replies:
            reply_to_id = next(iter(ctx.pending_replies))
        else:
            logger.debug("Received update with no pending question to route to")
            return

    # Try to extract text reply
    text = client.extract_text(update)
    source = "text"

    # Determine the reply message_id (for queue tracking)
    reply_message_id = _extract_message_id(update)

    # If no text, check for voice note
    if text is None:
        voice = client.extract_voice(update)
        if voice and ctx.voice and ctx.voice.is_available():
            file_id = voice.get("file_id")
            if file_id:
                try:
                    text = await ctx.voice.transcribe_voice_message(file_id)
                    source = "voice"
                    logger.info("Voice note transcribed for reply to %d", reply_to_id)
                except VoiceTranscriptionError as e:
                    logger.warning("Voice transcription failed: %s", e)
                    text = "[Voice note received but transcription failed]"
                    source = "voice"
        elif voice:
            text = "[Voice note received but transcription not configured]"
            source = "voice"

    # Detect callback source
    if callback_query_id:
        source = "callback"

    if text is None:
        logger.debug("Could not extract reply content from update")
        return

    # Check if we have a pending Future for this message (telegram_ask)
    if reply_to_id in ctx.pending_replies:
        resolved = ctx.resolve_reply(reply_to_id, text)
        if resolved:
            logger.info("Reply resolved for message_id %d", reply_to_id)
            return
        logger.debug("Future for message_id %d already resolved or missing", reply_to_id)

    # No pending Future matched — enqueue as a reply to a notification
    ctx.enqueue_reply(
        text=text,
        message_id=reply_message_id or 0,
        reply_to_message_id=reply_to_id,
        source=source,
    )


def _extract_message_id(update: dict) -> int | None:
    """
    Extract the message_id of the incoming message itself (not reply_to).

    Args:
        update: A Telegram update object.

    Returns:
        The message_id of this message, or None.
    """
    message = update.get("message", {})
    msg_id = message.get("message_id")
    if msg_id is not None:
        return msg_id

    callback_query = update.get("callback_query", {})
    cb_message = callback_query.get("message", {})
    return cb_message.get("message_id")


def create_server() -> FastMCP:
    """
    Create and configure the MCP server with all tools.

    Returns:
        Configured FastMCP instance ready to run.
    """
    server = FastMCP(
        "pact-telegram",
        lifespan=lifespan,
    )

    # Register tools with the MCP server
    @server.tool(
        name="telegram_notify",
        description=(
            "Send a one-way notification to the user's Telegram. "
            "Use for status updates, completions, and alerts when the user "
            "may be away from the terminal."
        ),
    )
    async def telegram_notify(
        message: str,
        parse_mode: str = "HTML",
    ) -> str:
        """
        Send a one-way notification to the user's Telegram.

        Args:
            message: Message text (supports HTML formatting).
            parse_mode: Formatting mode - "HTML" (default), "Markdown", or "plain".
        """
        return await tool_telegram_notify(message=message, parse_mode=parse_mode)

    @server.tool(
        name="telegram_ask",
        description=(
            "Send a question to Telegram and BLOCK until user replies. "
            "Use when user is away from terminal and you need their input. "
            "Supports quick-reply buttons via the options parameter."
        ),
    )
    async def telegram_ask(
        question: str,
        options: list[str] | None = None,
        timeout_seconds: int = 300,
    ) -> str:
        """
        Send a question to Telegram and wait for user reply.

        Args:
            question: The question to ask the user.
            options: Optional quick-reply button labels (inline keyboard).
            timeout_seconds: Maximum seconds to wait (default: 300, max: 600).
        """
        return await tool_telegram_ask(
            question=question,
            options=options,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(
        name="telegram_check_replies",
        description=(
            "Check for queued user replies to telegram_notify messages. "
            "Non-blocking poll — returns immediately with any buffered replies. "
            "Use periodically to see if the user responded to a notification."
        ),
    )
    async def telegram_check_replies(
        clear: bool = True,
    ) -> str:
        """
        Check for queued user replies to notifications.

        Args:
            clear: If True (default), drain the queue after reading.
                   If False, peek without removing items.
        """
        return await tool_telegram_check_replies(clear=clear)

    @server.tool(
        name="telegram_status",
        description="Get pact-telegram bridge status, configuration, and health.",
    )
    async def telegram_status() -> str:
        """Get pact-telegram bridge status and health."""
        return await tool_telegram_status()

    return server


def main() -> None:
    """
    Direct-execution entry point.

    Used when server.py is run directly via .mcp.json registration:
        python3 ${CLAUDE_PLUGIN_ROOT}/telegram/server.py

    Sets up logging to stderr (stdout is reserved for MCP stdio transport)
    and starts the server.
    """
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
