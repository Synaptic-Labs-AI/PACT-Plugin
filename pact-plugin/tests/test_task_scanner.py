"""
Tests for hooks/shared/task_scanner.py — scan_all_tasks(), scan_team_members(),
analyze_task_state(), and SYSTEM_TASK_PREFIXES.

Covers: valid parsing, ID fallback, fail-open on OSError, non-JSON filtering,
malformed JSON skipping, empty/nonexistent directories, multi-team scanning,
team config reading, feature/phase detection, variety score extraction.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.task_scanner import (
    SYSTEM_TASK_PREFIXES,
    analyze_task_state,
    scan_all_tasks,
    scan_team_members,
)


class TestScanAllTasksValidFiles:
    """Tests for scan_all_tasks with well-formed task files."""

    def test_returns_parsed_dicts_from_valid_json(self, tmp_path: Path):
        """Valid .json files in a team directory are parsed and returned."""
        team_dir = tmp_path / "pact-abc123"
        team_dir.mkdir()

        task_data = {
            "id": "task-1",
            "subject": "Implement auth",
            "status": "in_progress",
            "metadata": {"phase": "code"},
        }
        (team_dir / "task-1.json").write_text(json.dumps(task_data))

        result = scan_all_tasks(str(tmp_path))

        assert len(result) == 1
        assert result[0]["id"] == "task-1"
        assert result[0]["subject"] == "Implement auth"
        assert result[0]["status"] == "in_progress"

    def test_id_fallback_to_filename_stem(self, tmp_path: Path):
        """When 'id' is missing from JSON, falls back to filename stem."""
        team_dir = tmp_path / "pact-abc123"
        team_dir.mkdir()

        task_data = {"subject": "No ID field", "status": "pending"}
        (team_dir / "my-task-42.json").write_text(json.dumps(task_data))

        result = scan_all_tasks(str(tmp_path))

        assert len(result) == 1
        assert result[0]["id"] == "my-task-42"
        assert result[0]["subject"] == "No ID field"

    def test_id_preserved_when_present(self, tmp_path: Path):
        """When 'id' is present in JSON, it is not overwritten by stem."""
        team_dir = tmp_path / "pact-team"
        team_dir.mkdir()

        task_data = {"id": "original-id", "subject": "Has ID"}
        (team_dir / "different-name.json").write_text(json.dumps(task_data))

        result = scan_all_tasks(str(tmp_path))

        assert result[0]["id"] == "original-id"

    def test_multiple_team_directories_scanned(self, tmp_path: Path):
        """Tasks from multiple team directories are all collected."""
        for team_name in ("pact-team-a", "pact-team-b", "pact-team-c"):
            team_dir = tmp_path / team_name
            team_dir.mkdir()
            task = {"id": f"task-{team_name}", "subject": f"Task in {team_name}"}
            (team_dir / "1.json").write_text(json.dumps(task))

        result = scan_all_tasks(str(tmp_path))

        assert len(result) == 3
        ids = {t["id"] for t in result}
        assert ids == {"task-pact-team-a", "task-pact-team-b", "task-pact-team-c"}


class TestScanAllTasksFiltering:
    """Tests for filtering out non-JSON and malformed files."""

    def test_non_json_files_filtered_out(self, tmp_path: Path):
        """Files without .json extension are ignored."""
        team_dir = tmp_path / "pact-team"
        team_dir.mkdir()

        # Valid JSON task
        (team_dir / "task-1.json").write_text(json.dumps({"id": "1", "subject": "ok"}))
        # Non-JSON files that should be ignored
        (team_dir / "readme.txt").write_text("not a task")
        (team_dir / "notes.md").write_text("# Notes")
        (team_dir / "config.yaml").write_text("key: value")

        result = scan_all_tasks(str(tmp_path))

        assert len(result) == 1
        assert result[0]["id"] == "1"

    def test_malformed_json_files_skipped(self, tmp_path: Path):
        """Files with invalid JSON content are skipped gracefully."""
        team_dir = tmp_path / "pact-team"
        team_dir.mkdir()

        # Valid task
        (team_dir / "good.json").write_text(json.dumps({"id": "good", "subject": "ok"}))
        # Malformed JSON
        (team_dir / "bad.json").write_text("{not valid json at all")
        # Empty file
        (team_dir / "empty.json").write_text("")

        result = scan_all_tasks(str(tmp_path))

        assert len(result) == 1
        assert result[0]["id"] == "good"

    def test_non_directory_items_in_base_ignored(self, tmp_path: Path):
        """Files at the base level (not team directories) are ignored."""
        # Create a team dir with a task
        team_dir = tmp_path / "pact-team"
        team_dir.mkdir()
        (team_dir / "task.json").write_text(json.dumps({"id": "1"}))

        # Create a file at the base level (not a directory)
        (tmp_path / "stray-file.json").write_text(json.dumps({"id": "stray"}))

        result = scan_all_tasks(str(tmp_path))

        assert len(result) == 1
        assert result[0]["id"] == "1"


class TestScanAllTasksEmptyAndMissing:
    """Tests for empty and nonexistent directories."""

    def test_nonexistent_base_directory_returns_empty(self, tmp_path: Path):
        """Nonexistent base directory returns empty list without error."""
        result = scan_all_tasks(str(tmp_path / "does-not-exist"))

        assert result == []

    def test_empty_base_directory_returns_empty(self, tmp_path: Path):
        """Empty base directory (no team dirs) returns empty list."""
        result = scan_all_tasks(str(tmp_path))

        assert result == []

    def test_empty_team_directory_returns_empty(self, tmp_path: Path):
        """Team directory with no task files returns empty list."""
        team_dir = tmp_path / "pact-empty-team"
        team_dir.mkdir()

        result = scan_all_tasks(str(tmp_path))

        assert result == []


class TestScanAllTasksFailOpen:
    """Tests for fail-open behavior on OS errors."""

    def test_oserror_on_base_iterdir_returns_empty(self, tmp_path: Path):
        """OSError during base directory iteration returns empty list."""
        team_dir = tmp_path / "pact-team"
        team_dir.mkdir()
        (team_dir / "task.json").write_text(json.dumps({"id": "1"}))

        # Patch Path.iterdir to raise OSError on the base directory
        original_iterdir = Path.iterdir

        def mock_iterdir(self):
            if self == tmp_path:
                raise OSError("Permission denied")
            return original_iterdir(self)

        with patch.object(Path, "iterdir", mock_iterdir):
            result = scan_all_tasks(str(tmp_path))

        assert result == []

    def test_default_base_dir_uses_home(self):
        """When no base dir provided, defaults to ~/.claude/tasks/."""
        with patch("shared.task_scanner.Path.home") as mock_home:
            mock_home.return_value = Path("/fake/home")
            # The path won't exist, so we get empty list
            result = scan_all_tasks()

        assert result == []
        mock_home.assert_called_once()


# ---------------------------------------------------------------------------
# Helpers for team config and task files
# ---------------------------------------------------------------------------


def _create_task_file(task_dir: Path, task_id: str, data: dict) -> None:
    """Write a task JSON file into the given directory."""
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task_id}.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _create_team_config(
    teams_dir: Path, team_name: str, members: list[dict], name: str | None = None
) -> None:
    """Write a team config.json with the given members list."""
    team_dir = teams_dir / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    config = {"members": members}
    if name is not None:
        config["name"] = name
    (team_dir / "config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Unit tests: SYSTEM_TASK_PREFIXES
# ---------------------------------------------------------------------------


class TestSystemTaskPrefixes:
    """Verify the shared constant."""

    def test_contains_expected_prefixes(self):
        assert "Phase:" in SYSTEM_TASK_PREFIXES
        assert "BLOCKER:" in SYSTEM_TASK_PREFIXES
        assert "ALERT:" in SYSTEM_TASK_PREFIXES
        assert "HALT:" in SYSTEM_TASK_PREFIXES

    def test_is_tuple(self):
        assert isinstance(SYSTEM_TASK_PREFIXES, tuple)

    def test_has_four_entries(self):
        assert len(SYSTEM_TASK_PREFIXES) == 4


# ---------------------------------------------------------------------------
# Unit tests: scan_team_members
# ---------------------------------------------------------------------------


class TestScanTeamMembers:
    """Test team config reading for teammate names and team names."""

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        result = scan_team_members(str(tmp_path / "no-such-dir"))
        assert result["teammates"] == []
        assert result["team_names"] == []

    def test_empty_dir_returns_empty(self, tmp_path):
        teams_dir = tmp_path / "teams"
        teams_dir.mkdir()
        result = scan_team_members(str(teams_dir))
        assert result["teammates"] == []

    def test_reads_member_names(self, tmp_path):
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [
            {"name": "backend-coder"},
            {"name": "test-engineer"},
        ], name="pact-abc")

        result = scan_team_members(str(teams_dir))
        assert "backend-coder" in result["teammates"]
        assert "test-engineer" in result["teammates"]

    def test_reads_team_names(self, tmp_path):
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [], name="pact-abc")

        result = scan_team_members(str(teams_dir))
        assert "pact-abc" in result["team_names"]

    def test_team_name_falls_back_to_dir_name(self, tmp_path):
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-xyz", [])

        result = scan_team_members(str(teams_dir))
        assert "pact-xyz" in result["team_names"]

    def test_skips_empty_names(self, tmp_path):
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [
            {"name": "coder"},
            {"name": ""},
            {"role": "observer"},
        ])

        result = scan_team_members(str(teams_dir))
        assert result["teammates"] == ["coder"]

    def test_malformed_config_skipped(self, tmp_path):
        teams_dir = tmp_path / "teams"
        team_dir = teams_dir / "pact-bad"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text("not json", encoding="utf-8")

        result = scan_team_members(str(teams_dir))
        assert result["teammates"] == []

    def test_missing_config_file_skipped(self, tmp_path):
        teams_dir = tmp_path / "teams"
        (teams_dir / "pact-empty").mkdir(parents=True)

        result = scan_team_members(str(teams_dir))
        assert result["teammates"] == []

    def test_non_dict_members_handled(self, tmp_path):
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [
            "string-entry",
            42,
            {"name": "valid-coder"},
        ])

        result = scan_team_members(str(teams_dir))
        assert result["teammates"] == ["valid-coder"]

    def test_default_base_dir_uses_home(self):
        """When no base dir provided, defaults to ~/.claude/teams/."""
        with patch("shared.task_scanner.Path.home") as mock_home:
            mock_home.return_value = Path("/fake/home")
            result = scan_team_members()

        assert result["teammates"] == []
        assert result["team_names"] == []


# ---------------------------------------------------------------------------
# Unit tests: analyze_task_state
# ---------------------------------------------------------------------------


class TestAnalyzeTaskState:
    """Test combined task analysis (status counts + feature/phase detection)."""

    def test_nonexistent_dir_returns_defaults(self, tmp_path):
        result = analyze_task_state(str(tmp_path / "no-such-dir"))
        assert result["total"] == 0
        assert result["completed"] == 0
        assert result["in_progress"] == 0
        assert result["pending"] == 0
        assert result["feature_subject"] is None
        assert result["feature_id"] is None
        assert result["current_phase"] is None
        assert result["variety_score"] is None

    def test_empty_dir_returns_defaults(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        result = analyze_task_state(str(tasks_dir))
        assert result["total"] == 0

    def test_counts_tasks_by_status(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "completed", "subject": "Done"})
        _create_task_file(team_dir, "t2", {"status": "completed", "subject": "Done 2"})
        _create_task_file(team_dir, "t3", {"status": "in_progress", "subject": "Feature X"})
        _create_task_file(team_dir, "t4", {"status": "pending", "subject": "Queued"})

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["completed"] == 2
        assert result["in_progress"] == 1
        assert result["pending"] == 1
        assert result["total"] == 4

    def test_detects_feature_subject_and_id(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "42", {
            "id": "42",
            "status": "in_progress",
            "subject": "Implement auth flow",
        })

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["feature_subject"] == "Implement auth flow"
        assert result["feature_id"] == "42"

    def test_feature_id_falls_back_to_filename(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "abc-123", {
            "status": "in_progress",
            "subject": "Some feature",
        })

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["feature_id"] == "abc-123"

    def test_detects_current_phase(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {
            "status": "in_progress",
            "subject": "Phase: CODE",
        })

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["current_phase"] == "Phase: CODE"

    def test_detects_variety_score_from_metadata(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {
            "id": "7",
            "status": "in_progress",
            "subject": "Build dashboard",
            "metadata": {"variety": 9},
        })

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["variety_score"] == 9

    def test_skips_phase_prefix_for_feature_subject(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "Phase: CODE"})
        _create_task_file(team_dir, "t2", {"status": "in_progress", "subject": "Real feature"})

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["feature_subject"] == "Real feature"
        assert result["current_phase"] == "Phase: CODE"

    def test_skips_blocker_prefix_for_feature_subject(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "BLOCKER: Missing key"})

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["feature_subject"] is None

    def test_skips_algedonic_prefixes(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "HALT: Security"})
        _create_task_file(team_dir, "t2", {"status": "in_progress", "subject": "ALERT: Scope"})

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["feature_subject"] is None

    def test_malformed_json_files_skipped(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        team_dir.mkdir(parents=True)
        (team_dir / "bad.json").write_text("not json", encoding="utf-8")
        _create_task_file(team_dir, "good", {"status": "completed", "subject": "OK"})

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["completed"] == 1
        assert result["total"] == 1

    def test_non_json_files_ignored(self, tmp_path):
        team_dir = tmp_path / "tasks" / "team-abc"
        team_dir.mkdir(parents=True)
        (team_dir / "notes.txt").write_text("some notes", encoding="utf-8")

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["total"] == 0

    def test_multiple_teams_aggregated(self, tmp_path):
        team_a = tmp_path / "tasks" / "team-a"
        team_b = tmp_path / "tasks" / "team-b"
        _create_task_file(team_a, "t1", {"status": "completed", "subject": "A1"})
        _create_task_file(team_b, "t2", {"status": "in_progress", "subject": "B1"})

        result = analyze_task_state(str(tmp_path / "tasks"))
        assert result["total"] == 2
        assert result["completed"] == 1
        assert result["in_progress"] == 1
