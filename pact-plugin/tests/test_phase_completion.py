"""
Tests for phase_completion.py — Stop hook for phase completion reminders.

Tests cover:
1. check_phase_completion_via_tasks: CODE/TEST phase detection, reminder generation
2. check_for_code_phase_activity: transcript indicator matching
3. check_decision_log_mentioned: decision log term detection
4. check_decision_logs_exist: filesystem check for decision-logs directory
5. check_for_test_reminders: testing indicator detection
6. main: dual-path strategy (Task vs transcript), decision log checks, output format
"""
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# check_phase_completion_via_tasks
# ---------------------------------------------------------------------------

class TestCheckPhaseCompletionViaTasks:
    def test_code_completed_no_test(self):
        from phase_completion import check_phase_completion_via_tasks
        tasks = [
            {"subject": "CODE: auth", "status": "completed"},
            {"subject": "TEST: auth", "status": "pending"},
        ]
        result = check_phase_completion_via_tasks(tasks)
        assert result["code_completed"] is True
        assert result["test_started"] is False
        assert len(result["reminders"]) >= 1
        assert any("TEST Phase" in r for r in result["reminders"])

    def test_code_completed_test_in_progress(self):
        from phase_completion import check_phase_completion_via_tasks
        tasks = [
            {"subject": "CODE: auth", "status": "completed"},
            {"subject": "TEST: auth", "status": "in_progress"},
        ]
        result = check_phase_completion_via_tasks(tasks)
        assert result["code_completed"] is True
        assert result["test_started"] is True
        assert not any("TEST Phase Reminder: CODE phase" in r for r in result["reminders"])

    def test_code_completed_test_completed(self):
        from phase_completion import check_phase_completion_via_tasks
        tasks = [
            {"subject": "CODE: auth", "status": "completed"},
            {"subject": "TEST: auth", "status": "completed"},
        ]
        result = check_phase_completion_via_tasks(tasks)
        assert result["test_completed"] is True
        assert result["test_started"] is True

    def test_code_in_progress(self):
        from phase_completion import check_phase_completion_via_tasks
        tasks = [
            {"subject": "CODE: auth", "status": "in_progress"},
        ]
        result = check_phase_completion_via_tasks(tasks)
        assert result["code_completed"] is False
        assert result["reminders"] == []

    def test_no_phase_tasks(self):
        from phase_completion import check_phase_completion_via_tasks
        tasks = [
            {"subject": "Implement auth system", "status": "in_progress"},
        ]
        result = check_phase_completion_via_tasks(tasks)
        assert result["code_completed"] is False
        assert result["test_started"] is False

    def test_empty_task_list(self):
        from phase_completion import check_phase_completion_via_tasks
        result = check_phase_completion_via_tasks([])
        assert result["code_completed"] is False
        assert result["reminders"] == []

    def test_pending_test_reminder(self):
        from phase_completion import check_phase_completion_via_tasks
        tasks = [
            {"subject": "CODE: feature", "status": "in_progress"},
            {"subject": "TEST: feature", "status": "pending"},
        ]
        result = check_phase_completion_via_tasks(tasks)
        assert any("pending" in r and "blocked" in r.lower() for r in result["reminders"])

    def test_missing_subject_key(self):
        from phase_completion import check_phase_completion_via_tasks
        tasks = [{"status": "completed"}]
        result = check_phase_completion_via_tasks(tasks)
        assert result["code_completed"] is False


# ---------------------------------------------------------------------------
# check_for_code_phase_activity
# ---------------------------------------------------------------------------

class TestCheckForCodePhaseActivity:
    def test_detects_backend_coder(self):
        from phase_completion import check_for_code_phase_activity
        assert check_for_code_phase_activity("invoked pact-backend-coder for auth") is True

    def test_detects_frontend_coder(self):
        from phase_completion import check_for_code_phase_activity
        assert check_for_code_phase_activity("dispatched pact-frontend-coder") is True

    def test_detects_database_engineer(self):
        from phase_completion import check_for_code_phase_activity
        assert check_for_code_phase_activity("pact-database-engineer completed") is True

    def test_detects_devops_engineer(self):
        from phase_completion import check_for_code_phase_activity
        assert check_for_code_phase_activity("pact-devops-engineer running") is True

    def test_detects_underscore_variant(self):
        from phase_completion import check_for_code_phase_activity
        assert check_for_code_phase_activity("pact_backend_coder finished work") is True

    def test_no_code_phase(self):
        from phase_completion import check_for_code_phase_activity
        assert check_for_code_phase_activity("pact-test-engineer ran tests") is False

    def test_case_insensitive(self):
        from phase_completion import check_for_code_phase_activity
        assert check_for_code_phase_activity("PACT-BACKEND-CODER invoked") is True

    def test_empty_transcript(self):
        from phase_completion import check_for_code_phase_activity
        assert check_for_code_phase_activity("") is False


# ---------------------------------------------------------------------------
# check_decision_log_mentioned
# ---------------------------------------------------------------------------

class TestCheckDecisionLogMentioned:
    def test_detects_hyphenated(self):
        from phase_completion import check_decision_log_mentioned
        assert check_decision_log_mentioned("created a decision-log for auth") is True

    def test_detects_spaced(self):
        from phase_completion import check_decision_log_mentioned
        assert check_decision_log_mentioned("added to the decision log") is True

    def test_detects_underscore(self):
        from phase_completion import check_decision_log_mentioned
        assert check_decision_log_mentioned("decision_log created") is True

    def test_detects_directory_path(self):
        from phase_completion import check_decision_log_mentioned
        assert check_decision_log_mentioned("wrote to docs/decision-logs/auth.md") is True

    def test_no_mention(self):
        from phase_completion import check_decision_log_mentioned
        assert check_decision_log_mentioned("implemented the feature") is False

    def test_case_insensitive(self):
        from phase_completion import check_decision_log_mentioned
        assert check_decision_log_mentioned("Created a Decision-Log") is True


# ---------------------------------------------------------------------------
# check_decision_logs_exist
# ---------------------------------------------------------------------------

class TestCheckDecisionLogsExist:
    def test_directory_with_md_files(self, tmp_path):
        from phase_completion import check_decision_logs_exist
        logs_dir = tmp_path / "docs" / "decision-logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "auth-backend.md").write_text("# Auth decisions")
        assert check_decision_logs_exist(str(tmp_path)) is True

    def test_directory_without_md_files(self, tmp_path):
        from phase_completion import check_decision_logs_exist
        logs_dir = tmp_path / "docs" / "decision-logs"
        logs_dir.mkdir(parents=True)
        assert check_decision_logs_exist(str(tmp_path)) is False

    def test_no_directory(self, tmp_path):
        from phase_completion import check_decision_logs_exist
        assert check_decision_logs_exist(str(tmp_path)) is False

    def test_empty_project_dir(self, tmp_path):
        from phase_completion import check_decision_logs_exist
        assert check_decision_logs_exist(str(tmp_path / "nonexistent")) is False


# ---------------------------------------------------------------------------
# check_for_test_reminders
# ---------------------------------------------------------------------------

class TestCheckForTestReminders:
    def test_detects_test_engineer(self):
        from phase_completion import check_for_test_reminders
        assert check_for_test_reminders("invoked pact-test-engineer") is True

    def test_detects_testing_keyword(self):
        from phase_completion import check_for_test_reminders
        assert check_for_test_reminders("discussed testing strategy") is True

    def test_detects_unit_test(self):
        from phase_completion import check_for_test_reminders
        assert check_for_test_reminders("wrote unit test for auth") is True

    def test_detects_test_coverage(self):
        from phase_completion import check_for_test_reminders
        assert check_for_test_reminders("checked test coverage report") is True

    def test_no_test_indicators(self):
        from phase_completion import check_for_test_reminders
        assert check_for_test_reminders("implemented the auth system") is False

    def test_case_insensitive(self):
        from phase_completion import check_for_test_reminders
        assert check_for_test_reminders("Discussed TESTING strategy") is True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_task_path_code_completed_no_test(self, capsys, tmp_path):
        from phase_completion import main
        tasks = [
            {"id": "1", "subject": "CODE: auth", "status": "completed"},
            {"subject": "TEST: auth", "status": "pending"},
        ]
        input_data = {"transcript": ""}
        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("phase_completion.get_task_list", return_value=tasks),
            patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "TEST Phase" in output["systemMessage"]

    def test_task_path_no_reminders(self, capsys):
        from phase_completion import main
        tasks = [
            {"subject": "CODE: auth", "status": "in_progress"},
        ]
        input_data = {"transcript": ""}
        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("phase_completion.get_task_list", return_value=tasks),
            patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "."}),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_transcript_fallback_code_detected(self, capsys, tmp_path):
        from phase_completion import main
        input_data = {"transcript": "invoked pact-backend-coder for auth work"}
        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("phase_completion.get_task_list", return_value=None),
            patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        # Should have both test reminder and decision log reminder
        assert "TEST Phase" in output["systemMessage"]
        assert "Decision" in output["systemMessage"]

    def test_transcript_fallback_code_with_testing(self, capsys, tmp_path):
        from phase_completion import main
        input_data = {
            "transcript": "invoked pact-backend-coder and discussed testing strategy"
        }
        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("phase_completion.get_task_list", return_value=None),
            patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        # Testing was discussed, so no test reminder but still decision log reminder
        output = json.loads(captured.out)
        assert "TEST Phase Reminder: Consider" not in output["systemMessage"]
        assert "Decision" in output["systemMessage"]

    def test_decision_log_exists_no_reminder(self, capsys, tmp_path):
        from phase_completion import main
        logs_dir = tmp_path / "docs" / "decision-logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "auth.md").write_text("# Auth")
        tasks = [
            {"subject": "CODE: auth", "status": "completed"},
            {"subject": "TEST: auth", "status": "in_progress"},
        ]
        input_data = {"transcript": "completed code phase"}
        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("phase_completion.get_task_list", return_value=tasks),
            patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}  # No reminders: test started, decision logs exist

    def test_decision_log_mentioned_no_reminder(self, capsys, tmp_path):
        from phase_completion import main
        tasks = [
            {"subject": "CODE: auth", "status": "completed"},
            {"subject": "TEST: auth", "status": "in_progress"},
        ]
        input_data = {"transcript": "created decision-log for auth choices"}
        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("phase_completion.get_task_list", return_value=tasks),
            patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}  # No reminders: test started, decision log mentioned

    def test_no_code_phase_no_output(self, capsys):
        from phase_completion import main
        input_data = {"transcript": "just chatted about requirements"}
        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("phase_completion.get_task_list", return_value=None),
            patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "."}),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_invalid_json_input(self):
        from phase_completion import main
        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0

    def test_exception_exits_cleanly(self):
        from phase_completion import main
        with patch("sys.stdin", side_effect=Exception("boom")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
