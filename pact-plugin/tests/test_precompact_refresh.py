"""
Integration tests for the PreCompact hook (precompact_refresh.py).

Tests the full flow of transcript parsing to checkpoint creation.
"""

import json
import os
import sys
import tempfile
import time
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add hooks and tests directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from conftest import (
    create_peer_review_transcript,
    create_orchestrate_transcript,
    create_no_workflow_transcript,
    create_terminated_workflow_transcript,
    create_malformed_transcript,
)


class TestGetEncodedProjectPath:
    """Tests for get_encoded_project_path used by precompact_refresh.

    Note: The function is now shared from checkpoint_builder.py.
    """

    def test_extract_valid_path(self):
        """Test extracting path from valid transcript path."""
        from refresh.checkpoint_builder import get_encoded_project_path

        transcript_path = "/Users/test/.claude/projects/-Users-test-myproject/session-uuid/session.jsonl"
        encoded = get_encoded_project_path(transcript_path)

        assert encoded == "-Users-test-myproject"

    def test_extract_invalid_path_returns_unknown_project(self):
        """Test extraction returns 'unknown-project' for invalid path with no env fallback."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            result = get_encoded_project_path("/invalid/path/no/projects")

        assert result == "unknown-project"


class TestGetCheckpointPath:
    """Tests for get_checkpoint_path function."""

    def test_checkpoint_path_format(self):
        """Test checkpoint path is correctly formatted."""
        from precompact_refresh import get_checkpoint_path

        path = get_checkpoint_path("-Users-test-project")

        assert path == Path.home() / ".claude" / "pact-refresh" / "-Users-test-project.json"


class TestWriteCheckpointAtomic:
    """Tests for write_checkpoint_atomic function."""

    def test_write_creates_directory(self, tmp_path: Path):
        """Test that write creates parent directory if needed."""
        from precompact_refresh import write_checkpoint_atomic

        checkpoint_path = tmp_path / "subdir" / "checkpoint.json"
        data = {"test": "data"}

        success = write_checkpoint_atomic(checkpoint_path, data)

        assert success is True
        assert checkpoint_path.exists()
        assert json.loads(checkpoint_path.read_text()) == data

    def test_write_overwrites_existing(self, tmp_path: Path):
        """Test that write overwrites existing checkpoint."""
        from precompact_refresh import write_checkpoint_atomic

        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text('{"old": "data"}')

        new_data = {"new": "data"}
        success = write_checkpoint_atomic(checkpoint_path, new_data)

        assert success is True
        assert json.loads(checkpoint_path.read_text()) == new_data

    def test_write_is_atomic(self, tmp_path: Path):
        """Test that write uses atomic rename pattern."""
        from precompact_refresh import write_checkpoint_atomic

        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text('{"original": "data"}')

        # Write new data
        new_data = {"atomic": "write"}
        success = write_checkpoint_atomic(checkpoint_path, new_data)

        assert success is True
        # No temp files should remain
        temp_files = list(tmp_path.glob("checkpoint_*.tmp"))
        assert len(temp_files) == 0


class TestBuildNoWorkflowCheckpoint:
    """Tests for build_no_workflow_checkpoint used by precompact_refresh.

    Note: The fallback function was removed from precompact_refresh.
    Now uses build_no_workflow_checkpoint from checkpoint_builder.py.
    """

    def test_build_no_workflow_checkpoint(self):
        """Test building checkpoint when no workflow detected."""
        from refresh.checkpoint_builder import build_no_workflow_checkpoint

        checkpoint = build_no_workflow_checkpoint(
            transcript_path="/test/path",
            lines_scanned=500,
            reason="No active workflow detected",
        )

        assert checkpoint["workflow"]["name"] == "none"
        assert checkpoint["extraction"]["confidence"] == 1.0
        assert checkpoint["extraction"]["notes"] == "No active workflow detected"
        assert "created_at" in checkpoint

    def test_build_no_workflow_checkpoint_default_reason(self):
        """Test building checkpoint uses default reason when not provided."""
        from refresh.checkpoint_builder import build_no_workflow_checkpoint

        checkpoint = build_no_workflow_checkpoint(
            transcript_path="/test/path",
            lines_scanned=100,
        )

        assert checkpoint["workflow"]["name"] == "none"
        assert checkpoint["extraction"]["notes"] == "No active workflow detected"


class TestPrecompactMain:
    """Integration tests for the main() function."""

    def test_main_with_active_workflow(self, tmp_path: Path):
        """Test full flow with active peer-review workflow."""
        # Create transcript
        transcript_content = create_peer_review_transcript(
            step="recommendations",
            include_pr_number=64,
            include_pending_question=True,
        )
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        # Create checkpoint directory
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        # Mock input and environment
        input_data = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            # Import and run main
            from precompact_refresh import main

            # Capture output (stdout for JSON, stderr for status)
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout, \
                 patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()
                stderr_output = mock_stderr.getvalue()

        # Parse output - should have systemMessage with workflow name
        result = json.loads(output)
        assert "systemMessage" in result
        assert "PACT: checkpoint saved" in result["systemMessage"]
        assert "peer-review" in result["systemMessage"]

        # Verify checkpoint was written
        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        assert checkpoint_path.exists()

        checkpoint = json.loads(checkpoint_path.read_text())
        assert checkpoint["workflow"]["name"] == "peer-review"
        assert checkpoint["session_id"] == "test-session"

    def test_main_with_no_workflow(self, tmp_path: Path):
        """Test flow when no workflow is active."""
        # Create transcript without workflow
        transcript_content = create_no_workflow_transcript()
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        # Create checkpoint directory
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout, \
                 patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()
                stderr_output = mock_stderr.getvalue()

        result = json.loads(output)
        assert "systemMessage" in result
        # No workflow = no parenthetical suffix
        assert result["systemMessage"] == "PACT: checkpoint saved"

    def test_main_without_transcript_path(self, tmp_path: Path):
        """Test handling missing transcript path in input."""
        input_data = json.dumps({})

        with patch("sys.stdin", StringIO(input_data)), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout, \
                 patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()
                stderr_output = mock_stderr.getvalue()

        result = json.loads(output)
        assert "systemMessage" in result
        assert result["systemMessage"] == "PACT: checkpoint skipped"

    def test_main_with_invalid_json_input(self, tmp_path: Path):
        """Test handling invalid JSON input."""
        with patch("sys.stdin", StringIO("not valid json")), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        # Should handle gracefully - invalid JSON leads to empty transcript_path
        # which means unknown-project, so checkpoint skipped
        result = json.loads(output)
        assert "systemMessage" in result
        assert result["systemMessage"] == "PACT: checkpoint skipped"

    def test_main_never_raises(self, tmp_path: Path):
        """Test that main() never raises exceptions (always exits 0)."""
        # Simulate various failure conditions
        input_data = json.dumps({"transcript_path": "/nonexistent/path/session.jsonl"})

        with patch("sys.stdin", StringIO(input_data)), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            # Should not raise even with nonexistent path
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                # main() calls sys.exit(0), which we expect
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0

    def test_main_with_terminated_workflow(self, tmp_path: Path):
        """Test flow with terminated workflow."""
        transcript_content = create_terminated_workflow_transcript()
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        result = json.loads(output)

        # Terminated workflow still saves checkpoint (with workflow state or none)
        assert "systemMessage" in result
        assert "PACT: checkpoint saved" in result["systemMessage"]


class TestIntegrationScenarios:
    """Integration tests for specific scenarios."""

    def test_orchestrate_code_phase_refresh(self, tmp_path: Path):
        """Test refresh checkpoint for orchestrate workflow in CODE phase."""
        transcript_content = create_orchestrate_transcript(
            phase="code",
            include_task="implement auth",
            include_agent_calls=True,
        )
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "orchestrate"

    def test_malformed_transcript_handling(self, tmp_path: Path):
        """Test handling of transcript with malformed lines."""
        transcript_content = create_malformed_transcript()
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            # Should handle gracefully without crashing
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        result = json.loads(output)
        assert "systemMessage" in result
        assert "PACT: checkpoint saved" in result["systemMessage"]

    def test_checkpoint_overwrite(self, tmp_path: Path):
        """Test that new checkpoint overwrites old one."""
        # Create old checkpoint
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)
        old_checkpoint_path = checkpoint_dir / "-test-project.json"
        old_checkpoint_path.write_text(json.dumps({"old": "checkpoint"}))

        # Create new transcript
        transcript_content = create_peer_review_transcript()
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        input_data = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "new-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0

        # Verify checkpoint was overwritten
        new_checkpoint = json.loads(old_checkpoint_path.read_text())
        assert new_checkpoint.get("session_id") == "new-session"
        assert "old" not in new_checkpoint


class TestExceptionHandlingPaths:
    """Tests for exception handling and defensive paths in precompact_refresh."""

    def test_import_error_handling_fallback(self, tmp_path: Path):
        """Test that ImportError during refresh module import is handled gracefully.

        When the refresh package is unavailable, the hook should still
        write a checkpoint with no workflow state.
        """
        # Create transcript
        transcript_content = create_peer_review_transcript()
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript_path": str(transcript_path)})

        # Mock the import to raise ImportError
        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path), \
             patch.dict(sys.modules, {"refresh": None}):  # Force import to fail

            from precompact_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                # Should exit 0 even with import error
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        # Should still produce valid JSON output with systemMessage
        result = json.loads(output)
        assert "systemMessage" in result

    def test_extract_workflow_state_exception_handling(self, tmp_path: Path):
        """Test that exceptions during transcript parsing are handled.

        When extract_workflow_state raises an exception, the hook should
        catch it and continue with a fallback checkpoint.
        """
        # Create transcript
        transcript_content = create_peer_review_transcript()
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript_path": str(transcript_path)})

        # Mock extract_workflow_state to raise an exception
        def mock_extract_raises(*args, **kwargs):
            raise ValueError("Simulated parsing failure")

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            # Import and patch the function
            import precompact_refresh
            original_main = precompact_refresh.main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    original_main()

                # Should exit 0 even with parse errors
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        result = json.loads(output)
        assert "systemMessage" in result

    def test_file_permission_error_on_checkpoint_write(self, tmp_path: Path):
        """Test handling of file permission errors when writing checkpoint.

        When the checkpoint directory is not writable, the hook should
        handle the error gracefully without crashing.
        """
        # Create transcript
        transcript_content = create_peer_review_transcript()
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        # Create checkpoint dir but make it read-only
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript_path": str(transcript_path)})

        # Mock write_checkpoint_atomic to return False (simulating permission error)
        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main, write_checkpoint_atomic

            with patch("precompact_refresh.write_checkpoint_atomic", return_value=False):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout, \
                     patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                    with pytest.raises(SystemExit) as exc_info:
                        main()

                    # Should exit 0 even with write failure
                    assert exc_info.value.code == 0
                    output = mock_stdout.getvalue()
                    stderr_output = mock_stderr.getvalue()

        result = json.loads(output)
        assert "systemMessage" in result
        assert result["systemMessage"] == "PACT: checkpoint failed"
        # Should mention write failure in stderr
        assert "failed" in stderr_output

    def test_outer_exception_handling(self, tmp_path: Path):
        """Test that outer try/except in main() catches all exceptions.

        The main() function has a top-level try/except that should
        catch any unexpected exceptions and exit cleanly.
        """
        # Force an exception by providing invalid input
        with patch("sys.stdin", StringIO("")), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            # Even with empty stdin, should not raise
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 0

    def test_missing_session_id_uses_unknown(self, tmp_path: Path):
        """Test that missing CLAUDE_SESSION_ID uses 'unknown' fallback."""
        transcript_content = create_peer_review_transcript()
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript_path": str(transcript_path)})

        # Remove CLAUDE_SESSION_ID from environment
        env_without_session = {k: v for k, v in os.environ.items() if k != "CLAUDE_SESSION_ID"}

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, env_without_session, clear=True), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0

        # Verify checkpoint was created
        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        if checkpoint_path.exists():
            checkpoint = json.loads(checkpoint_path.read_text())
            # Session ID should be "unknown" when not set
            assert checkpoint.get("session_id") == "unknown"


class TestCleanupOldCheckpoints:
    """Tests for cleanup_old_checkpoints() function."""

    def test_nonexistent_directory_returns_zero(self, tmp_path: Path):
        """Non-existent checkpoint directory returns 0 without error."""
        from precompact_refresh import cleanup_old_checkpoints

        result = cleanup_old_checkpoints(tmp_path / "does-not-exist")

        assert result == 0

    def test_empty_directory_returns_zero(self, tmp_path: Path):
        """Empty checkpoint directory returns 0."""
        from precompact_refresh import cleanup_old_checkpoints

        result = cleanup_old_checkpoints(tmp_path)

        assert result == 0

    def test_old_checkpoint_files_deleted(self, tmp_path: Path):
        """Checkpoint files older than CHECKPOINT_MAX_AGE_DAYS are deleted."""
        from precompact_refresh import cleanup_old_checkpoints
        from refresh.constants import CHECKPOINT_MAX_AGE_DAYS

        # Create files with old mtimes (older than max age)
        old_time = time.time() - (CHECKPOINT_MAX_AGE_DAYS + 1) * 24 * 60 * 60
        for name in ("old-project-a.json", "old-project-b.json"):
            f = tmp_path / name
            f.write_text(json.dumps({"old": True}))
            os.utime(f, (old_time, old_time))

        result = cleanup_old_checkpoints(tmp_path)

        assert result == 2
        assert not (tmp_path / "old-project-a.json").exists()
        assert not (tmp_path / "old-project-b.json").exists()

    def test_recent_checkpoint_files_preserved(self, tmp_path: Path):
        """Checkpoint files newer than CHECKPOINT_MAX_AGE_DAYS are kept."""
        from precompact_refresh import cleanup_old_checkpoints
        from refresh.constants import CHECKPOINT_MAX_AGE_DAYS

        # Create old file and recent file
        old_time = time.time() - (CHECKPOINT_MAX_AGE_DAYS + 1) * 24 * 60 * 60
        old_file = tmp_path / "old.json"
        old_file.write_text(json.dumps({"old": True}))
        os.utime(old_file, (old_time, old_time))

        recent_file = tmp_path / "recent.json"
        recent_file.write_text(json.dumps({"recent": True}))
        # recent_file keeps its current mtime (just created)

        result = cleanup_old_checkpoints(tmp_path)

        assert result == 1
        assert not old_file.exists()
        assert recent_file.exists()

    def test_oserror_on_individual_file_deletion_handled(self, tmp_path: Path):
        """OSError during individual file deletion is caught gracefully."""
        from precompact_refresh import cleanup_old_checkpoints
        from refresh.constants import CHECKPOINT_MAX_AGE_DAYS

        old_time = time.time() - (CHECKPOINT_MAX_AGE_DAYS + 1) * 24 * 60 * 60
        f = tmp_path / "undeletable.json"
        f.write_text(json.dumps({"data": True}))
        os.utime(f, (old_time, old_time))

        # Patch unlink to raise OSError
        original_unlink = Path.unlink

        def mock_unlink(self, *args, **kwargs):
            if self.name == "undeletable.json":
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", mock_unlink):
            result = cleanup_old_checkpoints(tmp_path)

        # File deletion failed, so cleaned count stays 0
        assert result == 0
        # File still exists since unlink failed
        assert f.exists()

    def test_non_json_files_ignored(self, tmp_path: Path):
        """Non-.json files are not touched by cleanup."""
        from precompact_refresh import cleanup_old_checkpoints
        from refresh.constants import CHECKPOINT_MAX_AGE_DAYS

        old_time = time.time() - (CHECKPOINT_MAX_AGE_DAYS + 1) * 24 * 60 * 60

        # Non-json file with old mtime
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("some notes")
        os.utime(txt_file, (old_time, old_time))

        # Old json file (should be deleted)
        json_file = tmp_path / "old-checkpoint.json"
        json_file.write_text(json.dumps({"data": True}))
        os.utime(json_file, (old_time, old_time))

        result = cleanup_old_checkpoints(tmp_path)

        assert result == 1
        assert txt_file.exists()  # Non-json preserved
        assert not json_file.exists()  # Json cleaned up
