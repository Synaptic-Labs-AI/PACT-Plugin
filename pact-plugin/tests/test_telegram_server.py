"""
Tests for pact-plugin/telegram/server.py

Tests cover:
1. create_server: MCP server creation with all tools registered
2. lifespan: startup/shutdown lifecycle, graceful no-op when unconfigured
3. _polling_loop: background polling, error backoff
4. _process_update: update routing, callback query handling, voice transcription
5. Integration: reply routing to pending Futures, fallback to first pending
"""

import asyncio
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock the mcp module tree before importing server.py (mcp may not be installed)
if "mcp" not in sys.modules:
    _mcp = ModuleType("mcp")
    _mcp_server = ModuleType("mcp.server")
    _mcp_server_fastmcp = ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        """Minimal stub for FastMCP to allow server.py to load."""
        def __init__(self, name, **kwargs):
            self.name = name
            self._lifespan = kwargs.get("lifespan")
            self.instructions = kwargs.get("instructions")
        def tool(self, **kwargs):
            def decorator(fn):
                return fn
            return decorator
        def run(self, **kwargs):
            pass

    _mcp_server_fastmcp.FastMCP = _FakeFastMCP
    _mcp.server = _mcp_server
    _mcp_server.fastmcp = _mcp_server_fastmcp
    sys.modules["mcp"] = _mcp
    sys.modules["mcp.server"] = _mcp_server
    sys.modules["mcp.server.fastmcp"] = _mcp_server_fastmcp

from telegram.routing import DirectRouter
from telegram.server import (
    _process_update,
    _extract_message_id,
    _polling_loop,
    create_server,
    lifespan,
    POLL_INTERVAL,
    ERROR_BACKOFF,
)
from telegram.tools import ToolContext
from telegram.voice import VoiceTranscriptionError


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def tool_context():
    """Create a ToolContext with mocked client and voice.

    The TelegramClient has both sync (static) and async methods.
    We use MagicMock for the base and override async methods explicitly.
    """
    ctx = ToolContext()
    ctx.configured = True
    ctx.config = {"mode": "passive", "warnings": []}

    # Use MagicMock for sync methods; override async methods explicitly
    client = MagicMock()
    client.get_updates = AsyncMock(return_value=[])
    client.send_message = AsyncMock(return_value={"message_id": 1})
    client.send_message_with_buttons = AsyncMock(return_value={"message_id": 1})
    client.answer_callback_query = AsyncMock(return_value=True)
    client.get_file = AsyncMock(return_value={})
    client.close = AsyncMock()
    ctx.client = client

    voice = MagicMock()
    voice.is_available.return_value = True
    voice.transcribe_voice_message = AsyncMock(return_value="Transcribed text")
    voice.close = AsyncMock()
    ctx.voice = voice
    return ctx


# =============================================================================
# create_server Tests
# =============================================================================

class TestCreateServer:
    """Tests for create_server -- MCP server setup."""

    def test_creates_server(self):
        """Should return a FastMCP server instance with instructions."""
        server = create_server()
        assert server is not None
        assert server.name == "pact-telegram"
        assert server.instructions is not None
        # All 4 tool names must appear in the instructions
        for tool_name in ("telegram_notify", "telegram_ask", "telegram_check_replies", "telegram_status"):
            assert tool_name in server.instructions, f"{tool_name} missing from instructions"


# =============================================================================
# _process_update Tests
# =============================================================================

class TestProcessUpdate:
    """Tests for _process_update -- routing individual updates."""

    @pytest.mark.asyncio
    async def test_resolves_reply_by_reply_to_id(self, tool_context):
        """Should resolve pending Future when reply_to_message_id matches."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[42] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 42
        tool_context.client.extract_text.return_value = "user reply"
        tool_context.client.extract_voice.return_value = None

        update = {"message": {"text": "user reply", "reply_to_message": {"message_id": 42}}}

        await _process_update(tool_context, update)

        assert future.result() == "user reply"

    @pytest.mark.asyncio
    async def test_routes_to_first_pending_when_no_reply_to(self, tool_context):
        """Should route to first pending question when message has no reply_to."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[99] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = None
        tool_context.client.extract_text.return_value = "standalone message"
        tool_context.client.extract_voice.return_value = None

        update = {"message": {"text": "standalone message"}}

        await _process_update(tool_context, update)

        assert future.result() == "standalone message"

    @pytest.mark.asyncio
    async def test_ignores_update_when_no_pending(self, tool_context):
        """Should ignore update when no pending questions exist."""
        tool_context.pending_replies = {}

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = None

        update = {"message": {"text": "orphan message"}}

        # Should not raise
        await _process_update(tool_context, update)

    @pytest.mark.asyncio
    async def test_answers_callback_query(self, tool_context):
        """Should acknowledge callback query (button press)."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[10] = future

        tool_context.client.extract_callback_query_id.return_value = "cq_123"
        tool_context.client.extract_reply_to_message_id.return_value = 10
        tool_context.client.extract_text.return_value = "Yes"
        tool_context.client.extract_voice.return_value = None

        update = {
            "callback_query": {
                "id": "cq_123",
                "message": {"message_id": 10},
                "data": "Yes",
            }
        }

        await _process_update(tool_context, update)

        tool_context.client.answer_callback_query.assert_awaited_once_with("cq_123")
        assert future.result() == "Yes"

    @pytest.mark.asyncio
    async def test_transcribes_voice_reply(self, tool_context):
        """Should transcribe voice note and resolve with transcribed text."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[20] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 20
        tool_context.client.extract_text.return_value = None
        tool_context.client.extract_voice.return_value = {
            "file_id": "voice_file_abc",
            "duration": 5,
        }
        tool_context.voice.transcribe_voice_message.return_value = "Transcribed text"

        update = {
            "message": {
                "voice": {"file_id": "voice_file_abc", "duration": 5},
                "reply_to_message": {"message_id": 20},
            }
        }

        await _process_update(tool_context, update)

        assert future.result() == "Transcribed text"
        tool_context.voice.transcribe_voice_message.assert_awaited_once_with("voice_file_abc")

    @pytest.mark.asyncio
    async def test_handles_voice_transcription_failure(self, tool_context):
        """Should resolve with error message when transcription fails."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[30] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 30
        tool_context.client.extract_text.return_value = None
        tool_context.client.extract_voice.return_value = {
            "file_id": "bad_voice",
            "duration": 5,
        }
        tool_context.voice.transcribe_voice_message.side_effect = VoiceTranscriptionError("fail")

        update = {
            "message": {
                "voice": {"file_id": "bad_voice", "duration": 5},
                "reply_to_message": {"message_id": 30},
            }
        }

        await _process_update(tool_context, update)

        assert "transcription failed" in future.result()

    @pytest.mark.asyncio
    async def test_voice_without_transcription_configured(self, tool_context):
        """Should return message when voice received but transcription not configured."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[40] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 40
        tool_context.client.extract_text.return_value = None
        tool_context.client.extract_voice.return_value = {
            "file_id": "voice_123",
            "duration": 3,
        }
        tool_context.voice.is_available.return_value = False

        update = {
            "message": {
                "voice": {"file_id": "voice_123", "duration": 3},
                "reply_to_message": {"message_id": 40},
            }
        }

        await _process_update(tool_context, update)

        assert "not configured" in future.result()

    @pytest.mark.asyncio
    async def test_ignores_update_with_no_extractable_content(self, tool_context):
        """Should ignore update when no text or voice can be extracted."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[50] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 50
        tool_context.client.extract_text.return_value = None
        tool_context.client.extract_voice.return_value = None

        update = {"message": {"photo": [{"file_id": "photo123"}]}}

        await _process_update(tool_context, update)

        # Future should NOT be resolved (no content)
        assert not future.done()

    @pytest.mark.asyncio
    async def test_enqueues_reply_to_unknown_message_id(self, tool_context):
        """Should enqueue reply when reply_to_message_id has no pending Future."""
        tool_context.pending_replies = {}

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 999
        tool_context.client.extract_text.return_value = "reply to unknown"
        tool_context.client.extract_voice.return_value = None

        update = {"message": {"message_id": 200, "text": "reply to unknown"}}

        await _process_update(tool_context, update)

        assert len(tool_context.reply_queue) == 1
        entry = tool_context.reply_queue[0]
        assert entry.text == "reply to unknown"
        assert entry.reply_to_message_id == 999
        assert entry.source == "text"


# =============================================================================
# _polling_loop Tests
# =============================================================================

class TestPollingLoop:
    """Tests for _polling_loop -- background polling with error handling."""

    @pytest.mark.asyncio
    async def test_polls_and_processes_updates(self, tool_context):
        """Should poll for updates and process each one."""
        call_count = 0

        async def fake_get_updates(timeout=30):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"update_id": 1, "message": {"text": "hi"}}]
            # Stop the loop after first iteration
            raise asyncio.CancelledError()

        tool_context.client.get_updates = fake_get_updates
        router = DirectRouter(tool_context.client)

        with patch("telegram.server._process_update", new_callable=AsyncMock) as mock_process:
            with pytest.raises(asyncio.CancelledError):
                await _polling_loop(tool_context, router)

        mock_process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_backs_off_on_error(self, tool_context):
        """Should sleep ERROR_BACKOFF seconds on polling error."""
        call_count = 0

        async def fake_get_updates(timeout=30):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("polling error")
            raise asyncio.CancelledError()

        tool_context.client.get_updates = fake_get_updates
        router = DirectRouter(tool_context.client)

        with patch("telegram.server.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(asyncio.CancelledError):
                await _polling_loop(tool_context, router)

        mock_sleep.assert_awaited_once_with(ERROR_BACKOFF)

    @pytest.mark.asyncio
    async def test_propagates_cancellation(self, tool_context):
        """Should propagate CancelledError for clean shutdown."""
        tool_context.client.get_updates = AsyncMock(side_effect=asyncio.CancelledError())
        router = DirectRouter(tool_context.client)

        with pytest.raises(asyncio.CancelledError):
            await _polling_loop(tool_context, router)


# =============================================================================
# lifespan Tests
# =============================================================================

class TestLifespan:
    """Tests for lifespan -- MCP server startup/shutdown lifecycle."""

    @pytest.mark.asyncio
    async def test_lifespan_configured_starts_polling(self):
        """Should start polling task when config is available."""
        fake_config = {
            "bot_token": "123:ABC",
            "chat_id": "456",
            "mode": "passive",
            "openai_api_key": None,
        }

        mock_router = AsyncMock()

        with patch("telegram.server.load_config_safe", return_value=fake_config), \
             patch("telegram.server.get_context") as mock_get_ctx, \
             patch("telegram.server._polling_loop", new_callable=AsyncMock) as mock_poll, \
             patch("telegram.server.get_or_create_session_id", return_value="test-session"), \
             patch("telegram.server.register_session"), \
             patch("telegram.server.count_active_sessions", return_value=1), \
             patch("telegram.server.FileBasedRouter", return_value=mock_router), \
             patch("telegram.server.unregister_session"):
            ctx = MagicMock()
            ctx.close = AsyncMock()
            mock_get_ctx.return_value = ctx

            server = MagicMock()
            async with lifespan(server) as state:
                assert state == {}
                ctx.initialize.assert_called_once_with(fake_config)

    @pytest.mark.asyncio
    async def test_lifespan_unconfigured_no_polling(self):
        """Should not start polling when config is None (graceful no-op)."""
        with patch("telegram.server.load_config_safe", return_value=None), \
             patch("telegram.server.get_context") as mock_get_ctx:
            ctx = MagicMock()
            ctx.close = AsyncMock()
            mock_get_ctx.return_value = ctx

            server = MagicMock()
            async with lifespan(server) as state:
                assert state == {}
                ctx.initialize.assert_not_called()

    @pytest.mark.asyncio
    async def test_lifespan_cleans_up_on_shutdown(self):
        """Should call ctx.close on shutdown."""
        with patch("telegram.server.load_config_safe", return_value=None), \
             patch("telegram.server.get_context") as mock_get_ctx:
            ctx = MagicMock()
            ctx.close = AsyncMock()
            mock_get_ctx.return_value = ctx

            server = MagicMock()
            async with lifespan(server):
                pass

            ctx.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_creates_polling_task(self):
        """Should create an asyncio task for the polling loop when configured."""
        fake_config = {
            "bot_token": "123:ABC",
            "chat_id": "456",
            "mode": "passive",
            "openai_api_key": None,
        }

        mock_router = AsyncMock()

        with patch("telegram.server.load_config_safe", return_value=fake_config), \
             patch("telegram.server.get_context") as mock_get_ctx, \
             patch("telegram.server._polling_loop", new_callable=AsyncMock), \
             patch("telegram.server.asyncio.create_task", wraps=asyncio.create_task) as mock_create_task, \
             patch("telegram.server.get_or_create_session_id", return_value="test-session"), \
             patch("telegram.server.register_session"), \
             patch("telegram.server.count_active_sessions", return_value=1), \
             patch("telegram.server.FileBasedRouter", return_value=mock_router), \
             patch("telegram.server.unregister_session"):
            ctx = MagicMock()
            ctx.close = AsyncMock()
            mock_get_ctx.return_value = ctx

            server = MagicMock()
            async with lifespan(server):
                mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_cancels_polling_on_shutdown(self):
        """Should cancel the polling task when the lifespan context exits."""
        fake_config = {
            "bot_token": "123:ABC",
            "chat_id": "456",
            "mode": "passive",
            "openai_api_key": None,
        }

        # Use a real polling coroutine that blocks until cancelled
        async def blocking_poll(ctx):
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                raise

        mock_router = AsyncMock()

        with patch("telegram.server.load_config_safe", return_value=fake_config), \
             patch("telegram.server.get_context") as mock_get_ctx, \
             patch("telegram.server._polling_loop", side_effect=blocking_poll), \
             patch("telegram.server.get_or_create_session_id", return_value="test-session"), \
             patch("telegram.server.register_session"), \
             patch("telegram.server.count_active_sessions", return_value=1), \
             patch("telegram.server.FileBasedRouter", return_value=mock_router), \
             patch("telegram.server.unregister_session"):
            ctx = MagicMock()
            ctx.close = AsyncMock()
            mock_get_ctx.return_value = ctx

            server = MagicMock()
            async with lifespan(server):
                pass  # Exit triggers shutdown

            # If we reached here, the polling task was cancelled and
            # CancelledError was handled without propagating
            ctx.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_unconfigured_skips_cancel(self):
        """Should not attempt to cancel when no polling task was created."""
        with patch("telegram.server.load_config_safe", return_value=None), \
             patch("telegram.server.get_context") as mock_get_ctx:
            ctx = MagicMock()
            ctx.close = AsyncMock()
            mock_get_ctx.return_value = ctx

            server = MagicMock()
            async with lifespan(server):
                pass

            # Should complete without error even though no polling task exists
            ctx.close.assert_awaited_once()


# =============================================================================
# Additional _process_update Edge Cases
# =============================================================================

class TestProcessUpdateEdgeCases:
    """Additional edge case tests for _process_update."""

    @pytest.fixture
    def tool_context(self):
        """Create a ToolContext with mocked client and voice."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.config = {"mode": "passive", "warnings": []}

        client = MagicMock()
        client.get_updates = AsyncMock(return_value=[])
        client.send_message = AsyncMock(return_value={"message_id": 1})
        client.answer_callback_query = AsyncMock(return_value=True)
        client.close = AsyncMock()
        ctx.client = client

        voice = MagicMock()
        voice.is_available.return_value = True
        voice.transcribe_voice_message = AsyncMock(return_value="Transcribed text")
        voice.close = AsyncMock()
        ctx.voice = voice
        return ctx

    @pytest.mark.asyncio
    async def test_voice_without_file_id_skips_transcription(self, tool_context):
        """Should skip transcription when voice note has no file_id."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[60] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 60
        tool_context.client.extract_text.return_value = None
        # Voice metadata without file_id
        tool_context.client.extract_voice.return_value = {
            "duration": 5,
        }

        update = {
            "message": {
                "voice": {"duration": 5},
                "reply_to_message": {"message_id": 60},
            }
        }

        await _process_update(tool_context, update)

        # Transcription should not be attempted (no file_id)
        tool_context.voice.transcribe_voice_message.assert_not_awaited()
        # Future should not be resolved (no content extracted)
        assert not future.done()

    @pytest.mark.asyncio
    async def test_voice_with_voice_none_on_context(self, tool_context):
        """Should return not-configured message when ctx.voice is None."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[70] = future
        tool_context.voice = None

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 70
        tool_context.client.extract_text.return_value = None
        tool_context.client.extract_voice.return_value = {
            "file_id": "voice_abc",
            "duration": 3,
        }

        update = {
            "message": {
                "voice": {"file_id": "voice_abc", "duration": 3},
                "reply_to_message": {"message_id": 70},
            }
        }

        await _process_update(tool_context, update)

        assert future.done()
        assert "not configured" in future.result()


# =============================================================================
# _extract_message_id Tests
# =============================================================================

class TestExtractMessageId:
    """Tests for _extract_message_id -- extracting message_id from updates."""

    def test_extracts_from_regular_message(self):
        """Should extract message_id from a regular message update."""
        update = {"message": {"message_id": 42, "text": "hello"}}
        assert _extract_message_id(update) == 42

    def test_extracts_from_callback_query(self):
        """Should extract message_id from a callback query update."""
        update = {
            "callback_query": {
                "id": "cq_1",
                "message": {"message_id": 55},
                "data": "Yes",
            }
        }
        assert _extract_message_id(update) == 55

    def test_returns_none_for_empty_update(self):
        """Should return None when update has no message or callback."""
        update = {}
        assert _extract_message_id(update) is None

    def test_returns_none_for_message_without_id(self):
        """Should return None when message has no message_id."""
        update = {"message": {"text": "no id"}}
        assert _extract_message_id(update) is None


# =============================================================================
# Reply Queue Routing Tests (server-side)
# =============================================================================

class TestReplyQueueRouting:
    """Tests for reply queue routing in _process_update."""

    @pytest.fixture
    def tool_context(self):
        """Create a ToolContext with mocked client and voice."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.config = {"mode": "passive", "warnings": []}

        client = MagicMock()
        client.get_updates = AsyncMock(return_value=[])
        client.send_message = AsyncMock(return_value={"message_id": 1})
        client.answer_callback_query = AsyncMock(return_value=True)
        client.close = AsyncMock()
        ctx.client = client

        voice = MagicMock()
        voice.is_available.return_value = True
        voice.transcribe_voice_message = AsyncMock(return_value="Transcribed text")
        voice.close = AsyncMock()
        ctx.voice = voice
        return ctx

    @pytest.mark.asyncio
    async def test_reply_to_notification_enqueued_not_dropped(self, tool_context):
        """Should enqueue reply to a notification (no pending Future) instead of dropping."""
        tool_context.pending_replies = {}

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 50
        tool_context.client.extract_text.return_value = "Use PostgreSQL"
        tool_context.client.extract_voice.return_value = None

        update = {"message": {"message_id": 200, "text": "Use PostgreSQL", "reply_to_message": {"message_id": 50}}}

        await _process_update(tool_context, update)

        assert len(tool_context.reply_queue) == 1
        entry = tool_context.reply_queue[0]
        assert entry.text == "Use PostgreSQL"
        assert entry.reply_to_message_id == 50
        assert entry.message_id == 200
        assert entry.source == "text"

    @pytest.mark.asyncio
    async def test_ask_reply_routes_to_future_not_queue(self, tool_context):
        """Should route reply to pending Future, not to reply queue."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[42] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 42
        tool_context.client.extract_text.return_value = "user answer"
        tool_context.client.extract_voice.return_value = None

        update = {"message": {"message_id": 300, "text": "user answer", "reply_to_message": {"message_id": 42}}}

        await _process_update(tool_context, update)

        assert future.result() == "user answer"
        assert len(tool_context.reply_queue) == 0

    @pytest.mark.asyncio
    async def test_concurrent_ask_and_notification_reply(self, tool_context):
        """Should route ask reply to Future and notification reply to queue."""
        # Set up a pending ask Future
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[42] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_voice.return_value = None

        # First update: reply to the pending ask (message_id 42)
        tool_context.client.extract_reply_to_message_id.return_value = 42
        tool_context.client.extract_text.return_value = "ask answer"
        update1 = {"message": {"message_id": 300, "text": "ask answer", "reply_to_message": {"message_id": 42}}}
        await _process_update(tool_context, update1)

        # Second update: reply to a notification (message_id 99, no pending Future)
        tool_context.client.extract_reply_to_message_id.return_value = 99
        tool_context.client.extract_text.return_value = "notification reply"
        update2 = {"message": {"message_id": 301, "text": "notification reply", "reply_to_message": {"message_id": 99}}}
        await _process_update(tool_context, update2)

        # Ask reply went to Future
        assert future.result() == "ask answer"
        # Notification reply went to queue
        assert len(tool_context.reply_queue) == 1
        assert tool_context.reply_queue[0].text == "notification reply"

    @pytest.mark.asyncio
    async def test_voice_reply_to_notification_transcribed_and_queued(self, tool_context):
        """Should transcribe voice reply to notification and enqueue it."""
        tool_context.pending_replies = {}

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 50
        tool_context.client.extract_text.return_value = None
        tool_context.client.extract_voice.return_value = {
            "file_id": "voice_xyz",
            "duration": 5,
        }
        tool_context.voice.transcribe_voice_message.return_value = "Transcribed reply"

        update = {
            "message": {
                "message_id": 400,
                "voice": {"file_id": "voice_xyz", "duration": 5},
                "reply_to_message": {"message_id": 50},
            }
        }

        await _process_update(tool_context, update)

        assert len(tool_context.reply_queue) == 1
        entry = tool_context.reply_queue[0]
        assert entry.text == "Transcribed reply"
        assert entry.source == "voice"
        assert entry.reply_to_message_id == 50

    @pytest.mark.asyncio
    async def test_callback_reply_to_notification_queued(self, tool_context):
        """Should enqueue callback button press when no pending Future."""
        tool_context.pending_replies = {}

        tool_context.client.extract_callback_query_id.return_value = "cq_456"
        tool_context.client.extract_reply_to_message_id.return_value = 50
        tool_context.client.extract_text.return_value = "Approve"
        tool_context.client.extract_voice.return_value = None

        update = {
            "callback_query": {
                "id": "cq_456",
                "message": {"message_id": 50},
                "data": "Approve",
            }
        }

        await _process_update(tool_context, update)

        tool_context.client.answer_callback_query.assert_awaited_once_with("cq_456")
        assert len(tool_context.reply_queue) == 1
        entry = tool_context.reply_queue[0]
        assert entry.text == "Approve"
        assert entry.source == "callback"

    @pytest.mark.asyncio
    async def test_unmatched_reply_with_already_done_future_enqueues(self, tool_context):
        """Should enqueue when Future exists but is already done."""
        future = asyncio.get_event_loop().create_future()
        future.set_result("first")
        tool_context.pending_replies[42] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 42
        tool_context.client.extract_text.return_value = "second reply"
        tool_context.client.extract_voice.return_value = None

        update = {"message": {"message_id": 500, "text": "second reply", "reply_to_message": {"message_id": 42}}}

        await _process_update(tool_context, update)

        # Should have fallen through to enqueue since Future was already done
        assert len(tool_context.reply_queue) == 1
        assert tool_context.reply_queue[0].text == "second reply"

    @pytest.mark.asyncio
    async def test_no_pending_and_no_reply_to_enqueues_with_zero_reply_to(self, tool_context):
        """Update with no reply_to and no pending should be ignored (return early)."""
        tool_context.pending_replies = {}

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = None

        update = {"message": {"message_id": 600, "text": "standalone"}}

        await _process_update(tool_context, update)

        # No pending replies and no reply_to_id means early return
        assert len(tool_context.reply_queue) == 0
