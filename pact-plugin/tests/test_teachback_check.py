# pact-plugin/tests/test_teachback_check.py
"""
Tests for teachback_check.py — PostToolUse hook on Edit|Write that emits
a one-shot warning if an agent uses implementation tools before setting
teachback_sent metadata.

Tests cover:
1. check_teachback_sent() — custom task scanner: ownership, status filtering,
   metadata lookup, fail-open on errors, edge cases (no dir, empty dir,
   corrupted JSON, missing fields)
2. should_warn() — decision logic combining exemptions, per-task marker,
   and teachback metadata
3. Per-task marker lifecycle — _get_marker_path, _mark_warned, _was_already_warned
   with task_id parameter (format: teachback-warned-{agent}-{task_id})
4. Per-task marker isolation — same agent gets independent warnings per task
5. main() entry point — env var guards, stdin parsing, warn vs suppress output,
   fail-open exception handler, suppressOutput on bare exit paths
6. _get_project_slug() — CLAUDE_PROJECT_DIR derivation
7. Concurrent access stress tests — multiple writers, idempotency under threading
8. hooks.json structural tests — registration position verification
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
    """Tests for _get_marker_path() — per-task marker format."""

    def test_builds_correct_path_with_task_id(self, tmp_path):
        from teachback_check import _get_marker_path

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/my-app"}):
            result = _get_marker_path("backend-coder-1", "42", sessions_dir=str(tmp_path))

        assert result == tmp_path / "my-app" / "teachback-warned-backend-coder-1-42"

    def test_uses_project_slug(self, tmp_path):
        from teachback_check import _get_marker_path

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/PACT-prompt"}):
            result = _get_marker_path("test-engineer", "7", sessions_dir=str(tmp_path))

        assert "PACT-prompt" in str(result)
        assert result.name == "teachback-warned-test-engineer-7"

    def test_empty_slug_when_no_project_dir(self, tmp_path):
        from teachback_check import _get_marker_path

        with patch.dict("os.environ", {}, clear=True):
            result = _get_marker_path("agent-1", "1", sessions_dir=str(tmp_path))

        # Path ends with /<empty>/teachback-warned-agent-1-1
        assert result.name == "teachback-warned-agent-1-1"


class TestMarkWarned:
    """Tests for _mark_warned() and _was_already_warned() — per-task markers."""

    def test_mark_creates_file(self, tmp_path):
        from teachback_check import _mark_warned, _was_already_warned

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            assert not _was_already_warned("coder-1", "42", sessions_dir=str(tmp_path))
            _mark_warned("coder-1", "42", sessions_dir=str(tmp_path))
            assert _was_already_warned("coder-1", "42", sessions_dir=str(tmp_path))

    def test_mark_creates_parent_directories(self, tmp_path):
        from teachback_check import _mark_warned

        sessions_dir = str(tmp_path / "nonexistent" / "deep" / "path")
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", "42", sessions_dir=sessions_dir)

        # Verify the directory structure was created
        marker = Path(sessions_dir) / "test" / "teachback-warned-coder-1-42"
        assert marker.exists()

    def test_mark_uses_secure_permissions(self, tmp_path):
        from teachback_check import _mark_warned, _get_marker_path

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", "42", sessions_dir=str(tmp_path))
            marker = _get_marker_path("coder-1", "42", sessions_dir=str(tmp_path))

        # Check file permissions are 0o600 (user read/write only)
        mode = marker.stat().st_mode & 0o777
        assert mode == 0o600

    def test_mark_idempotent(self, tmp_path):
        from teachback_check import _mark_warned, _was_already_warned

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", "42", sessions_dir=str(tmp_path))
            _mark_warned("coder-1", "42", sessions_dir=str(tmp_path))  # Should not error
            assert _was_already_warned("coder-1", "42", sessions_dir=str(tmp_path))

    def test_different_agents_have_separate_markers(self, tmp_path):
        from teachback_check import _mark_warned, _was_already_warned

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", "42", sessions_dir=str(tmp_path))

            assert _was_already_warned("coder-1", "42", sessions_dir=str(tmp_path))
            assert not _was_already_warned("coder-2", "42", sessions_dir=str(tmp_path))

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
                _mark_warned("coder-1", "42", sessions_dir=str(readonly_dir / "sub"))
        finally:
            readonly_dir.chmod(0o755)  # Restore for cleanup


# =============================================================================
# Unit Tests: check_teachback_sent() — custom task scanner
# =============================================================================

class TestCheckTeachbackSent:
    """Tests for check_teachback_sent() — the custom task scanner.

    Returns tuple[bool, str]: (confirmed, task_id).
    - (True, "") when teachback confirmed or fail-open
    - (False, task_id) when unconfirmed — task_id is the filename stem

    This is the primary test surface for the auditor's YELLOW note about
    using custom task scanning instead of shared/task_scanner.py.
    """

    def test_returns_confirmed_when_teachback_set(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is True
        assert task_id == ""

    def test_returns_unconfirmed_with_task_id(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "1"

    def test_returns_unconfirmed_when_metadata_is_none(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": None,
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "1"

    def test_returns_unconfirmed_when_no_metadata_key(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "1"

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

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "2"

    def test_ignores_completed_tasks(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "completed",
            "metadata": {"teachback_sent": True},
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == ""

    def test_ignores_pending_tasks(self, tmp_path):
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "pending",
            "metadata": {"teachback_sent": True},
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == ""

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

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is True
        assert task_id == ""

    def test_fails_open_on_missing_task_dir(self, tmp_path):
        """No task directory → fail open (True, "")."""
        from teachback_check import check_teachback_sent

        confirmed, task_id = check_teachback_sent(
            "backend-coder-1", "nonexistent-team", str(tmp_path)
        )
        assert confirmed is True
        assert task_id == ""

    def test_fails_open_on_empty_agent_name(self, tmp_path):
        from teachback_check import check_teachback_sent

        confirmed, task_id = check_teachback_sent("", "pact-test", str(tmp_path))
        assert confirmed is True
        assert task_id == ""

    def test_fails_open_on_empty_team_name(self, tmp_path):
        from teachback_check import check_teachback_sent

        confirmed, task_id = check_teachback_sent("backend-coder-1", "", str(tmp_path))
        assert confirmed is True
        assert task_id == ""

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
        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is True

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

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "1"

    def test_rejects_teachback_sent_false(self, tmp_path):
        """teachback_sent: false should NOT count as confirmation."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": False},
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "1"

    def test_rejects_teachback_sent_string_true(self, tmp_path):
        """teachback_sent: "true" (string) should NOT count — strict boolean check."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": "true"},
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "1"

    def test_rejects_teachback_sent_truthy_int(self, tmp_path):
        """teachback_sent: 1 should NOT count — strict boolean check."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": 1},
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "1"

    def test_fails_open_on_oserror_during_scan(self, tmp_path):
        """OSError during directory iteration → fail open."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)

        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            confirmed, task_id = check_teachback_sent(
                "backend-coder-1", "pact-test", str(tmp_path)
            )
        assert confirmed is True
        assert task_id == ""

    def test_empty_task_directory(self, tmp_path):
        """Empty task directory → no tasks found → (False, "")."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == ""


# =============================================================================
# Unit Tests: should_warn() — decision logic
# =============================================================================

class TestShouldWarn:
    """Tests for should_warn() — combines exemptions, per-task marker, and metadata."""

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
            warn, task_id = should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn is True
            assert task_id == "1"

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
            warn, task_id = should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn is False
            assert task_id == ""

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
            _mark_warned("backend-coder-1", "1", sessions_dir=sessions_dir)
            warn, task_id = should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn is False
            assert task_id == ""

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
            warn, task_id = should_warn(
                agent_name, "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn is False
            assert task_id == ""

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
            warn, task_id = should_warn(
                agent_name, "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn is False
            assert task_id == ""

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
            warn, task_id = should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn is True
            assert task_id == "1"


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
             patch("teachback_check.should_warn", return_value=(True, "1")), \
             patch("teachback_check._mark_warned") as mock_mark:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert "systemMessage" in output
        assert "TEACHBACK REMINDER" in output["systemMessage"]
        mock_mark.assert_called_once_with("backend-coder-1", "1")

    def test_suppress_output_when_should_warn_false(self, capsys):
        from teachback_check import main

        with patch.dict("os.environ", {
            "CLAUDE_CODE_AGENT_NAME": "backend-coder-1",
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", return_value=(False, "")):
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
             patch("teachback_check.should_warn", return_value=(False, "")) as mock_warn:
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
             patch("teachback_check.should_warn", return_value=(True, "1")), \
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
             patch("teachback_check.should_warn", return_value=(False, "")):
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out.strip())
        assert output.get("suppressOutput") is True


# =============================================================================
# Integration: end-to-end with real filesystem
# =============================================================================

class TestEndToEnd:
    """Full integration tests with real task files and per-task marker files."""

    def test_full_flow_warns_then_suppresses(self, tmp_path, capsys):
        """First Edit/Write warns; second is suppressed by per-task marker."""
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
            warn1, tid1 = should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn1 is True
            assert tid1 == "1"

            # Simulate main() marking warned for this task
            _mark_warned("backend-coder-1", tid1, sessions_dir=sessions_dir)

            # Second call: should NOT warn (per-task marker exists)
            warn2, tid2 = should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn2 is False
            assert tid2 == ""

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
            warn, task_id = should_warn(
                "backend-coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn is False
            assert task_id == ""

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


# =============================================================================
# Per-task marker isolation
# =============================================================================

class TestPerTaskMarkerIsolation:
    """Tests for per-task marker behavior — same agent, different tasks get
    independent markers. This is the key behavioral change from per-agent to
    per-task markers."""

    def test_same_agent_warned_on_task_a_not_on_task_b(self, tmp_path):
        """Marker for task A should not suppress warning for task B."""
        from teachback_check import _mark_warned, _was_already_warned

        sessions_dir = str(tmp_path)

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", "10", sessions_dir)

            assert _was_already_warned("coder-1", "10", sessions_dir)
            assert not _was_already_warned("coder-1", "20", sessions_dir)

    def test_different_tasks_have_independent_markers(self, tmp_path):
        """Each task gets its own marker file, even for the same agent."""
        from teachback_check import _mark_warned, _get_marker_path

        sessions_dir = str(tmp_path)

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", "10", sessions_dir)
            _mark_warned("coder-1", "20", sessions_dir)

            marker_10 = _get_marker_path("coder-1", "10", sessions_dir)
            marker_20 = _get_marker_path("coder-1", "20", sessions_dir)

            assert marker_10.exists()
            assert marker_20.exists()
            assert marker_10 != marker_20

    def test_should_warn_per_task_isolation_full_flow(self, tmp_path):
        """Full flow: agent warned on task A, still warned on new task B.

        should_warn() derives task_id internally from check_teachback_sent().
        When only task A exists and has no teachback, should_warn returns
        (True, "10"). After marking that task warned, should_warn returns
        (False, ""). When a new task B is added (also without teachback),
        should_warn discovers it has no marker for task B and warns again.

        Note: when multiple in_progress tasks exist without teachback, the
        scanner returns the first unconfirmed task_id found during iteration.
        To isolate task B's behavior, we mark task A as completed first.
        """
        from teachback_check import should_warn, _mark_warned

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")

        # Create task A — no teachback yet
        _write_task(task_dir, "10.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            # First call for task A: should warn
            warn_a, tid_a = should_warn(
                "coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn_a is True
            assert tid_a == "10"

            # Mark warned for task A
            _mark_warned("coder-1", tid_a, sessions_dir=sessions_dir)

            # Now task A should NOT warn (per-task marker exists)
            warn_a2, tid_a2 = should_warn(
                "coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn_a2 is False
            assert tid_a2 == ""

            # Agent reassigned: mark task A completed, create task B
            _write_task(task_dir, "10.json", {
                "owner": "coder-1",
                "status": "completed",
                "metadata": {},
            })
            _write_task(task_dir, "20.json", {
                "owner": "coder-1",
                "status": "in_progress",
                "metadata": {},
            })

            # Task B should still warn (different task, no marker yet)
            warn_b, tid_b = should_warn(
                "coder-1", "pact-test",
                tasks_base_dir=str(tmp_path / "tasks"),
                sessions_dir=sessions_dir,
            )
            assert warn_b is True
            assert tid_b == "20"

    def test_marker_filename_includes_task_id(self, tmp_path):
        """Verify the marker filename contains both agent name and task ID."""
        from teachback_check import _get_marker_path

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            path = _get_marker_path("coder-1", "42", sessions_dir=str(tmp_path))

        assert path.name == "teachback-warned-coder-1-42"

    def test_different_agents_same_task_have_separate_markers(self, tmp_path):
        """Different agents on the same task get different markers."""
        from teachback_check import _mark_warned, _was_already_warned

        sessions_dir = str(tmp_path)

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            _mark_warned("coder-1", "42", sessions_dir)

            assert _was_already_warned("coder-1", "42", sessions_dir)
            assert not _was_already_warned("coder-2", "42", sessions_dir)


# =============================================================================
# Concurrent access: marker file stress tests
# =============================================================================

class TestConcurrentMarkerAccess:
    """Stress tests for marker file idempotency under concurrent access."""

    def test_concurrent_writes_same_agent_same_task(self, tmp_path):
        """Multiple threads writing the same per-task marker should not error."""
        import concurrent.futures
        from teachback_check import _mark_warned, _was_already_warned

        sessions_dir = str(tmp_path)
        num_writers = 10

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_writers) as pool:
                futures = [
                    pool.submit(_mark_warned, "coder-1", "42", sessions_dir)
                    for _ in range(num_writers)
                ]
                # All should complete without exception
                for f in futures:
                    f.result()

            # Marker should exist after all concurrent writes
            assert _was_already_warned("coder-1", "42", sessions_dir)

    def test_concurrent_writes_different_agents(self, tmp_path):
        """Multiple agents writing per-task markers concurrently should not interfere."""
        import concurrent.futures
        from teachback_check import _mark_warned, _was_already_warned

        sessions_dir = str(tmp_path)
        agent_task_pairs = [(f"coder-{i}", str(i)) for i in range(10)]

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = [
                    pool.submit(_mark_warned, name, tid, sessions_dir)
                    for name, tid in agent_task_pairs
                ]
                for f in futures:
                    f.result()

            # Each agent-task pair should have its own marker
            for name, tid in agent_task_pairs:
                assert _was_already_warned(name, tid, sessions_dir)

    def test_concurrent_read_write(self, tmp_path):
        """Reading and writing per-task markers concurrently should not raise."""
        import concurrent.futures
        from teachback_check import _mark_warned, _was_already_warned

        sessions_dir = str(tmp_path)

        def write_marker():
            _mark_warned("coder-1", "42", sessions_dir)

        def read_marker():
            return _was_already_warned("coder-1", "42", sessions_dir)

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/projects/test"}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = []
                for _ in range(5):
                    futures.append(pool.submit(write_marker))
                    futures.append(pool.submit(read_marker))

                # All operations should complete without exception
                results = [f.result() for f in futures]

            # After all writes, marker should exist
            assert _was_already_warned("coder-1", "42", sessions_dir)


# =============================================================================
# Structural: hooks.json registration
# =============================================================================

class TestHooksJsonRegistration:
    """Verify teachback_check.py is correctly registered in hooks.json."""

    def test_teachback_check_registered_in_post_tool_use(self):
        """teachback_check.py should be in PostToolUse Edit|Write hooks."""
        hooks_path = Path(__file__).parent.parent / "hooks" / "hooks.json"
        hooks_data = json.loads(hooks_path.read_text(encoding="utf-8"))

        post_tool_use = hooks_data.get("hooks", {}).get("PostToolUse", [])

        # Find the Edit|Write matcher entry
        edit_write_entry = None
        for entry in post_tool_use:
            if entry.get("matcher") == "Edit|Write":
                edit_write_entry = entry
                break

        assert edit_write_entry is not None, "No Edit|Write matcher in PostToolUse"

        # Extract command strings from hooks list
        commands = [h.get("command", "") for h in edit_write_entry.get("hooks", [])]
        teachback_commands = [c for c in commands if "teachback_check.py" in c]

        assert len(teachback_commands) == 1, (
            f"Expected exactly 1 teachback_check.py registration, found {len(teachback_commands)}"
        )

    def test_teachback_check_after_track_files(self):
        """teachback_check.py should come after track_files.py in the hook chain."""
        hooks_path = Path(__file__).parent.parent / "hooks" / "hooks.json"
        hooks_data = json.loads(hooks_path.read_text(encoding="utf-8"))

        post_tool_use = hooks_data.get("hooks", {}).get("PostToolUse", [])
        edit_write_entry = next(
            (e for e in post_tool_use if e.get("matcher") == "Edit|Write"), None
        )
        assert edit_write_entry is not None

        commands = [h.get("command", "") for h in edit_write_entry.get("hooks", [])]

        track_idx = next(
            (i for i, c in enumerate(commands) if "track_files.py" in c), None
        )
        teachback_idx = next(
            (i for i, c in enumerate(commands) if "teachback_check.py" in c), None
        )

        assert track_idx is not None, "track_files.py not found in Edit|Write hooks"
        assert teachback_idx is not None, "teachback_check.py not found in Edit|Write hooks"
        assert teachback_idx > track_idx, (
            f"teachback_check.py (index {teachback_idx}) should come after "
            f"track_files.py (index {track_idx})"
        )

    def test_teachback_check_before_file_size_check(self):
        """teachback_check.py should come before file_size_check.py."""
        hooks_path = Path(__file__).parent.parent / "hooks" / "hooks.json"
        hooks_data = json.loads(hooks_path.read_text(encoding="utf-8"))

        post_tool_use = hooks_data.get("hooks", {}).get("PostToolUse", [])
        edit_write_entry = next(
            (e for e in post_tool_use if e.get("matcher") == "Edit|Write"), None
        )
        assert edit_write_entry is not None

        commands = [h.get("command", "") for h in edit_write_entry.get("hooks", [])]

        teachback_idx = next(
            (i for i, c in enumerate(commands) if "teachback_check.py" in c), None
        )
        size_idx = next(
            (i for i, c in enumerate(commands) if "file_size_check.py" in c), None
        )

        assert teachback_idx is not None, "teachback_check.py not found"
        assert size_idx is not None, "file_size_check.py not found"
        assert teachback_idx < size_idx, (
            f"teachback_check.py (index {teachback_idx}) should come before "
            f"file_size_check.py (index {size_idx})"
        )
