"""
End-to-end tests for plan-mode, comPACT, and rePACT workflow refresh cycles.

Tests the complete PreCompact -> checkpoint -> SessionStart flow for each
workflow type to ensure proper state extraction and refresh injection.
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

    def test_plan_mode_precompact_checkpoint_sessionstart_flow(self, tmp_path: Path, pact_context):
        """Test complete refresh cycle for plan-mode workflow in consult phase."""
        pact_context(session_id="plan-mode-session")
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
             patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/test/project"}), \
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

    def test_plan_mode_present_phase_with_pending_question(self, tmp_path: Path, pact_context):
        """Test plan-mode in present phase with pending user question."""
        pact_context(session_id="plan-present-session")
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
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "plan-mode"

    def test_plan_mode_terminated_no_refresh(self, tmp_path: Path, pact_context):
        """Test that terminated plan-mode workflow doesn't trigger active refresh."""
        pact_context(session_id="plan-terminated-session")
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

    def test_compact_backend_precompact_checkpoint_sessionstart_flow(self, tmp_path: Path, pact_context):
        """Test complete refresh cycle for comPACT workflow with backend specialist."""
        pact_context(session_id="compact-session")
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
             patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/test/project"}), \
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

    def test_compact_frontend_specialist(self, tmp_path: Path, pact_context):
        """Test comPACT workflow with frontend specialist."""
        pact_context(session_id="compact-frontend-session")
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
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "comPACT"

    def test_compact_test_specialist(self, tmp_path: Path, pact_context):
        """Test comPACT workflow with test specialist."""
        pact_context(session_id="compact-test-session")
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
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "comPACT"

    def test_compact_terminated_workflow(self, tmp_path: Path, pact_context):
        """Test that terminated comPACT workflow is handled appropriately."""
        pact_context(session_id="compact-term-session")
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

    def test_repact_nested_code_precompact_checkpoint_sessionstart_flow(self, tmp_path: Path, pact_context):
        """Test complete refresh cycle for rePACT workflow in nested-code phase."""
        pact_context(session_id="repact-session")
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
             patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/test/project"}), \
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

    def test_repact_nested_architect_phase(self, tmp_path: Path, pact_context):
        """Test rePACT workflow in nested-architect phase."""
        pact_context(session_id="repact-arch-session")
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
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "rePACT"

    def test_repact_nested_test_phase(self, tmp_path: Path, pact_context):
        """Test rePACT workflow in nested-test phase."""
        pact_context(session_id="repact-test-session")
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
             patch("pathlib.Path.home", return_value=tmp_path):

            from precompact_refresh import main as precompact_main

            with patch("sys.stdout", new_callable=StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    precompact_main()
                assert exc_info.value.code == 0

        checkpoint_path = checkpoint_dir / f"{encoded_path}.json"
        checkpoint = json.loads(checkpoint_path.read_text())

        assert checkpoint["workflow"]["name"] == "rePACT"

    def test_repact_terminated_returns_to_parent(self, tmp_path: Path, pact_context):
        """Test that terminated rePACT workflow is handled appropriately."""
        pact_context(session_id="repact-term-session")
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

    def test_orchestrate_spawns_repact_detects_repact(self, tmp_path: Path, pact_context):
        """Test that when orchestrate spawns rePACT, the rePACT is detected."""
        pact_context(session_id="mixed-session")
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

    def test_workflow_detection_uses_most_recent_trigger(self, tmp_path: Path, pact_context):
        """Test that workflow detection always uses the most recent trigger."""
        pact_context(session_id="multi-workflow-session")
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
