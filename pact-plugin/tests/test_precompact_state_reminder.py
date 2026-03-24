"""
Tests for hooks/precompact_state_reminder.py — PreCompact hook that gathers
mechanical state from disk and emits custom_instructions (for the compaction
model) and a systemMessage (brain dump instructions for the orchestrator).

Tests cover:
1. Disk state gathering (task counts, feature subject/ID, phase, variety)
2. Team info gathering (teammate names, team names)
3. State summary formatting
4. Custom instructions composition
5. Full hook output (both fields)
6. Subprocess integration (JSON output, exit code)
7. Fail-open on malformed input, missing dirs, bad JSON files
"""
import json
import subprocess
import sys
from pathlib import Path

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
# Unit tests: _gather_task_state
# ---------------------------------------------------------------------------


class TestGatherTaskState:
    """Test disk-based task state gathering."""

    def test_nonexistent_dir_returns_defaults(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        result = _gather_task_state(str(tmp_path / "no-such-dir"))
        assert result["total"] == 0
        assert result["completed"] == 0
        assert result["in_progress"] == 0
        assert result["pending"] == 0
        assert result["feature_subject"] is None
        assert result["feature_id"] is None
        assert result["current_phase"] is None
        assert result["variety_score"] is None

    def test_empty_dir_returns_defaults(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        result = _gather_task_state(str(tasks_dir))
        assert result["total"] == 0

    def test_counts_tasks_by_status(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "completed", "subject": "Done"})
        _create_task_file(team_dir, "t2", {"status": "completed", "subject": "Done 2"})
        _create_task_file(team_dir, "t3", {"status": "in_progress", "subject": "Feature X"})
        _create_task_file(team_dir, "t4", {"status": "pending", "subject": "Queued"})

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["completed"] == 2
        assert result["in_progress"] == 1
        assert result["pending"] == 1
        assert result["total"] == 4

    def test_detects_feature_subject_and_id(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "42", {
            "id": "42",
            "status": "in_progress",
            "subject": "Implement auth flow",
        })

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["feature_subject"] == "Implement auth flow"
        assert result["feature_id"] == "42"

    def test_feature_id_falls_back_to_filename(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "abc-123", {
            "status": "in_progress",
            "subject": "Some feature",
        })

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["feature_id"] == "abc-123"

    def test_detects_current_phase(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {
            "status": "in_progress",
            "subject": "Phase: CODE",
        })

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["current_phase"] == "Phase: CODE"

    def test_detects_variety_score_from_metadata(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {
            "id": "7",
            "status": "in_progress",
            "subject": "Build dashboard",
            "metadata": {"variety": 9},
        })

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["variety_score"] == 9

    def test_skips_phase_prefix_for_feature_subject(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "Phase: CODE"})
        _create_task_file(team_dir, "t2", {"status": "in_progress", "subject": "Real feature"})

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["feature_subject"] == "Real feature"
        assert result["current_phase"] == "Phase: CODE"

    def test_skips_blocker_prefix_for_feature_subject(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "BLOCKER: Missing key"})

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["feature_subject"] is None

    def test_skips_algedonic_prefixes(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        _create_task_file(team_dir, "t1", {"status": "in_progress", "subject": "HALT: Security"})
        _create_task_file(team_dir, "t2", {"status": "in_progress", "subject": "ALERT: Scope"})

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["feature_subject"] is None

    def test_malformed_json_files_skipped(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        team_dir.mkdir(parents=True)
        (team_dir / "bad.json").write_text("not json", encoding="utf-8")
        _create_task_file(team_dir, "good", {"status": "completed", "subject": "OK"})

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["completed"] == 1
        assert result["total"] == 1

    def test_non_json_files_ignored(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_dir = tmp_path / "tasks" / "team-abc"
        team_dir.mkdir(parents=True)
        (team_dir / "notes.txt").write_text("some notes", encoding="utf-8")

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["total"] == 0

    def test_multiple_teams_aggregated(self, tmp_path):
        from precompact_state_reminder import _gather_task_state
        team_a = tmp_path / "tasks" / "team-a"
        team_b = tmp_path / "tasks" / "team-b"
        _create_task_file(team_a, "t1", {"status": "completed", "subject": "A1"})
        _create_task_file(team_b, "t2", {"status": "in_progress", "subject": "B1"})

        result = _gather_task_state(str(tmp_path / "tasks"))
        assert result["total"] == 2
        assert result["completed"] == 1
        assert result["in_progress"] == 1


# ---------------------------------------------------------------------------
# Unit tests: _gather_team_info
# ---------------------------------------------------------------------------


class TestGatherTeamInfo:
    """Test team config reading for teammate names and team names."""

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        from precompact_state_reminder import _gather_team_info
        result = _gather_team_info(str(tmp_path / "no-such-dir"))
        assert result["teammates"] == []
        assert result["team_names"] == []

    def test_empty_dir_returns_empty(self, tmp_path):
        from precompact_state_reminder import _gather_team_info
        teams_dir = tmp_path / "teams"
        teams_dir.mkdir()
        result = _gather_team_info(str(teams_dir))
        assert result["teammates"] == []

    def test_reads_member_names(self, tmp_path):
        from precompact_state_reminder import _gather_team_info
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [
            {"name": "backend-coder"},
            {"name": "test-engineer"},
        ], name="pact-abc")

        result = _gather_team_info(str(teams_dir))
        assert "backend-coder" in result["teammates"]
        assert "test-engineer" in result["teammates"]

    def test_reads_team_names(self, tmp_path):
        from precompact_state_reminder import _gather_team_info
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [], name="pact-abc")

        result = _gather_team_info(str(teams_dir))
        assert "pact-abc" in result["team_names"]

    def test_team_name_falls_back_to_dir_name(self, tmp_path):
        from precompact_state_reminder import _gather_team_info
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-xyz", [])  # no name field

        result = _gather_team_info(str(teams_dir))
        assert "pact-xyz" in result["team_names"]

    def test_skips_empty_names(self, tmp_path):
        from precompact_state_reminder import _gather_team_info
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [
            {"name": "coder"},
            {"name": ""},
            {"role": "observer"},
        ])

        result = _gather_team_info(str(teams_dir))
        assert result["teammates"] == ["coder"]

    def test_malformed_config_skipped(self, tmp_path):
        from precompact_state_reminder import _gather_team_info
        teams_dir = tmp_path / "teams"
        team_dir = teams_dir / "pact-bad"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text("not json", encoding="utf-8")

        result = _gather_team_info(str(teams_dir))
        assert result["teammates"] == []

    def test_missing_config_file_skipped(self, tmp_path):
        from precompact_state_reminder import _gather_team_info
        teams_dir = tmp_path / "teams"
        (teams_dir / "pact-empty").mkdir(parents=True)

        result = _gather_team_info(str(teams_dir))
        assert result["teammates"] == []

    def test_non_dict_members_handled(self, tmp_path):
        from precompact_state_reminder import _gather_team_info
        teams_dir = tmp_path / "teams"
        _create_team_config(teams_dir, "pact-abc", [
            "string-entry",
            42,
            {"name": "valid-coder"},
        ])

        result = _gather_team_info(str(teams_dir))
        assert result["teammates"] == ["valid-coder"]


# ---------------------------------------------------------------------------
# Unit tests: _build_state_summary
# ---------------------------------------------------------------------------


class TestBuildStateSummary:
    """Test state summary formatting."""

    def test_full_summary(self):
        from precompact_state_reminder import _build_state_summary
        task_state = {
            "completed": 3, "in_progress": 2, "pending": 1, "total": 6,
            "feature_subject": "Add auth flow", "feature_id": "5",
            "current_phase": "Phase: CODE", "variety_score": 9,
        }
        team_info = {"teammates": ["coder", "tester"], "team_names": ["pact-abc"]}
        result = _build_state_summary(task_state, team_info)
        assert "3 completed" in result
        assert "2 in_progress" in result
        assert "total: 6" in result
        assert "Add auth flow" in result
        assert "task #5" in result
        assert "Phase: CODE" in result
        assert "coder, tester" in result

    def test_no_tasks(self):
        from precompact_state_reminder import _build_state_summary
        task_state = {
            "completed": 0, "in_progress": 0, "pending": 0, "total": 0,
            "feature_subject": None, "feature_id": None,
            "current_phase": None, "variety_score": None,
        }
        team_info = {"teammates": [], "team_names": []}
        result = _build_state_summary(task_state, team_info)
        assert "none found on disk" in result

    def test_no_feature_omits_feature_line(self):
        from precompact_state_reminder import _build_state_summary
        task_state = {
            "completed": 1, "in_progress": 0, "pending": 0, "total": 1,
            "feature_subject": None, "feature_id": None,
            "current_phase": None, "variety_score": None,
        }
        result = _build_state_summary(task_state, {"teammates": [], "team_names": []})
        assert "Feature:" not in result

    def test_no_phase_omits_phase_line(self):
        from precompact_state_reminder import _build_state_summary
        task_state = {
            "completed": 0, "in_progress": 0, "pending": 0, "total": 0,
            "feature_subject": None, "feature_id": None,
            "current_phase": None, "variety_score": None,
        }
        result = _build_state_summary(task_state, {"teammates": [], "team_names": []})
        assert "Current phase:" not in result


# ---------------------------------------------------------------------------
# Unit tests: build_custom_instructions
# ---------------------------------------------------------------------------


class TestBuildCustomInstructions:
    """Test custom_instructions composition for compaction model."""

    def test_full_instructions(self):
        from precompact_state_reminder import build_custom_instructions
        task_state = {
            "feature_subject": "Add auth", "feature_id": "5",
            "current_phase": "Phase: CODE", "variety_score": 9,
        }
        team_info = {"teammates": ["coder", "tester"], "team_names": ["pact-abc"]}
        result = build_custom_instructions(task_state, team_info)
        assert "CRITICAL CONTEXT TO PRESERVE" in result
        assert "Add auth" in result
        assert "task #5" in result
        assert "Phase: CODE" in result
        assert "coder, tester" in result
        assert "Variety score: 9" in result
        assert "pact-abc" in result
        assert "Preserve task IDs and agent names exactly" in result

    def test_minimal_state(self):
        from precompact_state_reminder import build_custom_instructions
        task_state = {
            "feature_subject": None, "feature_id": None,
            "current_phase": None, "variety_score": None,
        }
        team_info = {"teammates": [], "team_names": []}
        result = build_custom_instructions(task_state, team_info)
        assert "CRITICAL CONTEXT" in result
        assert "unknown" in result  # phase unknown
        assert "none found" in result  # agents none found
        assert "Preserve task IDs" in result

    def test_no_variety_omits_variety_line(self):
        from precompact_state_reminder import build_custom_instructions
        task_state = {
            "feature_subject": "X", "feature_id": "1",
            "current_phase": "Phase: TEST", "variety_score": None,
        }
        team_info = {"teammates": ["a"], "team_names": ["t"]}
        result = build_custom_instructions(task_state, team_info)
        assert "Variety" not in result

    def test_variety_zero_included(self):
        from precompact_state_reminder import build_custom_instructions
        task_state = {
            "feature_subject": "X", "feature_id": "1",
            "current_phase": None, "variety_score": 0,
        }
        team_info = {"teammates": [], "team_names": []}
        result = build_custom_instructions(task_state, team_info)
        assert "Variety score: 0" in result


# ---------------------------------------------------------------------------
# Unit tests: build_hook_output (full composition)
# ---------------------------------------------------------------------------


class TestBuildHookOutput:
    """Test complete hook output with both fields."""

    def test_has_both_fields(self, tmp_path):
        from precompact_state_reminder import build_hook_output
        tasks_dir = tmp_path / "tasks"
        teams_dir = tmp_path / "teams"
        team_task_dir = tasks_dir / "pact-test"
        _create_task_file(team_task_dir, "t1", {
            "id": "7",
            "status": "in_progress",
            "subject": "Build dashboard",
        })
        _create_team_config(teams_dir, "pact-test", [
            {"name": "frontend-coder"},
        ], name="pact-test")

        result = build_hook_output(str(tasks_dir), str(teams_dir))
        assert "custom_instructions" in result
        assert "systemMessage" in result

    def test_custom_instructions_has_feature(self, tmp_path):
        from precompact_state_reminder import build_hook_output
        tasks_dir = tmp_path / "tasks"
        teams_dir = tmp_path / "teams"
        _create_task_file(tasks_dir / "pact-t", "3", {
            "id": "3",
            "status": "in_progress",
            "subject": "Auth feature",
        })
        _create_team_config(teams_dir, "pact-t", [{"name": "coder"}], name="pact-t")

        result = build_hook_output(str(tasks_dir), str(teams_dir))
        assert "Auth feature" in result["custom_instructions"]
        assert "task #3" in result["custom_instructions"]

    def test_system_message_has_brain_dump(self, tmp_path):
        from precompact_state_reminder import build_hook_output
        result = build_hook_output(str(tmp_path), str(tmp_path))
        assert "TaskCreate" in result["systemMessage"]
        assert "Pre-compaction state dump" in result["systemMessage"]

    def test_empty_dirs_produces_valid_output(self, tmp_path):
        from precompact_state_reminder import build_hook_output
        result = build_hook_output(
            str(tmp_path / "no-tasks"),
            str(tmp_path / "no-teams"),
        )
        assert "custom_instructions" in result
        assert "systemMessage" in result
        assert "CRITICAL CONTEXT" in result["custom_instructions"]


# ---------------------------------------------------------------------------
# Integration tests: subprocess
# ---------------------------------------------------------------------------


class TestPrecompactSubprocess:
    """Verify the hook emits expected JSON via subprocess."""

    def test_emits_both_fields(self):
        result = run_hook(json.dumps({"transcript_path": "/tmp/test.jsonl"}))
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output
        assert "custom_instructions" in output

    def test_custom_instructions_has_critical_context(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "CRITICAL CONTEXT" in output["custom_instructions"]
        assert "Preserve task IDs" in output["custom_instructions"]

    def test_system_message_has_compaction(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "compaction" in output["systemMessage"].lower()

    def test_system_message_has_task_create(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "TaskCreate" in output["systemMessage"]

    def test_system_message_has_secretary(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "secretary" in output["systemMessage"]


# ---------------------------------------------------------------------------
# Fail-open tests
# ---------------------------------------------------------------------------


class TestPrecompactFailOpen:
    """Verify fail-open behavior."""

    def test_empty_stdin_exits_zero(self):
        result = run_hook("")
        assert result.returncode == 0

    def test_malformed_json_exits_zero(self):
        result = run_hook("not json at all")
        assert result.returncode == 0

    def test_malformed_json_still_emits_output(self):
        result = run_hook("not json at all")
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output
        assert "custom_instructions" in output

    def test_null_input_exits_zero(self):
        result = run_hook("null")
        assert result.returncode == 0

    def test_array_input_exits_zero(self):
        result = run_hook("[]")
        assert result.returncode == 0

    def test_disk_read_error_fails_open(self, tmp_path):
        from precompact_state_reminder import build_hook_output
        fake_file = tmp_path / "not-a-dir"
        fake_file.write_text("x", encoding="utf-8")
        result = build_hook_output(str(fake_file), str(fake_file))
        assert "none found" in result["systemMessage"]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants."""

    def test_brain_dump_instructions(self):
        from precompact_state_reminder import BRAIN_DUMP_INSTRUCTIONS
        assert "TaskCreate" in BRAIN_DUMP_INSTRUCTIONS
        assert "SendMessage" in BRAIN_DUMP_INSTRUCTIONS
        assert "secretary" in BRAIN_DUMP_INSTRUCTIONS
