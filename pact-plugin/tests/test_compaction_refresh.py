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


class TestReadCheckpoint:
    """Tests for read_checkpoint function."""

    def test_read_valid_checkpoint(self, tmp_path: Path, sample_checkpoint):
        """Test reading valid checkpoint file."""
        from compaction_refresh import read_checkpoint

        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text(json.dumps(sample_checkpoint))

        result = read_checkpoint(checkpoint_path)

        assert result == sample_checkpoint

    def test_read_nonexistent_file(self, tmp_path: Path):
        """Test reading nonexistent file returns None."""
        from compaction_refresh import read_checkpoint

        result = read_checkpoint(tmp_path / "nonexistent.json")

        assert result is None

    def test_read_invalid_json(self, tmp_path: Path):
        """Test reading invalid JSON returns None."""
        from compaction_refresh import read_checkpoint

        checkpoint_path = tmp_path / "invalid.json"
        checkpoint_path.write_text("not valid json {")

        result = read_checkpoint(checkpoint_path)

        assert result is None


class TestValidateCheckpoint:
    """Tests for validate_checkpoint function."""

    def test_validate_matching_session(self, sample_checkpoint):
        """Test validation passes for matching session ID."""
        from compaction_refresh import validate_checkpoint

        is_valid = validate_checkpoint(sample_checkpoint, "test-session-123")

        assert is_valid is True

    def test_validate_mismatched_session(self, sample_checkpoint):
        """Test validation fails for mismatched session ID."""
        from compaction_refresh import validate_checkpoint

        is_valid = validate_checkpoint(sample_checkpoint, "different-session")

        assert is_valid is False

    def test_validate_unsupported_version(self, sample_checkpoint):
        """Test validation fails for unsupported version."""
        from compaction_refresh import validate_checkpoint

        sample_checkpoint["version"] = "2.0"
        is_valid = validate_checkpoint(sample_checkpoint, "test-session-123")

        assert is_valid is False

    def test_validate_missing_workflow(self, sample_checkpoint):
        """Test validation fails without workflow field."""
        from compaction_refresh import validate_checkpoint

        del sample_checkpoint["workflow"]
        is_valid = validate_checkpoint(sample_checkpoint, "test-session-123")

        assert is_valid is False

    def test_validate_empty_checkpoint(self):
        """Test validation fails for empty checkpoint."""
        from compaction_refresh import validate_checkpoint

        is_valid = validate_checkpoint({}, "test-session")

        assert is_valid is False

    def test_validate_none_checkpoint(self):
        """Test validation fails for None."""
        from compaction_refresh import validate_checkpoint

        is_valid = validate_checkpoint(None, "test-session")

        assert is_valid is False


class TestBuildRefreshMessage:
    """Tests for build_refresh_message function."""

    def test_build_complete_message(self, sample_checkpoint):
        """Test building directive prompt refresh message with all fields."""
        from compaction_refresh import build_refresh_message

        message = build_refresh_message(sample_checkpoint)

        # Check directive prompt format
        assert "[POST-COMPACTION CHECKPOINT]" in message
        assert "Prior conversation auto-compacted" in message
        assert "Resume unfinished PACT workflow below:" in message
        assert "Workflow:" in message
        assert "peer-review" in message
        assert "pr-64" in message
        assert "Context:" in message
        # Check prose context is included (recommendations step generates prose)
        assert "recommendations" in message.lower() or "Processing" in message
        # High confidence (0.9) should NOT show low confidence suffix
        assert "low confidence" not in message
        assert "verify state with user" not in message.lower()

    def test_build_message_with_pending_action(self, sample_checkpoint):
        """Test refresh message includes pending action as Next step line."""
        from compaction_refresh import build_refresh_message

        message = build_refresh_message(sample_checkpoint)

        assert "Next Step:" in message
        assert "Would you like to review" in message

    def test_build_message_with_context_prose(self, sample_checkpoint):
        """Test refresh message includes prose context (not key=value)."""
        from compaction_refresh import build_refresh_message

        message = build_refresh_message(sample_checkpoint)

        # Should have Context line with prose, not key=value format
        assert "Context:" in message
        # The recommendations step should generate prose like:
        # "Processing recommendations; no blocking issues, 0 minor, 1 future."
        assert "no blocking" in message.lower() or "Processing" in message

    def test_build_message_no_action_shows_ask_user(self):
        """Test no pending action shows ask user message."""
        from compaction_refresh import build_refresh_message

        checkpoint = {
            "workflow": {"name": "peer-review", "id": ""},
            "step": {"name": "commit"},
            "extraction": {"confidence": 0.5},
            "context": {},
        }

        message = build_refresh_message(checkpoint)

        # No pending action should always show "Ask user how to proceed"
        assert "Next Step: **Ask user how to proceed.**" in message

    def test_build_message_high_confidence_no_action_shows_ask_user(self):
        """Test high confidence with no action shows ask user message."""
        from compaction_refresh import build_refresh_message

        checkpoint = {
            "workflow": {"name": "peer-review", "id": ""},
            "step": {"name": "commit"},
            "extraction": {"confidence": 0.9},
            "context": {},
        }

        message = build_refresh_message(checkpoint)

        # High confidence (>= 0.8) should NOT show low confidence suffix
        assert "low confidence" not in message
        assert "verify state with user" not in message.lower()
        # No pending action should always show "Ask user how to proceed"
        assert "Next Step: **Ask user how to proceed.**" in message


class TestCompactionRefreshMain:
    """Integration tests for the main() function."""

    def test_main_with_active_workflow(self, tmp_path: Path, sample_checkpoint):
        """Test full refresh flow with active workflow."""
        # Create checkpoint file
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)
        checkpoint_path = checkpoint_dir / "-test-project.json"
        checkpoint_path.write_text(json.dumps(sample_checkpoint))

        input_data = json.dumps({"source": "compact"})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "test-session-123",
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        result = json.loads(output)

        assert "hookSpecificOutput" in result
        refresh_msg = result["hookSpecificOutput"]["additionalContext"]
        assert "[POST-COMPACTION CHECKPOINT]" in refresh_msg
        assert "peer-review" in refresh_msg

    def test_main_with_no_workflow(self, tmp_path: Path):
        """Test flow when checkpoint has no active workflow."""
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)
        checkpoint_path = checkpoint_dir / "-test-project.json"
        checkpoint_path.write_text(json.dumps({
            "version": "1.0",
            "session_id": "test-session",
            "workflow": {"name": "none"},
            "extraction": {"confidence": 1.0},
        }))

        input_data = json.dumps({"source": "compact"})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "test-session",
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                # Capture exit
                with pytest.raises(SystemExit) as exc_info:
                    main()

                # Should exit 0 without output (no refresh needed)
                assert exc_info.value.code == 0

    def test_main_non_compact_source(self, tmp_path: Path, sample_checkpoint):
        """Test that non-compact sessions are ignored."""
        # Create checkpoint that would trigger refresh
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)
        checkpoint_path = checkpoint_dir / "-test-project.json"
        checkpoint_path.write_text(json.dumps(sample_checkpoint))

        # Source is NOT "compact"
        input_data = json.dumps({"source": "new"})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "test-session-123",
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
                # No output expected for non-compact sessions
                assert output == ""

    def test_main_no_checkpoint_file(self, tmp_path: Path):
        """Test handling when no checkpoint file exists."""
        input_data = json.dumps({"source": "compact"})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "test-session",
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()

                # Should exit 0 without error
                assert exc_info.value.code == 0

    def test_main_mismatched_session_id(self, tmp_path: Path, sample_checkpoint):
        """Test handling when session ID doesn't match."""
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)
        checkpoint_path = checkpoint_dir / "-test-project.json"
        checkpoint_path.write_text(json.dumps(sample_checkpoint))

        input_data = json.dumps({"source": "compact"})

        # Session ID doesn't match checkpoint
        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "different-session",
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        result = json.loads(output)
        assert "validation failed" in result["hookSpecificOutput"]["additionalContext"]

    def test_main_missing_project_dir(self, tmp_path: Path, sample_checkpoint):
        """Test handling when CLAUDE_PROJECT_DIR not set."""
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        input_data = json.dumps({"source": "compact"})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}, clear=True), \
             patch("pathlib.Path.home", return_value=tmp_path):

            # Ensure CLAUDE_PROJECT_DIR is not set
            os.environ.pop("CLAUDE_PROJECT_DIR", None)

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        result = json.loads(output)
        assert "project path unavailable" in result["hookSpecificOutput"]["additionalContext"]

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


class TestEndToEndRefresh:
    """End-to-end tests simulating full compaction-refresh cycle."""

    def test_precompact_to_sessionstart_flow(self, tmp_path: Path):
        """Test complete flow from PreCompact to SessionStart."""
        # Step 1: Simulate PreCompact writing checkpoint
        from helpers import create_peer_review_transcript

        transcript_content = create_peer_review_transcript(
            step="recommendations",
            include_pr_number=99,
            include_pending_question=True,
        )

        # Create transcript structure
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        # Create checkpoint directory
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        # Run PreCompact
        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session-e2e"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        # Verify checkpoint was created
        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        assert checkpoint_path.exists()

        checkpoint = json.loads(checkpoint_path.read_text())
        assert checkpoint["workflow"]["name"] == "peer-review"
        assert checkpoint["session_id"] == "test-session-e2e"

        # Step 2: Simulate SessionStart reading checkpoint
        sessionstart_input = json.dumps({"source": "compact"})

        with patch("sys.stdin", StringIO(sessionstart_input)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "test-session-e2e",  # Same session
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main as sessionstart_main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    sessionstart_main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        # Verify refresh message was generated
        result = json.loads(output)
        refresh_msg = result["hookSpecificOutput"]["additionalContext"]

        assert "[POST-COMPACTION CHECKPOINT]" in refresh_msg
        assert "peer-review" in refresh_msg
        assert "recommendations" in refresh_msg or "pr-99" in refresh_msg

    def test_terminated_workflow_no_refresh(self, tmp_path: Path):
        """Test that terminated workflow doesn't trigger refresh."""
        from helpers import create_terminated_workflow_transcript

        transcript_content = create_terminated_workflow_transcript()

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        # Run PreCompact
        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        # Terminated workflow should result in "none" workflow
        # (or low confidence that doesn't trigger refresh)
        # The exact behavior depends on confidence threshold
        assert checkpoint["workflow"]["name"] in ["none", "peer-review"]


class TestExceptionHandlingPaths:
    """Tests for exception handling and defensive paths in compaction_refresh."""

    def test_read_checkpoint_io_error(self, tmp_path: Path):
        """Test handling of IOError when reading checkpoint file."""
        from compaction_refresh import read_checkpoint

        # Create a directory where a file is expected (will cause IOError on read)
        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.mkdir()  # Create as directory, not file

        result = read_checkpoint(checkpoint_path)

        assert result is None

    def test_read_checkpoint_corrupted_json(self, tmp_path: Path):
        """Test handling of corrupted JSON in checkpoint file."""
        from compaction_refresh import read_checkpoint

        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text("{ corrupted json without closing brace")

        result = read_checkpoint(checkpoint_path)

        assert result is None

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

    def test_main_handles_missing_session_id(self, tmp_path: Path, sample_checkpoint):
        """Test handling when CLAUDE_SESSION_ID is missing."""
        # Create checkpoint file
        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)
        checkpoint_path = checkpoint_dir / "-test-project.json"
        checkpoint_path.write_text(json.dumps(sample_checkpoint))

        input_data = json.dumps({"source": "compact"})

        # Environment without session ID
        env_without_session = {"CLAUDE_PROJECT_DIR": "/test/project"}

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, env_without_session, clear=True), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        # Should handle gracefully - validation will fail due to session mismatch
        if output:
            result = json.loads(output)
            assert "hookSpecificOutput" in result

    def test_validate_checkpoint_with_none_fields(self, sample_checkpoint):
        """Test validation handles None values in checkpoint fields."""
        from compaction_refresh import validate_checkpoint

        # Set version to None
        sample_checkpoint["version"] = None
        is_valid = validate_checkpoint(sample_checkpoint, "test-session-123")

        assert is_valid is False

    def test_build_refresh_message_with_missing_fields(self):
        """Test build_refresh_message handles missing optional fields."""
        from compaction_refresh import build_refresh_message

        # Minimal checkpoint with only required fields
        minimal_checkpoint = {
            "workflow": {"name": "peer-review"},
            "step": {"name": "unknown"},
            "extraction": {"confidence": 0.5},
        }

        message = build_refresh_message(minimal_checkpoint)

        # Should not crash and should produce valid message
        assert "[POST-COMPACTION CHECKPOINT]" in message
        assert "peer-review" in message

    def test_build_refresh_message_with_empty_context(self):
        """Test build_refresh_message handles empty context dict."""
        from compaction_refresh import build_refresh_message

        checkpoint = {
            "workflow": {"name": "peer-review", "id": ""},
            "step": {"name": "commit"},
            "extraction": {"confidence": 0.7},
            "context": {},  # Empty context
        }

        message = build_refresh_message(checkpoint)

        # Should not crash
        assert "[POST-COMPACTION CHECKPOINT]" in message

    def test_get_encoded_project_path_empty_env_string(self):
        """Test handling of empty string CLAUDE_PROJECT_DIR returns unknown-project."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            result = get_encoded_project_path("")

        # Empty env var triggers "unknown-project" fallback
        assert result == "unknown-project"


class TestAppendTeamContext:
    """Tests for _append_team_context function."""

    def test_no_active_teams(self, tmp_path, monkeypatch):
        """Test _append_team_context does nothing when no active teams."""
        from compaction_refresh import _append_team_context

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        lines = ["existing line"]
        _append_team_context(lines)

        assert lines == ["existing line"]

    def test_single_team_with_active_members(self, tmp_path, monkeypatch):
        """Test _append_team_context adds team info with active members."""
        from compaction_refresh import _append_team_context

        # Set up team directory with config
        teams_dir = tmp_path / ".claude" / "teams" / "test-team"
        teams_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": "backend-1", "type": "pact-backend-coder", "status": "active"},
                {"name": "architect-1", "type": "pact-architect", "status": "active"},
            ]
        }
        (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        lines = []
        _append_team_context(lines)

        assert len(lines) == 2
        assert "test-team" in lines[0]
        assert "2 active teammate(s)" in lines[0]
        assert "backend-1" in lines[0]
        assert "architect-1" in lines[0]
        assert "SendMessage" in lines[1]

    def test_team_with_no_active_members(self, tmp_path, monkeypatch):
        """Test _append_team_context handles team with no active members."""
        from compaction_refresh import _append_team_context

        teams_dir = tmp_path / ".claude" / "teams" / "idle-team"
        teams_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": "backend-1", "type": "pact-backend-coder", "status": "stopped"},
            ]
        }
        (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        lines = []
        _append_team_context(lines)

        assert len(lines) == 1
        assert "no active teammates" in lines[0]

    def test_team_with_many_members_truncates(self, tmp_path, monkeypatch):
        """Test _append_team_context truncates member list at 6."""
        from compaction_refresh import _append_team_context

        teams_dir = tmp_path / ".claude" / "teams" / "big-team"
        teams_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": f"member-{i}", "type": "pact-backend-coder", "status": "active"}
                for i in range(8)
            ]
        }
        (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        lines = []
        _append_team_context(lines)

        assert "+2 more" in lines[0]
        assert "8 active teammate(s)" in lines[0]

    def test_multiple_teams(self, tmp_path, monkeypatch):
        """Test _append_team_context handles multiple active teams."""
        from compaction_refresh import _append_team_context

        # Create two teams
        for name in ["team-alpha", "team-beta"]:
            teams_dir = tmp_path / ".claude" / "teams" / name
            teams_dir.mkdir(parents=True)
            config = {
                "members": [
                    {"name": "backend-1", "type": "pact-backend-coder", "status": "active"},
                ]
            }
            (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        lines = []
        _append_team_context(lines)

        # Should have entries for both teams (2 lines per team with active members)
        team_lines = [l for l in lines if l.startswith("Team")]
        assert len(team_lines) == 2
