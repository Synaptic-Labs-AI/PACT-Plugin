"""
Tests for pact-plugin/telegram/routing.py

Tests cover:
1. UpdateRouter ABC: interface contract verification
2. DirectRouter: pass-through behavior, single-session operation
3. FileBasedRouter: lock acquisition, session management, polling, routing,
   inbox fan-out, offset persistence, cleanup, crash recovery
4. count_active_sessions: session counting with stale cleanup
5. Session ID generation: config.py get_or_create_session_id
6. Edge cases: corrupted files, concurrent access, lock contention
"""

import asyncio
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram.config import get_or_create_session_id
from telegram.routing import (
    COORDINATOR_DIR,
    HEARTBEAT_INTERVAL,
    MAX_INBOX_ENTRIES,
    MAX_ROUTING_TABLE_ENTRIES,
    READER_POLL_INTERVAL,
    STALE_SESSION_TTL,
    DirectRouter,
    FileBasedRouter,
    UpdateRouter,
    count_active_sessions,
    register_session,
    unregister_session,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_client():
    """Create a mocked TelegramClient for router tests."""
    client = MagicMock()
    client.get_updates = AsyncMock(return_value=[])
    client.send_message = AsyncMock(return_value={"message_id": 1})
    client.close = AsyncMock()
    client.extract_reply_to_message_id = MagicMock(return_value=None)
    client._update_offset = 0
    return client


@pytest.fixture
def coordinator_dir(tmp_path):
    """Create a temporary coordinator directory structure."""
    coord = tmp_path / "coordinator"
    coord.mkdir()
    (coord / "sessions").mkdir()
    (coord / "updates").mkdir()
    return coord


@pytest.fixture
def make_session_file(coordinator_dir):
    """Factory to create session files for testing."""

    def _make(session_id, pid=None, registered_at=None, last_heartbeat=None):
        if pid is None:
            pid = os.getpid()
        if registered_at is None:
            registered_at = time.time()
        if last_heartbeat is None:
            last_heartbeat = time.time()

        data = {
            "pid": pid,
            "project": "/test/project",
            "registered_at": registered_at,
            "last_heartbeat": last_heartbeat,
        }
        session_file = coordinator_dir / "sessions" / f"{session_id}.json"
        session_file.write_text(json.dumps(data), encoding="utf-8")
        return session_file

    return _make


# =============================================================================
# DirectRouter Tests
# =============================================================================


class TestDirectRouter:
    """Tests for DirectRouter -- zero-overhead single-session pass-through."""

    @pytest.mark.asyncio
    async def test_start_stores_session_id(self, mock_client):
        """Should store session_id on start."""
        router = DirectRouter(mock_client)
        await router.start("session-abc")
        assert router._session_id == "session-abc"

    @pytest.mark.asyncio
    async def test_get_updates_passes_through_to_client(self, mock_client):
        """Should delegate directly to client.get_updates()."""
        mock_client.get_updates.return_value = [
            {"update_id": 1, "message": {"text": "hi"}}
        ]
        router = DirectRouter(mock_client)
        await router.start("session-1")

        updates = await router.get_updates(timeout=30)

        assert len(updates) == 1
        assert updates[0]["update_id"] == 1
        mock_client.get_updates.assert_awaited_once_with(timeout=30)

    @pytest.mark.asyncio
    async def test_get_updates_returns_empty_list(self, mock_client):
        """Should return empty list when client returns no updates."""
        mock_client.get_updates.return_value = []
        router = DirectRouter(mock_client)
        await router.start("session-1")

        updates = await router.get_updates(timeout=30)

        assert updates == []

    @pytest.mark.asyncio
    async def test_register_message_is_noop(self, mock_client):
        """Should do nothing on register_message (no routing table in single-session)."""
        router = DirectRouter(mock_client)
        await router.start("session-1")

        # Should not raise
        await router.register_message(42)

    @pytest.mark.asyncio
    async def test_stop_is_noop(self, mock_client):
        """Should do nothing on stop (no coordination resources to clean)."""
        router = DirectRouter(mock_client)
        await router.start("session-1")

        # Should not raise
        await router.stop()

    @pytest.mark.asyncio
    async def test_preserves_exact_client_behavior(self, mock_client):
        """Should pass through exact client response without transformation."""
        raw_updates = [
            {"update_id": 100, "message": {"message_id": 5, "text": "hello"}},
            {"update_id": 101, "message": {"message_id": 6, "text": "world"}},
        ]
        mock_client.get_updates.return_value = raw_updates
        router = DirectRouter(mock_client)
        await router.start("session-1")

        result = await router.get_updates(timeout=10)

        assert result is raw_updates  # Same object, not a copy


# =============================================================================
# FileBasedRouter -- Lifecycle Tests
# =============================================================================


class TestFileBasedRouterLifecycle:
    """Tests for FileBasedRouter lifecycle: start, stop, directory setup."""

    @pytest.mark.asyncio
    async def test_start_creates_coordinator_dirs(self, mock_client, tmp_path):
        """Should create coordinator directory structure on start."""
        coord = tmp_path / "new_coordinator"
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coord
        )

        await router.start("sess-1")

        assert (coord / "sessions").exists()
        assert (coord / "updates").exists()
        assert router._running is True

        await router.stop()

    @pytest.mark.asyncio
    async def test_start_does_not_write_session_file(self, mock_client, coordinator_dir):
        """Session file is written by register_session(), not start()."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        await router.start("sess-1")

        # start() no longer writes session file -- register_session() does
        session_file = coordinator_dir / "sessions" / "sess-1.json"
        assert not session_file.exists()

        await router.stop()

    @pytest.mark.asyncio
    async def test_start_session_id_from_parameter(self, mock_client, coordinator_dir):
        """Should use the session_id passed to start(), not the constructor."""
        router = FileBasedRouter(
            mock_client,
            session_id="constructor-id",
            coordinator_dir=coordinator_dir,
        )

        await router.start("start-id")

        assert router._session_id == "start-id"

        await router.stop()

    @pytest.mark.asyncio
    async def test_stop_does_not_remove_session_file(self, mock_client, coordinator_dir):
        """Session file removal is handled by unregister_session(), not stop()."""
        # Pre-register the session (as server.py would)
        register_session("sess-1", coordinator_dir=coordinator_dir)

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        session_file = coordinator_dir / "sessions" / "sess-1.json"
        assert session_file.exists()

        await router.stop()

        # stop() no longer removes session file -- unregister_session() does
        assert session_file.exists()

        # Clean up via standalone function
        unregister_session("sess-1", coordinator_dir=coordinator_dir)
        assert not session_file.exists()

    @pytest.mark.asyncio
    async def test_stop_cleans_up_inbox(self, mock_client, coordinator_dir):
        """Should remove inbox file on stop."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        # Create inbox file
        inbox = coordinator_dir / "updates" / "sess-1.jsonl"
        inbox.write_text('{"update_id": 1}\n')

        await router.stop()

        assert not inbox.exists()

    @pytest.mark.asyncio
    async def test_stop_removes_routing_table_entries(
        self, mock_client, coordinator_dir
    ):
        """Should remove this session's entries from routing table on stop."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        # Write routing table with entries for this session and another
        routing_table = {"100": "sess-1", "200": "sess-2", "300": "sess-1"}
        routing_path = coordinator_dir / "routing-table.json"
        routing_path.write_text(json.dumps(routing_table))

        await router.stop()

        remaining = json.loads(routing_path.read_text())
        assert "100" not in remaining
        assert "300" not in remaining
        assert remaining["200"] == "sess-2"

    @pytest.mark.asyncio
    async def test_stop_cancels_heartbeat(self, mock_client, coordinator_dir):
        """Should cancel the heartbeat task on stop."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        assert router._heartbeat_task is not None
        assert not router._heartbeat_task.done()

        await router.stop()

        assert router._heartbeat_task.done()


# =============================================================================
# FileBasedRouter -- Lock Management Tests
# =============================================================================


class TestFileBasedRouterLock:
    """Tests for FileBasedRouter lock acquisition and release."""

    @pytest.mark.asyncio
    async def test_first_session_becomes_poller(self, mock_client, coordinator_dir):
        """Should acquire lock and become poller when no other session holds it."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        assert router.is_poller is True
        assert router._lock_fd is not None

        await router.stop()

    @pytest.mark.asyncio
    async def test_stop_releases_lock(self, mock_client, coordinator_dir):
        """Should release the lock on stop."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")
        assert router.is_poller is True

        await router.stop()

        assert router._lock_fd is None
        assert router._is_poller is False

    def test_try_acquire_lock_returns_true_when_already_held(
        self, mock_client, coordinator_dir
    ):
        """Should return True if lock is already held by this session."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        # Acquire once
        assert router._try_acquire_lock() is True
        fd = router._lock_fd

        # Second call returns True (already held)
        assert router._try_acquire_lock() is True
        assert router._lock_fd == fd  # Same fd

        router._release_lock()

    def test_lock_contention_second_process_fails(
        self, mock_client, coordinator_dir
    ):
        """Should fail to acquire lock when another fd holds it."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        # Simulate another process holding the lock
        lock_path = coordinator_dir / "poll.lock"
        external_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(external_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            result = router._try_acquire_lock()
            assert result is False
            assert router._lock_fd is None
        finally:
            fcntl.flock(external_fd, fcntl.LOCK_UN)
            os.close(external_fd)

    @pytest.mark.asyncio
    async def test_reader_promoted_to_poller_when_lock_freed(
        self, mock_client, coordinator_dir
    ):
        """Should promote reader to poller when the lock becomes available."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._write_session_file()

        # Hold the lock externally so the router starts as a reader
        lock_path = coordinator_dir / "poll.lock"
        external_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(external_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        router._is_poller = False
        router._running = True
        router._session_id = "sess-1"

        # Simulate: reader tries get_updates, lock is held -> reads inbox
        # First, write something to inbox so it returns data
        inbox = coordinator_dir / "updates" / "sess-1.jsonl"
        inbox.write_text(
            json.dumps({"update_id": 1, "message": {"text": "routed"}}) + "\n"
        )

        updates = await router.get_updates(timeout=5)
        assert router.is_poller is False
        assert len(updates) == 1

        # Now release external lock
        fcntl.flock(external_fd, fcntl.LOCK_UN)
        os.close(external_fd)

        # Next get_updates should promote to poller
        mock_client.get_updates.return_value = [
            {"update_id": 2, "message": {"text": "polled"}}
        ]
        mock_client._update_offset = 3

        updates = await router.get_updates(timeout=5)
        assert router.is_poller is True

        await router.stop()


# =============================================================================
# FileBasedRouter -- Polling and Routing Tests
# =============================================================================


class TestFileBasedRouterPolling:
    """Tests for FileBasedRouter poll_and_route behavior."""

    @pytest.mark.asyncio
    async def test_poller_gets_updates_from_client(
        self, mock_client, coordinator_dir
    ):
        """Should call client.get_updates when acting as poller."""
        mock_client.get_updates.return_value = [
            {"update_id": 1, "message": {"text": "hello"}}
        ]
        mock_client._update_offset = 2

        # Register session first (as server.py would) so routing can find it
        register_session("sess-1", coordinator_dir=coordinator_dir)

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")
        assert router.is_poller

        updates = await router.get_updates(timeout=30)

        mock_client.get_updates.assert_awaited_once_with(timeout=30)
        assert len(updates) == 1

        await router.stop()

    @pytest.mark.asyncio
    async def test_poller_returns_empty_when_no_updates(
        self, mock_client, coordinator_dir
    ):
        """Should return empty list when Telegram has no updates."""
        mock_client.get_updates.return_value = []

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        updates = await router.get_updates(timeout=30)

        assert updates == []

        await router.stop()

    @pytest.mark.asyncio
    async def test_poller_saves_offset(self, mock_client, coordinator_dir):
        """Should persist the polling offset after receiving updates."""
        mock_client.get_updates.return_value = [
            {"update_id": 10, "message": {"text": "hi"}}
        ]
        mock_client._update_offset = 11

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")
        await router.get_updates(timeout=30)

        offset_path = coordinator_dir / "offset.json"
        assert offset_path.exists()
        data = json.loads(offset_path.read_text())
        assert data["offset"] == 11

        await router.stop()

    @pytest.mark.asyncio
    async def test_poller_reads_saved_offset(self, mock_client, coordinator_dir):
        """Should read and use the saved offset from a previous poller."""
        # Write a saved offset
        offset_path = coordinator_dir / "offset.json"
        offset_path.write_text(json.dumps({"offset": 50}))

        mock_client.get_updates.return_value = []

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")
        await router.get_updates(timeout=30)

        # The router should have set the client's offset to the saved value
        assert mock_client._update_offset == 50

        await router.stop()

    @pytest.mark.asyncio
    async def test_reply_routed_to_correct_session(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should route reply to the session that sent the original message."""
        # Set up two sessions
        make_session_file("sess-1")
        make_session_file("sess-2")

        # Set up routing table: message 100 was sent by sess-2
        routing_path = coordinator_dir / "routing-table.json"
        routing_path.write_text(json.dumps({"100": "sess-2"}))

        # Update is a reply to message 100
        mock_client.extract_reply_to_message_id.return_value = 100
        mock_client.get_updates.return_value = [
            {
                "update_id": 1,
                "message": {
                    "text": "reply",
                    "reply_to_message": {"message_id": 100},
                },
            }
        ]
        mock_client._update_offset = 2

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        # Poller is sess-1, so reply to sess-2 goes to sess-2's inbox
        updates = await router.get_updates(timeout=30)

        # sess-1 should NOT get this update (it's for sess-2)
        assert len(updates) == 0

        # Check sess-2's inbox
        inbox = coordinator_dir / "updates" / "sess-2.jsonl"
        assert inbox.exists()
        lines = inbox.read_text().strip().split("\n")
        assert len(lines) == 1
        routed_update = json.loads(lines[0])
        assert routed_update["message"]["text"] == "reply"

        await router.stop()

    @pytest.mark.asyncio
    async def test_reply_routed_to_self(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should return update directly when reply is for this session."""
        make_session_file("sess-1")

        # Routing table: message 100 was sent by sess-1
        routing_path = coordinator_dir / "routing-table.json"
        routing_path.write_text(json.dumps({"100": "sess-1"}))

        mock_client.extract_reply_to_message_id.return_value = 100
        mock_client.get_updates.return_value = [
            {
                "update_id": 1,
                "message": {
                    "text": "self-reply",
                    "reply_to_message": {"message_id": 100},
                },
            }
        ]
        mock_client._update_offset = 2

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        updates = await router.get_updates(timeout=30)

        assert len(updates) == 1
        assert updates[0]["message"]["text"] == "self-reply"

        await router.stop()

    @pytest.mark.asyncio
    async def test_unrouted_message_goes_to_primary_session(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should route unrouted messages to the primary (oldest) session."""
        # sess-1 is older (primary), sess-2 is newer
        make_session_file("sess-1", registered_at=1000.0, last_heartbeat=time.time())
        make_session_file("sess-2", registered_at=2000.0, last_heartbeat=time.time())

        # No routing table entry for the reply_to_message_id
        mock_client.extract_reply_to_message_id.return_value = None
        mock_client.get_updates.return_value = [
            {"update_id": 1, "message": {"text": "new message"}}
        ]
        mock_client._update_offset = 2

        # Poller is sess-2, but primary is sess-1
        router = FileBasedRouter(
            mock_client, session_id="sess-2", coordinator_dir=coordinator_dir
        )
        await router.start("sess-2")

        updates = await router.get_updates(timeout=30)

        # sess-2 is the poller but not primary, so unrouted goes to sess-1's inbox
        assert len(updates) == 0

        inbox = coordinator_dir / "updates" / "sess-1.jsonl"
        assert inbox.exists()

        await router.stop()

    @pytest.mark.asyncio
    async def test_unrouted_message_returned_when_poller_is_primary(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should return unrouted message directly when poller is also primary."""
        make_session_file("sess-1", registered_at=1000.0, last_heartbeat=time.time())

        mock_client.extract_reply_to_message_id.return_value = None
        mock_client.get_updates.return_value = [
            {"update_id": 1, "message": {"text": "new message"}}
        ]
        mock_client._update_offset = 2

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        updates = await router.get_updates(timeout=30)

        assert len(updates) == 1
        assert updates[0]["message"]["text"] == "new message"

        await router.stop()

    @pytest.mark.asyncio
    async def test_reply_to_dead_session_falls_through(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should fall back to primary when routing target session is not active."""
        # Only sess-1 is active
        make_session_file("sess-1", registered_at=1000.0, last_heartbeat=time.time())

        # Routing table says message 100 was sent by sess-2, but sess-2 is not active
        routing_path = coordinator_dir / "routing-table.json"
        routing_path.write_text(json.dumps({"100": "sess-2"}))

        mock_client.extract_reply_to_message_id.return_value = 100
        mock_client.get_updates.return_value = [
            {
                "update_id": 1,
                "message": {
                    "text": "reply to dead session",
                    "reply_to_message": {"message_id": 100},
                },
            }
        ]
        mock_client._update_offset = 2

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        updates = await router.get_updates(timeout=30)

        # Should fall back to primary (sess-1) since sess-2 is not active
        assert len(updates) == 1

        await router.stop()


# =============================================================================
# FileBasedRouter -- Reader Behavior Tests
# =============================================================================


class TestFileBasedRouterReader:
    """Tests for FileBasedRouter reader (non-poller) behavior."""

    @pytest.mark.asyncio
    async def test_reader_reads_from_inbox(self, mock_client, coordinator_dir):
        """Should read updates from inbox file when acting as reader."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._session_id = "sess-1"
        router._is_poller = False
        router._running = True

        # Write updates to inbox
        inbox = coordinator_dir / "updates" / "sess-1.jsonl"
        updates_data = [
            {"update_id": 1, "message": {"text": "msg1"}},
            {"update_id": 2, "message": {"text": "msg2"}},
        ]
        content = "\n".join(json.dumps(u) for u in updates_data) + "\n"
        inbox.write_text(content)

        # Hold lock externally to prevent promotion
        lock_path = coordinator_dir / "poll.lock"
        external_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(external_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            updates = await router.get_updates(timeout=30)

            assert len(updates) == 2
            assert updates[0]["message"]["text"] == "msg1"
            assert updates[1]["message"]["text"] == "msg2"

            # Inbox should be cleared after reading
            assert not inbox.exists()
        finally:
            fcntl.flock(external_fd, fcntl.LOCK_UN)
            os.close(external_fd)

    @pytest.mark.asyncio
    async def test_reader_sleeps_on_empty_inbox(self, mock_client, coordinator_dir):
        """Should sleep READER_POLL_INTERVAL when inbox is empty."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._session_id = "sess-1"
        router._is_poller = False
        router._running = True

        # Hold lock externally
        lock_path = coordinator_dir / "poll.lock"
        external_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(external_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            with patch("telegram.routing.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                updates = await router.get_updates(timeout=30)

            assert updates == []
            mock_sleep.assert_awaited_once_with(READER_POLL_INTERVAL)
        finally:
            fcntl.flock(external_fd, fcntl.LOCK_UN)
            os.close(external_fd)

    def test_read_inbox_handles_empty_file(self, mock_client, coordinator_dir):
        """Should return empty list for empty inbox file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._session_id = "sess-1"

        inbox = coordinator_dir / "updates" / "sess-1.jsonl"
        inbox.write_text("")

        updates = router._read_inbox()
        assert updates == []

    def test_read_inbox_skips_malformed_lines(self, mock_client, coordinator_dir):
        """Should skip malformed JSON lines in inbox."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._session_id = "sess-1"

        inbox = coordinator_dir / "updates" / "sess-1.jsonl"
        inbox.write_text(
            '{"update_id":1}\nBAD_JSON\n{"update_id":2}\n'
        )

        updates = router._read_inbox()
        assert len(updates) == 2
        assert updates[0]["update_id"] == 1
        assert updates[1]["update_id"] == 2

    def test_read_inbox_returns_empty_when_no_file(
        self, mock_client, coordinator_dir
    ):
        """Should return empty list when inbox file doesn't exist."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._session_id = "sess-1"

        updates = router._read_inbox()
        assert updates == []


# =============================================================================
# FileBasedRouter -- Register Message Tests
# =============================================================================


class TestFileBasedRouterRegisterMessage:
    """Tests for routing table management via register_message."""

    @pytest.mark.asyncio
    async def test_register_message_adds_to_routing_table(
        self, mock_client, coordinator_dir
    ):
        """Should add message_id -> session_id mapping to routing table."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        await router.register_message(42)

        routing_path = coordinator_dir / "routing-table.json"
        assert routing_path.exists()
        table = json.loads(routing_path.read_text())
        assert table["42"] == "sess-1"

        await router.stop()

    @pytest.mark.asyncio
    async def test_register_message_bounds_table_size(
        self, mock_client, coordinator_dir
    ):
        """Should evict oldest entries when table exceeds MAX_ROUTING_TABLE_ENTRIES."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        # Register more than MAX entries
        for i in range(MAX_ROUTING_TABLE_ENTRIES + 10):
            await router.register_message(i)

        routing_path = coordinator_dir / "routing-table.json"
        table = json.loads(routing_path.read_text())
        assert len(table) == MAX_ROUTING_TABLE_ENTRIES

        # Oldest entries should have been evicted
        assert "0" not in table
        assert "9" not in table
        # Newest entries should remain
        assert str(MAX_ROUTING_TABLE_ENTRIES + 9) in table

        await router.stop()

    @pytest.mark.asyncio
    async def test_register_message_preserves_other_sessions(
        self, mock_client, coordinator_dir
    ):
        """Should not overwrite entries from other sessions."""
        # Pre-populate routing table with another session's entry
        routing_path = coordinator_dir / "routing-table.json"
        routing_path.write_text(json.dumps({"100": "other-session"}))

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        await router.register_message(200)

        table = json.loads(routing_path.read_text())
        assert table["100"] == "other-session"
        assert table["200"] == "sess-1"

        await router.stop()


# =============================================================================
# FileBasedRouter -- Session Management Tests
# =============================================================================


class TestFileBasedRouterSessions:
    """Tests for session file management and stale session cleanup."""

    def test_cleanup_removes_dead_pid_sessions(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should remove session files for processes that no longer exist."""
        # Create a session file with a non-existent PID
        make_session_file("dead-sess", pid=99999999, last_heartbeat=time.time())

        router = FileBasedRouter(
            mock_client, session_id="my-sess", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        router._cleanup_stale_sessions()

        dead_file = coordinator_dir / "sessions" / "dead-sess.json"
        assert not dead_file.exists()

    def test_cleanup_removes_stale_heartbeat_sessions(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should remove sessions with heartbeat older than TTL."""
        stale_time = time.time() - STALE_SESSION_TTL - 10
        make_session_file(
            "stale-sess",
            pid=os.getpid(),  # Our PID (alive), but stale heartbeat
            last_heartbeat=stale_time,
        )

        router = FileBasedRouter(
            mock_client, session_id="my-sess", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        router._cleanup_stale_sessions()

        stale_file = coordinator_dir / "sessions" / "stale-sess.json"
        assert not stale_file.exists()

    def test_cleanup_preserves_live_sessions(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should keep session files for alive processes with fresh heartbeat."""
        make_session_file("live-sess", pid=os.getpid(), last_heartbeat=time.time())

        router = FileBasedRouter(
            mock_client, session_id="my-sess", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        router._cleanup_stale_sessions()

        live_file = coordinator_dir / "sessions" / "live-sess.json"
        assert live_file.exists()

    def test_cleanup_handles_corrupted_session_file(
        self, mock_client, coordinator_dir
    ):
        """Should remove session files with corrupted JSON."""
        corrupted = coordinator_dir / "sessions" / "bad-sess.json"
        corrupted.write_text("NOT JSON {{{")

        router = FileBasedRouter(
            mock_client, session_id="my-sess", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        router._cleanup_stale_sessions()

        assert not corrupted.exists()

    def test_cleanup_also_removes_dead_session_inbox(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should also clean up inbox files for stale sessions."""
        make_session_file("dead-sess", pid=99999999, last_heartbeat=time.time())

        inbox = coordinator_dir / "updates" / "dead-sess.jsonl"
        inbox.write_text('{"update_id": 1}\n')

        router = FileBasedRouter(
            mock_client, session_id="my-sess", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        router._cleanup_stale_sessions()

        assert not inbox.exists()

    def test_get_active_session_ids(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should return set of all session IDs with session files."""
        make_session_file("sess-a")
        make_session_file("sess-b")
        make_session_file("sess-c")

        router = FileBasedRouter(
            mock_client, session_id="unused", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        sessions = router._get_active_session_ids()

        assert sessions == {"sess-a", "sess-b", "sess-c"}

    def test_get_active_session_ids_empty_dir(
        self, mock_client, coordinator_dir
    ):
        """Should return empty set when sessions dir exists but is empty."""
        router = FileBasedRouter(
            mock_client, session_id="solo", coordinator_dir=coordinator_dir
        )

        sessions = router._get_active_session_ids()

        assert sessions == set()

    def test_get_active_session_ids_no_sessions_dir(
        self, mock_client, tmp_path
    ):
        """Should return set with own session ID when sessions dir doesn't exist."""
        coord = tmp_path / "no_sessions_coord"
        # Don't create the sessions subdirectory
        router = FileBasedRouter(
            mock_client, session_id="solo", coordinator_dir=coord
        )

        sessions = router._get_active_session_ids()

        assert sessions == {"solo"}

    def test_get_primary_session_returns_oldest(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should identify the oldest registered session as primary."""
        make_session_file("old-sess", registered_at=1000.0, last_heartbeat=time.time())
        make_session_file("new-sess", registered_at=2000.0, last_heartbeat=time.time())

        router = FileBasedRouter(
            mock_client, session_id="new-sess", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        primary = router._get_primary_session({"old-sess", "new-sess"})

        assert primary == "old-sess"

    def test_get_primary_session_empty_set(self, mock_client, coordinator_dir):
        """Should return None for empty session set."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        primary = router._get_primary_session(set())

        assert primary is None

    def test_get_primary_session_handles_missing_file(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should skip sessions with missing files and return valid primary."""
        make_session_file("real-sess", registered_at=1000.0, last_heartbeat=time.time())
        # ghost-sess has no file

        router = FileBasedRouter(
            mock_client, session_id="real-sess", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        primary = router._get_primary_session({"real-sess", "ghost-sess"})

        assert primary == "real-sess"


# =============================================================================
# FileBasedRouter -- Heartbeat Tests
# =============================================================================


class TestFileBasedRouterHeartbeat:
    """Tests for session heartbeat mechanism."""

    def test_update_heartbeat_refreshes_timestamp(
        self, mock_client, coordinator_dir
    ):
        """Should update the last_heartbeat in session file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._session_id = "sess-1"

        # Write initial session file with old heartbeat
        initial_time = time.time() - 100
        session_file = coordinator_dir / "sessions" / "sess-1.json"
        session_file.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "project": "/test",
                    "registered_at": initial_time,
                    "last_heartbeat": initial_time,
                }
            )
        )

        router._update_heartbeat()

        data = json.loads(session_file.read_text())
        assert data["last_heartbeat"] > initial_time

    def test_update_heartbeat_recreates_corrupted_file(
        self, mock_client, coordinator_dir
    ):
        """Should re-create session file if it's corrupted."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._session_id = "sess-1"

        # Write corrupted session file
        session_file = coordinator_dir / "sessions" / "sess-1.json"
        session_file.write_text("NOT JSON")

        router._update_heartbeat()

        # Should have re-created the file
        data = json.loads(session_file.read_text())
        assert data["pid"] == os.getpid()
        assert "last_heartbeat" in data


# =============================================================================
# FileBasedRouter -- Offset Management Tests
# =============================================================================


class TestFileBasedRouterOffset:
    """Tests for shared polling offset persistence."""

    def test_read_offset_returns_zero_when_no_file(
        self, mock_client, coordinator_dir
    ):
        """Should return 0 when offset file doesn't exist."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        assert router._read_offset() == 0

    def test_read_offset_returns_saved_value(self, mock_client, coordinator_dir):
        """Should return the offset value from the file."""
        offset_path = coordinator_dir / "offset.json"
        offset_path.write_text(json.dumps({"offset": 42}))

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        assert router._read_offset() == 42

    def test_read_offset_handles_corrupted_file(
        self, mock_client, coordinator_dir
    ):
        """Should return 0 when offset file is corrupted."""
        offset_path = coordinator_dir / "offset.json"
        offset_path.write_text("NOT JSON")

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        assert router._read_offset() == 0

    def test_write_offset_persists_value(self, mock_client, coordinator_dir):
        """Should write offset to file atomically."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        router._write_offset(99)

        offset_path = coordinator_dir / "offset.json"
        data = json.loads(offset_path.read_text())
        assert data["offset"] == 99

    @pytest.mark.asyncio
    async def test_offset_survives_poller_transition(
        self, mock_client, coordinator_dir
    ):
        """Should preserve offset across poller transitions (crash recovery)."""
        # First poller writes offset
        router1 = FileBasedRouter(
            mock_client, session_id="poller-1", coordinator_dir=coordinator_dir
        )
        router1._ensure_coordinator_dirs()
        router1._write_offset(100)

        # Second poller reads saved offset
        router2 = FileBasedRouter(
            mock_client, session_id="poller-2", coordinator_dir=coordinator_dir
        )

        assert router2._read_offset() == 100


# =============================================================================
# FileBasedRouter -- Routing Table Tests
# =============================================================================


class TestFileBasedRouterRoutingTable:
    """Tests for shared routing table I/O."""

    def test_read_routing_table_returns_empty_when_no_file(
        self, mock_client, coordinator_dir
    ):
        """Should return empty dict when routing table file doesn't exist."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        assert router._read_routing_table() == {}

    def test_read_routing_table_returns_saved_data(
        self, mock_client, coordinator_dir
    ):
        """Should return the routing table contents from file."""
        routing_path = coordinator_dir / "routing-table.json"
        routing_path.write_text(json.dumps({"100": "sess-a", "200": "sess-b"}))

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        table = router._read_routing_table()
        assert table == {"100": "sess-a", "200": "sess-b"}

    def test_read_routing_table_handles_corruption(
        self, mock_client, coordinator_dir
    ):
        """Should return empty dict when routing table is corrupted."""
        routing_path = coordinator_dir / "routing-table.json"
        routing_path.write_text("NOT JSON")

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        assert router._read_routing_table() == {}

    def test_read_routing_table_handles_non_dict(
        self, mock_client, coordinator_dir
    ):
        """Should return empty dict when routing table contains non-dict JSON."""
        routing_path = coordinator_dir / "routing-table.json"
        routing_path.write_text(json.dumps([1, 2, 3]))

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        assert router._read_routing_table() == {}


# =============================================================================
# FileBasedRouter -- Inbox Append Tests
# =============================================================================


class TestFileBasedRouterInbox:
    """Tests for inbox file management."""

    def test_append_to_inbox_creates_file(self, mock_client, coordinator_dir):
        """Should create inbox file and append update."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        update = {"update_id": 1, "message": {"text": "hello"}}
        router._append_to_inbox("target-sess", update)

        inbox = coordinator_dir / "updates" / "target-sess.jsonl"
        assert inbox.exists()
        lines = inbox.read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0]) == update

    def test_append_to_inbox_appends_multiple(self, mock_client, coordinator_dir):
        """Should append multiple updates to same inbox file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        for i in range(3):
            router._append_to_inbox("target", {"update_id": i})

        inbox = coordinator_dir / "updates" / "target.jsonl"
        lines = inbox.read_text().strip().split("\n")
        assert len(lines) == 3


# =============================================================================
# FileBasedRouter -- Atomic Write Tests
# =============================================================================


class TestAtomicWrite:
    """Tests for _atomic_write_json helper."""

    def test_atomic_write_creates_file(self, tmp_path):
        """Should create the file with correct JSON content."""
        target = tmp_path / "test.json"
        FileBasedRouter._atomic_write_json(target, {"key": "value"})

        assert target.exists()
        data = json.loads(target.read_text())
        assert data == {"key": "value"}

    def test_atomic_write_overwrites_existing(self, tmp_path):
        """Should overwrite existing file content."""
        target = tmp_path / "test.json"
        target.write_text(json.dumps({"old": True}))

        FileBasedRouter._atomic_write_json(target, {"new": True})

        data = json.loads(target.read_text())
        assert data == {"new": True}

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        """Should create parent directories if they don't exist."""
        target = tmp_path / "nested" / "dir" / "test.json"
        FileBasedRouter._atomic_write_json(target, {"nested": True})

        assert target.exists()


# =============================================================================
# count_active_sessions Tests
# =============================================================================


class TestCountActiveSessions:
    """Tests for count_active_sessions -- global session counting."""

    def test_returns_zero_when_no_coordinator_dir(self, tmp_path):
        """Should return 0 when coordinator directory doesn't exist."""
        nonexistent = tmp_path / "nonexistent"
        count = count_active_sessions(coordinator_dir=nonexistent)
        assert count == 0

    def test_returns_zero_when_sessions_dir_empty(self, coordinator_dir):
        """Should return 0 when sessions directory is empty."""
        count = count_active_sessions(coordinator_dir=coordinator_dir)
        assert count == 0

    def test_counts_live_sessions(self, coordinator_dir, make_session_file):
        """Should count session files with alive PIDs and fresh heartbeats."""
        make_session_file("sess-1", pid=os.getpid(), last_heartbeat=time.time())
        make_session_file("sess-2", pid=os.getpid(), last_heartbeat=time.time())

        count = count_active_sessions(coordinator_dir=coordinator_dir)
        assert count == 2

    def test_excludes_dead_pid_sessions(self, coordinator_dir, make_session_file):
        """Should not count sessions with dead PIDs."""
        make_session_file("live", pid=os.getpid(), last_heartbeat=time.time())
        make_session_file("dead", pid=99999999, last_heartbeat=time.time())

        count = count_active_sessions(coordinator_dir=coordinator_dir)
        assert count == 1

    def test_excludes_stale_sessions(self, coordinator_dir, make_session_file):
        """Should not count sessions with heartbeat older than TTL."""
        make_session_file("fresh", pid=os.getpid(), last_heartbeat=time.time())
        stale_time = time.time() - STALE_SESSION_TTL - 10
        make_session_file("stale", pid=os.getpid(), last_heartbeat=stale_time)

        count = count_active_sessions(coordinator_dir=coordinator_dir)
        assert count == 1

    def test_cleans_up_stale_files_during_count(
        self, coordinator_dir, make_session_file
    ):
        """Should remove stale session files during counting."""
        make_session_file("dead", pid=99999999, last_heartbeat=time.time())

        count_active_sessions(coordinator_dir=coordinator_dir)

        stale_file = coordinator_dir / "sessions" / "dead.json"
        assert not stale_file.exists()

    def test_handles_corrupted_session_files(self, coordinator_dir):
        """Should handle and clean up corrupted session files."""
        corrupted = coordinator_dir / "sessions" / "bad.json"
        corrupted.write_text("NOT JSON")

        count = count_active_sessions(coordinator_dir=coordinator_dir)
        assert count == 0
        assert not corrupted.exists()


# =============================================================================
# Session ID Generation Tests
# =============================================================================


class TestSessionIdGeneration:
    """Tests for session ID generation in config.py."""

    def test_generates_uuid(self):
        """Should return a UUID string."""
        sid = get_or_create_session_id()
        assert isinstance(sid, str)
        assert len(sid) == 36  # UUID format: 8-4-4-4-12
        assert sid.count("-") == 4

    def test_generates_unique_ids(self):
        """Should generate different UUIDs on each call."""
        ids = {get_or_create_session_id() for _ in range(10)}
        assert len(ids) == 10

    def test_accepts_path_parameter_without_error(self):
        """Should accept session_id_path parameter for test compatibility."""
        sid = get_or_create_session_id(session_id_path=Path("/tmp/test"))
        assert isinstance(sid, str)
        assert len(sid) == 36


# =============================================================================
# FileBasedRouter -- Route Update Logic Tests
# =============================================================================


class TestRouteUpdateLogic:
    """Tests for _route_update method -- update routing decisions."""

    def test_routes_by_reply_to_message_id(self, mock_client, coordinator_dir):
        """Should route update to session that owns the replied-to message."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._session_id = "sess-1"

        mock_client.extract_reply_to_message_id.return_value = 100

        update = {
            "message": {
                "text": "reply",
                "reply_to_message": {"message_id": 100},
            }
        }
        routing_table = {"100": "sess-target"}
        active_sessions = {"sess-1", "sess-target"}

        target = router._route_update(update, routing_table, active_sessions, "sess-1")

        assert target == "sess-target"

    def test_falls_back_to_primary_when_no_routing_entry(
        self, mock_client, coordinator_dir
    ):
        """Should route to primary session when reply_to not in routing table."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._session_id = "sess-1"

        mock_client.extract_reply_to_message_id.return_value = 999

        update = {
            "message": {
                "text": "reply to untracked",
                "reply_to_message": {"message_id": 999},
            }
        }
        routing_table = {}
        active_sessions = {"sess-1", "sess-2"}

        target = router._route_update(
            update, routing_table, active_sessions, "sess-1"
        )

        assert target == "sess-1"  # Primary session

    def test_falls_back_to_primary_for_no_reply_to(
        self, mock_client, coordinator_dir
    ):
        """Should route to primary when update has no reply_to_message_id."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._session_id = "sess-1"

        mock_client.extract_reply_to_message_id.return_value = None

        update = {"message": {"text": "new message"}}
        routing_table = {}
        active_sessions = {"sess-1"}

        target = router._route_update(
            update, routing_table, active_sessions, "sess-1"
        )

        assert target == "sess-1"

    def test_ignores_routing_to_inactive_session(
        self, mock_client, coordinator_dir
    ):
        """Should fall back to primary when target session is no longer active."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._session_id = "sess-1"

        mock_client.extract_reply_to_message_id.return_value = 100

        update = {
            "message": {
                "text": "reply",
                "reply_to_message": {"message_id": 100},
            }
        }
        routing_table = {"100": "dead-session"}
        active_sessions = {"sess-1"}  # dead-session is not in active set

        target = router._route_update(
            update, routing_table, active_sessions, "sess-1"
        )

        assert target == "sess-1"  # Falls back to primary


# =============================================================================
# Single-Session Fast-Path Tests
# =============================================================================


class TestSingleSessionFastPath:
    """Tests verifying the single-session fast-path optimization."""

    def test_count_zero_when_no_sessions(self, coordinator_dir):
        """count_active_sessions returns 0 when no session files exist."""
        count = count_active_sessions(coordinator_dir=coordinator_dir)
        assert count == 0

    def test_direct_router_has_no_coordination_overhead(self, mock_client):
        """DirectRouter should not create any files or locks."""
        router = DirectRouter(mock_client)
        # No filesystem interaction at all
        assert not hasattr(router, "_lock_fd") or router.__dict__.get("_lock_fd") is None

    @pytest.mark.asyncio
    async def test_single_session_uses_direct_router_behavior(self, mock_client):
        """In single-session mode, behavior should be identical to pre-routing."""
        mock_client.get_updates.return_value = [
            {"update_id": 1, "message": {"text": "test"}}
        ]

        router = DirectRouter(mock_client)
        await router.start("solo-session")

        updates = await router.get_updates(timeout=30)

        assert len(updates) == 1
        mock_client.get_updates.assert_awaited_once_with(timeout=30)

        # register_message and stop are no-ops
        await router.register_message(42)
        await router.stop()


# =============================================================================
# FileBasedRouter -- Directory Permissions Tests
# =============================================================================


class TestFileBasedRouterPermissions:
    """Tests for directory permission setting."""

    def test_coordinator_dirs_created_with_700(self, mock_client, tmp_path):
        """Should set 700 permissions on coordinator directories."""
        coord = tmp_path / "new_coord"
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coord
        )

        router._ensure_coordinator_dirs()

        assert (coord / "sessions").exists()
        assert (coord / "updates").exists()
        # Check permissions (700 = rwx------)
        assert oct(coord.stat().st_mode)[-3:] == "700"
        assert oct((coord / "sessions").stat().st_mode)[-3:] == "700"
        assert oct((coord / "updates").stat().st_mode)[-3:] == "700"


# =============================================================================
# FileBasedRouter -- Path Property Tests
# =============================================================================


class TestFileBasedRouterPaths:
    """Tests for path property methods."""

    def test_session_file_default(self, mock_client, coordinator_dir):
        """Should return path for own session file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._session_id = "sess-1"

        expected = coordinator_dir / "sessions" / "sess-1.json"
        assert router._session_file() == expected

    def test_session_file_custom_sid(self, mock_client, coordinator_dir):
        """Should return path for specified session file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        expected = coordinator_dir / "sessions" / "other.json"
        assert router._session_file("other") == expected

    def test_inbox_path_default(self, mock_client, coordinator_dir):
        """Should return path for own inbox file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._session_id = "sess-1"

        expected = coordinator_dir / "updates" / "sess-1.jsonl"
        assert router._inbox_path() == expected

    def test_inbox_path_custom_sid(self, mock_client, coordinator_dir):
        """Should return path for specified session inbox."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        expected = coordinator_dir / "updates" / "other.jsonl"
        assert router._inbox_path("other") == expected

    def test_unrouted_inbox_path(self, mock_client, coordinator_dir):
        """Should return path for unrouted inbox file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        expected = coordinator_dir / "updates" / "_unrouted.jsonl"
        assert router._unrouted_inbox_path == expected

    def test_lock_path(self, mock_client, coordinator_dir):
        """Should return path for lock file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        expected = coordinator_dir / "poll.lock"
        assert router._lock_path == expected

    def test_offset_path(self, mock_client, coordinator_dir):
        """Should return path for offset file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        expected = coordinator_dir / "offset.json"
        assert router._offset_path == expected

    def test_routing_table_path(self, mock_client, coordinator_dir):
        """Should return path for routing table file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        expected = coordinator_dir / "routing-table.json"
        assert router._routing_table_path == expected
