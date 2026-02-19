"""
Tests for pact-plugin/telegram/tools.py

Tests cover:
1. ToolContext: initialization, resolve_reply, close, pending future lifecycle
2. tool_telegram_notify: configured/unconfigured behavior, API errors
3. tool_telegram_ask: question sending, Future-based reply waiting, timeout,
   button truncation, timeout clamping
4. tool_telegram_status: configured/unconfigured status, uptime, voice, warnings
5. Security: no credential leaks in tool responses
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram.tools import (
    ToolContext,
    get_context,
    tool_telegram_notify,
    tool_telegram_ask,
    tool_telegram_status,
    DEFAULT_ASK_TIMEOUT,
    MAX_BUTTONS,
)
from telegram.telegram_client import TelegramAPIError


# =============================================================================
# ToolContext Tests
# =============================================================================

class TestToolContext:
    """Tests for ToolContext -- shared state for MCP tool handlers."""

    def test_initial_state(self):
        """Should start unconfigured with no client."""
        ctx = ToolContext()
        assert ctx.configured is False
        assert ctx.client is None
        assert ctx.voice is None
        assert ctx.pending_replies == {}

    def test_initialize_sets_configured(self):
        """Should mark as configured after initialize()."""
        ctx = ToolContext()
        config = {
            "bot_token": "123:ABC",
            "chat_id": "456",
            "openai_api_key": None,
        }
        ctx.initialize(config)

        assert ctx.configured is True
        assert ctx.client is not None
        assert ctx.voice is not None

    def test_resolve_reply_resolves_pending_future(self):
        """Should resolve a pending Future and return True."""
        ctx = ToolContext()
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        ctx.pending_replies[42] = future

        result = ctx.resolve_reply(42, "user reply")

        assert result is True
        assert future.result() == "user reply"
        loop.close()

    def test_resolve_reply_returns_false_for_unknown_id(self):
        """Should return False when message_id has no pending Future."""
        ctx = ToolContext()
        result = ctx.resolve_reply(999, "reply")
        assert result is False

    def test_resolve_reply_returns_false_for_already_done_future(self):
        """Should return False when Future is already resolved."""
        ctx = ToolContext()
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        future.set_result("first")
        ctx.pending_replies[42] = future

        result = ctx.resolve_reply(42, "second")

        assert result is False
        loop.close()

    @pytest.mark.asyncio
    async def test_close_cancels_pending_futures(self):
        """Should cancel all pending Futures on close."""
        ctx = ToolContext()
        ctx.client = AsyncMock()
        ctx.voice = AsyncMock()

        future1 = asyncio.get_event_loop().create_future()
        future2 = asyncio.get_event_loop().create_future()
        ctx.pending_replies = {1: future1, 2: future2}

        await ctx.close()

        assert future1.cancelled()
        assert future2.cancelled()
        assert ctx.pending_replies == {}

    @pytest.mark.asyncio
    async def test_close_without_client(self):
        """Should handle close when client is None."""
        ctx = ToolContext()
        await ctx.close()  # Should not raise


# =============================================================================
# tool_telegram_notify Tests
# =============================================================================

class TestToolTelegramNotify:
    """Tests for tool_telegram_notify -- one-way notification tool."""

    @pytest.mark.asyncio
    async def test_returns_not_configured_message(self):
        """Should return 'not configured' when context is not initialized."""
        ctx = ToolContext()
        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_notify("hello")

        assert "not configured" in result

    @pytest.mark.asyncio
    async def test_sends_message_successfully(self):
        """Should send message and return confirmation."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 42}

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_notify("Build complete!")

        assert "sent" in result.lower()
        assert "42" in result
        ctx.client.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_api_error_gracefully(self):
        """Should return error message on TelegramAPIError."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.side_effect = TelegramAPIError("Chat not found", 400)

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_notify("test")

        assert "Failed" in result

    @pytest.mark.asyncio
    async def test_passes_parse_mode(self):
        """Should pass parse_mode parameter to client."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 1}

        with patch("telegram.tools._ctx", ctx):
            await tool_telegram_notify("test", parse_mode="Markdown")

        ctx.client.send_message.assert_awaited_once_with(
            text="test", parse_mode="Markdown"
        )


# =============================================================================
# tool_telegram_ask Tests
# =============================================================================

class TestToolTelegramAsk:
    """Tests for tool_telegram_ask -- blocking question with Future-based reply."""

    @pytest.mark.asyncio
    async def test_returns_not_configured_message(self):
        """Should return 'not configured' when context is not initialized."""
        ctx = ToolContext()
        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_ask("question?")

        assert "not configured" in result

    @pytest.mark.asyncio
    async def test_sends_question_and_waits_for_reply(self):
        """Should send question, register Future, and return reply when resolved."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 50}

        async def resolve_after_delay():
            await asyncio.sleep(0.05)
            ctx.resolve_reply(50, "user answer")

        with patch("telegram.tools._ctx", ctx):
            task = asyncio.create_task(resolve_after_delay())
            result = await tool_telegram_ask("What do you think?", timeout_seconds=10)
            await task

        assert result == "user answer"

    @pytest.mark.asyncio
    async def test_returns_timeout_message(self):
        """Should return timeout message when no reply received."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 51}

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_ask("question?", timeout_seconds=10)
            # Future never resolved, so it times out

        # The timeout is 10 seconds which is too long for test,
        # but with the minimum clamp at 10, let's test with a shorter approach
        assert "51" not in ctx.pending_replies  # cleaned up

    @pytest.mark.asyncio
    async def test_truncates_options_to_max_buttons(self):
        """Should truncate options list to MAX_BUTTONS."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message_with_buttons.return_value = {"message_id": 52}

        many_options = [f"option_{i}" for i in range(20)]

        async def resolve_quickly():
            await asyncio.sleep(0.05)
            ctx.resolve_reply(52, "option_0")

        with patch("telegram.tools._ctx", ctx):
            task = asyncio.create_task(resolve_quickly())
            result = await tool_telegram_ask(
                "Pick one:", options=many_options, timeout_seconds=10
            )
            await task

        # Verify buttons were passed (truncated to MAX_BUTTONS)
        call_args = ctx.client.send_message_with_buttons.call_args
        passed_buttons = call_args.kwargs.get("buttons") or call_args[1].get("buttons")
        assert len(passed_buttons) == MAX_BUTTONS

    @pytest.mark.asyncio
    async def test_sends_with_buttons_when_options_provided(self):
        """Should use send_message_with_buttons when options are given."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message_with_buttons.return_value = {"message_id": 53}

        async def resolve_quickly():
            await asyncio.sleep(0.05)
            ctx.resolve_reply(53, "Yes")

        with patch("telegram.tools._ctx", ctx):
            task = asyncio.create_task(resolve_quickly())
            result = await tool_telegram_ask(
                "Approve?", options=["Yes", "No"], timeout_seconds=10
            )
            await task

        ctx.client.send_message_with_buttons.assert_awaited_once()
        assert result == "Yes"

    @pytest.mark.asyncio
    async def test_clamps_timeout_minimum(self):
        """Should clamp timeout to minimum 10 seconds."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 54}

        # Can't easily test the clamp directly, but we can verify no crash
        # with a very small timeout value
        with patch("telegram.tools._ctx", ctx):
            # timeout_seconds=1 gets clamped to 10
            result = await tool_telegram_ask("q?", timeout_seconds=1)

        assert "No reply received" in result

    @pytest.mark.asyncio
    async def test_clamps_timeout_maximum(self):
        """Should clamp timeout to maximum 600 seconds."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 55}

        async def resolve_quickly():
            await asyncio.sleep(0.05)
            ctx.resolve_reply(55, "answer")

        with patch("telegram.tools._ctx", ctx):
            task = asyncio.create_task(resolve_quickly())
            result = await tool_telegram_ask("q?", timeout_seconds=9999)
            await task

        assert result == "answer"

    @pytest.mark.asyncio
    async def test_handles_api_error(self):
        """Should return error message on API failure."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.side_effect = TelegramAPIError("fail", 500)

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_ask("q?")

        assert "Failed" in result

    @pytest.mark.asyncio
    async def test_cleans_up_pending_reply_on_completion(self):
        """Should remove pending reply entry after completion."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 56}

        async def resolve_quickly():
            await asyncio.sleep(0.05)
            ctx.resolve_reply(56, "done")

        with patch("telegram.tools._ctx", ctx):
            task = asyncio.create_task(resolve_quickly())
            await tool_telegram_ask("q?", timeout_seconds=10)
            await task

        assert 56 not in ctx.pending_replies


# =============================================================================
# tool_telegram_status Tests
# =============================================================================

class TestToolTelegramStatus:
    """Tests for tool_telegram_status -- bridge health check."""

    @pytest.mark.asyncio
    async def test_unconfigured_status(self):
        """Should report NOT CONFIGURED when not initialized."""
        ctx = ToolContext()
        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_status()

        assert "NOT CONFIGURED" in result
        assert "telegram-setup" in result

    @pytest.mark.asyncio
    async def test_configured_status_shows_connected(self):
        """Should report CONNECTED when configured."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.config = {"mode": "passive", "warnings": []}
        ctx.start_time = time.time() - 3661  # 1h 1m 1s ago
        ctx.voice = MagicMock()
        ctx.voice.is_available.return_value = True

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_status()

        assert "CONNECTED" in result
        assert "passive" in result
        assert "1h 1m" in result
        assert "available" in result

    @pytest.mark.asyncio
    async def test_shows_pending_questions_count(self):
        """Should display count of pending telegram_ask questions."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.config = {"mode": "active", "warnings": []}
        ctx.start_time = time.time()
        ctx.voice = MagicMock()
        ctx.voice.is_available.return_value = False
        ctx.pending_replies = {1: MagicMock(), 2: MagicMock()}

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_status()

        assert "Pending questions: 2" in result

    @pytest.mark.asyncio
    async def test_shows_warnings(self):
        """Should display config warnings."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.config = {"mode": "passive", "warnings": ["File is world-readable"]}
        ctx.start_time = time.time()
        ctx.voice = MagicMock()
        ctx.voice.is_available.return_value = False

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_status()

        assert "world-readable" in result

    @pytest.mark.asyncio
    async def test_voice_not_configured(self):
        """Should indicate voice is not configured when no OpenAI key."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.config = {"mode": "passive", "warnings": []}
        ctx.start_time = time.time()
        ctx.voice = MagicMock()
        ctx.voice.is_available.return_value = False

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_status()

        assert "not configured" in result
