"""
Tests for vector dimension mismatch fixes (GitHub issue #227).

Location: pact-plugin/tests/test_vector_dimension_fixes.py

Tests cover all 7 fixes across 4 files:
1. database.py: ensure_initialized() now calls _init_vector_table() for existing DBs
2. memory_init.py: maybe_migrate_embeddings() uses SELECT * + dict(row) + generate_embedding_text()
3. memory_init.py: maybe_migrate_embeddings() includes project_id in vec_memories INSERT
4. search.py: get_search_capabilities() returns correct keys (embedding_backend, embedding_dimension)
5. search.py: get_search_capabilities() does NOT return old keys (model_path, model_exists, backends)
6. search.py: vector_search() logs failures at WARNING level (not DEBUG)
7. config.py: EMBEDDING_DIMENSION constant removed (no duplicate of embeddings.EMBEDDING_DIM)

Risk tier: STANDARD — well-understood pattern fix.
"""

import json
import logging
import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch, call

import pytest

# Add paths for imports — scripts/ is a package with __init__.py,
# so we add its parent to sys.path for proper relative imports.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def db_conn():
    """Create a temporary database with full schema for testing."""
    from scripts.database import get_connection, init_schema
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        conn = get_connection(db_path)
        # Patch _init_vector_table to avoid needing sqlite-vec extension
        with patch('scripts.database._init_vector_table', return_value=False):
            init_schema(conn)
        yield conn
        conn.close()


@pytest.fixture
def legacy_db_conn():
    """Create a temporary database simulating a pre-fix existing DB (memories table only)."""
    from scripts.database import get_connection
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "legacy_memory.db"
        conn = get_connection(db_path)
        # Create legacy schema: memories table exists but NO vec_memories
        conn.execute("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                context TEXT,
                goal TEXT,
                active_tasks TEXT,
                lessons_learned TEXT,
                decisions TEXT,
                entities TEXT,
                reasoning_chains TEXT,
                agreements_reached TEXT,
                disagreements_resolved TEXT,
                project_id TEXT,
                session_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        yield conn
        conn.close()


# =============================================================================
# 1. ensure_initialized() migration path — database.py line 318
# =============================================================================

class TestEnsureInitializedVectorMigration:
    """Test that ensure_initialized() calls _init_vector_table() for existing databases."""

    def test_existing_db_triggers_vector_table_init(self, legacy_db_conn):
        """When memories table exists, ensure_initialized should call _init_vector_table."""
        from scripts.database import ensure_initialized

        with patch('scripts.database._init_vector_table') as mock_init_vec:
            mock_init_vec.return_value = True
            ensure_initialized(legacy_db_conn)
            mock_init_vec.assert_called_once_with(legacy_db_conn)

    def test_new_db_initializes_full_schema(self):
        """When no memories table exists, ensure_initialized should call init_schema."""
        from scripts.database import ensure_initialized, get_connection

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fresh.db"
            conn = get_connection(db_path)

            with patch('scripts.database.init_schema') as mock_schema:
                ensure_initialized(conn)
                mock_schema.assert_called_once_with(conn)

            conn.close()

    def test_existing_db_also_runs_ct_migration(self, legacy_db_conn):
        """Existing DB should run both _migrate_ct_fields AND _init_vector_table."""
        from scripts.database import ensure_initialized

        with patch('scripts.database._migrate_ct_fields') as mock_ct, \
             patch('scripts.database._init_vector_table') as mock_vec:
            mock_vec.return_value = False
            ensure_initialized(legacy_db_conn)
            mock_ct.assert_called_once_with(legacy_db_conn)
            mock_vec.assert_called_once_with(legacy_db_conn)

    def test_existing_db_ct_migration_before_vector_init(self, legacy_db_conn):
        """CT field migration should run before vector table init (order matters)."""
        from scripts.database import ensure_initialized

        call_order = []

        def track_ct(conn):
            call_order.append('ct')

        def track_vec(conn):
            call_order.append('vec')
            return False

        with patch('scripts.database._migrate_ct_fields', side_effect=track_ct), \
             patch('scripts.database._init_vector_table', side_effect=track_vec):
            ensure_initialized(legacy_db_conn)

        assert call_order == ['ct', 'vec'], (
            f"Expected CT migration before vector init, got: {call_order}"
        )


# =============================================================================
# 2. maybe_migrate_embeddings() — field completeness
# =============================================================================

class FakeRow:
    """A sqlite3.Row-like object that supports dict() conversion."""

    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __iter__(self):
        return iter(self._data.items())

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._data[key]
        keys = list(self._data.keys())
        return self._data[keys[key]]


def _build_mock_execute(table_exists, embedding_bytes, memory_rows):
    """Build a side_effect function for mock_conn.execute that handles multiple SQL calls."""

    def mock_execute(sql, params=None):
        mock_cursor = MagicMock()
        sql_lower = sql.strip().lower()

        if "sqlite_master" in sql_lower and "vec_memories" in sql_lower:
            if table_exists:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            else:
                mock_cursor.fetchone.return_value = None
        elif "select embedding from vec_memories" in sql_lower:
            if embedding_bytes:
                mock_cursor.fetchone.return_value = (embedding_bytes,)
            else:
                mock_cursor.fetchone.return_value = None
        elif "drop table" in sql_lower:
            pass
        elif "create virtual table" in sql_lower:
            pass
        elif "select * from memories" in sql_lower:
            # Use FakeRow objects that support dict() conversion
            mock_cursor.fetchall.return_value = [
                FakeRow(row_dict) for row_dict in memory_rows
            ]
        elif "insert or replace into vec_memories" in sql_lower:
            pass

        return mock_cursor

    return mock_execute


class TestMaybeMigrateEmbeddingsFieldCoverage:
    """Test that maybe_migrate_embeddings uses SELECT * and generate_embedding_text."""

    def test_uses_select_star_for_all_fields(self):
        """Migration should use SELECT * and pass full dict to generate_embedding_text."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        mock_service.generate.return_value = [0.1] * 256

        memory_row = {
            'id': 'mem1',
            'context': 'test context',
            'goal': 'test goal',
            'lessons_learned': '["lesson1"]',
            'decisions': '["decision1"]',
            'entities': None,
            'reasoning_chains': '["chain1"]',
            'agreements_reached': '["agree1"]',
            'disagreements_resolved': '["resolved1"]',
            'project_id': 'proj-abc',
            'session_id': 'sess-xyz',
            'created_at': '2026-01-01',
            'updated_at': '2026-01-01',
            'active_tasks': None,
        }

        mock_conn.execute.side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=[memory_row],
        )

        # Patch at the source modules where deferred imports resolve
        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text') as mock_gen_text, \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                mock_gen_text.return_value = "combined text for embedding"
                result = maybe_migrate_embeddings()

        # Verify generate_embedding_text was called with full dict (including CT fields)
        assert mock_gen_text.called, "generate_embedding_text should have been called"
        call_dict = mock_gen_text.call_args[0][0]
        assert 'reasoning_chains' in call_dict, "CT field reasoning_chains missing"
        assert 'agreements_reached' in call_dict, "CT field agreements_reached missing"
        assert 'disagreements_resolved' in call_dict, "CT field disagreements_resolved missing"
        assert 'context' in call_dict, "Basic field context missing"
        assert 'goal' in call_dict, "Basic field goal missing"

    def test_ct_fields_included_in_embedding_text(self):
        """Verify that generate_embedding_text (via MemoryObject) includes CT fields."""
        from scripts.embeddings import generate_embedding_text

        memory_dict = {
            'id': 'test-ct',
            'context': 'Auth implementation',
            'goal': 'Add JWT support',
            'reasoning_chains': ['Chose bcrypt because OWASP recommendation'],
            'agreements_reached': ['Lead confirmed Redis for blacklist'],
            'disagreements_resolved': ['JWT won over session tokens'],
        }

        text = generate_embedding_text(memory_dict)

        assert 'bcrypt' in text, "reasoning_chains content not in embedding text"
        assert 'Redis' in text, "agreements_reached content not in embedding text"
        assert 'JWT won' in text, "disagreements_resolved content not in embedding text"
        assert 'Auth implementation' in text, "context not in embedding text"
        assert 'JWT support' in text, "goal not in embedding text"


# =============================================================================
# 3. maybe_migrate_embeddings() — project_id in INSERT
# =============================================================================

class TestMaybeMigrateEmbeddingsProjectId:
    """Test that project_id is correctly inserted into vec_memories during migration."""

    def test_project_id_included_in_insert(self):
        """Re-embedding INSERT should include project_id from the memory row."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        mock_service.generate.return_value = [0.1] * 256

        memory_row = {
            'id': 'mem-proj',
            'context': 'project test',
            'goal': None,
            'lessons_learned': None,
            'decisions': None,
            'entities': None,
            'reasoning_chains': None,
            'agreements_reached': None,
            'disagreements_resolved': None,
            'project_id': 'my-important-project',
            'session_id': None,
            'created_at': '2026-01-01',
            'updated_at': '2026-01-01',
            'active_tasks': None,
        }

        # Track all execute calls to verify INSERT params
        execute_calls = []
        original_side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=[memory_row],
        )

        def tracking_execute(sql, params=None):
            execute_calls.append((sql, params))
            return original_side_effect(sql, params)

        mock_conn.execute.side_effect = tracking_execute

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text', return_value="text"), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        # Find the INSERT call and verify project_id is included
        insert_calls = [
            (sql, params) for sql, params in execute_calls
            if 'INSERT OR REPLACE INTO vec_memories' in sql
        ]
        assert len(insert_calls) == 1, f"Expected 1 INSERT call, got {len(insert_calls)}"

        insert_sql, insert_params = insert_calls[0]
        assert 'project_id' in insert_sql, "INSERT SQL should include project_id column"
        assert insert_params[0] == 'mem-proj', "First param should be memory_id"
        assert insert_params[1] == 'my-important-project', "Second param should be project_id"

    def test_project_id_none_when_not_set(self):
        """When memory has no project_id, None should be passed in the INSERT."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        mock_service.generate.return_value = [0.1] * 256

        memory_row = {
            'id': 'mem-no-proj',
            'context': 'no project',
            'goal': None,
            'lessons_learned': None,
            'decisions': None,
            'entities': None,
            'reasoning_chains': None,
            'agreements_reached': None,
            'disagreements_resolved': None,
            'project_id': None,  # No project
            'session_id': None,
            'created_at': '2026-01-01',
            'updated_at': '2026-01-01',
            'active_tasks': None,
        }

        execute_calls = []
        original_side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=[memory_row],
        )

        def tracking_execute(sql, params=None):
            execute_calls.append((sql, params))
            return original_side_effect(sql, params)

        mock_conn.execute.side_effect = tracking_execute

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text', return_value="text"), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        insert_calls = [
            (sql, params) for sql, params in execute_calls
            if 'INSERT OR REPLACE INTO vec_memories' in sql
        ]
        assert len(insert_calls) == 1
        insert_sql, insert_params = insert_calls[0]
        assert insert_params[1] is None, "project_id should be None when not set on memory"


# =============================================================================
# 4 & 5. get_search_capabilities() return structure — search.py
# =============================================================================

class TestGetSearchCapabilities:
    """Test that get_search_capabilities returns correct keys after the fix."""

    def test_returns_embedding_backend_key(self):
        """Result should contain 'embedding_backend' (not 'active_backend')."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'model': 'minishlab/potion-base-8M',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        assert 'embedding_backend' in result
        assert result['embedding_backend'] == 'model2vec'

    def test_returns_embedding_dimension_key(self):
        """Result should contain 'embedding_dimension' key."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'model': 'minishlab/potion-base-8M',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        assert 'embedding_dimension' in result
        assert result['embedding_dimension'] == 256

    def test_does_not_return_model_path(self):
        """Result should NOT contain 'model_path' (removed in fix)."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'model': 'minishlab/potion-base-8M',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        assert 'model_path' not in result, "model_path should have been removed"

    def test_does_not_return_model_exists(self):
        """Result should NOT contain 'model_exists' (removed in fix)."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'model': 'minishlab/potion-base-8M',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        assert 'model_exists' not in result, "model_exists should have been removed"

    def test_details_does_not_contain_backends(self):
        """The 'details' sub-dict should NOT contain 'backends' (removed in fix)."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'model': 'minishlab/potion-base-8M',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        assert 'backends' not in result.get('details', {}), \
            "backends should have been removed from details"

    def test_semantic_search_mode_when_available(self):
        """Search mode should be 'semantic' when extensions + embeddings available."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        assert result['search_mode'] == 'semantic'
        assert result['semantic_search'] is True

    def test_keyword_mode_when_extensions_disabled(self):
        """Search mode should be 'keyword' when SQLite extensions unavailable."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', False):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        assert result['search_mode'] == 'keyword'
        assert result['semantic_search'] is False

    def test_keyword_mode_when_embeddings_unavailable(self):
        """Search mode should be 'keyword' when embeddings not available."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': False,
                'backend': None,
                'embedding_dimension': None,
            }

            result = get_search_capabilities()

        assert result['search_mode'] == 'keyword'
        assert result['semantic_search'] is False

    def test_always_has_keyword_and_graph_boosting(self):
        """keyword_search and graph_boosting should always be True."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', False):
            mock_check.return_value = {
                'available': False,
                'backend': None,
                'embedding_dimension': None,
            }

            result = get_search_capabilities()

        assert result['keyword_search'] is True
        assert result['graph_boosting'] is True

    def test_complete_return_structure(self):
        """Verify the complete set of top-level keys in the return dict."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'model': 'minishlab/potion-base-8M',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        expected_keys = {
            'semantic_search',
            'keyword_search',
            'graph_boosting',
            'search_mode',
            'sqlite_extensions_enabled',
            'embedding_backend',
            'embedding_dimension',
            'details',
        }
        assert set(result.keys()) == expected_keys, (
            f"Unexpected keys. Got: {set(result.keys())}, expected: {expected_keys}"
        )


# =============================================================================
# 6. vector_search() logging — search.py line 128
# =============================================================================

class TestVectorSearchLogging:
    """Test that vector_search logs failures at WARNING level."""

    def test_dimension_error_logged_at_warning_level(self):
        """When vector search fails with dimension-related error, it should log at WARNING."""
        from scripts.search import vector_search

        # Use a MagicMock connection to avoid pysqlite3 read-only attribute issue
        mock_conn = MagicMock()
        mock_conn.enable_load_extension.side_effect = Exception(
            "dimension mismatch: expected 256, got 384"
        )

        with patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True), \
             patch('scripts.search.generate_embedding', return_value=[0.1] * 256), \
             patch('scripts.search.logger') as mock_logger:

            result = vector_search(mock_conn, "test query")

        assert result == [], "Should return empty list on error"
        mock_logger.warning.assert_called_once()
        assert "dimension mismatch" in str(mock_logger.warning.call_args)

    def test_non_dimension_error_logged_at_debug_level(self):
        """Non-dimension errors should log at DEBUG, not WARNING."""
        from scripts.search import vector_search

        mock_conn = MagicMock()
        mock_conn.enable_load_extension.side_effect = Exception("connection lost")

        with patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True), \
             patch('scripts.search.generate_embedding', return_value=[0.1] * 256), \
             patch('scripts.search.logger') as mock_logger:

            result = vector_search(mock_conn, "test query")

        assert result == [], "Should return empty list on error"
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_called()
        assert "connection lost" in str(mock_logger.debug.call_args)

    def test_no_debug_log_for_vector_search_failed(self):
        """The exception handler should use warning, not debug for 'Vector search failed'."""
        from scripts.search import vector_search

        mock_conn = MagicMock()
        mock_conn.enable_load_extension.side_effect = Exception("dim mismatch")

        with patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True), \
             patch('scripts.search.generate_embedding', return_value=[0.1] * 256), \
             patch('scripts.search.logger') as mock_logger:

            vector_search(mock_conn, "test query")

        # The exception path should use warning (not debug) for "Vector search failed"
        for call_args in mock_logger.debug.call_args_list:
            msg = str(call_args)
            assert "Vector search failed" not in msg, (
                "Exception should be logged at WARNING level, not DEBUG"
            )

    def test_warning_includes_error_details(self):
        """Warning log should include the exception message for diagnosability."""
        from scripts.search import vector_search

        mock_conn = MagicMock()
        mock_conn.enable_load_extension.side_effect = Exception(
            "dimension mismatch: expected 256, got 384"
        )

        with patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True), \
             patch('scripts.search.generate_embedding', return_value=[0.1] * 256), \
             patch('scripts.search.logger') as mock_logger:

            vector_search(mock_conn, "test query")

        warning_msg = str(mock_logger.warning.call_args)
        assert "dimension mismatch" in warning_msg


# =============================================================================
# 7. config.py — EMBEDDING_DIMENSION removed
# =============================================================================

class TestConfigEmbeddingDimensionRemoved:
    """Test that the dead EMBEDDING_DIMENSION constant was removed from config.py."""

    def test_no_embedding_dimension_in_config(self):
        """config.py should NOT export EMBEDDING_DIMENSION."""
        from scripts import config

        assert not hasattr(config, 'EMBEDDING_DIMENSION'), (
            "EMBEDDING_DIMENSION should have been removed from config.py"
        )

    def test_embedding_dim_lives_in_embeddings(self):
        """The canonical EMBEDDING_DIM constant should be in embeddings.py."""
        from scripts.embeddings import EMBEDDING_DIM

        assert EMBEDDING_DIM == 256

    def test_no_config_embedding_dimension_imports(self):
        """No module should import EMBEDDING_DIMENSION from config."""
        import scripts.config as config_module

        # Verify the constant is not in the module's namespace
        all_names = dir(config_module)
        assert 'EMBEDDING_DIMENSION' not in all_names


# =============================================================================
# Edge cases
# =============================================================================

class TestEdgeCases:
    """Edge cases for the vector dimension fixes."""

    def test_ensure_initialized_empty_db(self):
        """ensure_initialized on a brand new DB should create full schema."""
        from scripts.database import ensure_initialized, get_connection

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty.db"
            conn = get_connection(db_path)

            with patch('scripts.database._init_vector_table', return_value=False):
                ensure_initialized(conn)

            # DB should now have memories table
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
            )
            assert cursor.fetchone() is not None, "memories table should exist"

            conn.close()

    def test_ensure_initialized_idempotent(self, legacy_db_conn):
        """Calling ensure_initialized twice should not error."""
        from scripts.database import ensure_initialized

        with patch('scripts.database._init_vector_table', return_value=False):
            ensure_initialized(legacy_db_conn)
            ensure_initialized(legacy_db_conn)

        # Verify DB still works
        legacy_db_conn.execute(
            "INSERT INTO memories (id, context) VALUES ('test1', 'test')"
        )
        legacy_db_conn.commit()
        cursor = legacy_db_conn.execute("SELECT context FROM memories WHERE id='test1'")
        assert cursor.fetchone()[0] == 'test'

    def test_maybe_migrate_no_vec_table(self):
        """If vec_memories doesn't exist, migration should return ok without doing anything."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # vec_memories doesn't exist
        mock_conn.execute.return_value = mock_cursor

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        assert result['status'] == 'ok'

    def test_maybe_migrate_empty_vec_table(self):
        """If vec_memories exists but is empty, migration should return ok."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        def mock_execute(sql, params=None):
            mock_cursor = MagicMock()
            sql_lower = sql.strip().lower()

            if "sqlite_master" in sql_lower:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "select embedding from vec_memories" in sql_lower:
                mock_cursor.fetchone.return_value = None  # Empty table
            return mock_cursor

        mock_conn = MagicMock()
        mock_conn.execute = mock_execute

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        assert result['status'] == 'ok'

    def test_maybe_migrate_matching_dimensions(self):
        """If dimensions already match, migration should return ok (no-op)."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        def mock_execute(sql, params=None):
            mock_cursor = MagicMock()
            sql_lower = sql.strip().lower()

            if "sqlite_master" in sql_lower:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "select embedding from vec_memories" in sql_lower:
                # Return 256-dim embedding (matches expected)
                mock_cursor.fetchone.return_value = (
                    struct.pack('256f', *([0.1] * 256)),
                )
            return mock_cursor

        mock_conn = MagicMock()
        mock_conn.execute = mock_execute

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        assert result['status'] == 'ok'
        assert result['message'] is None  # No migration needed

    def test_maybe_migrate_deps_unavailable(self):
        """If pysqlite3 is not available, migration should return skipped_deps."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        # Remove pysqlite3 from sys.modules to simulate it being unavailable,
        # which causes the inner `import pysqlite3 as sqlite3` to fail
        with patch.dict('sys.modules', {'pysqlite3': None}):
            result = maybe_migrate_embeddings()

        # The inner try/except ImportError should catch and return skipped_deps
        assert result['status'] == 'skipped_deps'
        assert result['message'] == 'Dependencies not available'

    def test_check_embedding_availability_returns_correct_structure(self):
        """Verify check_embedding_availability returns the keys that search.py expects."""
        from scripts.embeddings import check_embedding_availability

        result = check_embedding_availability()

        assert 'available' in result
        assert 'backend' in result
        assert 'embedding_dimension' in result
        assert result['backend'] == 'model2vec'
        assert result['embedding_dimension'] == 256


# =============================================================================
# Integration: generate_embedding_text with all fields
# =============================================================================

class TestGenerateEmbeddingTextIntegration:
    """Integration tests for generate_embedding_text covering all memory fields."""

    def test_includes_basic_fields(self):
        """Embedding text should include context, goal, lessons, decisions."""
        from scripts.embeddings import generate_embedding_text

        memory = {
            'id': 'test-basic',
            'context': 'Working on authentication',
            'goal': 'Implement OAuth2 flow',
            'lessons_learned': ['Always hash passwords with bcrypt'],
            'decisions': [{'decision': 'Use Redis for sessions'}],
        }

        text = generate_embedding_text(memory)

        assert 'authentication' in text
        assert 'OAuth2' in text
        assert 'bcrypt' in text
        assert 'Redis' in text

    def test_includes_all_ct_fields(self):
        """Embedding text should include all three CT fields."""
        from scripts.embeddings import generate_embedding_text

        memory = {
            'id': 'test-ct',
            'reasoning_chains': ['Chose model2vec because pure Python, no crashes'],
            'agreements_reached': ['Confirmed via teachback: 256-dim sufficient'],
            'disagreements_resolved': ['Backend wanted 384-dim, architect preferred 256-dim'],
        }

        text = generate_embedding_text(memory)

        assert 'model2vec' in text
        assert '256-dim sufficient' in text
        assert '384-dim' in text

    def test_empty_memory_produces_empty_text(self):
        """A memory with no content fields should produce empty or minimal text."""
        from scripts.embeddings import generate_embedding_text

        memory = {'id': 'test-empty'}

        text = generate_embedding_text(memory)
        # Should not crash; text may be empty or contain field labels only
        assert isinstance(text, str)

    def test_handles_json_string_ct_fields(self):
        """CT fields stored as JSON strings (from DB) should be parsed correctly."""
        from scripts.embeddings import generate_embedding_text

        memory = {
            'id': 'test-json-str',
            'reasoning_chains': json.dumps(['Chain from database row']),
            'agreements_reached': json.dumps(['Agreement from database row']),
        }

        text = generate_embedding_text(memory)

        assert 'Chain from database' in text
        assert 'Agreement from database' in text


# =============================================================================
# Regression: ensure old behaviors still work
# =============================================================================

class TestRegressionSafety:
    """Ensure fixes don't break existing functionality."""

    def test_init_schema_still_works(self):
        """init_schema should still create all tables correctly."""
        from scripts.database import get_connection, init_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "schema_test.db"
            conn = get_connection(db_path)

            with patch('scripts.database._init_vector_table', return_value=False):
                init_schema(conn)

            # Check all tables exist
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in cursor.fetchall()}

            assert 'memories' in tables
            assert 'files' in tables
            assert 'memory_files' in tables
            assert 'file_relations' in tables

            conn.close()

    def test_create_and_get_memory_still_works(self, db_conn):
        """Basic create/get memory operations should still work."""
        from scripts.database import create_memory, get_memory

        memory = {
            'context': 'Regression test',
            'goal': 'Ensure nothing broke',
            'project_id': 'test-project',
            'reasoning_chains': ['Chain A'],
        }

        memory_id = create_memory(db_conn, memory)
        retrieved = get_memory(db_conn, memory_id)

        assert retrieved is not None
        assert retrieved['context'] == 'Regression test'
        assert retrieved['project_id'] == 'test-project'
        assert retrieved['reasoning_chains'] == ['Chain A']

    def test_keyword_search_still_works(self, db_conn):
        """Keyword search should still work with the fixes in place."""
        from scripts.database import create_memory, search_memories_by_text

        create_memory(db_conn, {
            'context': 'Vector dimension mismatch debugging',
            'reasoning_chains': ['Used probe query to detect dimension'],
        })

        results = search_memories_by_text(db_conn, 'dimension')
        assert len(results) >= 1
        assert 'dimension' in results[0]['context']


# =============================================================================
# F2: Multi-row and partial failure in maybe_migrate_embeddings
# =============================================================================

class TestMaybeMigrateEmbeddingsMultiRow:
    """Test migration with multiple memory rows, including partial failure."""

    def _make_memory_row(self, mem_id, context='test', project_id=None):
        """Helper to create a minimal memory row dict."""
        return {
            'id': mem_id,
            'context': context,
            'goal': None,
            'lessons_learned': None,
            'decisions': None,
            'entities': None,
            'reasoning_chains': None,
            'agreements_reached': None,
            'disagreements_resolved': None,
            'project_id': project_id,
            'session_id': None,
            'created_at': '2026-01-01',
            'updated_at': '2026-01-01',
            'active_tasks': None,
        }

    def test_multiple_rows_all_succeed(self):
        """All rows should be re-embedded when embedding generation succeeds."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        mock_service.generate.return_value = [0.1] * 256

        rows = [
            self._make_memory_row('mem-1', 'first context', 'proj-a'),
            self._make_memory_row('mem-2', 'second context', 'proj-b'),
            self._make_memory_row('mem-3', 'third context'),
        ]

        mock_conn.execute.side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=rows,
        )

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text', return_value="text"), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        assert result['status'] == 'ok'
        assert '3/3' in result['message'], f"Expected 3/3 in message, got: {result['message']}"

    def test_partial_failure_continues_remaining(self):
        """When some rows fail embedding, migration should continue and report partial success."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()

        # First call succeeds, second raises, third succeeds
        mock_service.generate.side_effect = [
            [0.1] * 256,
            Exception("model crashed on bad input"),
            [0.1] * 256,
        ]

        rows = [
            self._make_memory_row('mem-ok-1', 'good row'),
            self._make_memory_row('mem-fail', 'bad row'),
            self._make_memory_row('mem-ok-2', 'another good row'),
        ]

        mock_conn.execute.side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=rows,
        )

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text', return_value="text"), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        assert result['status'] == 'ok'
        assert '2/3' in result['message'], (
            f"Expected 2/3 (2 succeeded, 1 failed), got: {result['message']}"
        )

    def test_all_rows_fail_still_completes(self):
        """If every row fails embedding, migration should still complete with 0/N count."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        mock_service.generate.side_effect = Exception("model unavailable")

        rows = [
            self._make_memory_row('mem-f1'),
            self._make_memory_row('mem-f2'),
        ]

        mock_conn.execute.side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=rows,
        )

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text', return_value="text"), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        assert result['status'] == 'ok'
        assert '0/2' in result['message'], (
            f"Expected 0/2 (all failed), got: {result['message']}"
        )

    def test_empty_embedding_not_counted_as_success(self):
        """When service.generate returns empty/falsy, row should not count as success."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        # First returns valid embedding, second returns empty list (falsy)
        mock_service.generate.side_effect = [
            [0.1] * 256,
            [],
        ]

        rows = [
            self._make_memory_row('mem-valid'),
            self._make_memory_row('mem-empty-embed'),
        ]

        mock_conn.execute.side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=rows,
        )

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text', return_value="text"), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        assert result['status'] == 'ok'
        assert '1/2' in result['message'], (
            f"Expected 1/2 (one empty embedding), got: {result['message']}"
        )


# =============================================================================
# F3: Direct tests for _check_and_migrate_vector_table
# =============================================================================

class TestCheckAndMigrateVectorTable:
    """Direct tests for _check_and_migrate_vector_table dimension detection."""

    def test_no_table_is_noop(self):
        """When vec_memories table doesn't exist, function returns without action."""
        from scripts.database import _check_and_migrate_vector_table

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # Table doesn't exist
        mock_conn.execute.return_value = mock_cursor

        _check_and_migrate_vector_table(mock_conn, 256)

        # Should only have called execute once (the sqlite_master check)
        assert mock_conn.execute.call_count == 1
        # Should NOT have called DROP TABLE
        for call_args in mock_conn.execute.call_args_list:
            sql = str(call_args)
            assert 'DROP TABLE' not in sql

    def test_matching_dimensions_is_noop(self):
        """When probe query succeeds, dimensions match — no migration needed."""
        from scripts.database import _check_and_migrate_vector_table

        call_count = {'n': 0}

        def mock_execute(sql, params=None):
            call_count['n'] += 1
            mock_cursor = MagicMock()
            sql_lower = sql.strip().lower()

            if "sqlite_master" in sql_lower:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "embedding match" in sql_lower:
                # Probe succeeds — dimensions match
                mock_cursor.fetchall.return_value = []
            return mock_cursor

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = mock_execute

        _check_and_migrate_vector_table(mock_conn, 256)

        # Should NOT have called DROP TABLE or commit
        for call_args in mock_conn.execute.call_args_list:
            sql = str(call_args)
            assert 'DROP TABLE' not in sql

    def test_dimension_mismatch_drops_table(self):
        """When probe query fails with dimension error, table should be dropped."""
        from scripts.database import _check_and_migrate_vector_table

        dropped = []

        def mock_execute(sql, params=None):
            mock_cursor = MagicMock()
            sql_lower = sql.strip().lower()

            if "sqlite_master" in sql_lower:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "embedding match" in sql_lower:
                raise Exception("dimension mismatch: expected 256, got 384")
            elif "drop table" in sql_lower:
                dropped.append(sql)
            return mock_cursor

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = mock_execute

        _check_and_migrate_vector_table(mock_conn, 256)

        assert len(dropped) == 1, f"Expected 1 DROP TABLE call, got {len(dropped)}"
        assert 'vec_memories' in dropped[0]
        mock_conn.commit.assert_called_once()

    def test_mismatch_keyword_variants_trigger_drop(self):
        """Error messages containing 'mismatch', 'invalid', or 'dimension' should all trigger drop."""
        from scripts.database import _check_and_migrate_vector_table

        error_messages = [
            "dimension mismatch in vector table",
            "invalid vector size",
            "dimension does not match expected",
        ]

        for error_msg in error_messages:
            dropped = []

            def make_execute(err):
                def mock_execute(sql, params=None):
                    mock_cursor = MagicMock()
                    sql_lower = sql.strip().lower()

                    if "sqlite_master" in sql_lower:
                        mock_cursor.fetchone.return_value = ('vec_memories',)
                    elif "embedding match" in sql_lower:
                        raise Exception(err)
                    elif "drop table" in sql_lower:
                        dropped.append(sql)
                    return mock_cursor
                return mock_execute

            mock_conn = MagicMock()
            mock_conn.execute.side_effect = make_execute(error_msg)

            _check_and_migrate_vector_table(mock_conn, 256)

            assert len(dropped) == 1, (
                f"Error '{error_msg}' should trigger DROP TABLE, got {len(dropped)} drops"
            )

    def test_unrelated_error_does_not_drop(self):
        """Probe errors without dimension keywords should NOT trigger table drop."""
        from scripts.database import _check_and_migrate_vector_table

        def mock_execute(sql, params=None):
            mock_cursor = MagicMock()
            sql_lower = sql.strip().lower()

            if "sqlite_master" in sql_lower:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "embedding match" in sql_lower:
                raise Exception("database is locked")
            return mock_cursor

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = mock_execute

        _check_and_migrate_vector_table(mock_conn, 256)

        # Should NOT have called DROP TABLE
        for call_args in mock_conn.execute.call_args_list:
            sql = str(call_args)
            assert 'DROP TABLE' not in sql

    def test_outer_exception_caught_gracefully(self):
        """If the initial sqlite_master query fails, function should not raise."""
        from scripts.database import _check_and_migrate_vector_table

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("connection lost")

        # Should not raise
        _check_and_migrate_vector_table(mock_conn, 256)
