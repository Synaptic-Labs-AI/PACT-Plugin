"""
Tests for file permissions hardening (M18, M19, L13).

Location: pact-plugin/tests/test_file_permissions.py

Verifies that:
1. pact-memory directories are created with mode 0o700 (owner-only)
2. SQLite database files are set to mode 0o600 after creation
3. Permission hardening is applied consistently across creation points
"""

import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add the pact-memory scripts to path for direct imports
SCRIPTS_DIR = Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR.parent))


# =============================================================================
# database.py Permission Tests
# =============================================================================

class TestDatabasePermissions:
    """Tests for database.py file permission hardening."""

    def test_get_db_path_creates_directory_with_700(self, tmp_path):
        """get_db_path() should create the memory directory with mode 0o700."""
        memory_dir = tmp_path / "pact-memory"
        db_path = memory_dir / "memory.db"

        with patch("scripts.config.PACT_MEMORY_DIR", memory_dir), \
             patch("scripts.database.PACT_MEMORY_DIR", memory_dir), \
             patch("scripts.database.DB_PATH", db_path):
            from scripts.database import get_db_path
            result = get_db_path()

        assert memory_dir.exists()
        dir_mode = stat.S_IMODE(memory_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Directory should have mode 0o700, got {oct(dir_mode)}"
        )
        assert result == db_path

    def test_get_connection_sets_db_file_permissions_600(self, tmp_path):
        """get_connection() should set database file to mode 0o600."""
        db_path = tmp_path / "test.db"

        # Use standard sqlite3 to avoid pysqlite3 dependency issues
        import sqlite3 as stdlib_sqlite3
        with patch("scripts.database.sqlite3", stdlib_sqlite3):
            from scripts.database import get_connection
            conn = get_connection(db_path=db_path)
            conn.close()

        assert db_path.exists()
        file_mode = stat.S_IMODE(db_path.stat().st_mode)
        assert file_mode == 0o600, (
            f"Database file should have mode 0o600, got {oct(file_mode)}"
        )

    def test_get_connection_chmod_failure_does_not_raise(self, tmp_path):
        """get_connection() should not raise if os.chmod fails."""
        db_path = tmp_path / "test.db"

        import sqlite3 as stdlib_sqlite3
        with patch("scripts.database.sqlite3", stdlib_sqlite3), \
             patch("scripts.database.os.chmod", side_effect=OSError("Permission denied")):
            from scripts.database import get_connection
            # Should not raise
            conn = get_connection(db_path=db_path)
            conn.close()


# =============================================================================
# setup_memory.py Permission Tests
# =============================================================================

class TestSetupMemoryPermissions:
    """Tests for setup_memory.py directory permission hardening."""

    def test_ensure_directories_creates_with_700(self, tmp_path):
        """ensure_directories() should create directory with mode 0o700."""
        memory_dir = tmp_path / "pact-memory"

        with patch("scripts.config.PACT_MEMORY_DIR", memory_dir), \
             patch("scripts.setup_memory.PACT_MEMORY_DIR", memory_dir):
            from scripts.setup_memory import ensure_directories
            ensure_directories()

        assert memory_dir.exists()
        dir_mode = stat.S_IMODE(memory_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Directory should have mode 0o700, got {oct(dir_mode)}"
        )

    def test_ensure_directories_idempotent(self, tmp_path):
        """Calling ensure_directories() twice should not fail or change permissions."""
        memory_dir = tmp_path / "pact-memory"

        with patch("scripts.config.PACT_MEMORY_DIR", memory_dir), \
             patch("scripts.setup_memory.PACT_MEMORY_DIR", memory_dir):
            from scripts.setup_memory import ensure_directories
            ensure_directories()
            ensure_directories()

        dir_mode = stat.S_IMODE(memory_dir.stat().st_mode)
        assert dir_mode == 0o700


# =============================================================================
# telegram/config.py Permission Tests (verify existing hardening)
# =============================================================================

class TestTelegramConfigPermissions:
    """Verify that telegram/config.py already has permission hardening."""

    def test_ensure_config_dir_sets_700(self, tmp_path):
        """ensure_config_dir() should create directory with mode 0o700."""
        config_dir = tmp_path / "pact-telegram"

        with patch("telegram.config.CONFIG_DIR", config_dir):
            from telegram.config import ensure_config_dir
            result = ensure_config_dir()

        assert config_dir.exists()
        dir_mode = stat.S_IMODE(config_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Config directory should have mode 0o700, got {oct(dir_mode)}"
        )
        assert result == config_dir
