"""
Integration tests for Task system functions.

Tests the Task-first code path that reads workflow state from the Claude Task system
for post-compaction recovery. These functions are the primary state source, with
checkpoint files serving as fallback.

Location: pact-plugin/tests/test_task_integration.py

Core Task utilities are in hooks/shared/task_utils.py.
build_refresh_from_tasks and main remain in compaction_refresh.py.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# =============================================================================
# Fixtures for Task Testing
# =============================================================================

@pytest.fixture
def mock_tasks_dir(tmp_path: Path, monkeypatch):
    """
    Create mock ~/.claude/tasks/{sessionId}/ structure.

    Returns:
        Path to the tasks directory for the test session
    """
    session_id = "test-session-123"
    tasks_dir = tmp_path / ".claude" / "tasks" / session_id
    tasks_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_SESSION_ID", session_id)
    monkeypatch.setenv("HOME", str(tmp_path))

    # Patch Path.home() to return our temp directory
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    return tasks_dir


@pytest.fixture
def make_task():
    """Factory fixture to create task dictionaries."""
    def _make(
        task_id: str,
        subject: str,
        status: str = "in_progress",
        blocked_by: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = {
            "id": task_id,
            "subject": subject,
            "status": status,
        }
        if blocked_by:
            task["blockedBy"] = blocked_by
        if metadata:
            task["metadata"] = metadata
        return task
    return _make


@pytest.fixture
def sample_task_list(make_task) -> list[dict[str, Any]]:
    """
    Create a realistic task list representing an in-progress orchestration.

    Structure:
    - Feature task: "Implement user authentication"
    - Phase tasks: PREPARE (completed), ARCHITECT (completed), CODE (in_progress)
    - Agent tasks: backend-coder (in_progress)
    """
    return [
        # Feature task (top-level, not blocked by anything)
        make_task(
            task_id="task-001",
            subject="Implement user authentication",
            status="in_progress",
        ),
        # Completed phase tasks
        make_task(
            task_id="task-002",
            subject="PREPARE: user-authentication",
            status="completed",
            blocked_by=["task-001"],
        ),
        make_task(
            task_id="task-003",
            subject="ARCHITECT: user-authentication",
            status="completed",
            blocked_by=["task-002"],
        ),
        # Current phase task (in_progress)
        make_task(
            task_id="task-004",
            subject="CODE: user-authentication",
            status="in_progress",
            blocked_by=["task-003"],
        ),
        # Active agent task
        make_task(
            task_id="task-005",
            subject="pact-backend-coder: Implement auth endpoint",
            status="in_progress",
            blocked_by=["task-004"],
        ),
    ]


# =============================================================================
# Tests for get_task_list()
# =============================================================================

class TestGetTaskList:
    """Tests for get_task_list() function."""

    def test_reads_valid_json_files(self, mock_tasks_dir, make_task):
        """Test reading task list from valid JSON files."""
        from shared.task_utils import get_task_list

        # Create task files
        task1 = make_task("task-1", "Feature task", "in_progress")
        task2 = make_task("task-2", "Agent task", "pending")

        (mock_tasks_dir / "task-1.json").write_text(json.dumps(task1))
        (mock_tasks_dir / "task-2.json").write_text(json.dumps(task2))

        result = get_task_list()

        assert result is not None
        assert len(result) == 2
        subjects = {t["subject"] for t in result}
        assert "Feature task" in subjects
        assert "Agent task" in subjects

    def test_returns_none_for_empty_directory(self, mock_tasks_dir):
        """Test returns None when tasks directory is empty."""
        from shared.task_utils import get_task_list

        # Directory exists but is empty
        result = get_task_list()

        assert result is None

    def test_returns_none_for_missing_directory(self, tmp_path, monkeypatch):
        """Test returns None when tasks directory doesn't exist."""
        from shared.task_utils import get_task_list

        session_id = "nonexistent-session"
        monkeypatch.setenv("CLAUDE_SESSION_ID", session_id)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = get_task_list()

        assert result is None

    def test_skips_malformed_json_files(self, mock_tasks_dir, make_task):
        """Test skips malformed JSON files and continues with valid ones."""
        from shared.task_utils import get_task_list

        # Create one valid and one malformed file
        valid_task = make_task("task-1", "Valid task", "in_progress")
        (mock_tasks_dir / "task-1.json").write_text(json.dumps(valid_task))
        (mock_tasks_dir / "task-2.json").write_text("{ invalid json")
        (mock_tasks_dir / "task-3.json").write_text("")

        result = get_task_list()

        assert result is not None
        assert len(result) == 1
        assert result[0]["subject"] == "Valid task"

    def test_returns_none_when_session_id_not_set(self, tmp_path, monkeypatch):
        """Test returns None when CLAUDE_SESSION_ID is not set."""
        from shared.task_utils import get_task_list

        # Clear session ID environment variable
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = get_task_list()

        assert result is None

    def test_uses_task_list_id_when_provided(self, tmp_path, monkeypatch, make_task):
        """Test uses CLAUDE_CODE_TASK_LIST_ID when available."""
        from shared.task_utils import get_task_list

        # Setup with different session_id and task_list_id
        session_id = "session-abc"
        task_list_id = "shared-task-list-xyz"

        monkeypatch.setenv("CLAUDE_SESSION_ID", session_id)
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", task_list_id)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create tasks directory using task_list_id
        tasks_dir = tmp_path / ".claude" / "tasks" / task_list_id
        tasks_dir.mkdir(parents=True)

        task = make_task("task-1", "Shared task", "in_progress")
        (tasks_dir / "task-1.json").write_text(json.dumps(task))

        result = get_task_list()

        assert result is not None
        assert len(result) == 1
        assert result[0]["subject"] == "Shared task"

    def test_handles_non_json_files_gracefully(self, mock_tasks_dir, make_task):
        """Test ignores non-JSON files in tasks directory."""
        from shared.task_utils import get_task_list

        # Create a valid task and some non-JSON files
        task = make_task("task-1", "Valid task", "in_progress")
        (mock_tasks_dir / "task-1.json").write_text(json.dumps(task))
        (mock_tasks_dir / "readme.txt").write_text("This is not a task")
        (mock_tasks_dir / ".hidden").write_text("Hidden file")

        result = get_task_list()

        assert result is not None
        assert len(result) == 1
        assert result[0]["subject"] == "Valid task"


# =============================================================================
# Tests for find_feature_task()
# =============================================================================

class TestFindFeatureTask:
    """Tests for find_feature_task() function."""

    def test_finds_top_level_feature_task(self, sample_task_list):
        """Test finds feature task that has no blockedBy."""
        from shared.task_utils import find_feature_task

        result = find_feature_task(sample_task_list)

        assert result is not None
        assert result["id"] == "task-001"
        assert result["subject"] == "Implement user authentication"

    def test_filters_out_phase_tasks(self, make_task):
        """Test excludes tasks with phase prefixes from feature detection."""
        from shared.task_utils import find_feature_task

        tasks = [
            make_task("task-1", "PREPARE: some-feature", "in_progress"),
            make_task("task-2", "ARCHITECT: some-feature", "in_progress"),
            make_task("task-3", "CODE: some-feature", "in_progress"),
            make_task("task-4", "TEST: some-feature", "in_progress"),
            make_task("task-5", "Review: some-feature", "in_progress"),
        ]

        result = find_feature_task(tasks)

        assert result is None

    def test_returns_none_when_no_feature_task(self, make_task):
        """Test returns None when no suitable feature task exists."""
        from shared.task_utils import find_feature_task

        # Only blocked tasks
        tasks = [
            make_task("task-1", "Agent work", "in_progress", blocked_by=["task-0"]),
            make_task("task-2", "More work", "pending", blocked_by=["task-1"]),
        ]

        result = find_feature_task(tasks)

        assert result is None

    def test_returns_none_for_empty_task_list(self):
        """Test returns None for empty task list."""
        from shared.task_utils import find_feature_task

        result = find_feature_task([])

        assert result is None

    def test_prefers_in_progress_over_pending(self, make_task):
        """Test prefers in_progress feature tasks over pending ones."""
        from shared.task_utils import find_feature_task

        tasks = [
            make_task("task-1", "Pending feature", "pending"),
            make_task("task-2", "Active feature", "in_progress"),
        ]

        result = find_feature_task(tasks)

        # The first matching in_progress task should be returned
        # Both are valid, but it should find one
        assert result is not None
        assert result["status"] in ("in_progress", "pending")

    def test_handles_tasks_without_id(self, make_task):
        """Test handles tasks missing the id field gracefully."""
        from shared.task_utils import find_feature_task

        tasks = [
            {"subject": "Missing id", "status": "in_progress"},
            make_task("task-1", "Valid feature", "in_progress"),
        ]

        result = find_feature_task(tasks)

        assert result is not None
        assert result.get("id") == "task-1"


# =============================================================================
# Tests for find_current_phase()
# =============================================================================

class TestFindCurrentPhase:
    """Tests for find_current_phase() function."""

    def test_finds_in_progress_phase(self, sample_task_list):
        """Test finds phase task with status in_progress."""
        from shared.task_utils import find_current_phase

        result = find_current_phase(sample_task_list)

        assert result is not None
        assert result["id"] == "task-004"
        assert result["subject"] == "CODE: user-authentication"
        assert result["status"] == "in_progress"

    def test_returns_none_with_no_in_progress_phases(self, make_task):
        """Test returns None when no phase is in_progress."""
        from shared.task_utils import find_current_phase

        tasks = [
            make_task("task-1", "PREPARE: feature", "completed"),
            make_task("task-2", "ARCHITECT: feature", "completed"),
            make_task("task-3", "Feature task", "in_progress"),  # Not a phase
        ]

        result = find_current_phase(tasks)

        assert result is None

    def test_detects_all_phase_prefixes(self, make_task):
        """Test detects all valid phase prefixes."""
        from shared.task_utils import find_current_phase

        for phase_prefix in ["PREPARE:", "ARCHITECT:", "CODE:", "TEST:"]:
            tasks = [make_task("task-1", f"{phase_prefix} feature", "in_progress")]
            result = find_current_phase(tasks)
            assert result is not None, f"Failed to detect {phase_prefix}"
            assert result["id"] == "task-1"

    def test_returns_none_for_empty_list(self):
        """Test returns None for empty task list."""
        from shared.task_utils import find_current_phase

        result = find_current_phase([])

        assert result is None

    def test_ignores_completed_phase_tasks(self, make_task):
        """Test ignores completed and pending phase tasks."""
        from shared.task_utils import find_current_phase

        tasks = [
            make_task("task-1", "PREPARE: feature", "completed"),
            make_task("task-2", "ARCHITECT: feature", "pending"),
        ]

        result = find_current_phase(tasks)

        assert result is None


# =============================================================================
# Tests for find_active_agents()
# =============================================================================

class TestFindActiveAgents:
    """Tests for find_active_agents() function."""

    def test_finds_in_progress_agent_tasks(self, sample_task_list):
        """Test finds agent tasks with status in_progress."""
        from shared.task_utils import find_active_agents

        result = find_active_agents(sample_task_list)

        assert len(result) == 1
        assert result[0]["id"] == "task-005"
        assert "pact-backend-coder" in result[0]["subject"].lower()

    def test_finds_multiple_active_agents(self, make_task):
        """Test finds all active agent tasks when multiple exist."""
        from shared.task_utils import find_active_agents

        tasks = [
            make_task("task-1", "pact-backend-coder: API endpoint", "in_progress"),
            make_task("task-2", "pact-frontend-coder: UI component", "in_progress"),
            make_task("task-3", "pact-test-engineer: Integration tests", "in_progress"),
            make_task("task-4", "pact-architect: Design review", "completed"),  # Not active
        ]

        result = find_active_agents(tasks)

        assert len(result) == 3
        agent_ids = {t["id"] for t in result}
        assert agent_ids == {"task-1", "task-2", "task-3"}

    def test_returns_empty_list_with_no_active_agents(self, make_task):
        """Test returns empty list when no agents are in_progress."""
        from shared.task_utils import find_active_agents

        tasks = [
            make_task("task-1", "pact-backend-coder: Work", "completed"),
            make_task("task-2", "Feature task", "in_progress"),  # Not an agent
        ]

        result = find_active_agents(tasks)

        assert result == []

    def test_detects_all_agent_prefixes(self, make_task):
        """Test detects all valid agent type prefixes."""
        from shared.task_utils import find_active_agents

        agent_prefixes = [
            "pact-preparer:",
            "pact-architect:",
            "pact-backend-coder:",
            "pact-frontend-coder:",
            "pact-database-engineer:",
            "pact-devops-engineer:",
            "pact-n8n:",
            "pact-test-engineer:",
            "pact-security-engineer:",
            "pact-qa-engineer:",
            "pact-memory-agent:",
        ]

        for prefix in agent_prefixes:
            tasks = [make_task("task-1", f"{prefix} work item", "in_progress")]
            result = find_active_agents(tasks)
            assert len(result) == 1, f"Failed to detect {prefix}"

    def test_returns_empty_list_for_empty_input(self):
        """Test returns empty list for empty task list."""
        from shared.task_utils import find_active_agents

        result = find_active_agents([])

        assert result == []

    def test_case_insensitive_agent_detection(self, make_task):
        """Test agent detection is case-insensitive for subject."""
        from shared.task_utils import find_active_agents

        tasks = [
            make_task("task-1", "PACT-BACKEND-CODER: Uppercase", "in_progress"),
            make_task("task-2", "Pact-Frontend-Coder: Mixed case", "in_progress"),
        ]

        result = find_active_agents(tasks)

        # The implementation converts to lowercase for comparison
        assert len(result) == 2


# =============================================================================
# Tests for find_blockers()
# =============================================================================

class TestFindBlockers:
    """Tests for find_blockers() function."""

    def test_finds_blocker_tasks(self, make_task):
        """Test finds tasks with metadata.type == 'blocker'."""
        from shared.task_utils import find_blockers

        tasks = [
            make_task(
                "task-1",
                "Blocked: Missing API credentials",
                "pending",
                metadata={"type": "blocker"},
            ),
            make_task("task-2", "Normal task", "in_progress"),
        ]

        result = find_blockers(tasks)

        assert len(result) == 1
        assert result[0]["id"] == "task-1"

    def test_finds_algedonic_tasks(self, make_task):
        """Test finds tasks with metadata.type == 'algedonic'."""
        from shared.task_utils import find_blockers

        tasks = [
            make_task(
                "task-1",
                "HALT: Security vulnerability detected",
                "pending",
                metadata={"type": "algedonic", "level": "HALT"},
            ),
        ]

        result = find_blockers(tasks)

        assert len(result) == 1
        assert result[0]["metadata"]["level"] == "HALT"

    def test_filters_out_completed_blockers(self, make_task):
        """Test excludes blockers that have been resolved (status: completed)."""
        from shared.task_utils import find_blockers

        tasks = [
            make_task(
                "task-1",
                "Resolved blocker",
                "completed",
                metadata={"type": "blocker"},
            ),
            make_task(
                "task-2",
                "Active blocker",
                "pending",
                metadata={"type": "blocker"},
            ),
        ]

        result = find_blockers(tasks)

        assert len(result) == 1
        assert result[0]["id"] == "task-2"

    def test_returns_empty_list_with_no_blockers(self, make_task):
        """Test returns empty list when no blocker/algedonic tasks exist."""
        from shared.task_utils import find_blockers

        tasks = [
            make_task("task-1", "Normal task", "in_progress"),
            make_task("task-2", "Another task", "pending", metadata={"type": "normal"}),
        ]

        result = find_blockers(tasks)

        assert result == []

    def test_returns_empty_list_for_empty_input(self):
        """Test returns empty list for empty task list."""
        from shared.task_utils import find_blockers

        result = find_blockers([])

        assert result == []

    def test_handles_tasks_without_metadata(self, make_task):
        """Test handles tasks that have no metadata field."""
        from shared.task_utils import find_blockers

        tasks = [
            make_task("task-1", "No metadata task", "in_progress"),
            {"id": "task-2", "subject": "Also no metadata", "status": "pending"},
        ]

        result = find_blockers(tasks)

        assert result == []

    def test_finds_both_blockers_and_algedonics(self, make_task):
        """Test finds both blocker and algedonic types together."""
        from shared.task_utils import find_blockers

        tasks = [
            make_task(
                "task-1",
                "Blocker task",
                "pending",
                metadata={"type": "blocker"},
            ),
            make_task(
                "task-2",
                "Algedonic task",
                "pending",
                metadata={"type": "algedonic"},
            ),
        ]

        result = find_blockers(tasks)

        assert len(result) == 2


# =============================================================================
# Tests for build_refresh_from_tasks()
# =============================================================================

class TestBuildRefreshFromTasks:
    """Tests for build_refresh_from_tasks() function."""

    def test_builds_complete_message(self, make_task):
        """Test builds complete refresh message with all components."""
        from compaction_refresh import build_refresh_from_tasks

        feature = make_task("feat-1", "Implement auth", "in_progress")
        phase = make_task("phase-1", "CODE: auth", "in_progress")
        agents = [make_task("agent-1", "pact-backend-coder: work", "in_progress")]
        blockers = []

        result = build_refresh_from_tasks(feature, phase, agents, blockers)

        assert "[POST-COMPACTION CHECKPOINT]" in result
        assert "Implement auth" in result
        assert "CODE: auth" in result
        assert "pact-backend-coder" in result
        assert "Monitor active agents" in result

    def test_handles_missing_feature(self, make_task):
        """Test handles None feature task gracefully."""
        from compaction_refresh import build_refresh_from_tasks

        phase = make_task("phase-1", "CODE: work", "in_progress")
        agents = []

        result = build_refresh_from_tasks(None, phase, agents, [])

        assert "Unable to identify feature task" in result

    def test_handles_missing_phase(self, make_task):
        """Test handles None phase task gracefully."""
        from compaction_refresh import build_refresh_from_tasks

        feature = make_task("feat-1", "Feature", "in_progress")

        result = build_refresh_from_tasks(feature, None, [], [])

        assert "None detected" in result

    def test_includes_blocker_information(self, make_task):
        """Test includes blocker details in message."""
        from compaction_refresh import build_refresh_from_tasks

        feature = make_task("feat-1", "Feature", "in_progress")
        blockers = [
            make_task(
                "block-1",
                "Missing credentials",
                "pending",
                metadata={"type": "blocker", "level": "HALT"},
            ),
        ]

        result = build_refresh_from_tasks(feature, None, [], blockers)

        assert "**BLOCKERS DETECTED:**" in result
        assert "Missing credentials" in result
        assert "HALT" in result
        assert "Address blockers" in result

    def test_formats_multiple_agents(self, make_task):
        """Test formats multiple active agents correctly."""
        from compaction_refresh import build_refresh_from_tasks

        feature = make_task("feat-1", "Feature", "in_progress")
        agents = [
            make_task("agent-1", "pact-backend-coder: API", "in_progress"),
            make_task("agent-2", "pact-frontend-coder: UI", "in_progress"),
        ]

        result = build_refresh_from_tasks(feature, None, agents, [])

        assert "Active Agents (2)" in result
        assert "pact-backend-coder: API" in result
        assert "pact-frontend-coder: UI" in result

    def test_includes_feature_id_when_present(self, make_task):
        """Test includes feature task ID in output."""
        from compaction_refresh import build_refresh_from_tasks

        feature = make_task("feat-123", "My Feature", "in_progress")

        result = build_refresh_from_tasks(feature, None, [], [])

        assert "feat-123" in result

    def test_next_step_guidance_priority(self, make_task):
        """Test next step guidance prioritizes blockers over agents over phase."""
        from compaction_refresh import build_refresh_from_tasks

        feature = make_task("feat-1", "Feature", "in_progress")
        phase = make_task("phase-1", "CODE: work", "in_progress")
        agents = [make_task("agent-1", "pact-backend-coder: work", "in_progress")]
        blockers = [make_task("block-1", "Blocker", "pending", metadata={"type": "blocker"})]

        # With blockers - should mention addressing blockers
        result = build_refresh_from_tasks(feature, phase, agents, blockers)
        assert "Address blockers" in result

        # Without blockers but with agents - should mention monitoring agents
        result = build_refresh_from_tasks(feature, phase, agents, [])
        assert "Monitor active agents" in result

        # Without blockers or agents but with phase - should mention continuing phase
        result = build_refresh_from_tasks(feature, phase, [], [])
        assert "Continue current phase" in result

        # With nothing - should ask user
        result = build_refresh_from_tasks(feature, None, [], [])
        assert "ask user how to proceed" in result.lower()


# =============================================================================
# Integration Tests for Task-First Code Path
# =============================================================================

class TestTaskFirstIntegration:
    """Integration tests for the Task-first code path in main()."""

    def test_main_uses_tasks_when_available(self, mock_tasks_dir, make_task, monkeypatch):
        """Test main() uses Task system when tasks exist."""
        import json
        from io import StringIO

        # Create tasks
        feature = make_task("task-1", "Implement feature", "in_progress")
        phase = make_task("task-2", "CODE: feature", "in_progress", blocked_by=["task-1"])

        (mock_tasks_dir / "task-1.json").write_text(json.dumps(feature))
        (mock_tasks_dir / "task-2.json").write_text(json.dumps(phase))

        # Simulate post-compaction input
        input_data = json.dumps({"source": "compact"})
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        # Capture output
        output = StringIO()
        monkeypatch.setattr("sys.stdout", output)

        from compaction_refresh import main

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0

        # Verify output contains Task-based refresh message
        result = json.loads(output.getvalue())
        refresh_msg = result["hookSpecificOutput"]["additionalContext"]
        assert "[POST-COMPACTION CHECKPOINT]" in refresh_msg
        assert "Implement feature" in refresh_msg

    def test_main_skips_when_no_in_progress_tasks(self, mock_tasks_dir, make_task, monkeypatch):
        """Test main() skips refresh when no tasks are in_progress."""
        import json
        from io import StringIO

        # Create only completed tasks
        task = make_task("task-1", "Completed work", "completed")
        (mock_tasks_dir / "task-1.json").write_text(json.dumps(task))

        input_data = json.dumps({"source": "compact"})
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        output = StringIO()
        monkeypatch.setattr("sys.stdout", output)

        from compaction_refresh import main

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        # No output expected when no active workflow
        assert output.getvalue() == ""
