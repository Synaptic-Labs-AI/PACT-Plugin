"""
Tests for hooks/precompact_state_reminder.py — PreCompact hook that gathers
mechanical state from disk and emits custom_instructions (for the compaction
model) and a systemMessage (brain dump instructions for the orchestrator).

Tests cover:
1. State summary formatting
2. Custom instructions composition
3. Full hook output (both fields)
4. Subprocess integration (JSON output, exit code)
5. Fail-open on malformed input, missing dirs, bad JSON files
6. Outer exception handler (hook_error_json output on unexpected errors)

Note: Disk state gathering (task analysis, team scanning) is tested in
test_task_scanner.py since those functions now live in shared/task_scanner.py.
"""
import json
import subprocess
import sys
from io import StringIO
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

    def test_custom_instructions_has_feature(self, tmp_path, monkeypatch):
        """Feature surfaces when a journal event names the feature task
        and the team's task file is reachable via session-scoped disk read.

        Exercises the new journal-based code path: variety_assessed in the
        journal identifies feature_id=3; with no matching agent_handoff,
        session_state reads ~/.claude/tasks/pact-t/3.json for the subject.
        build_hook_output accepts only tasks/teams base dirs, so session_dir
        and team_name are threaded in via monkeypatched pact_context."""
        from shared.session_journal import make_event
        import shared.pact_context as ctx_module
        from precompact_state_reminder import build_hook_output

        tasks_dir = tmp_path / "tasks"
        teams_dir = tmp_path / "teams"
        session_dir = tmp_path / "session-abc"

        # Journal event: feature_id=3; no handoff → disk fallback supplies subject
        session_dir.mkdir(parents=True)
        (session_dir / "session-journal.jsonl").write_text(
            json.dumps(make_event(
                "variety_assessed", task_id="3",
                variety={"score": 6, "level": "MEDIUM"},
                ts="2026-04-14T00:00:01Z",
            )) + "\n",
            encoding="utf-8",
        )

        _create_task_file(tasks_dir / "pact-t", "3", {
            "id": "3",
            "status": "in_progress",
            "subject": "Auth feature",
        })
        _create_team_config(teams_dir, "pact-t", [{"name": "coder"}], name="pact-t")

        # Thread session_dir + team_name via pact_context (build_hook_output
        # does not accept them directly)
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: str(session_dir))
        monkeypatch.setattr(ctx_module, "get_team_name", lambda: "pact-t")

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


# ---------------------------------------------------------------------------
# Outer exception handler tests
# ---------------------------------------------------------------------------


class TestPrecompactOuterExceptionHandler:
    """Verify that main() catches unexpected exceptions, exits 0,
    emits hook_error_json on stdout and error info on stderr."""

    def test_exits_zero_on_unexpected_error(self):
        """main() must exit 0 even when build_hook_output raises."""
        from precompact_state_reminder import main

        with patch("sys.stdin", StringIO("{}")), \
             patch("precompact_state_reminder.build_hook_output",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stderr_contains_error_info(self, capsys):
        """Error details must appear on stderr for logging."""
        from precompact_state_reminder import main

        with patch("sys.stdin", StringIO("{}")), \
             patch("precompact_state_reminder.build_hook_output",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "precompact_state_reminder" in captured.err
        assert "test error" in captured.err

    def test_stdout_contains_hook_error_json(self, capsys):
        """Stdout must contain structured JSON from hook_error_json."""
        from precompact_state_reminder import main

        with patch("sys.stdin", StringIO("{}")), \
             patch("precompact_state_reminder.build_hook_output",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "systemMessage" in output
        assert "PACT hook warning" in output["systemMessage"]
        assert "precompact_state_reminder" in output["systemMessage"]
        assert "test error" in output["systemMessage"]
