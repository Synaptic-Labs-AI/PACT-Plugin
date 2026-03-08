"""
Tests for pact-memory/scripts/setup_memory.py — memory system initialization.

Tests cover:
1. ensure_directories: creates PACT_MEMORY_DIR with correct permissions
2. check_dependencies: sqlite_vec and model2vec detection
3. ensure_initialized: directory + database init, failure handling
4. get_setup_status: full status report
5. _get_recommendations: dependency-based recommendations
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))


# ---------------------------------------------------------------------------
# ensure_directories
# ---------------------------------------------------------------------------

class TestEnsureDirectories:
    def test_creates_directory(self, tmp_path):
        from scripts.setup_memory import ensure_directories
        target = tmp_path / "pact-memory"
        with patch("scripts.setup_memory.PACT_MEMORY_DIR", target):
            ensure_directories()
        assert target.is_dir()

    def test_idempotent(self, tmp_path):
        from scripts.setup_memory import ensure_directories
        target = tmp_path / "pact-memory"
        with patch("scripts.setup_memory.PACT_MEMORY_DIR", target):
            ensure_directories()
            ensure_directories()  # Should not raise
        assert target.is_dir()


# ---------------------------------------------------------------------------
# check_dependencies
# ---------------------------------------------------------------------------

class TestCheckDependencies:
    def test_both_missing(self):
        from scripts.setup_memory import check_dependencies
        with (
            patch.dict("sys.modules", {"sqlite_vec": None}),
            patch("builtins.__import__", side_effect=_import_blocker({"sqlite_vec", "model2vec"})),
        ):
            result = check_dependencies()
        assert result["sqlite_vec"] is False
        assert result["model2vec"] is False

    def test_returns_dict_keys(self):
        from scripts.setup_memory import check_dependencies
        result = check_dependencies()
        assert "sqlite_vec" in result
        assert "model2vec" in result


# ---------------------------------------------------------------------------
# ensure_initialized
# ---------------------------------------------------------------------------

class TestEnsureInitialized:
    def test_success(self, tmp_path):
        from scripts.setup_memory import ensure_initialized
        target = tmp_path / "pact-memory"
        with (
            patch("scripts.setup_memory.PACT_MEMORY_DIR", target),
            patch("scripts.setup_memory.ensure_directories"),
        ):
            result = ensure_initialized()
        assert result is True

    def test_returns_false_on_db_failure(self, tmp_path):
        from scripts.setup_memory import ensure_initialized
        target = tmp_path / "pact-memory"
        with (
            patch("scripts.setup_memory.PACT_MEMORY_DIR", target),
            patch("scripts.setup_memory.ensure_directories"),
        ):
            # Make the relative import fail
            original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
            def fail_import(name, *args, **kwargs):
                if 'database' in str(name):
                    raise Exception("DB init failed")
                return original_import(name, *args, **kwargs)
            with patch("builtins.__import__", side_effect=fail_import):
                result = ensure_initialized()
        assert result is False


# ---------------------------------------------------------------------------
# _get_recommendations
# ---------------------------------------------------------------------------

class TestGetRecommendations:
    def test_both_missing(self):
        from scripts.setup_memory import _get_recommendations
        recs = _get_recommendations({"sqlite_vec": False, "model2vec": False})
        assert len(recs) == 2
        assert any("sqlite-vec" in r for r in recs)
        assert any("model2vec" in r for r in recs)

    def test_both_installed(self):
        from scripts.setup_memory import _get_recommendations
        recs = _get_recommendations({"sqlite_vec": True, "model2vec": True})
        assert recs == []

    def test_partial_install(self):
        from scripts.setup_memory import _get_recommendations
        recs = _get_recommendations({"sqlite_vec": True, "model2vec": False})
        assert len(recs) == 1
        assert "model2vec" in recs[0]


# ---------------------------------------------------------------------------
# get_setup_status
# ---------------------------------------------------------------------------

class TestGetSetupStatus:
    def test_returns_expected_keys(self, tmp_path):
        from scripts.setup_memory import get_setup_status
        with patch("scripts.setup_memory.PACT_MEMORY_DIR", tmp_path):
            status = get_setup_status()
        assert "initialized" in status
        assert "dependencies" in status
        assert "can_use_semantic_search" in status
        assert "paths" in status
        assert "recommendations" in status

    def test_initialized_when_dir_exists(self, tmp_path):
        from scripts.setup_memory import get_setup_status
        with patch("scripts.setup_memory.PACT_MEMORY_DIR", tmp_path):
            status = get_setup_status()
        assert status["initialized"] is True

    def test_not_initialized_when_dir_missing(self, tmp_path):
        from scripts.setup_memory import get_setup_status
        missing = tmp_path / "nonexistent"
        with patch("scripts.setup_memory.PACT_MEMORY_DIR", missing):
            status = get_setup_status()
        assert status["initialized"] is False

    def test_semantic_search_requires_both_deps(self, tmp_path):
        from scripts.setup_memory import get_setup_status
        with (
            patch("scripts.setup_memory.PACT_MEMORY_DIR", tmp_path),
            patch("scripts.setup_memory.check_dependencies", return_value={"sqlite_vec": True, "model2vec": False}),
        ):
            status = get_setup_status()
        assert status["can_use_semantic_search"] is False


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _import_blocker(blocked_names):
    """Create an import side_effect that blocks specific modules."""
    real_import = __import__
    def _import(name, *args, **kwargs):
        if any(blocked in name for blocked in blocked_names):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)
    return _import
