"""
End-to-end tests for plan-mode, comPACT, rePACT, and Agent Teams workflow refresh cycles.

Tests the complete PreCompact -> checkpoint -> SessionStart flow for each
workflow type to ensure proper state extraction and refresh injection.
Includes Agent Teams (v3) scenarios with TeamCreate, SendMessage, and
team interaction counting.
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
sys.path.insert(0, str(Path(__file__).parent))

from conftest import (
    create_plan_mode_transcript,
    create_compact_transcript,
    create_repact_transcript,
)


class TestPlanModeWorkflowE2E:
    """End-to-end tests for plan-mode workflow refresh cycle."""

    def test_plan_mode_precompact_checkpoint_sessionstart_flow(self, tmp_path: Path):
        """Test complete refresh cycle for plan-mode workflow in consult phase."""
        # Step 1: Create plan-mode transcript mid-workflow
        transcript_content = create_plan_mode_transcript(
            step="consult",
            include_task="implement authentication",
            include_termination=False,
        )

        # Set up directory structure
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        # Step 2: Run PreCompact hook
        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "plan-mode-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        # Step 3: Verify checkpoint was created with plan-mode workflow
        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        assert checkpoint_path.exists()

        checkpoint = json.loads(checkpoint_path.read_text())
        assert checkpoint["workflow"]["name"] == "plan-mode"
        assert checkpoint["session_id"] == "plan-mode-session"

        # Step 4: Run SessionStart hook (simulating post-compaction)
        sessionstart_input = json.dumps({"source": "compact"})

        with patch("sys.stdin", StringIO(sessionstart_input)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "plan-mode-session",
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main as sessionstart_main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    sessionstart_main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        # Step 5: Verify refresh message was generated
        result = json.loads(output)
        refresh_msg = result["hookSpecificOutput"]["additionalContext"]

        assert "[POST-COMPACTION CHECKPOINT]" in refresh_msg
        assert "plan-mode" in refresh_msg

    def test_plan_mode_present_phase_with_pending_question(self, tmp_path: Path):
        """Test plan-mode in present phase with pending user question."""
        transcript_content = create_plan_mode_transcript(
            step="present",
            include_task="implement feature X",
            include_termination=False,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "plan-present-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "plan-mode"

    def test_plan_mode_terminated_no_refresh(self, tmp_path: Path):
        """Test that terminated plan-mode workflow doesn't trigger active refresh."""
        transcript_content = create_plan_mode_transcript(
            step="present",
            include_task="implement feature",
            include_termination=True,  # Plan saved, workflow complete
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "plan-terminated-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        # May detect as plan-mode (terminated) or none depending on pattern matching
        assert checkpoint["workflow"]["name"] in ["plan-mode", "none"]


class TestComPACTWorkflowE2E:
    """End-to-end tests for comPACT workflow refresh cycle."""

    def test_compact_backend_precompact_checkpoint_sessionstart_flow(self, tmp_path: Path):
        """Test complete refresh cycle for comPACT workflow with backend specialist."""
        transcript_content = create_compact_transcript(
            specialist="backend",
            include_task="fix authentication bug",
            include_termination=False,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "compact-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        assert checkpoint_path.exists()

        checkpoint = json.loads(checkpoint_path.read_text())
        assert checkpoint["workflow"]["name"] == "comPACT"
        assert checkpoint["session_id"] == "compact-session"

        # Run SessionStart
        sessionstart_input = json.dumps({"source": "compact"})

        with patch("sys.stdin", StringIO(sessionstart_input)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "compact-session",
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main as sessionstart_main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    sessionstart_main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        result = json.loads(output)
        refresh_msg = result["hookSpecificOutput"]["additionalContext"]

        assert "[POST-COMPACTION CHECKPOINT]" in refresh_msg
        assert "comPACT" in refresh_msg

    def test_compact_frontend_specialist(self, tmp_path: Path):
        """Test comPACT workflow with frontend specialist."""
        transcript_content = create_compact_transcript(
            specialist="frontend",
            include_task="update button styles",
            include_termination=False,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "compact-frontend-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "comPACT"

    def test_compact_test_specialist(self, tmp_path: Path):
        """Test comPACT workflow with test specialist."""
        transcript_content = create_compact_transcript(
            specialist="test",
            include_task="add unit tests for auth module",
            include_termination=False,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "compact-test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "comPACT"

    def test_compact_terminated_workflow(self, tmp_path: Path):
        """Test that terminated comPACT workflow is handled appropriately."""
        transcript_content = create_compact_transcript(
            specialist="backend",
            include_task="fix bug",
            include_termination=True,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "compact-term-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        # Terminated workflow may be detected as comPACT or none
        assert checkpoint["workflow"]["name"] in ["comPACT", "none"]


class TestRePACTWorkflowE2E:
    """End-to-end tests for rePACT (nested PACT) workflow refresh cycle."""

    def test_repact_nested_code_precompact_checkpoint_sessionstart_flow(self, tmp_path: Path):
        """Test complete refresh cycle for rePACT workflow in nested-code phase."""
        transcript_content = create_repact_transcript(
            nested_phase="nested-code",
            parent_workflow="orchestrate",
            include_termination=False,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "repact-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        assert checkpoint_path.exists()

        checkpoint = json.loads(checkpoint_path.read_text())
        # rePACT is the most recent trigger, so should be detected
        assert checkpoint["workflow"]["name"] == "rePACT"
        assert checkpoint["session_id"] == "repact-session"

        # Run SessionStart
        sessionstart_input = json.dumps({"source": "compact"})

        with patch("sys.stdin", StringIO(sessionstart_input)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "repact-session",
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main as sessionstart_main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    sessionstart_main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        result = json.loads(output)
        refresh_msg = result["hookSpecificOutput"]["additionalContext"]

        assert "[POST-COMPACTION CHECKPOINT]" in refresh_msg
        assert "rePACT" in refresh_msg

    def test_repact_nested_architect_phase(self, tmp_path: Path):
        """Test rePACT workflow in nested-architect phase."""
        transcript_content = create_repact_transcript(
            nested_phase="nested-architect",
            parent_workflow="orchestrate",
            include_termination=False,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "repact-arch-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "rePACT"

    def test_repact_nested_test_phase(self, tmp_path: Path):
        """Test rePACT workflow in nested-test phase."""
        transcript_content = create_repact_transcript(
            nested_phase="nested-test",
            parent_workflow="orchestrate",
            include_termination=False,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "repact-test-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "rePACT"

    def test_repact_terminated_returns_to_parent(self, tmp_path: Path):
        """Test that terminated rePACT workflow is handled appropriately."""
        transcript_content = create_repact_transcript(
            nested_phase="nested-test",
            parent_workflow="orchestrate",
            include_termination=True,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "repact-term-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        # Terminated rePACT may be detected as rePACT or none
        assert checkpoint["workflow"]["name"] in ["rePACT", "none"]


class TestMixedWorkflowScenarios:
    """Tests for scenarios involving transitions between workflows."""

    def test_orchestrate_spawns_repact_detects_repact(self, tmp_path: Path):
        """Test that when orchestrate spawns rePACT, the rePACT is detected."""
        # This is tested implicitly by create_repact_transcript which includes
        # the parent orchestrate trigger before the rePACT trigger
        transcript_content = create_repact_transcript(
            nested_phase="nested-code",
            include_termination=False,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "mixed-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        # Should detect rePACT as the most recent workflow
        assert checkpoint["workflow"]["name"] == "rePACT"

    def test_workflow_detection_uses_most_recent_trigger(self, tmp_path: Path):
        """Test that workflow detection always uses the most recent trigger."""
        from conftest import make_user_message, make_assistant_message, create_transcript_lines

        # Create transcript with multiple workflow triggers
        lines = [
            make_user_message("/PACT:orchestrate old task", "2025-01-22T08:00:00Z"),
            make_assistant_message("Orchestrate completed.", "2025-01-22T08:30:00Z"),
            make_user_message("/PACT:plan-mode new feature", "2025-01-22T09:00:00Z"),
            make_assistant_message("Plan mode completed.", "2025-01-22T09:30:00Z"),
            make_user_message("/PACT:comPACT backend fix", "2025-01-22T10:00:00Z"),
            make_assistant_message("invoking-specialist: Starting comPACT...", "2025-01-22T10:00:05Z"),
        ]

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(create_transcript_lines(lines))

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "multi-workflow-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        # Should detect comPACT as the most recent active workflow
        assert checkpoint["workflow"]["name"] == "comPACT"


class TestAgentTeamsWorkflowE2E:
    """End-to-end tests for Agent Teams (v3) workflow refresh cycle."""

    def test_agent_teams_orchestrate_precompact_checkpoint_sessionstart_flow(self, tmp_path: Path):
        """Test complete refresh cycle for orchestrate workflow with Agent Teams."""
        from conftest import create_orchestrate_with_teams_transcript

        transcript_content = create_orchestrate_with_teams_transcript(
            phase="code",
            include_task="implement auth with Agent Teams",
            include_termination=False,
        )

        # Set up directory structure
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        # Step 1: Run PreCompact hook
        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "agent-teams-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        # Step 2: Verify checkpoint was created with orchestrate workflow
        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        assert checkpoint_path.exists()

        checkpoint = json.loads(checkpoint_path.read_text())
        assert checkpoint["workflow"]["name"] == "orchestrate"
        assert checkpoint["session_id"] == "agent-teams-session"

        # Step 3: Run SessionStart hook (simulating post-compaction)
        sessionstart_input = json.dumps({"source": "compact"})

        with patch("sys.stdin", StringIO(sessionstart_input)), \
             patch.dict(os.environ, {
                 "CLAUDE_SESSION_ID": "agent-teams-session",
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main as sessionstart_main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    sessionstart_main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        # Step 4: Verify refresh message was generated
        result = json.loads(output)
        refresh_msg = result["hookSpecificOutput"]["additionalContext"]

        assert "[POST-COMPACTION CHECKPOINT]" in refresh_msg
        assert "orchestrate" in refresh_msg

    def test_agent_teams_confidence_includes_team_interactions(self, tmp_path: Path):
        """Test that team interactions (SendMessage, TeamCreate) boost confidence."""
        from conftest import create_orchestrate_with_teams_transcript

        transcript_content = create_orchestrate_with_teams_transcript(
            phase="code",
            include_termination=False,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "teams-confidence-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        # Confidence should include agent_invocation weight (0.2)
        # because the transcript has both Task calls AND team interactions
        assert checkpoint["extraction"]["confidence"] >= 0.6

        # Notes should mention team interactions
        notes = checkpoint["extraction"]["notes"]
        assert "team interaction" in notes

    def test_agent_teams_send_messages_found_in_parsed_transcript(self, tmp_path: Path):
        """Test that SendMessage calls are correctly found in parsed transcript."""
        from conftest import create_orchestrate_with_teams_transcript

        transcript_content = create_orchestrate_with_teams_transcript(
            phase="code",
            include_termination=False,
        )

        # Write transcript and parse it directly
        transcript_path = tmp_path / "session.jsonl"
        transcript_path.write_text(transcript_content)

        from refresh.transcript_parser import parse_transcript, find_send_messages

        turns = parse_transcript(transcript_path)

        # Find all SendMessage calls
        send_messages = find_send_messages(turns)

        # The "code" phase transcript has 2 SendMessage calls:
        # one to preparer-1, one to backend-1
        assert len(send_messages) == 2

        # Verify recipients
        recipients = [tc.input_data.get("recipient") for _, tc in send_messages]
        assert "preparer-1" in recipients
        assert "backend-1" in recipients

    def test_agent_teams_team_create_detected_in_transcript(self, tmp_path: Path):
        """Test that TeamCreate calls are detected in parsed transcript."""
        from conftest import create_orchestrate_with_teams_transcript

        transcript_content = create_orchestrate_with_teams_transcript(
            phase="code",
            include_termination=False,
        )

        transcript_path = tmp_path / "session.jsonl"
        transcript_path.write_text(transcript_content)

        from refresh.transcript_parser import parse_transcript

        turns = parse_transcript(transcript_path)

        # Find turns with TeamCreate
        team_create_turns = [t for t in turns if t.has_team_create()]
        assert len(team_create_turns) == 1

        # Verify team name in the TeamCreate call
        tc = team_create_turns[0].get_tool_call("TeamCreate")
        assert tc is not None
        assert tc.input_data.get("team_name") == "v3-agent-teams"

    def test_agent_teams_count_team_interactions(self, tmp_path: Path):
        """Test that count_team_interactions counts SendMessage and TeamCreate."""
        from conftest import create_orchestrate_with_teams_transcript

        transcript_content = create_orchestrate_with_teams_transcript(
            phase="code",
            include_termination=False,
        )

        transcript_path = tmp_path / "session.jsonl"
        transcript_path.write_text(transcript_content)

        from refresh.transcript_parser import parse_transcript
        from refresh.workflow_detector import count_team_interactions

        turns = parse_transcript(transcript_path)

        # Count all team interactions from the beginning
        interactions = count_team_interactions(turns, after_index=0)

        # "code" phase transcript has: 1 TeamCreate + 2 SendMessage = 3 turns
        # with team interactions
        assert interactions == 3

    def test_agent_teams_terminated_workflow(self, tmp_path: Path):
        """Test that a terminated Agent Teams orchestrate workflow is handled."""
        from conftest import create_orchestrate_with_teams_transcript

        transcript_content = create_orchestrate_with_teams_transcript(
            phase="test",
            include_termination=True,
        )

        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True)
        transcript_path = session_dir / "session.jsonl"
        transcript_path.write_text(transcript_content)

        checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
        checkpoint_dir.mkdir(parents=True)

        precompact_input = json.dumps({"transcript_path": str(transcript_path)})

        with patch("sys.stdin", StringIO(precompact_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "teams-term-session"}), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        # Terminated workflow should be detected as orchestrate or none
        assert checkpoint["workflow"]["name"] in ["orchestrate", "none"]
