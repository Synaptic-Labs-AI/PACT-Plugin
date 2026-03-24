"""
Tests for hooks/precompact_state_reminder.py — PreCompact hook that gathers
mechanical state from disk and instructs the orchestrator to persist ephemeral
context before compaction.

Tests cover:
1. Disk state gathering (task counts, feature subject, active teammates)
2. State summary formatting
3. Full message composition with instructions
4. Subprocess integration (systemMessage output, exit code)
5. Fail-open on malformed input, missing dirs, bad JSON files
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


HOOK_PATH = str(Path(__file__).parent.parent / "hooks" / "precompact_state_reminder.py")


def run_hook(stdin_data: str | None = None) -> subprocess.CompletedProcess:
    """Run the hook as a subprocess and return the result."""
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=stdin_data or "",
        capture_output=True,
        text=True,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Helpers for creating fake task/team directories
# ---------------------------------------------------------------------------


def _create_task_file(task_dir: Path, task_id: str, data: dict) -> None:
    """Write a task JSON file into the given directory."""
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task_id}.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _create_team_config(teams_dir: Path, team_name: str, members: list[dict]) -> None:
    """Write a team config.json with the given members list."""
    team_dir = teams_dir / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"members": members}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Unit tests: _gather_task_counts
# ---------------------------------------------------------------------------


class TestGatherTaskCounts:
    """Test disk-based task count gathering."""

    def test_nonexistent_dir_returns_defaults(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        result = _gather_task_counts(str(tmp_path / "no-such-dir"))
        assert result["total"] == 0
        assert result["completed"] == 0
        assert result["in_progress"] == 0
        assert result["pending"] == 0
        assert result["feature_subject"] is None

    def test_empty_dir_returns_defaults(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        result = _gather_task_counts(str(tasks_dir))
        assert result["total"] == 0

    def test_counts_tasks_by_status(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "completed", "subject": "Done thing"})
        _create_task_file(team_dir, "t2", {"status": "completed", "subject": "Done thing 2"})
        _create_task_file(team_dir, "t3", {"status": "in_progress", "subject": "Feature X"})
        _create_task_file(team_dir, "t4", {"status": "pending", "subject": "Queued"})

        result = _gather_task_counts(str(tmp_path / "tasks"))
        assert result["completed"] == 2
        assert result["in_progress"] == 1
        assert result["pending"] == 1
        assert result["total"] == 4

    def test_detects_feature_subject(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "Implement auth flow"})

        result = _gather_task_counts(str(tmp_path / "tasks"))
        assert result["feature_subject"] == "Implement auth flow"

    def test_skips_phase_prefix_for_feature_subject(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "Phase: CODE"})
        _create_task_file(team_dir, "t2", {"status": "in_progress", "subject": "Real feature work"})

        result = _gather_task_counts(str(tmp_path / "tasks"))
        assert result["feature_subject"] == "Real feature work"

    def test_skips_blocker_prefix_for_feature_subject(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "BLOCKER: Missing API key"})

        result = _gather_task_counts(str(tmp_path / "tasks"))
        assert result["feature_subject"] is None

    def test_skips_algedonic_prefixes_for_feature_subject(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "HALT: Security issue"})
        _create_task_file(team_dir, "t2", {"status": "in_progress", "subject": "ALERT: Scope creep"})

        result = _gather_task_counts(str(tmp_path / "tasks"))
        assert result["feature_subject"] is None

    def test_malformed_json_files_skipped(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        team_dir = tmp_path / "tasks" / "team-abc"
        team_dir.mkdir(parents=True)
        (team_dir / "bad.json").write_text("not json", encoding="utf-8")
        _create_task_file(team_dir, "good", {"status": "completed", "subject": "OK"})

        result = _gather_task_counts(str(tmp_path / "tasks"))
        assert result["completed"] == 1
        assert result["total"] == 1

    def test_non_json_files_ignored(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        team_dir = tmp_path / "tasks" / "team-abc"
        team_dir.mkdir(parents=True)
        (team_dir / "notes.txt").write_text("some notes", encoding="utf-8")

        result = _gather_task_counts(str(tmp_path / "tasks"))
        assert result["total"] == 0

    def test_multiple_teams_aggregated(self, tmp_path):
        from precompact_state_reminder import _gather_task_counts
        team_a = tmp_path / "tasks" / "team-a"
        team_b = tmp_path / "tasks" / "team-b"
        _create_task_file(team_a, "t1", {"status": "completed", "subject": "A1"})
        _create_task_file(team_b, "t2", {"status": "in_progress", "subject": "B1"})

        result = _gather_task_counts(str(tmp_path / "tasks"))
        assert result["total"] == 2
        assert result["completed"] == 1
        assert result["in_progress"] == 1


# ---------------------------------------------------------------------------
# Unit tests: _gather_active_teammates
# ---------------------------------------------------------------------------


class TestGatherActiveTeammates:
    """Test team config reading for active teammate names."""

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        from precompact_state_reminder import _gather_active_teammates
        result = _gather_active_teammates(str(tmp_path / "no-such-dir"))
        assert result == []

    def test_empty_dir_returns_empty(self, tmp_path):
        from precompact_state_reminder import _gather_active_teammates
        teams_dir = tmp_path / "teams"
        teams_dir.mkdir()
        result = _gather_active_teammates(str(teams_dir))
        assert result == []

    def test_reads_member_names(self, tmp_path):
        from precompact_state_reminder import _gather_active_teammates
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [
            {"name": "backend-coder"},
            {"name": "test-engineer"},
        ])

        result = _gather_active_teammates(str(teams_dir))
        assert "backend-coder" in result
        assert "test-engineer" in result

    def test_skips_empty_names(self, tmp_path):
        from precompact_state_reminder import _gather_active_teammates
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [
            {"name": "coder"},
            {"name": ""},
            {"role": "observer"},  # no name key
        ])

        result = _gather_active_teammates(str(teams_dir))
        assert result == ["coder"]

    def test_malformed_config_skipped(self, tmp_path):
        from precompact_state_reminder import _gather_active_teammates
        teams_dir = tmp_path / "teams"
        team_dir = teams_dir / "pact-bad"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text("not json", encoding="utf-8")

        result = _gather_active_teammates(str(teams_dir))
        assert result == []

    def test_missing_config_file_skipped(self, tmp_path):
        from precompact_state_reminder import _gather_active_teammates
        teams_dir = tmp_path / "teams"
        (teams_dir / "pact-empty").mkdir(parents=True)

        result = _gather_active_teammates(str(teams_dir))
        assert result == []

    def test_non_dict_members_handled(self, tmp_path):
        from precompact_state_reminder import _gather_active_teammates
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [
            "string-entry",
            42,
            {"name": "valid-coder"},
        ])

        result = _gather_active_teammates(str(teams_dir))
        assert result == ["valid-coder"]


# ---------------------------------------------------------------------------
# Unit tests: _build_state_summary
# ---------------------------------------------------------------------------


class TestBuildStateSummary:
    """Test state summary formatting."""

    def test_full_summary(self):
        from precompact_state_reminder import _build_state_summary
        counts = {
            "completed": 3,
            "in_progress": 2,
            "pending": 1,
            "total": 6,
            "feature_subject": "Add auth flow",
        }
        result = _build_state_summary(counts, ["coder", "tester"])
        assert "3 completed" in result
        assert "2 in_progress" in result
        assert "1 pending" in result
        assert "total: 6" in result
        assert "Add auth flow" in result
        assert "coder, tester" in result

    def test_no_tasks(self):
        from precompact_state_reminder import _build_state_summary
        counts = {
            "completed": 0,
            "in_progress": 0,
            "pending": 0,
            "total": 0,
            "feature_subject": None,
        }
        result = _build_state_summary(counts, [])
        assert "none found on disk" in result
        assert "none found" in result

    def test_no_feature_subject_omitted(self):
        from precompact_state_reminder import _build_state_summary
        counts = {
            "completed": 1,
            "in_progress": 0,
            "pending": 0,
            "total": 1,
            "feature_subject": None,
        }
        result = _build_state_summary(counts, [])
        assert "Feature:" not in result

    def test_no_teammates(self):
        from precompact_state_reminder import _build_state_summary
        counts = {
            "completed": 0,
            "in_progress": 0,
            "pending": 0,
            "total": 0,
            "feature_subject": None,
        }
        result = _build_state_summary(counts, [])
        assert "Active teammates: none found" in result


# ---------------------------------------------------------------------------
# Unit tests: build_message (full composition)
# ---------------------------------------------------------------------------


class TestBuildMessage:
    """Test full message composition with dir overrides."""

    def test_includes_state_summary_and_instructions(self, tmp_path):
        from precompact_state_reminder import build_message
        tasks_dir = tmp_path / "tasks"
        teams_dir = tmp_path / "teams"
        team_task_dir = tasks_dir / "pact-test"
        _create_task_file(team_task_dir, "t1", {
            "status": "in_progress",
            "subject": "Build dashboard",
        })
        _create_team_config(teams_dir, "pact-test", [
            {"name": "frontend-coder"},
        ])

        result = build_message(str(tasks_dir), str(teams_dir))
        assert "Compaction imminent" in result
        assert "Build dashboard" in result
        assert "frontend-coder" in result
        assert "TaskCreate" in result
        assert "Pre-compaction state dump" in result
        assert "SendMessage" in result
        assert "secretary" in result

    def test_empty_dirs_still_produces_valid_message(self, tmp_path):
        from precompact_state_reminder import build_message
        result = build_message(
            str(tmp_path / "no-tasks"),
            str(tmp_path / "no-teams"),
        )
        assert "Compaction imminent" in result
        assert "none found" in result
        assert "TaskCreate" in result

    def test_brain_dump_instructions_present(self, tmp_path):
        from precompact_state_reminder import build_message
        result = build_message(str(tmp_path), str(tmp_path))
        assert "brain dump" in result.lower() or "Pre-compaction state dump" in result
        assert "ephemeral context" in result or "ephemeral" in result


# ---------------------------------------------------------------------------
# Integration tests: subprocess (systemMessage output)
# ---------------------------------------------------------------------------


class TestPrecompactSubprocess:
    """Verify the hook emits the expected systemMessage via subprocess."""

    def test_emits_system_message_with_valid_input(self):
        result = run_hook(json.dumps({"transcript_path": "/tmp/test.jsonl"}))
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output

    def test_message_mentions_compaction(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "compaction" in output["systemMessage"].lower()

    def test_message_mentions_task_create(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "TaskCreate" in output["systemMessage"]

    def test_message_mentions_send_message_to_secretary(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        msg = output["systemMessage"]
        assert "SendMessage" in msg
        assert "secretary" in msg

    def test_message_mentions_brain_dump(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        msg = output["systemMessage"]
        assert "Pre-compaction state dump" in msg


# ---------------------------------------------------------------------------
# Fail-open tests
# ---------------------------------------------------------------------------


class TestPrecompactFailOpen:
    """Verify fail-open behavior on malformed input and errors."""

    def test_empty_stdin_exits_zero(self):
        result = run_hook("")
        assert result.returncode == 0

    def test_malformed_json_exits_zero(self):
        result = run_hook("not json at all")
        assert result.returncode == 0

    def test_malformed_json_still_emits_message(self):
        result = run_hook("not json at all")
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output

    def test_null_input_exits_zero(self):
        result = run_hook("null")
        assert result.returncode == 0

    def test_array_input_exits_zero(self):
        result = run_hook("[]")
        assert result.returncode == 0

    def test_disk_read_error_fails_open(self, tmp_path):
        """If Path.home() raises or dirs are unreadable, still exits 0."""
        from precompact_state_reminder import build_message
        # Point at a file instead of a directory — iterdir() will fail
        fake_file = tmp_path / "not-a-dir"
        fake_file.write_text("x", encoding="utf-8")
        # Should not raise — returns defaults
        result = build_message(str(fake_file), str(fake_file))
        assert "none found" in result


# ---------------------------------------------------------------------------
# Constants check
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants are accessible and sensible."""

    def test_brain_dump_instructions_constant(self):
        from precompact_state_reminder import BRAIN_DUMP_INSTRUCTIONS
        assert "TaskCreate" in BRAIN_DUMP_INSTRUCTIONS
        assert "SendMessage" in BRAIN_DUMP_INSTRUCTIONS
        assert "secretary" in BRAIN_DUMP_INSTRUCTIONS
        assert "Pre-compaction state dump" in BRAIN_DUMP_INSTRUCTIONS
