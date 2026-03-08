"""
Tests for the embedding_catchup module - background embedding recovery with RAM awareness.

Tests cover:
1. get_available_ram_mb() - platform-specific RAM detection with mocked backends
2. embed_pending_memories() - RAM threshold gating, processing loop, error handling
3. MIN_CATCHUP_RAM_MB constant - value assertion and default parameter wiring
4. get_unembedded_memories() - extension check, error paths
5. embed_single_memory() - extension check, error paths

Note: embedding_catchup.py uses relative imports from its parent package (.database,
.embeddings). To test functions that depend on these imports, we mock the database and
embeddings modules in sys.modules before importing embedding_catchup. For the pure
function get_available_ram_mb(), we test it by importing the module after mocking its
package dependencies.
"""

import inspect
import subprocess
import sys
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Add paths for imports
_scripts_path = str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts")
if _scripts_path not in sys.path:
    sys.path.insert(0, _scripts_path)


def _make_mock_database():
    """Create a mock standing in for the .database module."""
    mod = ModuleType("database")
    mod.db_connection = MagicMock()
    mod.ensure_initialized = MagicMock()
    mod.get_memory = MagicMock()
    mod.SQLITE_EXTENSIONS_ENABLED = True
    return mod


def _load_embedding_catchup():
    """Load embedding_catchup module with mocked database dependency.

    Returns the module object with database functions mocked.
    """
    # Clear cached
    for key in list(sys.modules.keys()):
        if "embedding_catchup" in key:
            del sys.modules[key]

    # Create a fake package that embedding_catchup thinks it belongs to
    pkg_name = "_test_scripts_pkg"
    pkg = ModuleType(pkg_name)
    pkg.__path__ = [_scripts_path]
    pkg.__package__ = pkg_name

    # Mock submodules
    mock_db = _make_mock_database()

    # The real embeddings module - import it to get the actual constant
    sys.modules.pop("embeddings", None)
    from embeddings import MIN_CATCHUP_RAM_MB, generate_embedding, generate_embedding_text
    mock_emb_mod = ModuleType(f"{pkg_name}.embeddings")
    mock_emb_mod.generate_embedding = generate_embedding
    mock_emb_mod.generate_embedding_text = generate_embedding_text
    mock_emb_mod.MIN_CATCHUP_RAM_MB = MIN_CATCHUP_RAM_MB

    mock_db_mod = _make_mock_database()
    mock_db_mod.__name__ = f"{pkg_name}.database"

    sys.modules[pkg_name] = pkg
    sys.modules[f"{pkg_name}.database"] = mock_db_mod
    sys.modules[f"{pkg_name}.embeddings"] = mock_emb_mod

    # Now load embedding_catchup as a submodule of our fake package
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.embedding_catchup",
        Path(_scripts_path) / "embedding_catchup.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.embedding_catchup"] = mod
    spec.loader.exec_module(mod)

    # Cleanup fake package from sys.modules
    for key in [pkg_name, f"{pkg_name}.database",
                f"{pkg_name}.embeddings", f"{pkg_name}.embedding_catchup"]:
        sys.modules.pop(key, None)

    return mod


@pytest.fixture
def ec_module():
    """Provide a freshly-loaded embedding_catchup module with mocked DB deps."""
    return _load_embedding_catchup()


class TestMinCatchupRamMbConstant:
    """Tests for the MIN_CATCHUP_RAM_MB constant and its wiring."""

    def test_constant_value_is_75(self):
        """The constant should be 75.0 MB - guards against unintended changes."""
        from embeddings import MIN_CATCHUP_RAM_MB
        assert MIN_CATCHUP_RAM_MB == 75.0

    def test_constant_is_positive(self):
        """MIN_CATCHUP_RAM_MB must be positive to be a meaningful threshold."""
        from embeddings import MIN_CATCHUP_RAM_MB
        assert MIN_CATCHUP_RAM_MB > 0

    def test_embed_pending_memories_default_uses_constant(self, ec_module):
        """Default min_ram_mb parameter should equal MIN_CATCHUP_RAM_MB."""
        from embeddings import MIN_CATCHUP_RAM_MB
        sig = inspect.signature(ec_module.embed_pending_memories)
        default = sig.parameters["min_ram_mb"].default
        assert default == MIN_CATCHUP_RAM_MB


class TestGetAvailableRamMb:
    """Tests for get_available_ram_mb() - platform-specific RAM detection."""

    def test_psutil_path_returns_available_mb(self, ec_module):
        """When psutil is available, should return available memory in MB."""
        mock_psutil = MagicMock()
        mock_psutil.virtual_memory.return_value = MagicMock(
            available=512 * 1024 * 1024  # 512 MB in bytes
        )

        with patch.dict(sys.modules, {"psutil": mock_psutil}):
            result = ec_module.get_available_ram_mb()
            assert result == pytest.approx(512.0)

    def test_psutil_import_error_falls_through(self, ec_module):
        """When psutil is not available, should fall through to platform methods."""
        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="Unknown"):
            result = ec_module.get_available_ram_mb()
            assert result == -1.0

    def test_darwin_vm_stat_fallback(self, ec_module):
        """On macOS without psutil, should parse vm_stat output."""
        vm_stat_output = (
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages free:                               10000.\n"
            "Pages active:                             50000.\n"
            "Pages inactive:                           30000.\n"
            "Pages speculative:                         5000.\n"
            "Pages wired down:                         20000.\n"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = vm_stat_output

        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="Darwin"), \
             patch.object(ec_module.subprocess, "run", return_value=mock_result):
            result = ec_module.get_available_ram_mb()
            # (free + speculative + inactive) * page_size / (1024 * 1024)
            # (10000 + 5000 + 30000) * 16384 / (1024 * 1024) = 703.125
            assert result == pytest.approx(703.125)

    def test_darwin_vm_stat_default_page_size(self, ec_module):
        """On macOS, should use 4096 as default page size if not parsed."""
        vm_stat_output = (
            "Mach Virtual Memory Statistics:\n"
            "Pages free:                               25600.\n"
            "Pages speculative:                            0.\n"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = vm_stat_output

        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="Darwin"), \
             patch.object(ec_module.subprocess, "run", return_value=mock_result):
            result = ec_module.get_available_ram_mb()
            # 25600 * 4096 / (1024 * 1024) = 100.0
            assert result == pytest.approx(100.0)

    def test_darwin_vm_stat_nonzero_return_code(self, ec_module):
        """On macOS, if vm_stat returns error, should fall through."""
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="Darwin"), \
             patch.object(ec_module.subprocess, "run", return_value=mock_result):
            result = ec_module.get_available_ram_mb()
            assert result == -1.0

    def test_darwin_vm_stat_timeout(self, ec_module):
        """On macOS, if vm_stat times out, should fall through."""
        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="Darwin"), \
             patch.object(ec_module.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(
                              cmd="vm_stat", timeout=5)):
            result = ec_module.get_available_ram_mb()
            assert result == -1.0

    def test_linux_proc_meminfo_fallback(self, ec_module):
        """On Linux without psutil, should parse /proc/meminfo."""
        meminfo_content = (
            "MemTotal:       16384000 kB\n"
            "MemFree:         4096000 kB\n"
            "MemAvailable:    8192000 kB\n"
            "Buffers:          512000 kB\n"
        )

        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="Linux"), \
             patch("builtins.open", return_value=StringIO(meminfo_content)):
            result = ec_module.get_available_ram_mb()
            # 8192000 kB / 1024 = 8000.0 MB
            assert result == pytest.approx(8000.0)

    def test_linux_proc_meminfo_io_error(self, ec_module):
        """On Linux, if /proc/meminfo can't be read, should return -1.0."""
        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="Linux"), \
             patch("builtins.open", side_effect=IOError("Permission denied")):
            result = ec_module.get_available_ram_mb()
            assert result == -1.0

    def test_unknown_platform_returns_negative_one(self, ec_module):
        """On unsupported platforms without psutil, should return -1.0."""
        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="FreeBSD"):
            result = ec_module.get_available_ram_mb()
            assert result == -1.0


class TestEmbedPendingMemories:
    """Tests for embed_pending_memories() - RAM threshold and processing logic."""

    def test_skips_when_ram_below_threshold(self, ec_module):
        """Should set skipped_ram=True when available RAM is below min_ram_mb."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=50.0):
            result = ec_module.embed_pending_memories(min_ram_mb=75.0)
            assert result["skipped_ram"] is True
            assert result["processed"] == 0

    def test_proceeds_when_ram_above_threshold(self, ec_module):
        """Should not skip when available RAM exceeds min_ram_mb."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=100.0), \
             patch.object(ec_module, "get_unembedded_memories", return_value=[]):
            result = ec_module.embed_pending_memories(min_ram_mb=75.0)
            assert result["skipped_ram"] is False

    def test_proceeds_when_ram_exactly_at_threshold(self, ec_module):
        """At exactly the threshold, should proceed (condition is strictly less than)."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=75.0), \
             patch.object(ec_module, "get_unembedded_memories", return_value=[]):
            result = ec_module.embed_pending_memories(min_ram_mb=75.0)
            assert result["skipped_ram"] is False

    def test_proceeds_when_ram_undetermined(self, ec_module):
        """When RAM can't be determined (-1.0), should proceed (not skip)."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=-1.0), \
             patch.object(ec_module, "get_unembedded_memories", return_value=[]):
            result = ec_module.embed_pending_memories()
            assert result["skipped_ram"] is False

    def test_returns_early_when_no_unembedded(self, ec_module):
        """Should return early with zero processed when no unembedded memories."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=200.0), \
             patch.object(ec_module, "get_unembedded_memories", return_value=[]):
            result = ec_module.embed_pending_memories()
            assert result["processed"] == 0
            assert result["failed"] is False

    def test_processes_memories_serially(self, ec_module):
        """Should process memories one at a time, counting successes."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=200.0), \
             patch.object(ec_module, "get_unembedded_memories",
                          return_value=["id1", "id2", "id3"]), \
             patch.object(ec_module, "embed_single_memory", return_value=True):
            result = ec_module.embed_pending_memories()
            assert result["processed"] == 3
            assert result["failed"] is False

    def test_stops_on_first_failure(self, ec_module):
        """Should stop processing on first embed_single_memory failure."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=200.0), \
             patch.object(ec_module, "get_unembedded_memories",
                          return_value=["id1", "id2", "id3"]), \
             patch.object(ec_module, "embed_single_memory",
                          side_effect=[True, False]):
            result = ec_module.embed_pending_memories()
            assert result["processed"] == 1
            assert result["failed"] is True
            assert "id2" in result["error"]

    def test_stops_on_exception(self, ec_module):
        """Should stop and record error on exception during embedding."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=200.0), \
             patch.object(ec_module, "get_unembedded_memories",
                          return_value=["id1"]), \
             patch.object(ec_module, "embed_single_memory",
                          side_effect=RuntimeError("Connection lost")):
            result = ec_module.embed_pending_memories()
            assert result["failed"] is True
            assert "Connection lost" in result["error"]

    def test_error_message_truncated_to_100_chars(self, ec_module):
        """Error messages should be truncated to 100 characters."""
        long_error = "x" * 200

        with patch.object(ec_module, "get_available_ram_mb", return_value=200.0), \
             patch.object(ec_module, "get_unembedded_memories",
                          return_value=["id1"]), \
             patch.object(ec_module, "embed_single_memory",
                          side_effect=RuntimeError(long_error)):
            result = ec_module.embed_pending_memories()
            assert len(result["error"]) == 100

    def test_custom_min_ram_mb_parameter(self, ec_module):
        """Should respect custom min_ram_mb value."""
        # 50MB available, threshold set to 100MB - should skip
        with patch.object(ec_module, "get_available_ram_mb", return_value=50.0):
            result = ec_module.embed_pending_memories(min_ram_mb=100.0)
            assert result["skipped_ram"] is True

        # 50MB available, threshold set to 25MB - should proceed
        with patch.object(ec_module, "get_available_ram_mb", return_value=50.0), \
             patch.object(ec_module, "get_unembedded_memories", return_value=[]):
            result = ec_module.embed_pending_memories(min_ram_mb=25.0)
            assert result["skipped_ram"] is False

    def test_result_structure(self, ec_module):
        """Result dict should have all expected keys."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=200.0), \
             patch.object(ec_module, "get_unembedded_memories", return_value=[]):
            result = ec_module.embed_pending_memories()
            assert "processed" in result
            assert "failed" in result
            assert "skipped_ram" in result
            assert "error" in result

    def test_limit_parameter_passed_through(self, ec_module):
        """The limit parameter should be passed to get_unembedded_memories."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=200.0), \
             patch.object(ec_module, "get_unembedded_memories",
                          return_value=[]) as mock_get:
            ec_module.embed_pending_memories(limit=5)
            mock_get.assert_called_once_with(project_id=None, limit=5)

    def test_project_id_parameter_passed_through(self, ec_module):
        """The project_id parameter should be passed to get_unembedded_memories."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=200.0), \
             patch.object(ec_module, "get_unembedded_memories",
                          return_value=[]) as mock_get:
            ec_module.embed_pending_memories(project_id="test-project")
            mock_get.assert_called_once_with(project_id="test-project", limit=20)

    def test_default_min_ram_mb_uses_constant(self, ec_module):
        """Default min_ram_mb parameter should equal MIN_CATCHUP_RAM_MB."""
        from embeddings import MIN_CATCHUP_RAM_MB
        sig = inspect.signature(ec_module.embed_pending_memories)
        default = sig.parameters["min_ram_mb"].default
        assert default == MIN_CATCHUP_RAM_MB


class TestGetUnembeddedMemories:
    """Tests for get_unembedded_memories() - finding memories without embeddings."""

    def test_returns_empty_when_extensions_disabled(self, ec_module):
        """Should return empty list when SQLite extensions are not available."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = False
        result = ec_module.get_unembedded_memories()
        assert result == []

    def test_returns_empty_on_db_exception(self, ec_module):
        """Should return empty list on any database error."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True
        ec_module.db_connection = MagicMock(side_effect=Exception("DB error"))
        result = ec_module.get_unembedded_memories()
        assert result == []

    def test_returns_memory_ids_from_db(self, ec_module):
        """Should return list of memory IDs that lack embeddings."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True

        # Mock the cursor chain: sqlite_master check returns a row,
        # then the LEFT JOIN query returns memory IDs
        mock_conn = MagicMock()
        mock_cursor_master = MagicMock()
        mock_cursor_master.fetchone.return_value = ("vec_memories",)
        mock_cursor_query = MagicMock()
        mock_cursor_query.fetchall.return_value = [("mem-1",), ("mem-2",), ("mem-3",)]
        mock_conn.execute.side_effect = [mock_cursor_master, mock_cursor_query]

        ec_module.db_connection = MagicMock()
        ec_module.db_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        ec_module.db_connection.return_value.__exit__ = MagicMock(return_value=False)

        result = ec_module.get_unembedded_memories(limit=10)
        assert result == ["mem-1", "mem-2", "mem-3"]

    def test_returns_empty_when_vec_table_missing(self, ec_module):
        """Should return empty list when vec_memories table does not exist."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # table not found
        mock_conn.execute.return_value = mock_cursor

        ec_module.db_connection = MagicMock()
        ec_module.db_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        ec_module.db_connection.return_value.__exit__ = MagicMock(return_value=False)

        result = ec_module.get_unembedded_memories()
        assert result == []

    def test_passes_project_id_filter(self, ec_module):
        """Should include project_id in query when provided."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True

        mock_conn = MagicMock()
        mock_cursor_master = MagicMock()
        mock_cursor_master.fetchone.return_value = ("vec_memories",)
        mock_cursor_query = MagicMock()
        mock_cursor_query.fetchall.return_value = [("mem-1",)]
        mock_conn.execute.side_effect = [mock_cursor_master, mock_cursor_query]

        ec_module.db_connection = MagicMock()
        ec_module.db_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        ec_module.db_connection.return_value.__exit__ = MagicMock(return_value=False)

        result = ec_module.get_unembedded_memories(project_id="my-proj", limit=5)
        assert result == ["mem-1"]
        # Verify the query params include project_id and limit
        call_args = mock_conn.execute.call_args_list[1]
        params = call_args[0][1]
        assert "my-proj" in params
        assert 5 in params


class TestEmbedSingleMemory:
    """Tests for embed_single_memory() - embedding a single memory by ID."""

    def test_returns_false_when_extensions_disabled(self, ec_module):
        """Should return False when SQLite extensions are not available."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = False
        result = ec_module.embed_single_memory("test-id")
        assert result is False

    def test_returns_false_on_exception(self, ec_module):
        """Should return False on any error."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True
        ec_module.db_connection = MagicMock(side_effect=Exception("DB error"))
        result = ec_module.embed_single_memory("test-id")
        assert result is False

    def test_returns_true_on_successful_embedding(self, ec_module):
        """Should return True when memory is found, embedded, and stored."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True

        mock_memory = {
            "id": "test-id",
            "context": "Test memory context",
            "project_id": "proj-1",
        }

        mock_conn = MagicMock()
        ec_module.db_connection = MagicMock()
        ec_module.db_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        ec_module.db_connection.return_value.__exit__ = MagicMock(return_value=False)

        ec_module.get_memory = MagicMock(return_value=mock_memory)
        ec_module.generate_embedding_text = MagicMock(return_value="embedding text")
        ec_module.generate_embedding = MagicMock(return_value=[0.1, 0.2, 0.3])

        # Mock sqlite_vec import within the function
        mock_sqlite_vec = MagicMock()
        with patch.dict(sys.modules, {"sqlite_vec": mock_sqlite_vec}):
            result = ec_module.embed_single_memory("test-id")

        assert result is True
        mock_conn.commit.assert_called_once()

    def test_returns_false_when_memory_not_found(self, ec_module):
        """Should return False when memory ID does not exist in DB."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True

        mock_conn = MagicMock()
        ec_module.db_connection = MagicMock()
        ec_module.db_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        ec_module.db_connection.return_value.__exit__ = MagicMock(return_value=False)

        ec_module.get_memory = MagicMock(return_value=None)

        result = ec_module.embed_single_memory("nonexistent-id")
        assert result is False

    def test_returns_false_when_embedding_generation_fails(self, ec_module):
        """Should return False when generate_embedding returns None."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True

        mock_conn = MagicMock()
        ec_module.db_connection = MagicMock()
        ec_module.db_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        ec_module.db_connection.return_value.__exit__ = MagicMock(return_value=False)

        ec_module.get_memory = MagicMock(return_value={"id": "test-id", "context": "ctx"})
        ec_module.generate_embedding_text = MagicMock(return_value="text")
        ec_module.generate_embedding = MagicMock(return_value=None)

        result = ec_module.embed_single_memory("test-id")
        assert result is False

    def test_returns_false_when_no_embedding_text(self, ec_module):
        """Should return False when generate_embedding_text returns empty string."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True

        mock_conn = MagicMock()
        ec_module.db_connection = MagicMock()
        ec_module.db_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        ec_module.db_connection.return_value.__exit__ = MagicMock(return_value=False)

        ec_module.get_memory = MagicMock(return_value={"id": "test-id", "context": "ctx"})
        ec_module.generate_embedding_text = MagicMock(return_value="")

        result = ec_module.embed_single_memory("test-id")
        assert result is False
