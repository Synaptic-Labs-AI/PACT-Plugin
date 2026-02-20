"""
Location: pact-plugin/telegram/routing.py
Summary: Update routing abstraction for multi-session Telegram polling coordination.
Used by: server.py (selects and manages the active router), tools.py (registers
         sent message_ids for routing).

Defines the UpdateRouter interface and two implementations:
- DirectRouter: Zero-overhead wrapper around TelegramClient.get_updates() for
  single-session operation (current behavior, no coordination).
- FileBasedRouter: File-lock-based leader election + shared update store for
  multi-session coordination. One session holds the polling lock and fans out
  updates to per-session inboxes; other sessions read from their inbox.

Design decisions:
- UpdateRouter is an ABC so future implementations (e.g., CoordinatorRouter
  via IPC daemon) can be added without changing server.py.
- DirectRouter is deliberately a thin pass-through to preserve exact current
  behavior and ensure existing tests pass unchanged.
- FileBasedRouter uses stdlib only (fcntl, json, os, uuid, asyncio, time)
  for zero additional dependencies.
- Atomic file writes (tmpfile + os.rename) prevent corruption from concurrent
  access or crashes mid-write.
- Session files use PID-based liveness checks + TTL for stale session cleanup.
- Coordinator directory created lazily on first multi-session detection.

File layout (~/.claude/pact-telegram/coordinator/):
    poll.lock           - flock()-based exclusive lock
    offset.json         - Shared polling offset (survives crashes)
    routing-table.json  - {message_id: session_id, ...}
    updates/            - Per-session update inboxes
      <session_id>.jsonl  - Queued updates for this session
      _unrouted.jsonl     - Updates that couldn't be routed
    sessions/           - Session registry
      <session_id>.json   - {pid, project, registered_at, last_heartbeat}
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from telegram.telegram_client import TelegramClient

logger = logging.getLogger("pact-telegram.routing")

# Coordinator base directory
COORDINATOR_DIR = Path.home() / ".claude" / "pact-telegram" / "coordinator"

# Session heartbeat interval (seconds)
HEARTBEAT_INTERVAL = 30

# Stale session TTL: sessions with no heartbeat for this long are considered dead
STALE_SESSION_TTL = 120  # 2 minutes

# Reader poll interval: how often non-poller sessions check their inbox (seconds)
READER_POLL_INTERVAL = 3

# Maximum routing table entries (bounded to prevent unbounded growth)
MAX_ROUTING_TABLE_ENTRIES = 200

# Maximum inbox file entries before rotation
MAX_INBOX_ENTRIES = 1000


class UpdateRouter(ABC):
    """
    Abstract interface for Telegram update routing.

    Implementations determine how updates from Telegram's getUpdates API
    are distributed to one or more MCP server sessions sharing the same
    bot token.
    """

    @abstractmethod
    async def start(self, session_id: str) -> None:
        """
        Initialize the router for the given session.

        Called during server lifespan startup after configuration is loaded.

        Args:
            session_id: Unique identifier for this MCP server session.
        """
        ...

    @abstractmethod
    async def get_updates(self, timeout: int) -> list[dict]:
        """
        Retrieve new Telegram updates for this session.

        For DirectRouter, this calls client.get_updates() directly.
        For FileBasedRouter, the poller session calls getUpdates and
        fans out; reader sessions read from their inbox file.

        Args:
            timeout: Long-polling timeout in seconds (used by poller only).

        Returns:
            List of Telegram update objects for this session.
        """
        ...

    @abstractmethod
    async def register_message(self, message_id: int) -> None:
        """
        Register a sent message_id in the routing table.

        Called after telegram_notify or telegram_ask sends a message,
        so replies to that message can be routed back to this session.

        Args:
            message_id: The Telegram message_id of the sent message.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """
        Shut down the router and clean up resources.

        Called during server lifespan shutdown. Implementations should
        release locks, remove session files, and cancel background tasks.
        """
        ...


class DirectRouter(UpdateRouter):
    """
    Zero-overhead pass-through router for single-session operation.

    Wraps TelegramClient.get_updates() directly with no coordination
    overhead. This preserves exact current behavior and is the default
    when only one session is active.
    """

    def __init__(self, client: TelegramClient) -> None:
        self._client = client
        self._session_id: str | None = None

    async def start(self, session_id: str) -> None:
        """Store session_id for identity. No coordination setup needed."""
        self._session_id = session_id
        logger.info("DirectRouter started (session=%s)", session_id)

    async def get_updates(self, timeout: int) -> list[dict]:
        """Pass through to TelegramClient.get_updates()."""
        return await self._client.get_updates(timeout=timeout)

    async def register_message(self, message_id: int) -> None:
        """No-op for single-session mode. No routing table needed."""
        pass

    async def stop(self) -> None:
        """No-op. No coordination resources to clean up."""
        logger.info("DirectRouter stopped")


class FileBasedRouter(UpdateRouter):
    """
    File-based multi-session polling coordinator.

    Uses flock()-based leader election to ensure only one session polls
    Telegram at a time. The poller fans out updates to per-session inbox
    files based on a routing table (reply_to_message_id -> session_id).

    Non-poller sessions periodically read from their inbox file to get
    updates routed to them.

    Crash recovery:
    - flock() is automatically released when a process exits
    - Next session to poll acquires the lock and becomes the new poller
    - offset.json persists the offset across poller transitions
    - Stale session files detected via PID check + TTL

    Args:
        client: TelegramClient instance for making API calls.
        session_id: Unique identifier for this session (from config.py).
        coordinator_dir: Override for coordinator directory (testing).
    """

    def __init__(
        self,
        client: TelegramClient,
        session_id: str | None = None,
        coordinator_dir: Path | None = None,
    ) -> None:
        self._client = client
        self._session_id: str = session_id or str(uuid.uuid4())
        self._coordinator_dir = coordinator_dir or COORDINATOR_DIR
        self._lock_fd: int | None = None
        self._is_poller: bool = False
        self._heartbeat_task: asyncio.Task | None = None
        self._running: bool = False

    @property
    def is_poller(self) -> bool:
        """Whether this session currently holds the polling lock."""
        return self._is_poller

    # -- Directory / file paths --

    @property
    def _sessions_dir(self) -> Path:
        return self._coordinator_dir / "sessions"

    @property
    def _updates_dir(self) -> Path:
        return self._coordinator_dir / "updates"

    @property
    def _lock_path(self) -> Path:
        return self._coordinator_dir / "poll.lock"

    @property
    def _offset_path(self) -> Path:
        return self._coordinator_dir / "offset.json"

    @property
    def _routing_table_path(self) -> Path:
        return self._coordinator_dir / "routing-table.json"

    def _session_file(self, sid: str | None = None) -> Path:
        return self._sessions_dir / f"{sid or self._session_id}.json"

    def _inbox_path(self, sid: str | None = None) -> Path:
        return self._updates_dir / f"{sid or self._session_id}.jsonl"

    @property
    def _unrouted_inbox_path(self) -> Path:
        return self._updates_dir / "_unrouted.jsonl"

    # -- Lifecycle --

    async def start(self, session_id: str) -> None:
        """
        Initialize multi-session coordination.

        Creates coordinator directory structure, attempts to acquire the
        polling lock, and starts heartbeat. Session registration is handled
        by the standalone register_session() function called before router
        selection in server.py.

        Args:
            session_id: Unique identifier for this session.
        """
        self._session_id = session_id
        self._running = True

        # Ensure directory structure (updates/ dir may not exist yet)
        self._ensure_coordinator_dirs()

        # Clean up stale sessions (session already registered by register_session())
        self._cleanup_stale_sessions()

        # Try to acquire the polling lock
        self._is_poller = self._try_acquire_lock()

        if self._is_poller:
            logger.info(
                "FileBasedRouter started as POLLER (session=%s)", session_id
            )
        else:
            logger.info(
                "FileBasedRouter started as READER (session=%s)", session_id
            )

        # Start heartbeat task
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="session-heartbeat",
        )

    async def get_updates(self, timeout: int) -> list[dict]:
        """
        Get updates for this session.

        If this session is the poller: call getUpdates, route updates
        to session inboxes, and return this session's updates.

        If this session is a reader: read from the inbox file.

        Args:
            timeout: Long-polling timeout in seconds.

        Returns:
            List of Telegram update objects for this session.
        """
        if self._is_poller:
            return await self._poll_and_route(timeout)
        else:
            # Try to become poller if the current poller died
            if self._try_acquire_lock():
                self._is_poller = True
                logger.info(
                    "Session %s promoted to POLLER (previous poller released lock)",
                    self._session_id,
                )
                return await self._poll_and_route(timeout)

            # Read from inbox as a reader
            updates = self._read_inbox()
            if not updates:
                # Sleep to avoid tight-loop when inbox is empty
                await asyncio.sleep(READER_POLL_INTERVAL)
            return updates

    async def register_message(self, message_id: int) -> None:
        """
        Register a sent message_id in the shared routing table.

        Maps message_id -> session_id so that replies to this message
        will be routed to this session.

        Args:
            message_id: The Telegram message_id of the sent message.
        """
        table = self._read_routing_table()
        table[str(message_id)] = self._session_id

        # Bound the table size
        if len(table) > MAX_ROUTING_TABLE_ENTRIES:
            # Remove oldest entries (dict preserves insertion order in Python 3.7+)
            excess = len(table) - MAX_ROUTING_TABLE_ENTRIES
            keys_to_remove = list(table.keys())[:excess]
            for key in keys_to_remove:
                del table[key]

        self._atomic_write_json(self._routing_table_path, table)
        logger.debug(
            "Registered message_id %d for session %s",
            message_id,
            self._session_id,
        )

    async def stop(self) -> None:
        """
        Shut down the router.

        Cancels heartbeat, releases lock, and cleans up inbox file.
        Session file removal is handled by the standalone unregister_session()
        function called in server.py's finally block.
        """
        self._running = False

        # Cancel heartbeat
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Release lock
        self._release_lock()

        # Clean up inbox file
        inbox = self._inbox_path()
        try:
            inbox.unlink(missing_ok=True)
        except OSError:
            pass

        # Remove our entries from routing table
        try:
            table = self._read_routing_table()
            table = {
                k: v for k, v in table.items() if v != self._session_id
            }
            self._atomic_write_json(self._routing_table_path, table)
        except Exception:
            pass

        logger.info("FileBasedRouter stopped (session=%s)", self._session_id)

    # -- Polling and routing --

    async def _poll_and_route(self, timeout: int) -> list[dict]:
        """
        Poll Telegram for updates and route them to session inboxes.

        Reads the shared offset, calls getUpdates, matches each update
        against the routing table, and writes to per-session inboxes.

        Args:
            timeout: Long-polling timeout in seconds.

        Returns:
            Updates belonging to this session.
        """
        # Read shared offset and set it on the client
        saved_offset = self._read_offset()
        if saved_offset > 0:
            self._client._update_offset = saved_offset

        # Call Telegram API
        updates = await self._client.get_updates(timeout=timeout)

        if not updates:
            return []

        # Save the new offset
        self._write_offset(self._client._update_offset)

        # Route each update
        my_updates: list[dict] = []
        routing_table = self._read_routing_table()
        active_sessions = self._get_active_session_ids()
        primary_session = self._get_primary_session(active_sessions)

        for update in updates:
            target_session = self._route_update(
                update, routing_table, active_sessions, primary_session
            )

            if target_session == self._session_id:
                my_updates.append(update)
            elif target_session is not None:
                self._append_to_inbox(target_session, update)
            else:
                # Unrouted: goes to primary session
                if primary_session == self._session_id:
                    my_updates.append(update)
                elif primary_session is not None:
                    self._append_to_inbox(primary_session, update)

        return my_updates

    def _route_update(
        self,
        update: dict,
        routing_table: dict[str, str],
        active_sessions: set[str],
        primary_session: str | None,
    ) -> str | None:
        """
        Determine which session should receive an update.

        Routes by reply_to_message_id lookup in the routing table.
        Falls back to primary session for unrouted messages.

        Args:
            update: A Telegram update object.
            routing_table: {message_id_str: session_id} mapping.
            active_sessions: Set of currently active session IDs.
            primary_session: The oldest active session (primary).

        Returns:
            The target session_id, or None for unrouted.
        """
        reply_to_id = self._client.extract_reply_to_message_id(update)

        if reply_to_id is not None:
            target = routing_table.get(str(reply_to_id))
            if target and target in active_sessions:
                return target

        # Unrouted: assign to primary session
        return primary_session

    # -- File I/O helpers --

    def _ensure_coordinator_dirs(self) -> None:
        """Create the coordinator directory structure with secure permissions."""
        for d in [self._coordinator_dir, self._sessions_dir, self._updates_dir]:
            d.mkdir(parents=True, exist_ok=True)
            try:
                d.chmod(0o700)
            except OSError:
                pass

    def _write_session_file(self) -> None:
        """Write this session's registration file."""
        data = {
            "pid": os.getpid(),
            "project": os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()),
            "registered_at": time.time(),
            "last_heartbeat": time.time(),
        }
        self._atomic_write_json(self._session_file(), data)

    def _update_heartbeat(self) -> None:
        """Update this session's heartbeat timestamp."""
        session_file = self._session_file()
        try:
            if session_file.exists():
                data = json.loads(session_file.read_text(encoding="utf-8"))
                data["last_heartbeat"] = time.time()
                self._atomic_write_json(session_file, data)
        except (json.JSONDecodeError, OSError):
            # Re-create if corrupted
            self._write_session_file()

    async def _heartbeat_loop(self) -> None:
        """Background task that updates the heartbeat periodically."""
        while self._running:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                self._update_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("Heartbeat error: %s", e)

    def _cleanup_stale_sessions(self) -> None:
        """Remove session files for dead processes."""
        if not self._sessions_dir.exists():
            return

        now = time.time()
        for session_file in self._sessions_dir.glob("*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                pid = data.get("pid")
                last_heartbeat = data.get("last_heartbeat", 0)

                # Check if process is still alive
                is_alive = False
                if pid is not None:
                    try:
                        os.kill(pid, 0)
                        is_alive = True
                    except (ProcessLookupError, PermissionError):
                        pass

                # Remove if dead or stale
                if not is_alive or (now - last_heartbeat > STALE_SESSION_TTL):
                    sid = session_file.stem
                    session_file.unlink(missing_ok=True)
                    # Also clean up inbox
                    inbox = self._inbox_path(sid)
                    inbox.unlink(missing_ok=True)
                    logger.info("Cleaned up stale session: %s", sid)

            except (json.JSONDecodeError, OSError) as e:
                logger.debug("Error reading session file %s: %s", session_file, e)
                try:
                    session_file.unlink(missing_ok=True)
                except OSError:
                    pass

    def _get_active_session_ids(self) -> set[str]:
        """Get the set of currently active session IDs."""
        if not self._sessions_dir.exists():
            return {self._session_id}

        sessions = set()
        for session_file in self._sessions_dir.glob("*.json"):
            sessions.add(session_file.stem)
        return sessions

    def _get_primary_session(self, active_sessions: set[str]) -> str | None:
        """
        Determine the primary session (oldest registered).

        The primary session receives unrouted messages.

        Args:
            active_sessions: Set of active session IDs.

        Returns:
            The session_id of the oldest session, or None.
        """
        if not active_sessions:
            return None

        oldest_time = float("inf")
        oldest_session = None

        for sid in active_sessions:
            session_file = self._session_file(sid)
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                registered_at = data.get("registered_at", float("inf"))
                if registered_at < oldest_time:
                    oldest_time = registered_at
                    oldest_session = sid
            except (json.JSONDecodeError, OSError):
                continue

        return oldest_session

    # -- Lock management --

    def _try_acquire_lock(self) -> bool:
        """
        Try to acquire the exclusive polling lock.

        Uses fcntl.flock(LOCK_EX | LOCK_NB) which is non-blocking.
        The lock is automatically released when the process exits.

        Returns:
            True if the lock was acquired (this session is the poller).
        """
        if self._lock_fd is not None:
            return True  # Already holding the lock

        try:
            # Ensure lock file exists
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd = fd
            return True
        except (OSError, BlockingIOError):
            # Lock is held by another process
            try:
                os.close(fd)
            except (OSError, UnboundLocalError):
                pass
            return False

    def _release_lock(self) -> None:
        """Release the polling lock if held."""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None
            self._is_poller = False

    # -- Offset management --

    def _read_offset(self) -> int:
        """Read the shared polling offset."""
        try:
            if self._offset_path.exists():
                data = json.loads(self._offset_path.read_text(encoding="utf-8"))
                return data.get("offset", 0)
        except (json.JSONDecodeError, OSError):
            pass
        return 0

    def _write_offset(self, offset: int) -> None:
        """Write the shared polling offset atomically."""
        self._atomic_write_json(self._offset_path, {"offset": offset})

    # -- Routing table --

    def _read_routing_table(self) -> dict[str, str]:
        """Read the shared routing table."""
        try:
            if self._routing_table_path.exists():
                data = json.loads(
                    self._routing_table_path.read_text(encoding="utf-8")
                )
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    # -- Inbox management --

    def _append_to_inbox(self, session_id: str, update: dict) -> None:
        """
        Append an update to a session's inbox file (JSONL format).

        Args:
            session_id: Target session ID.
            update: Telegram update object.
        """
        inbox_path = self._inbox_path(session_id)
        try:
            line = json.dumps(update, separators=(",", ":")) + "\n"
            with open(inbox_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            logger.warning("Failed to write to inbox %s: %s", session_id, e)

    def _read_inbox(self) -> list[dict]:
        """
        Read and clear this session's inbox file.

        Returns:
            List of Telegram update objects from the inbox.
        """
        inbox_path = self._inbox_path()
        if not inbox_path.exists():
            return []

        updates: list[dict] = []
        try:
            content = inbox_path.read_text(encoding="utf-8")
            # Atomically clear by unlinking
            inbox_path.unlink(missing_ok=True)

            for line in content.strip().split("\n"):
                line = line.strip()
                if line:
                    try:
                        updates.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.debug("Skipping malformed inbox line")
        except OSError:
            pass

        return updates

    # -- Atomic file helpers --

    @staticmethod
    def _atomic_write_json(path: Path, data: Any) -> None:
        """
        Write JSON data atomically using tmpfile + os.rename.

        This prevents corruption from concurrent reads or crashes mid-write.

        Args:
            path: Target file path.
            data: JSON-serializable data.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, separators=(",", ":"))
                os.rename(tmp_path, str(path))
            except Exception:
                # Clean up temp file on error
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.warning("Atomic write failed for %s: %s", path, e)


def register_session(
    session_id: str, coordinator_dir: Path | None = None
) -> None:
    """
    Register a session in the coordinator directory BEFORE router selection.

    Creates the coordinator/sessions/ directory structure and writes the
    session file so that count_active_sessions() can detect this session.
    Must be called before count_active_sessions() to avoid the chicken-and-egg
    problem where the directory doesn't exist yet.

    Args:
        session_id: Unique identifier for this MCP server session.
        coordinator_dir: Override for coordinator directory (testing).
    """
    base = coordinator_dir or COORDINATOR_DIR
    sessions_dir = base / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    try:
        sessions_dir.chmod(0o700)
    except OSError:
        pass

    data = {
        "pid": os.getpid(),
        "project": os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()),
        "registered_at": time.time(),
        "last_heartbeat": time.time(),
    }
    session_file = sessions_dir / f"{session_id}.json"
    FileBasedRouter._atomic_write_json(session_file, data)
    logger.info("Session registered: %s", session_id)


def unregister_session(
    session_id: str, coordinator_dir: Path | None = None
) -> None:
    """
    Unregister a session from the coordinator directory during cleanup.

    Removes the session file so that count_active_sessions() no longer
    counts this session. Called in the server.py lifespan finally block
    regardless of which router type was used.

    Args:
        session_id: Unique identifier for this MCP server session.
        coordinator_dir: Override for coordinator directory (testing).
    """
    base = coordinator_dir or COORDINATOR_DIR
    session_file = base / "sessions" / f"{session_id}.json"
    try:
        session_file.unlink(missing_ok=True)
    except OSError:
        pass
    logger.info("Session unregistered: %s", session_id)


def count_active_sessions(coordinator_dir: Path | None = None) -> int:
    """
    Count the number of active sessions in the coordinator directory.

    Used by server.py to decide between DirectRouter and FileBasedRouter.
    Cleans up stale sessions before counting.

    Args:
        coordinator_dir: Override for coordinator directory (testing).

    Returns:
        Number of active session files found (0 if coordinator dir doesn't exist).
    """
    sessions_dir = (coordinator_dir or COORDINATOR_DIR) / "sessions"
    if not sessions_dir.exists():
        return 0

    now = time.time()
    active_count = 0

    for session_file in sessions_dir.glob("*.json"):
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            pid = data.get("pid")
            last_heartbeat = data.get("last_heartbeat", 0)

            # Check process liveness
            is_alive = False
            if pid is not None:
                try:
                    os.kill(pid, 0)
                    is_alive = True
                except (ProcessLookupError, PermissionError):
                    pass

            if is_alive and (now - last_heartbeat <= STALE_SESSION_TTL):
                active_count += 1
            else:
                # Clean up stale
                try:
                    session_file.unlink(missing_ok=True)
                except OSError:
                    pass

        except (json.JSONDecodeError, OSError):
            try:
                session_file.unlink(missing_ok=True)
            except OSError:
                pass

    return active_count
