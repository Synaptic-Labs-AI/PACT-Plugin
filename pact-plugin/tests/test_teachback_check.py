# pact-plugin/tests/test_teachback_check.py
"""
Tests for teachback_check.py — PostToolUse hook on Edit|Write that emits
a one-shot warning if an agent uses implementation tools before setting
teachback_sent metadata.

Tests cover:
1. check_teachback_sent() — custom task scanner: ownership, status filtering,
   metadata lookup, fail-open on errors, edge cases (no dir, empty dir,
   corrupted JSON, missing fields)
2. should_warn() — decision logic combining exemptions, one-shot marker,
   and teachback metadata
3. Marker file lifecycle — _get_marker_path, _mark_warned, _was_already_warned
4. main() entry point — env var guards, stdin parsing, warn vs suppress output,
   fail-open exception handler, suppressOutput on bare exit paths
5. _get_project_slug() — CLAUDE_PROJECT_DIR derivation
"""
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# =============================================================================
# Helper: Create a task JSON file in the task directory
# =============================================================================

def _write_task(task_dir: Path, filename: str, data: dict) -> Path:
    """Write a task JSON file and return its path."""
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# =============================================================================
# Unit Tests: _get_project_slug()
# =============================================================================

class TestGetProjectSlug:
    """Tests for _get_project_slug() env var derivation."""

    def test_extracts_basename_from_project_dir(self):
        from teachback_check import _get_project_slug

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/Users/dev/projects/my-app"}):
            assert _get_project_slug() == "my-app"

    def test_returns_empty_when_env_not_set(self):
        from teachback_check import _get_project_slug

        with patch.dict("os.environ", {}, clear=True):
            assert _get_project_slug() == ""

    def test_returns_empty_when_env_is_empty_string(self):
        from teachback_check import _get_project_slug

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": ""}):
            assert _get_project_slug() == ""

    def test_handles_root_path(self):
        from teachback_check import _get_project_slug

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/"}):
            # Path("/").name returns "" on most systems
            result = _get_project_slug()
            assert isinstance(result, str)

    def test_handles_nested_path(self):
        from teachback_check import _get_project_slug

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/a/b/c/deep-project"}):
            assert _get_project_slug() == "deep-project"


# =============================================================================
# Unit Tests: Marker file lifecycle
# =============================================================================

class TestMarkerPath:
    """Tests for _get_marker_path()."""

    def test_builds_correct_path(self, tmp_path):
        from teachback_check import _get_marker_path

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/my-app"}):
            result = _get_marker_path("backend-coder-1", sessions_dir=str(tmp_path))

        assert result == tmp_path / "my-app" / "teachback-warned-backend-coder-1"

    def test_uses_project_slug(self, tmp_path):
        from teachback_check import _get_marker_path

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/PACT-prompt"}):
            result = _get_marker_path("test-engineer", sessions_dir=str(tmp_path))

        assert "PACT-prompt" in str(result)

    def test_empty_slug_when_no_project_dir(self, tmp_path):
        from teachback_check import _get_marker_path

        with patch.dict("os.environ", {}, clear=True):
            result = _get_marker_path("agent-1", sessions_dir=str(tmp_path))

        # Path ends with /<empty>/teachback-warned-agent-1
        assert result.name == "teachback-warned-agent-1"


class TestMarkWarned:
    """Tests for _mark_warned() and _was_already_warned()."""

    def test_mark_creates_file(self, tmp_path):
        from teachback_check import _mark_warned, _was_already_warned

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            assert not _was_already_warned("coder-1", sessions_dir=str(tmp_path))
            _mark_warned("coder-1", sessions_dir=str(tmp_path))
            assert _was_already_warned("coder-1", sessions_dir=str(tmp_path))

    def test_mark_creates_parent_directories(self, tmp_path):
        from teachback_check import _mark_warned

        sessions_dir = str(tmp_path / "nonexistent" / "deep" / "path")
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", sessions_dir=sessions_dir)

        # Verify the directory structure was created
        marker = Path(sessions_dir) / "test" / "teachback-warned-coder-1"
        assert marker.exists()

    def test_mark_uses_secure_permissions(self, tmp_path):
        from teachback_check import _mark_warned, _get_marker_path

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", sessions_dir=str(tmp_path))
            marker = _get_marker_path("coder-1", sessions_dir=str(tmp_path))

        # Check file permissions are 0o600 (user read/write only)
        mode = marker.stat().st_mode & 0o777
        assert mode == 0o600

    def test_mark_idempotent(self, tmp_path):
        from teachback_check import _mark_warned, _was_already_warned

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", sessions_dir=str(tmp_path))
            _mark_warned("coder-1", sessions_dir=str(tmp_path))  # Should not error
            assert _was_already_warned("coder-1", sessions_dir=str(tmp_path))

    def test_different_agents_have_separate_markers(self, tmp_path):
        from teachback_check import _mark_warned, _was_already_warned

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", sessions_dir=str(tmp_path))

            assert _was_already_warned("coder-1", sessions_dir=str(tmp_path))
            assert not _was_already_warned("coder-2", sessions_dir=str(tmp_path))

    def test_mark_survives_readonly_parent_gracefully(self, tmp_path):
        """_mark_warned should not raise even if writing fails."""
        from teachback_check import _mark_warned

        # Create a read-only directory to force OSError
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)

        try:
            with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
                # Should not raise — the OSError is caught internally
                _mark_warned("coder-1", sessions_dir=str(readonly_dir / "sub"))
        finally:
            readonly_dir.chmod(0o755)  # Restore for cleanup


# =============================================================================
# Unit Tests: check_teachback_sent() — custom task scanner
# =============================================================================

class TestCheckTeachbackSent:
    """Tests for check_teachback_sent() — the custom task scanner.

    This is the primary test surface for the auditor's YELLOW note about
    using custom task scanning instead of shared/task_scanner.py.
    """

    def test_returns_true_when_teachback_confirmed(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is True

    def test_returns_false_when_no_teachback_metadata(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_returns_false_when_metadata_is_none(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": None,
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_returns_false_when_no_metadata_key(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_ignores_tasks_not_owned_by_agent(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "frontend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        _write_task(task_dir, "2.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_ignores_completed_tasks(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "completed",
            "metadata": {"teachback_sent": True},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_ignores_pending_tasks(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "pending",
            "metadata": {"teachback_sent": True},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_scans_multiple_tasks_finds_one_with_teachback(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        # Task without teachback
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })
        # Task with teachback
        _write_task(task_dir, "2.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is True

    def test_fails_open_on_missing_task_dir(self, tmp_path):
        """No task directory → fail open (return True)."""
        from teachback_check import check_teachback_sent

        assert check_teachback_sent(
            "backend-coder-1", "nonexistent-team", str(tmp_path)
        ) is True

    def test_fails_open_on_empty_agent_name(self, tmp_path):
        from teachback_check import check_teachback_sent

        assert check_teachback_sent("", "pact-test", str(tmp_path)) is True

    def test_fails_open_on_empty_team_name(self, tmp_path):
        from teachback_check import check_teachback_sent

        assert check_teachback_sent("backend-coder-1", "", str(tmp_path)) is True

    def test_skips_corrupted_json_files(self, tmp_path):
        """Corrupted JSON task files should be skipped, not crash the scanner."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        (task_dir / "1.json").write_text("not valid json{{{")
        _write_task(task_dir, "2.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })

        # Should skip the corrupted file and find the valid one
        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is True

    def test_skips_non_json_files(self, tmp_path):
        """Non-.json files in the task directory should be ignored."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        (task_dir / "readme.txt").write_text("not a task")
        (task_dir / "config.yaml").write_text("also not a task")
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_rejects_teachback_sent_false(self, tmp_path):
        """teachback_sent: false should NOT count as confirmation."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": False},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_rejects_teachback_sent_string_true(self, tmp_path):
        """teachback_sent: "true" (string) should NOT count — strict boolean check."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": "true"},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_rejects_teachback_sent_truthy_int(self, tmp_path):
        """teachback_sent: 1 should NOT count — strict boolean check."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": 1},
        })

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False

    def test_fails_open_on_oserror_during_scan(self, tmp_path):
        """OSError during directory iteration → fail open."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)

        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            assert check_teachback_sent(
                "backend-coder-1", "pact-test", str(tmp_path)
            ) is True

    def test_empty_task_directory(self, tmp_path):
        """Empty task directory → no tasks found → return False."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)

        assert check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path)) is False


# =============================================================================
# Unit Tests: should_warn() — decision logic
# =============================================================================

class TestShouldWarn:
    """Tests for should_warn() — combines exemptions, marker, and metadata."""

    def test_warns_when_no_teachback_sent(self, tmp_path):
        from teachback_check import should_warn

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            assert should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            ) is True

    def test_no_warn_when_teachback_sent(self, tmp_path):
        from teachback_check import should_warn

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            assert should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            ) is False

    def test_no_warn_when_already_warned(self, tmp_path):
        from teachback_check import should_warn, _mark_warned

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("backend-coder-1", sessions_dir=sessions_dir)
            assert should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            ) is False

    @pytest.mark.parametrize("agent_name", [
        "secretary",
        "pact-secretary",
        "auditor",
        "pact-auditor",
    ])
    def test_no_warn_for_exempt_agents(self, agent_name, tmp_path):
        from teachback_check import should_warn

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")
        _write_task(task_dir, "1.json", {
            "owner": agent_name,
            "status": "in_progress",
            "metadata": {},
        })

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            assert should_warn(
                agent_name, "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            ) is False

    @pytest.mark.parametrize("agent_name", [
        "Secretary",
        "SECRETARY",
        "Pact-Secretary",
        "AUDITOR",
    ])
    def test_exempt_agents_case_insensitive(self, agent_name, tmp_path):
        """Exemption check uses .lower() so mixed case should still be exempt."""
        from teachback_check import should_warn

        sessions_dir = str(tmp_path / "sessions")

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            assert should_warn(
                agent_name, "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            ) is False

    def test_warns_for_non_exempt_agent(self, tmp_path):
        from teachback_check import should_warn

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            assert should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            ) is True


# =============================================================================
# Integration Tests: main() entry point
# =============================================================================

class TestMainEntryPoint:
    """Tests for main() — env var guards, stdin, output format, exit codes."""

    def test_suppress_output_when_no_agent_name(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {}, clear=True), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_suppress_output_when_agent_name_empty(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {"CLAUDE_CODE_AGENT_NAME": ""}), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_suppress_output_when_no_team_name(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
        }), patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_suppress_output_when_team_name_empty(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
            "CLAUDE_CODE_TEAM_NAME": "",
        }), patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_suppress_output_on_invalid_json_stdin(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }), patch("sys.stdin", io.StringIO("not valid json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_emits_warning_when_should_warn_true(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", return_value=True), \
             patch("teachback_check._mark_warned") as mock_mark:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert "systemMessage" in output
        assert "TEACHBACK REMINDER" in output["systemMessage"]
        mock_mark.assert_called_once_with("backend-coder-1")

    def test_suppress_output_when_should_warn_false(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_team_name_lowercased(self, capsys):
        """CLAUDE_CODE_TEAM_NAME should be lowercased before passing to should_warn."""
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
            "CLAUDE_CODE_TEAM_NAME": "PACT-TEST",
        }), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", return_value=False) as mock_warn:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_warn.assert_called_once_with("backend-coder-1", "pact-test")

    def test_always_exits_0(self, capsys):
        """Hook should always exit 0 — it's a warning layer, not a gate."""
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", return_value=True), \
             patch("teachback_check._mark_warned"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


# =============================================================================
# Exception handler: fail-open behavior
# =============================================================================

class TestExceptionHandler:
    """Tests for the outer exception handler — fail open with hook_error_json."""

    def test_fail_open_on_unexpected_error(self, capsys):
        """Any unhandled exception should produce hook_error_json and exit 0."""
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "systemMessage" in output
        assert "teachback_check" in output["systemMessage"]
        assert "boom" in captured.err

    def test_error_and_suppress_mutually_exclusive(self, capsys):
        """Error path should emit systemMessage, NOT suppressOutput."""
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out.strip())
        assert "systemMessage" in output
        assert "suppressOutput" not in output


# =============================================================================
# suppressOutput on bare exit paths
# =============================================================================

class TestSuppressOutput:
    """Verify all bare exit paths emit suppressOutput, not empty stdout."""

    def test_no_agent_name_emits_suppress(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {}, clear=True), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out.strip())
        assert output.get("suppressOutput") is True

    def test_no_team_name_emits_suppress(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "coder-1",
        }), patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out.strip())
        assert output.get("suppressOutput") is True

    def test_invalid_json_emits_suppress(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "coder-1",
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }), patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out.strip())
        assert output.get("suppressOutput") is True

    def test_should_warn_false_emits_suppress(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "coder-1",
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", return_value=False):
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out.strip())
        assert output.get("suppressOutput") is True


# =============================================================================
# Integration: end-to-end with real filesystem
# =============================================================================

class TestEndToEnd:
    """Full integration tests with real task files and marker files."""

    def test_full_flow_warns_then_suppresses(self, tmp_path, capsys):
        """First Edit/Write warns; second is suppressed by marker."""
        from teachback_check import should_warn, _mark_warned

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            # First call: should warn
            result1 = should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert result1 is True

            # Simulate main() marking warned
            _mark_warned("backend-coder-1", sessions_dir=sessions_dir)

            # Second call: should NOT warn (marker exists)
            result2 = should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert result2 is False

    def test_full_flow_no_warn_after_teachback_set(self, tmp_path):
        """Once teachback_sent is set in metadata, no warning needed."""
        from teachback_check import should_warn

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")

        # Agent sets teachback_sent in their task
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            assert should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            ) is False

    def test_warning_message_content(self):
        """Verify the warning message contains expected instructions."""
        from teachback_check import _WARNING_MESSAGE

        assert "TEACHBACK REMINDER" in _WARNING_MESSAGE
        assert "SendMessage" in _WARNING_MESSAGE
        assert "TaskUpdate" in _WARNING_MESSAGE
        assert "teachback_sent" in _WARNING_MESSAGE

    def test_exempt_agents_frozenset(self):
        """Verify the exempt agents set contains expected names."""
        from teachback_check import _EXEMPT_AGENTS

        assert "secretary" in _EXEMPT_AGENTS
        assert "pact-secretary" in _EXEMPT_AGENTS
        assert "auditor" in _EXEMPT_AGENTS
        assert "pact-auditor" in _EXEMPT_AGENTS
        # Non-exempt agents should not be in the set
        assert "backend-coder-1" not in _EXEMPT_AGENTS
        assert "test-engineer" not in _EXEMPT_AGENTS
