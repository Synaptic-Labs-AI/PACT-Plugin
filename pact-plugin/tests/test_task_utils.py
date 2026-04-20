"""
Tests for shared/task_utils.py — Task system integration utilities.

Tests cover:
1. get_task_list: filesystem reading, session ID resolution, error handling
2. find_feature_task: top-level task identification, phase prefix exclusion
3. find_current_phase: active phase detection
4. find_active_agents: agent task filtering by prefix and status
5. find_blockers: blocker/algedonic task detection
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks" / "shared"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_task(tasks_dir, task_id, task_data):
    """Write a task JSON file to the tasks directory."""
    task_file = tasks_dir / f"{task_id}.json"
    task_data.setdefault("id", task_id)
    task_file.write_text(json.dumps(task_data), encoding="utf-8")


# ---------------------------------------------------------------------------
# get_task_list
# ---------------------------------------------------------------------------

class TestGetTaskList:
    """Tests for get_task_list() — filesystem-based task reading."""

    def test_returns_none_when_no_session_id(self, monkeypatch, pact_context):
        from task_utils import get_task_list
        pact_context(session_id="")  # No session ID available
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        result = get_task_list()
        assert result is None

    def test_returns_none_when_tasks_dir_missing(self, tmp_path, monkeypatch, pact_context):
        from task_utils import get_task_list
        pact_context(session_id="test-session")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = get_task_list()
        assert result is None

    def test_reads_tasks_from_filesystem(self, tmp_path, monkeypatch, pact_context):
        from task_utils import get_task_list
        pact_context(session_id="test-session")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        tasks_dir = tmp_path / ".claude" / "tasks" / "test-session"
        tasks_dir.mkdir(parents=True)
        write_task(tasks_dir, "1", {"subject": "Test task", "status": "pending"})
        write_task(tasks_dir, "2", {"subject": "Another task", "status": "in_progress"})

        result = get_task_list()
        assert result is not None
        assert len(result) == 2

    def test_prefers_task_list_id_over_session_id(self, tmp_path, monkeypatch, pact_context):
        from task_utils import get_task_list
        pact_context(session_id="session-id")
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "task-list-id")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create tasks under task-list-id, not session-id
        tasks_dir = tmp_path / ".claude" / "tasks" / "task-list-id"
        tasks_dir.mkdir(parents=True)
        write_task(tasks_dir, "1", {"subject": "Task", "status": "pending"})

        result = get_task_list()
        assert result is not None
        assert len(result) == 1

    def test_returns_none_for_empty_dir(self, tmp_path, monkeypatch, pact_context):
        from task_utils import get_task_list
        pact_context(session_id="test-session")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        tasks_dir = tmp_path / ".claude" / "tasks" / "test-session"
        tasks_dir.mkdir(parents=True)

        result = get_task_list()
        assert result is None

    def test_skips_invalid_json_files(self, tmp_path, monkeypatch, pact_context):
        from task_utils import get_task_list
        pact_context(session_id="test-session")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        tasks_dir = tmp_path / ".claude" / "tasks" / "test-session"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "bad.json").write_text("not json", encoding="utf-8")
        write_task(tasks_dir, "1", {"subject": "Good task", "status": "pending"})

        result = get_task_list()
        assert result is not None
        assert len(result) == 1

    def test_returns_none_on_exception(self, tmp_path, monkeypatch, pact_context):
        from task_utils import get_task_list
        pact_context(session_id="test-session")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        tasks_dir = tmp_path / ".claude" / "tasks" / "test-session"
        tasks_dir.mkdir(parents=True)

        with patch.object(Path, "glob", side_effect=PermissionError("denied")):
            result = get_task_list()
        assert result is None


# ---------------------------------------------------------------------------
# find_feature_task
# ---------------------------------------------------------------------------

class TestFindFeatureTask:
    """Tests for find_feature_task() — top-level task identification."""

    def test_finds_feature_task(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "Implement auth system", "status": "in_progress"},
            {"id": "2", "subject": "PREPARE: auth", "status": "completed",
             "blockedBy": ["1"]},
        ]
        result = find_feature_task(tasks)
        assert result is not None
        assert result["id"] == "1"

    def test_skips_phase_tasks(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "PREPARE: feature", "status": "in_progress"},
            {"id": "2", "subject": "CODE: feature", "status": "pending",
             "blockedBy": ["1"]},
        ]
        result = find_feature_task(tasks)
        assert result is None

    def test_skips_blocked_tasks(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "Feature A", "status": "in_progress",
             "blockedBy": ["99"]},
        ]
        result = find_feature_task(tasks)
        assert result is None

    def test_returns_none_for_empty_list(self):
        from task_utils import find_feature_task
        result = find_feature_task([])
        assert result is None

    def test_skips_completed_tasks(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "Old feature", "status": "completed"},
        ]
        result = find_feature_task(tasks)
        assert result is None

    def test_finds_pending_feature_task(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "New feature", "status": "pending"},
        ]
        result = find_feature_task(tasks)
        assert result is not None
        assert result["id"] == "1"

    def test_skips_review_phase_tasks(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "Review: auth PR", "status": "in_progress"},
        ]
        result = find_feature_task(tasks)
        assert result is None

    def test_handles_missing_id(self):
        from task_utils import find_feature_task
        tasks = [
            {"subject": "No ID task", "status": "in_progress"},
        ]
        result = find_feature_task(tasks)
        assert result is None


# ---------------------------------------------------------------------------
# find_current_phase
# ---------------------------------------------------------------------------

class TestFindCurrentPhase:
    """Tests for find_current_phase() — active phase detection."""

    def test_finds_active_phase(self):
        from task_utils import find_current_phase
        tasks = [
            {"id": "1", "subject": "Feature", "status": "in_progress"},
            {"id": "2", "subject": "PREPARE: feature", "status": "completed"},
            {"id": "3", "subject": "CODE: feature", "status": "in_progress"},
        ]
        result = find_current_phase(tasks)
        assert result is not None
        assert result["subject"] == "CODE: feature"

    def test_returns_none_when_no_active_phase(self):
        from task_utils import find_current_phase
        tasks = [
            {"id": "1", "subject": "PREPARE: feature", "status": "completed"},
            {"id": "2", "subject": "CODE: feature", "status": "pending"},
        ]
        result = find_current_phase(tasks)
        assert result is None

    def test_returns_none_for_empty_list(self):
        from task_utils import find_current_phase
        result = find_current_phase([])
        assert result is None

    def test_detects_all_phase_types(self):
        from task_utils import find_current_phase
        for phase in ["PREPARE:", "ARCHITECT:", "CODE:", "TEST:"]:
            tasks = [{"id": "1", "subject": f"{phase} feature", "status": "in_progress"}]
            result = find_current_phase(tasks)
            assert result is not None, f"Failed to detect {phase} phase"

    def test_ignores_non_phase_tasks(self):
        from task_utils import find_current_phase
        tasks = [
            {"id": "1", "subject": "Implement auth", "status": "in_progress"},
        ]
        result = find_current_phase(tasks)
        assert result is None


# ---------------------------------------------------------------------------
# find_active_agents
# ---------------------------------------------------------------------------

class TestFindActiveAgents:
    """Tests for find_active_agents() — agent task filtering."""

    def test_finds_active_agents(self):
        from task_utils import find_active_agents
        tasks = [
            {"id": "1", "subject": "backend-coder: implement auth",
             "status": "in_progress"},
            {"id": "2", "subject": "test-engineer: write tests",
             "status": "in_progress"},
        ]
        result = find_active_agents(tasks)
        assert len(result) == 2

    def test_excludes_completed_agents(self):
        from task_utils import find_active_agents
        tasks = [
            {"id": "1", "subject": "backend-coder: implement auth",
             "status": "completed"},
        ]
        result = find_active_agents(tasks)
        assert result == []

    def test_excludes_non_agent_tasks(self):
        from task_utils import find_active_agents
        tasks = [
            {"id": "1", "subject": "Feature task", "status": "in_progress"},
            {"id": "2", "subject": "CODE: feature", "status": "in_progress"},
        ]
        result = find_active_agents(tasks)
        assert result == []

    def test_returns_empty_for_empty_list(self):
        from task_utils import find_active_agents
        result = find_active_agents([])
        assert result == []

    def test_detects_all_agent_types(self):
        from task_utils import find_active_agents
        agent_types = [
            "preparer", "architect", "backend-coder",
            "frontend-coder", "database-engineer",
            "devops-engineer", "n8n", "test-engineer",
            "security-engineer", "qa-engineer", "secretary",
        ]
        tasks = [
            {"id": str(i), "subject": f"{agent}: task {i}",
             "status": "in_progress"}
            for i, agent in enumerate(agent_types)
        ]
        result = find_active_agents(tasks)
        assert len(result) == len(agent_types)

    def test_case_insensitive_matching(self):
        from task_utils import find_active_agents
        tasks = [
            {"id": "1", "subject": "Backend-Coder: task",
             "status": "in_progress"},
        ]
        result = find_active_agents(tasks)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# find_blockers
# ---------------------------------------------------------------------------

class TestFindBlockers:
    """Tests for find_blockers() — blocker/algedonic task detection."""

    def test_finds_blocker_tasks(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "BLOCKER: missing API",
             "status": "pending",
             "metadata": {"type": "blocker"}},
        ]
        result = find_blockers(tasks)
        assert len(result) == 1

    def test_finds_algedonic_tasks(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "HALT: security issue",
             "status": "pending",
             "metadata": {"type": "algedonic"}},
        ]
        result = find_blockers(tasks)
        assert len(result) == 1

    def test_excludes_completed_blockers(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "BLOCKER: resolved",
             "status": "completed",
             "metadata": {"type": "blocker"}},
        ]
        result = find_blockers(tasks)
        assert result == []

    def test_excludes_normal_tasks(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "Regular task", "status": "pending",
             "metadata": {}},
        ]
        result = find_blockers(tasks)
        assert result == []

    def test_handles_missing_metadata(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "Task without metadata", "status": "pending"},
        ]
        result = find_blockers(tasks)
        assert result == []

    def test_returns_empty_for_empty_list(self):
        from task_utils import find_blockers
        result = find_blockers([])
        assert result == []

    def test_multiple_blockers(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "BLOCKER: A", "status": "pending",
             "metadata": {"type": "blocker"}},
            {"id": "2", "subject": "HALT: B", "status": "in_progress",
             "metadata": {"type": "algedonic"}},
            {"id": "3", "subject": "BLOCKER: resolved", "status": "completed",
             "metadata": {"type": "blocker"}},
        ]
        result = find_blockers(tasks)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _read_task_json / read_task_metadata / read_task_owner
# Hoisted from handoff_gate.py in #401 Commit #4. Tests cover:
#   - positive-allowlist path sanitization (upgrade from re.sub negative regex)
#   - happy-path reads (team-scoped + base fallback)
#   - corrupted JSON / missing files / empty task_id → fail-open {}
#   - thin-wrapper semantics of read_task_metadata / read_task_owner
# ---------------------------------------------------------------------------

class TestReadTaskJsonHoistedHelpers:
    """Sanitization contract for the hoisted helpers in shared.task_utils."""

    def test_happy_path_team_dir(self, tmp_path):
        from shared.task_utils import _read_task_json

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        task_data = {"id": "42", "subject": "hi", "owner": "coder-1",
                     "metadata": {"variety": {"total": 7}}}
        (team_dir / "42.json").write_text(json.dumps(task_data))

        result = _read_task_json("42", "pact-test", tasks_base_dir=str(tmp_path))
        assert result == task_data

    def test_falls_back_to_base_when_team_dir_missing(self, tmp_path):
        from shared.task_utils import _read_task_json

        (tmp_path / "5.json").write_text(json.dumps({"id": "5"}))
        result = _read_task_json("5", "pact-does-not-exist", tasks_base_dir=str(tmp_path))
        assert result == {"id": "5"}

    def test_team_name_none_uses_base_only(self, tmp_path):
        from shared.task_utils import _read_task_json

        (tmp_path / "9.json").write_text(json.dumps({"id": "9"}))
        result = _read_task_json("9", None, tasks_base_dir=str(tmp_path))
        assert result == {"id": "9"}

    def test_missing_file_returns_empty(self, tmp_path):
        from shared.task_utils import _read_task_json

        assert _read_task_json("404", "pact-test", tasks_base_dir=str(tmp_path)) == {}

    def test_corrupted_json_returns_empty(self, tmp_path):
        from shared.task_utils import _read_task_json

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "1.json").write_text("{{{not json")

        assert _read_task_json("1", "pact-test", tasks_base_dir=str(tmp_path)) == {}

    def test_empty_task_id_returns_empty(self):
        from shared.task_utils import _read_task_json

        assert _read_task_json("", "pact-test") == {}


class TestReadTaskJsonPathSanitization:
    """Positive-allowlist path-traversal defense (is_safe_path_component)."""

    @pytest.mark.parametrize("bad_id", [
        "..",
        "../../etc/passwd",
        "/absolute/path",
        "..\\windows",
        "foo/bar",
        "foo\\bar",
        "with space",
        "with\tab",
        "with\nnewline",
        "\u2028line_sep",
        "\u2029para_sep",
        "\u0085nel_sep",
        "\x00null",
        ".",
        "..foo",  # rejected — contains '.'
        "foo.bar",  # rejected — contains '.'
    ])
    def test_sanitizer_rejects_traversal_and_separators(self, bad_id, tmp_path):
        from shared.task_utils import _read_task_json

        # Bad id never resolves to any file regardless of what's on disk
        assert _read_task_json(bad_id, "pact-test", tasks_base_dir=str(tmp_path)) == {}

    @pytest.mark.parametrize("good_id", [
        "42",
        "task-17",
        "ABC_def",
        "a1b2c3",
        "3c82-d41e-f76b",  # UUID-like
        "0",
    ])
    def test_sanitizer_accepts_safe_ids(self, good_id, tmp_path):
        from shared.task_utils import _read_task_json

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / f"{good_id}.json").write_text(json.dumps({"id": good_id}))

        assert _read_task_json(good_id, "pact-test", tasks_base_dir=str(tmp_path)) == {"id": good_id}

    def test_unsafe_team_name_rejected(self, tmp_path):
        from shared.task_utils import _read_task_json

        # Even with a valid task_id, an unsafe team_name must reject
        (tmp_path / "42.json").write_text(json.dumps({"id": "42"}))

        assert _read_task_json("42", "../escape", tasks_base_dir=str(tmp_path)) == {}


class TestReadTaskMetadataWrapper:
    def test_returns_metadata_dict(self, tmp_path):
        from shared.task_utils import read_task_metadata

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "1.json").write_text(json.dumps({"id": "1", "metadata": {"foo": "bar"}}))

        assert read_task_metadata("1", "pact-test", tasks_base_dir=str(tmp_path)) == {"foo": "bar"}

    def test_missing_metadata_returns_empty_dict(self, tmp_path):
        from shared.task_utils import read_task_metadata

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "1.json").write_text(json.dumps({"id": "1"}))  # no metadata key

        assert read_task_metadata("1", "pact-test", tasks_base_dir=str(tmp_path)) == {}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        from shared.task_utils import read_task_metadata

        assert read_task_metadata("404", "pact-test", tasks_base_dir=str(tmp_path)) == {}


class TestReadTaskOwnerWrapper:
    def test_returns_owner_when_present(self, tmp_path):
        from shared.task_utils import read_task_owner

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "1.json").write_text(
            json.dumps({"id": "1", "owner": "backend-coder-2"})
        )

        assert read_task_owner("1", "pact-test", tasks_base_dir=str(tmp_path)) == "backend-coder-2"

    def test_returns_none_when_owner_absent(self, tmp_path):
        from shared.task_utils import read_task_owner

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "1.json").write_text(json.dumps({"id": "1"}))

        assert read_task_owner("1", "pact-test", tasks_base_dir=str(tmp_path)) is None

    def test_returns_none_on_missing_file(self, tmp_path):
        from shared.task_utils import read_task_owner

        assert read_task_owner("nope", "pact-test", tasks_base_dir=str(tmp_path)) is None


class TestHandoffGateReexportDriftGuard:
    """handoff_gate.py MUST still re-export these symbols for test patch targets."""

    def test_handoff_gate_reexports_read_task_json(self):
        import handoff_gate
        from shared.task_utils import _read_task_json as shared_read

        assert handoff_gate._read_task_json is shared_read

    def test_handoff_gate_reexports_read_task_metadata(self):
        import handoff_gate
        from shared.task_utils import read_task_metadata as shared_rm

        assert handoff_gate.read_task_metadata is shared_rm

    def test_handoff_gate_reexports_read_task_owner(self):
        import handoff_gate
        from shared.task_utils import read_task_owner as shared_ro

        assert handoff_gate.read_task_owner is shared_ro
