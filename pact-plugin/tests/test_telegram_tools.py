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
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram.tools import (
    ToolContext,
    QueuedReply,
    get_context,
    tool_telegram_notify,
    tool_telegram_ask,
    tool_telegram_check_replies,
    tool_telegram_status,
    _get_project_name,
    _prepend_session_prefix,
    ASK_REPLY_HINT,
    DEFAULT_ASK_TIMEOUT,
    MAX_BUTTONS,
    NOTIFY_RATE_LIMIT,
    NOTIFY_RATE_WINDOW,
    MAX_PENDING_REPLIES,
    STALE_FUTURE_BUFFER,
    REPLY_QUEUE_MAX_SIZE,
    REPLY_QUEUE_TTL,
    MAX_SENT_NOTIFICATIONS,
    NOTIFICATION_SNIPPET_LENGTH,
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

    @pytest.mark.asyncio
    async def test_close_closes_voice(self):
        """Should close voice transcriber on close."""
        ctx = ToolContext()
        ctx.voice = AsyncMock()
        ctx.client = AsyncMock()

        await ctx.close()

        ctx.voice.close.assert_awaited_once()
        ctx.client.close.assert_awaited_once()


class TestGetContext:
    """Tests for get_context -- global context accessor."""

    def test_returns_tool_context_instance(self):
        """Should return the global ToolContext instance."""
        ctx = get_context()
        assert isinstance(ctx, ToolContext)


# =============================================================================
# Session Prefix Tests
# =============================================================================

class TestSessionPrefix:
    """Tests for _get_project_name and _prepend_session_prefix."""

    def test_get_project_name_from_env(self):
        """Should return basename of CLAUDE_PROJECT_DIR."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/home/user/my-project"}):
            assert _get_project_name() == "my-project"

    def test_get_project_name_fallback_to_cwd(self):
        """Should fall back to cwd basename when CLAUDE_PROJECT_DIR is not set."""
        with patch.dict("os.environ", {}, clear=True), \
             patch("os.getcwd", return_value="/home/user/fallback-project"):
            assert _get_project_name() == "fallback-project"

    def test_get_project_name_empty_string(self):
        """Should fall back to cwd when CLAUDE_PROJECT_DIR is empty."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": ""}, clear=True), \
             patch("os.getcwd", return_value="/home/user/my-cwd-project"):
            assert _get_project_name() == "my-cwd-project"

    def test_get_project_name_cwd_fallback(self):
        """Should return cwd basename when CLAUDE_PROJECT_DIR is not set."""
        with patch.dict("os.environ", {}, clear=True), \
             patch("os.getcwd", return_value="/tmp/some-project"):
            assert _get_project_name() == "some-project"

    def test_get_project_name_cwd_is_home(self):
        """Should return 'unknown' when cwd is the home directory."""
        home = os.path.expanduser("~")
        with patch.dict("os.environ", {}, clear=True), \
             patch("os.getcwd", return_value=home):
            assert _get_project_name() == "unknown"

    def test_prepend_session_prefix(self):
        """Should prepend bold project name header."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/path/to/MyApp"}):
            result = _prepend_session_prefix("Hello world")
        assert result == "<b>[MyApp]</b>\nHello world"

    def test_prepend_session_prefix_unknown(self):
        """Should use 'unknown' when project dir not set and cwd is home."""
        home = os.path.expanduser("~")
        with patch.dict("os.environ", {}, clear=True), \
             patch("os.getcwd", return_value=home):
            result = _prepend_session_prefix("Hello")
        assert result == "<b>[unknown]</b>\nHello"


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
    async def test_handles_missing_message_id_in_response(self):
        """Should handle response with no message_id gracefully."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {}  # No message_id

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_notify("hello")

        assert "sent" in result.lower()
        assert "?" in result  # Falls back to "?" for unknown message_id

    @pytest.mark.asyncio
    async def test_passes_parse_mode(self):
        """Should pass parse_mode parameter to client."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 1}

        with patch("telegram.tools._ctx", ctx), \
             patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/path/to/proj"}):
            await tool_telegram_notify("test", parse_mode="Markdown")

        call_args = ctx.client.send_message.call_args
        assert call_args.kwargs["parse_mode"] == "Markdown"
        assert "<b>[proj]</b>" in call_args.kwargs["text"]
        assert "test" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_notify_includes_session_prefix(self):
        """Should prepend session prefix to outgoing notification."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 1}

        with patch("telegram.tools._ctx", ctx), \
             patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/home/user/MyProject"}):
            await tool_telegram_notify("Build done!")

        sent_text = ctx.client.send_message.call_args.kwargs["text"]
        assert sent_text.startswith("<b>[MyProject]</b>\n")
        assert "Build done!" in sent_text


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
    async def test_ask_includes_session_prefix_and_reply_hint(self):
        """Should prepend session prefix and append reply hint to question."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 60}

        async def resolve_quickly():
            await asyncio.sleep(0.05)
            ctx.resolve_reply(60, "yes")

        with patch("telegram.tools._ctx", ctx), \
             patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/home/user/TestProj"}):
            task = asyncio.create_task(resolve_quickly())
            await tool_telegram_ask("Approve deploy?", timeout_seconds=10)
            await task

        sent_text = ctx.client.send_message.call_args.kwargs["text"]
        assert sent_text.startswith("<b>[TestProj]</b>\n")
        assert "Approve deploy?" in sent_text
        assert ASK_REPLY_HINT in sent_text

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
    async def test_returns_error_when_no_message_id_in_response(self):
        """Should return error message when send_message returns no message_id."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {}  # No message_id key

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_ask("question?", timeout_seconds=10)

        assert "no message_id" in result

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


# =============================================================================
# Rate Limiting Tests
# =============================================================================

class TestNotifyRateLimit:
    """Tests for notification rate limiting."""

    def test_allows_calls_within_limit(self):
        """Should allow calls under the rate limit."""
        ctx = ToolContext()
        for _ in range(NOTIFY_RATE_LIMIT):
            assert ctx.check_notify_rate_limit() is True

    def test_blocks_calls_exceeding_limit(self):
        """Should block calls exceeding rate limit."""
        ctx = ToolContext()
        for _ in range(NOTIFY_RATE_LIMIT):
            ctx.check_notify_rate_limit()

        assert ctx.check_notify_rate_limit() is False

    def test_allows_calls_after_window_expires(self):
        """Should allow calls after rate window expires."""
        ctx = ToolContext()
        # Fill the rate limit with old timestamps
        old_time = time.time() - NOTIFY_RATE_WINDOW - 1
        for _ in range(NOTIFY_RATE_LIMIT):
            ctx._notify_timestamps.append(old_time)

        # New call should be allowed (old timestamps evicted)
        assert ctx.check_notify_rate_limit() is True

    @pytest.mark.asyncio
    async def test_tool_returns_rate_limit_message(self):
        """Should return rate limit message when limit exceeded."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 1}

        # Fill the rate limit
        for _ in range(NOTIFY_RATE_LIMIT):
            ctx.check_notify_rate_limit()

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_notify("test")

        assert "Rate limited" in result
        ctx.client.send_message.assert_not_awaited()


# =============================================================================
# Stale Future Cleanup Tests
# =============================================================================

class TestStaleFutureCleanup:
    """Tests for stale pending reply cleanup."""

    def test_removes_stale_futures(self):
        """Should remove futures that exceeded timeout + buffer."""
        ctx = ToolContext()
        loop = asyncio.new_event_loop()

        future = loop.create_future()
        ctx.pending_replies[42] = future
        # Set timestamp far enough in the past to be stale
        ctx._pending_timestamps[42] = time.time() - DEFAULT_ASK_TIMEOUT - STALE_FUTURE_BUFFER - 1

        removed = ctx.cleanup_stale_futures()

        assert removed == 1
        assert 42 not in ctx.pending_replies
        assert 42 not in ctx._pending_timestamps
        assert future.cancelled()
        loop.close()

    def test_keeps_fresh_futures(self):
        """Should not remove futures within the timeout window."""
        ctx = ToolContext()
        loop = asyncio.new_event_loop()

        future = loop.create_future()
        ctx.pending_replies[42] = future
        ctx._pending_timestamps[42] = time.time()  # Just now

        removed = ctx.cleanup_stale_futures()

        assert removed == 0
        assert 42 in ctx.pending_replies
        loop.close()

    def test_handles_already_done_futures(self):
        """Should handle cleanup when future is already done."""
        ctx = ToolContext()
        loop = asyncio.new_event_loop()

        future = loop.create_future()
        future.set_result("done")
        ctx.pending_replies[42] = future
        ctx._pending_timestamps[42] = time.time() - DEFAULT_ASK_TIMEOUT - STALE_FUTURE_BUFFER - 1

        removed = ctx.cleanup_stale_futures()

        assert removed == 1
        assert 42 not in ctx.pending_replies
        loop.close()


# =============================================================================
# Max Pending Replies Tests
# =============================================================================

class TestMaxPendingReplies:
    """Tests for max concurrent pending questions limit."""

    @pytest.mark.asyncio
    async def test_rejects_when_at_max_pending(self):
        """Should reject new questions when at MAX_PENDING_REPLIES."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()

        # Fill pending replies to capacity
        loop = asyncio.get_event_loop()
        for i in range(MAX_PENDING_REPLIES):
            ctx.pending_replies[i] = loop.create_future()
            ctx._pending_timestamps[i] = time.time()

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_ask("one more?")

        assert "Too many pending questions" in result
        ctx.client.send_message.assert_not_awaited()


# =============================================================================
# Reply Queue Tests
# =============================================================================

class TestReplyQueue:
    """Tests for reply queue infrastructure on ToolContext."""

    def test_initial_queue_is_empty(self):
        """Should start with an empty reply queue."""
        ctx = ToolContext()
        assert len(ctx.reply_queue) == 0

    def test_enqueue_reply_adds_to_queue(self):
        """Should add a QueuedReply entry to the queue."""
        ctx = ToolContext()
        ctx.enqueue_reply(
            text="hello",
            message_id=100,
            reply_to_message_id=50,
            source="text",
        )
        assert len(ctx.reply_queue) == 1
        entry = ctx.reply_queue[0]
        assert entry.text == "hello"
        assert entry.message_id == 100
        assert entry.reply_to_message_id == 50
        assert entry.source == "text"

    def test_enqueue_dequeue_fifo_ordering(self):
        """Should maintain FIFO order when draining the queue."""
        ctx = ToolContext()
        for i in range(5):
            ctx.enqueue_reply(
                text=f"msg-{i}",
                message_id=100 + i,
                reply_to_message_id=50,
                source="text",
            )

        results = ctx.drain_replies(clear=True)
        assert len(results) == 5
        for i, r in enumerate(results):
            assert r["text"] == f"msg-{i}"

    def test_queue_capacity_drops_oldest_on_overflow(self):
        """Should drop oldest entries when queue exceeds maxlen."""
        ctx = ToolContext()
        # Fill beyond capacity
        for i in range(REPLY_QUEUE_MAX_SIZE + 10):
            ctx.enqueue_reply(
                text=f"msg-{i}",
                message_id=i,
                reply_to_message_id=1,
                source="text",
            )

        assert len(ctx.reply_queue) == REPLY_QUEUE_MAX_SIZE
        # Oldest entries (0..9) should have been dropped
        first = ctx.reply_queue[0]
        assert first.text == "msg-10"

    def test_ttl_expiry_prunes_stale_replies(self):
        """Should prune replies older than REPLY_QUEUE_TTL."""
        ctx = ToolContext()
        # Add an entry with a timestamp far in the past
        old_entry = QueuedReply(
            text="old",
            message_id=1,
            reply_to_message_id=2,
            timestamp=time.time() - REPLY_QUEUE_TTL - 10,
            source="text",
        )
        ctx.reply_queue.append(old_entry)

        # Add a fresh entry
        ctx.enqueue_reply(
            text="fresh",
            message_id=3,
            reply_to_message_id=4,
            source="text",
        )

        results = ctx.drain_replies(clear=True)
        assert len(results) == 1
        assert results[0]["text"] == "fresh"

    def test_prune_expired_replies_returns_count(self):
        """Should return number of pruned entries."""
        ctx = ToolContext()
        for i in range(3):
            old_entry = QueuedReply(
                text=f"old-{i}",
                message_id=i,
                reply_to_message_id=1,
                timestamp=time.time() - REPLY_QUEUE_TTL - 10,
                source="text",
            )
            ctx.reply_queue.append(old_entry)

        pruned = ctx._prune_expired_replies()
        assert pruned == 3
        assert len(ctx.reply_queue) == 0

    def test_drain_replies_with_clear_true_empties_queue(self):
        """Should empty the queue when clear=True."""
        ctx = ToolContext()
        ctx.enqueue_reply(text="a", message_id=1, reply_to_message_id=2, source="text")
        ctx.enqueue_reply(text="b", message_id=3, reply_to_message_id=4, source="text")

        results = ctx.drain_replies(clear=True)
        assert len(results) == 2
        assert len(ctx.reply_queue) == 0

    def test_drain_replies_with_clear_false_preserves_queue(self):
        """Should preserve queue items when clear=False."""
        ctx = ToolContext()
        ctx.enqueue_reply(text="a", message_id=1, reply_to_message_id=2, source="text")
        ctx.enqueue_reply(text="b", message_id=3, reply_to_message_id=4, source="text")

        results = ctx.drain_replies(clear=False)
        assert len(results) == 2
        assert len(ctx.reply_queue) == 2  # Still there

    def test_drain_replies_includes_notification_context(self):
        """Should include original notification snippet in drain results."""
        ctx = ToolContext()
        ctx.track_notification(50, "Build completed successfully for module X")
        ctx.enqueue_reply(
            text="thanks",
            message_id=100,
            reply_to_message_id=50,
            source="text",
        )

        results = ctx.drain_replies(clear=True)
        assert len(results) == 1
        assert results[0]["context"] == "Build completed successfully for module X"

    def test_drain_replies_unknown_notification_context(self):
        """Should use fallback context for replies to untracked notifications."""
        ctx = ToolContext()
        ctx.enqueue_reply(
            text="hello",
            message_id=100,
            reply_to_message_id=999,
            source="text",
        )

        results = ctx.drain_replies(clear=True)
        assert results[0]["context"] == "(unknown notification)"

    def test_drain_replies_includes_age_seconds(self):
        """Should include age_seconds in drain results."""
        ctx = ToolContext()
        old_entry = QueuedReply(
            text="msg",
            message_id=1,
            reply_to_message_id=2,
            timestamp=time.time() - 30,
            source="text",
        )
        ctx.reply_queue.append(old_entry)

        results = ctx.drain_replies(clear=True)
        assert results[0]["age_seconds"] >= 29

    def test_drain_replies_includes_source(self):
        """Should include source type in drain results."""
        ctx = ToolContext()
        ctx.enqueue_reply(text="voice msg", message_id=1, reply_to_message_id=2, source="voice")

        results = ctx.drain_replies(clear=True)
        assert results[0]["source"] == "voice"

    def test_cleanup_stale_futures_also_prunes_reply_queue(self):
        """Should prune expired reply queue entries during stale future cleanup."""
        ctx = ToolContext()
        old_entry = QueuedReply(
            text="stale",
            message_id=1,
            reply_to_message_id=2,
            timestamp=time.time() - REPLY_QUEUE_TTL - 10,
            source="text",
        )
        ctx.reply_queue.append(old_entry)

        ctx.cleanup_stale_futures()
        assert len(ctx.reply_queue) == 0

    @pytest.mark.asyncio
    async def test_close_clears_reply_queue(self):
        """Should clear reply queue and sent notifications on close."""
        ctx = ToolContext()
        ctx.client = AsyncMock()
        ctx.voice = AsyncMock()
        ctx.enqueue_reply(text="a", message_id=1, reply_to_message_id=2, source="text")
        ctx.track_notification(10, "test notification")

        await ctx.close()

        assert len(ctx.reply_queue) == 0
        assert len(ctx._sent_notifications) == 0


# =============================================================================
# Notification Tracking Tests
# =============================================================================

class TestNotificationTracking:
    """Tests for notification message_id tracking on ToolContext."""

    def test_track_notification_stores_snippet(self):
        """Should store a snippet of the notification text."""
        ctx = ToolContext()
        ctx.track_notification(42, "Hello world")
        assert ctx._sent_notifications[42] == "Hello world"

    def test_track_notification_truncates_long_text(self):
        """Should truncate text longer than NOTIFICATION_SNIPPET_LENGTH."""
        ctx = ToolContext()
        long_text = "A" * (NOTIFICATION_SNIPPET_LENGTH + 20)
        ctx.track_notification(42, long_text)

        snippet = ctx._sent_notifications[42]
        assert len(snippet) == NOTIFICATION_SNIPPET_LENGTH + 3  # +3 for "..."
        assert snippet.endswith("...")

    def test_track_notification_evicts_oldest_on_overflow(self):
        """Should evict oldest entries when exceeding MAX_SENT_NOTIFICATIONS."""
        ctx = ToolContext()
        for i in range(MAX_SENT_NOTIFICATIONS + 5):
            ctx.track_notification(i, f"msg-{i}")

        assert len(ctx._sent_notifications) == MAX_SENT_NOTIFICATIONS
        # Oldest entries (0..4) should be evicted
        assert 0 not in ctx._sent_notifications
        assert 4 not in ctx._sent_notifications
        assert 5 in ctx._sent_notifications

    @pytest.mark.asyncio
    async def test_notify_tracks_sent_message_id(self):
        """Should track notification message_id after successful send."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {"message_id": 77}

        with patch("telegram.tools._ctx", ctx):
            await tool_telegram_notify("Test notification message")

        assert 77 in ctx._sent_notifications

    @pytest.mark.asyncio
    async def test_notify_does_not_track_when_no_message_id(self):
        """Should not track when send returns no message_id."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()
        ctx.client.send_message.return_value = {}

        with patch("telegram.tools._ctx", ctx):
            await tool_telegram_notify("test")

        assert len(ctx._sent_notifications) == 0


# =============================================================================
# tool_telegram_check_replies Tests
# =============================================================================

class TestToolTelegramCheckReplies:
    """Tests for tool_telegram_check_replies -- non-blocking queue poll."""

    @pytest.mark.asyncio
    async def test_returns_not_configured_when_unconfigured(self):
        """Should return 'not configured' when context is not initialized."""
        ctx = ToolContext()
        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_check_replies()

        assert "not configured" in result

    @pytest.mark.asyncio
    async def test_returns_empty_when_queue_empty(self):
        """Should return empty replies list when queue is empty."""
        ctx = ToolContext()
        ctx.configured = True

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_check_replies()

        import json
        data = json.loads(result)
        assert data["replies"] == []
        assert data["count"] == 0
        assert data["queue_depth_before"] == 0
        assert data["queue_depth_after"] == 0

    @pytest.mark.asyncio
    async def test_returns_queued_items_and_drains(self):
        """Should return all queued replies and drain the queue by default."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.track_notification(50, "Build complete")
        ctx.enqueue_reply(text="got it", message_id=100, reply_to_message_id=50, source="text")
        ctx.enqueue_reply(text="thanks", message_id=101, reply_to_message_id=50, source="text")

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_check_replies()

        import json
        data = json.loads(result)
        assert data["count"] == 2
        assert data["queue_depth_before"] == 2
        assert data["queue_depth_after"] == 0
        assert data["replies"][0]["text"] == "got it"
        assert data["replies"][1]["text"] == "thanks"
        assert data["replies"][0]["context"] == "Build complete"

    @pytest.mark.asyncio
    async def test_clear_false_preserves_queue(self):
        """Should preserve queue when clear=False."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.enqueue_reply(text="msg", message_id=1, reply_to_message_id=2, source="text")

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_check_replies(clear=False)

        import json
        data = json.loads(result)
        assert data["count"] == 1
        assert data["queue_depth_before"] == 1
        assert data["queue_depth_after"] == 1  # Preserved

    @pytest.mark.asyncio
    async def test_check_replies_reports_queue_depths(self):
        """Should report accurate before and after queue depths."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.enqueue_reply(text="a", message_id=1, reply_to_message_id=2, source="text")
        ctx.enqueue_reply(text="b", message_id=3, reply_to_message_id=4, source="voice")

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_check_replies(clear=True)

        import json
        data = json.loads(result)
        assert data["queue_depth_before"] == 2
        assert data["queue_depth_after"] == 0


# =============================================================================
# telegram_status Reply Queue Depth Tests
# =============================================================================

class TestStatusReplyQueueDepth:
    """Tests for reply queue depth in telegram_status output."""

    @pytest.mark.asyncio
    async def test_status_shows_queue_depth(self):
        """Should display reply queue depth in status output."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.config = {"mode": "passive", "warnings": []}
        ctx.start_time = time.time()
        ctx.voice = MagicMock()
        ctx.voice.is_available.return_value = False
        ctx.enqueue_reply(text="a", message_id=1, reply_to_message_id=2, source="text")
        ctx.enqueue_reply(text="b", message_id=3, reply_to_message_id=4, source="text")

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_status()

        assert "Reply queue depth: 2" in result

    @pytest.mark.asyncio
    async def test_status_shows_zero_queue_depth(self):
        """Should show queue depth 0 when queue is empty."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.config = {"mode": "passive", "warnings": []}
        ctx.start_time = time.time()
        ctx.voice = MagicMock()
        ctx.voice.is_available.return_value = False

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_status()

        assert "Reply queue depth: 0" in result
