"""
Integration tests for the SessionStart hook (compaction_refresh.py).

Tests refresh detection and instruction injection after compaction.
"""

import json
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
# Add tests directory to path for helpers module
sys.path.insert(0, str(Path(__file__).parent))


class TestGetEncodedProjectPath:
    """Tests for get_encoded_project_path used by compaction_refresh.

    Note: The function is now shared from checkpoint_builder.py.
    These tests verify the usage pattern in compaction_refresh where
    an empty transcript path is passed to trigger the env var fallback.
    """

    def test_encodes_project_path_from_env(self):
        """Test encoding project path from environment when transcript path is empty."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/Users/test/myproject"}):
            # compaction_refresh passes empty string to use env fallback
            encoded = get_encoded_project_path("")

        assert encoded == "-Users-test-myproject"

    def test_handles_nested_path(self):
        """Test encoding deeply nested project path."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/home/user/code/org/repo"}):
            encoded = get_encoded_project_path("")

        assert encoded == "-home-user-code-org-repo"

    def test_returns_unknown_project_when_not_set(self):
        """Test returns 'unknown-project' when CLAUDE_PROJECT_DIR not set."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            encoded = get_encoded_project_path("")

        assert encoded == "unknown-project"


class TestCompactionRefreshMain:
    """Integration tests for the main() function.

    Post-#413: only the TaskList-based primary path remains. The checkpoint
    fallback (and all its edge-case tests) were removed when precompact_refresh
    was deleted; covered tests here are the source!=compact short-circuit,
    empty-tasks → suppressOutput, and defensive exception handling.
    """

    def test_main_non_compact_source(self, tmp_path: Path, pact_context):
        """Test that non-compact sessions are ignored."""
        pact_context(session_id="test-session-123")

        # Source is NOT "compact"
        input_data = json.dumps({"source": "new"})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()

                # Should exit 0 without refresh (not a compact session)
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()
                # Bare exit path: suppressOutput to prevent false "hook error"
                assert json.loads(output.strip()) == {"suppressOutput": True}

    def test_main_tasks_empty_suppresses_output(self, tmp_path: Path, pact_context):
        """Post-#413: when get_task_list() returns None on compact source,
        emit suppressOutput (no stale checkpoint fallback)."""
        pact_context(session_id="test-session")

        input_data = json.dumps({"source": "compact"})
        with patch("sys.stdin", StringIO(input_data)), \
             patch("compaction_refresh.get_task_list", return_value=None), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        assert json.loads(output.strip()) == {"suppressOutput": True}

    def test_main_never_raises(self, tmp_path: Path):
        """Test that main() never raises exceptions."""
        # Invalid JSON input
        with patch("sys.stdin", StringIO("invalid json {")), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            # Should not raise
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 0

    def test_main_with_invalid_json_input(self, tmp_path: Path):
        """Test handling of invalid JSON input."""
        with patch("sys.stdin", StringIO("not json")), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with pytest.raises(SystemExit) as exc_info:
                main()

            # Should exit cleanly
            assert exc_info.value.code == 0


class TestExceptionHandlingPaths:
    """Tests for exception handling and defensive paths in compaction_refresh."""

    def test_main_outer_exception_handling(self, tmp_path: Path):
        """Test that outer try/except in main() catches all exceptions.

        The main() function has a top-level try/except that should
        catch any unexpected exceptions and exit cleanly.
        """
        # Simulate an exception by patching stdin to raise
        class RaisingStdin:
            def read(self):
                raise RuntimeError("Simulated stdin error")

        with patch("sys.stdin", RaisingStdin()), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            # Should not raise, should exit 0
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 0

    def test_get_encoded_project_path_empty_env_string(self):
        """Test handling of empty string CLAUDE_PROJECT_DIR returns unknown-project."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            result = get_encoded_project_path("")

        # Empty env var triggers "unknown-project" fallback
        assert result == "unknown-project"
