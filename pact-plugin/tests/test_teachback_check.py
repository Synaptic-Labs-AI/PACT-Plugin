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
6. _get_marker_path() — session-scoped marker path construction
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
# Unit Tests: Marker file lifecycle
# =============================================================================

class TestMarkerPath:
    """Tests for _get_marker_path() — per-task marker format.

    When sessions_dir is provided (test injection), markers go directly into
    that directory. In production, markers use get_session_dir() to construct
    session-scoped paths: {slug}/{session_id}/teachback-warned-{agent}-{task_id}.
    """

    def test_builds_correct_path_with_task_id(self, tmp_path):
        from teachback_check import _get_marker_path

        result = _get_marker_path("backend-coder-1", "42", sessions_dir=str(tmp_path))

        assert result == tmp_path / "teachback-warned-backend-coder-1-42"

    def test_marker_name_format(self, tmp_path):
        from teachback_check import _get_marker_path

        result = _get_marker_path("test-engineer", "7", sessions_dir=str(tmp_path))

        assert result.name == "teachback-warned-test-engineer-7"

    def test_uses_session_dir_when_no_override(self):
        """Without sessions_dir override, uses get_session_dir() for session-scoped path."""
        from teachback_check import _get_marker_path

        with patch("teachback_check.get_session_dir",
                   return_value="/home/user/.claude/pact-sessions/my-app/abc-123"):
            result = _get_marker_path("agent-1", "1")

        expected = Path("/home/user/.claude/pact-sessions/my-app/abc-123/teachback-warned-agent-1-1")
        assert result == expected

    def test_fallback_when_no_session_dir(self):
        """When get_session_dir() returns '', falls back to pact-sessions root."""
        from teachback_check import _get_marker_path

        with patch("teachback_check.get_session_dir", return_value=""):
            result = _get_marker_path("agent-1", "1")

        assert result.name == "teachback-warned-agent-1-1"
        assert "pact-sessions" in str(result)

    def test_empty_task_id_omits_suffix(self, tmp_path):
        """Empty task_id should produce path without trailing dash or task_id.

        This is the fallback path used when check_teachback_sent returns
        (False, "") — i.e., the agent has no in_progress tasks at all.
        """
        from teachback_check import _get_marker_path

        result = _get_marker_path("backend-coder-1", "", sessions_dir=str(tmp_path))

        assert result == tmp_path / "teachback-warned-backend-coder-1"
        assert result.name == "teachback-warned-backend-coder-1"
        # No trailing dash — empty task_id should not append "-"
        assert not result.name.endswith("-")


class TestMarkWarned:
    """Tests for _mark_warned() and _was_already_warned() — per-task markers."""

    def test_mark_creates_file(self, tmp_path):
        from teachback_check import _mark_warned, _was_already_warned

        assert not _was_already_warned("coder-1", "42", sessions_dir=str(tmp_path))
        _mark_warned("coder-1", "42", sessions_dir=str(tmp_path))
        assert _was_already_warned("coder-1", "42", sessions_dir=str(tmp_path))

    def test_mark_creates_parent_directories(self, tmp_path):
        from teachback_check import _mark_warned

        sessions_dir = str(tmp_path / "nonexistent" / "deep" / "path")
        _mark_warned("coder-1", "42", sessions_dir=sessions_dir)

        # Verify the marker was created
        marker = Path(sessions_dir) / "teachback-warned-coder-1-42"
        assert marker.exists()

    def test_mark_uses_secure_permissions(self, tmp_path):
        from teachback_check import _mark_warned, _get_marker_path

        _mark_warned("coder-1", "42", sessions_dir=str(tmp_path))
        marker = _get_marker_path("coder-1", "42", sessions_dir=str(tmp_path))

        # Check file permissions are 0o600 (user read/write only)
        mode = marker.stat().st_mode & 0o777
        assert mode == 0o600

    def test_mark_idempotent(self, tmp_path):
        from teachback_check import _mark_warned, _was_already_warned

        _mark_warned("coder-1", "42", sessions_dir=str(tmp_path))
        _mark_warned("coder-1", "42", sessions_dir=str(tmp_path))  # Should not error
        assert _was_already_warned("coder-1", "42", sessions_dir=str(tmp_path))

    def test_different_agents_have_separate_markers(self, tmp_path):
        from teachback_check import _mark_warned, _was_already_warned

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
    using custom task scanning instead of shared/session_state.py.
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

    def test_scans_multiple_tasks_one_without_teachback_warns(self, tmp_path):
        """When one of two in_progress tasks lacks teachback, should warn.

        This is the core fix for bug #331: the old code short-circuited on ANY
        task with teachback_sent=True. The fixed code requires ALL in_progress
        tasks to have teachback_sent=True.
        """
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
        assert confirmed is False
        assert task_id == "1"

    def test_all_in_progress_tasks_have_teachback_confirms(self, tmp_path):
        """When ALL in_progress tasks have teachback_sent, should confirm."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        _write_task(task_dir, "2.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is True
        assert task_id == ""

    def test_agent_reuse_old_task_teachback_new_task_without_warns(self, tmp_path):
        """Agent reuse scenario: old task has teachback, new task does not.

        This is the primary bug vector for #331: when an agent is reused via
        SendMessage, the old task retains teachback_sent=True but the new task
        has not yet sent a teachback. The check must warn for the new task.
        """
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        # Old task (lower ID) — agent already sent teachback
        _write_task(task_dir, "10.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        # New task (higher ID) — no teachback yet
        _write_task(task_dir, "20.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        confirmed, task_id = check_teachback_sent("backend-coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "20"

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

    def test_suppress_output_when_agent_name_empty(self, capsys, pact_context):
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("teachback_check.resolve_agent_name", return_value=""), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_suppress_output_when_no_team_name(self, capsys, pact_context):
        from teachback_check import main

        # pact_context not called → get_team_name() returns ""

        with patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_suppress_output_when_team_name_empty(self, capsys, pact_context):
        from teachback_check import main

        pact_context(team_name="")

        with patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_suppress_output_on_invalid_json_stdin(self, capsys, pact_context):
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("sys.stdin", io.StringIO("not valid json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_emits_warning_when_should_warn_true(self, capsys, pact_context):
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
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

    def test_suppress_output_when_should_warn_false(self, capsys, pact_context):
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", return_value=(False, "")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {"suppressOutput": True}

    def test_team_name_lowercased(self, capsys, pact_context):
        """get_team_name() returns lowercased value for should_warn."""
        from teachback_check import main

        pact_context(team_name="PACT-TEST")

        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", return_value=(False, "")) as mock_warn:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_warn.assert_called_once_with("backend-coder-1", "pact-test")

    def test_always_exits_0(self, capsys, pact_context):
        """Hook should always exit 0 — it's a warning layer, not a gate."""
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
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

    def test_fail_open_on_unexpected_error(self, capsys, pact_context):
        """Any unhandled exception should produce hook_error_json and exit 0."""
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
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

    def test_error_and_suppress_mutually_exclusive(self, capsys, pact_context):
        """Error path should emit systemMessage, NOT suppressOutput."""
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
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

    def test_no_agent_name_emits_suppress(self, capsys, pact_context):
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("teachback_check.resolve_agent_name", return_value=""), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out.strip())
        assert output.get("suppressOutput") is True

    def test_no_team_name_emits_suppress(self, capsys, pact_context):
        from teachback_check import main

        # pact_context not called → get_team_name() returns ""

        with patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out.strip())
        assert output.get("suppressOutput") is True

    def test_invalid_json_emits_suppress(self, capsys, pact_context):
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out.strip())
        assert output.get("suppressOutput") is True

    def test_should_warn_false_emits_suppress(self, capsys, pact_context):
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("teachback_check.resolve_agent_name", return_value="coder-1"), \
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

        warn, task_id = should_warn(
            "backend-coder-1", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=sessions_dir,
        )
        assert warn is False
        assert task_id == ""

    def test_agent_reuse_warns_for_new_task(self, tmp_path):
        """Full agent reuse scenario: old task has teachback, new task added
        without teachback → should_warn returns True for the new task.

        This is the end-to-end test for bug #331. The agent was previously
        working on task 10 (teachback sent), then reassigned to task 20
        (no teachback yet). Both tasks are still in_progress.
        """
        from teachback_check import should_warn

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")

        # Old task — agent already sent teachback for this one
        _write_task(task_dir, "10.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        # New task — no teachback yet
        _write_task(task_dir, "20.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        warn, task_id = should_warn(
            "backend-coder-1", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=sessions_dir,
        )
        assert warn is True
        assert task_id == "20"

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

        _mark_warned("coder-1", "10", sessions_dir)

        assert _was_already_warned("coder-1", "10", sessions_dir)
        assert not _was_already_warned("coder-1", "20", sessions_dir)

    def test_different_tasks_have_independent_markers(self, tmp_path):
        """Each task gets its own marker file, even for the same agent."""
        from teachback_check import _mark_warned, _get_marker_path

        sessions_dir = str(tmp_path)

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

        path = _get_marker_path("coder-1", "42", sessions_dir=str(tmp_path))

        assert path.name == "teachback-warned-coder-1-42"

    def test_different_agents_same_task_have_separate_markers(self, tmp_path):
        """Different agents on the same task get different markers."""
        from teachback_check import _mark_warned, _was_already_warned

        sessions_dir = str(tmp_path)

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
# Edge case tests for bug #331 fix (ANY→ALL semantics)
# =============================================================================

class TestAllMatchEdgeCases:
    """Tests targeting edge cases of the ALL-match semantics introduced by the
    bug #331 fix. These complement the coder's tests by exercising:
    - Three or more concurrent tasks
    - Race condition simulation (old task disappears mid-lifecycle)
    - Reviewer-to-fixer reuse scenario
    - Empty task_id propagation through should_warn
    - Complex multi-agent mixed ownership
    """

    def test_three_tasks_one_missing_teachback_warns(self, tmp_path):
        """Three in_progress tasks, two with teachback, one without → warns.

        Validates ALL-match semantics generalize beyond 2 tasks. The scanner
        must check every in_progress task, not just the first two.
        """
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "1.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        _write_task(task_dir, "2.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        _write_task(task_dir, "3.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        confirmed, task_id = check_teachback_sent("coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "3"

    def test_multiple_unconfirmed_returns_first_sorted(self, tmp_path):
        """When 2+ in_progress tasks both lack teachback_sent, should return
        the first unconfirmed task_id in sorted order.

        Exercises the branch at line 183 where first_unconfirmed_task_id is
        already set from a prior iteration — the second unconfirmed task is
        encountered but does not overwrite the first.
        """
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        _write_task(task_dir, "5.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},
        })
        _write_task(task_dir, "15.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},
        })
        _write_task(task_dir, "25.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        confirmed, task_id = check_teachback_sent("coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        # sorted() ensures deterministic order: 15.json, 25.json, 5.json
        # (lexicographic: "15" < "25" < "5"), so first unconfirmed is "15"
        assert task_id == "15"

    def test_three_tasks_all_confirmed_passes(self, tmp_path):
        """Three in_progress tasks, all with teachback → confirmed."""
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        for i in range(1, 4):
            _write_task(task_dir, f"{i}.json", {
                "owner": "coder-1",
                "status": "in_progress",
                "metadata": {"teachback_sent": True},
            })

        confirmed, task_id = check_teachback_sent("coder-1", "pact-test", str(tmp_path))
        assert confirmed is True
        assert task_id == ""

    def test_race_condition_old_task_completed_before_scan(self, tmp_path):
        """Simulates edge case 5: old task transitions to completed, leaving
        only the new task (without teachback) visible to the scanner.

        This is the benign race condition from the prep doc. The hook reads a
        point-in-time snapshot, so if the lead marks the old task completed
        before the scan reaches it, only the new task is visible.
        """
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        # Old task already completed by the time the hook runs
        _write_task(task_dir, "10.json", {
            "owner": "coder-1",
            "status": "completed",
            "metadata": {"teachback_sent": True},
        })
        # New task — no teachback yet
        _write_task(task_dir, "20.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        confirmed, task_id = check_teachback_sent("coder-1", "pact-test", str(tmp_path))
        assert confirmed is False
        assert task_id == "20"

    def test_reviewer_to_fixer_reuse_scenario(self, tmp_path):
        """Reviewer-to-fixer reuse: agent's review task (with teachback) is
        still in_progress when a fix task is assigned via SendMessage.

        Per CLAUDE.md line 462, the orchestrator reuses reviewers as fixers.
        The review task has teachback_sent=True; the fix task does not.
        The fix must enforce teachback on the new task.
        """
        from teachback_check import should_warn

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")

        # Review task — teachback sent during review phase
        _write_task(task_dir, "15.json", {
            "owner": "architect",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
            "subject": "Review PR #42",
        })
        # Fix task — newly assigned, no teachback yet
        _write_task(task_dir, "25.json", {
            "owner": "architect",
            "status": "in_progress",
            "metadata": {},
            "subject": "Fix review findings from PR #42",
        })

        warn, task_id = should_warn(
            "architect", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=sessions_dir,
        )
        assert warn is True
        assert task_id == "25"

    def test_self_claim_followup_both_in_progress(self, tmp_path):
        """Self-claim follow-up: agent claims task B before lead marks task A
        completed. Both are in_progress. Task A has teachback; task B does not.

        Per prep doc scenario 1, there's a timing window where both tasks
        coexist as in_progress.
        """
        from teachback_check import should_warn

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")

        # Task A — agent already sent teachback, about to be completed
        _write_task(task_dir, "5.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        # Task B — self-claimed, no teachback yet
        _write_task(task_dir, "6.json", {
            "owner": "backend-coder-1",
            "status": "in_progress",
            "metadata": {},
        })

        warn, task_id = should_warn(
            "backend-coder-1", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=sessions_dir,
        )
        assert warn is True
        assert task_id == "6"

    def test_no_in_progress_tasks_should_warn_with_empty_task_id(self, tmp_path):
        """When check_teachback_sent returns (False, ""), should_warn proceeds
        to the marker check with empty task_id. Verify no crash and that the
        marker path with empty task_id doesn't cause issues.
        """
        from teachback_check import should_warn

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")
        # Only completed tasks — no in_progress for this agent
        _write_task(task_dir, "1.json", {
            "owner": "backend-coder-1",
            "status": "completed",
            "metadata": {"teachback_sent": True},
        })

        warn, task_id = should_warn(
            "backend-coder-1", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=sessions_dir,
        )
        # check_teachback_sent returns (False, "") for no in_progress tasks
        # should_warn checks confirmed=False, then checks marker for empty task_id
        # No marker exists, so it should warn with empty task_id
        assert warn is True
        assert task_id == ""

    def test_complex_multi_agent_mixed_ownership(self, tmp_path):
        """Multiple agents with interleaved tasks in the same directory.

        Verifies that the scanner correctly filters by owner and only
        evaluates the target agent's in_progress tasks.
        """
        from teachback_check import check_teachback_sent

        task_dir = tmp_path / "pact-test"
        # Agent A's tasks
        _write_task(task_dir, "1.json", {
            "owner": "coder-A",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        _write_task(task_dir, "4.json", {
            "owner": "coder-A",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        # Agent B's tasks — one without teachback
        _write_task(task_dir, "2.json", {
            "owner": "coder-B",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        _write_task(task_dir, "5.json", {
            "owner": "coder-B",
            "status": "in_progress",
            "metadata": {},
        })
        # Agent C — completed task (should not affect anyone)
        _write_task(task_dir, "3.json", {
            "owner": "coder-C",
            "status": "completed",
            "metadata": {"teachback_sent": True},
        })

        # Agent A: all confirmed
        confirmed_a, tid_a = check_teachback_sent("coder-A", "pact-test", str(tmp_path))
        assert confirmed_a is True
        assert tid_a == ""

        # Agent B: one task missing teachback
        confirmed_b, tid_b = check_teachback_sent("coder-B", "pact-test", str(tmp_path))
        assert confirmed_b is False
        assert tid_b == "5"

        # Agent C: no in_progress tasks
        confirmed_c, tid_c = check_teachback_sent("coder-C", "pact-test", str(tmp_path))
        assert confirmed_c is False
        assert tid_c == ""

    def test_agent_reuse_full_lifecycle_e2e(self, tmp_path):
        """End-to-end lifecycle test for the complete agent reuse scenario:

        1. Agent starts task A → warned (no teachback)
        2. Agent sends teachback for task A → no longer warned
        3. Agent is reused for task B (both in_progress) → warned for task B
        4. Agent sends teachback for task B → no longer warned
        """
        from teachback_check import should_warn, _mark_warned

        task_dir = tmp_path / "tasks" / "pact-test"
        sessions_dir = str(tmp_path / "sessions")

        # Step 1: Agent starts task A — no teachback
        _write_task(task_dir, "10.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},
        })
        warn, tid = should_warn(
            "coder-1", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=sessions_dir,
        )
        assert warn is True
        assert tid == "10"
        _mark_warned("coder-1", tid, sessions_dir=sessions_dir)

        # Step 2: Agent sends teachback (metadata updated)
        _write_task(task_dir, "10.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        warn, tid = should_warn(
            "coder-1", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=sessions_dir,
        )
        assert warn is False

        # Step 3: Agent reused — new task B added, both in_progress
        _write_task(task_dir, "20.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},
        })
        warn, tid = should_warn(
            "coder-1", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=sessions_dir,
        )
        assert warn is True
        assert tid == "20"
        _mark_warned("coder-1", tid, sessions_dir=sessions_dir)

        # Step 4: Agent sends teachback for task B
        _write_task(task_dir, "20.json", {
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {"teachback_sent": True},
        })
        warn, tid = should_warn(
            "coder-1", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=sessions_dir,
        )
        assert warn is False


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

    def test_teachback_check_registered_in_bash_matcher(self):
        """teachback_check.py should also be registered under PostToolUse Bash.

        The hook fires on Edit|Write AND Bash to catch agents using shell
        commands for implementation work before sending a teachback.
        """
        hooks_path = Path(__file__).parent.parent / "hooks" / "hooks.json"
        hooks_data = json.loads(hooks_path.read_text(encoding="utf-8"))

        post_tool_use = hooks_data.get("hooks", {}).get("PostToolUse", [])

        bash_entry = next(
            (e for e in post_tool_use if e.get("matcher") == "Bash"), None
        )
        assert bash_entry is not None, "No Bash matcher in PostToolUse"

        commands = [h.get("command", "") for h in bash_entry.get("hooks", [])]
        teachback_commands = [c for c in commands if "teachback_check.py" in c]

        assert len(teachback_commands) == 1, (
            f"Expected exactly 1 teachback_check.py in Bash hooks, "
            f"found {len(teachback_commands)}"
        )


# =============================================================================
# Parallel Session Marker Isolation — CORE FIX (Test Engineer)
# =============================================================================

class TestParallelSessionMarkerIsolation:
    """Verify that two concurrent sessions produce markers in isolated directories.

    This is the primary behavioral change of issue #345 — the whole reason
    for session-scoping. Before this fix, two sessions on the same project
    would share the same marker directory, causing one session's "already warned"
    state to suppress warnings in the other session.

    These tests exercise the full marker lifecycle through _get_marker_path(),
    _mark_warned(), and _was_already_warned() with different session contexts.
    """

    def test_markers_in_different_session_dirs_dont_interfere(self, tmp_path):
        """Markers in session A should not affect marker checks in session B.

        This is the CORE fix: previously, marking agent warned in session A
        would suppress the warning in session B because they shared a directory.
        """
        from teachback_check import _mark_warned, _was_already_warned

        session_a_dir = tmp_path / "session-a"
        session_b_dir = tmp_path / "session-b"
        session_a_dir.mkdir()
        session_b_dir.mkdir()

        # Mark warned in session A
        _mark_warned("backend-coder", "42", sessions_dir=str(session_a_dir))

        # Session A should be warned
        assert _was_already_warned("backend-coder", "42", sessions_dir=str(session_a_dir))
        # Session B should NOT be warned — different directory
        assert not _was_already_warned("backend-coder", "42", sessions_dir=str(session_b_dir))

    def test_same_agent_different_sessions_get_independent_markers(self, tmp_path):
        """Same agent name in two sessions should get separate marker files."""
        from teachback_check import _get_marker_path

        session_a = tmp_path / "session-aaa"
        session_b = tmp_path / "session-bbb"

        path_a = _get_marker_path("coder-1", "10", sessions_dir=str(session_a))
        path_b = _get_marker_path("coder-1", "10", sessions_dir=str(session_b))

        assert path_a != path_b
        assert path_a.parent == session_a
        assert path_b.parent == session_b
        # Same filename
        assert path_a.name == path_b.name

    def test_should_warn_uses_session_scoped_markers(self, tmp_path):
        """should_warn() should respect session-scoped markers.

        When called with sessions_dir pointing to session A's directory,
        a marker created for session B should have no effect.
        """
        from teachback_check import should_warn, _mark_warned

        task_dir = tmp_path / "tasks" / "test-team"
        task_dir.mkdir(parents=True)
        (task_dir / "1.json").write_text(json.dumps({
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},
        }))

        session_a = tmp_path / "session-a"
        session_b = tmp_path / "session-b"
        session_a.mkdir()
        session_b.mkdir()

        # Mark warned in session A
        _mark_warned("coder-1", "1", sessions_dir=str(session_a))

        # Session A: should NOT warn (already warned)
        warn_a, _ = should_warn(
            "coder-1", "test-team",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=str(session_a),
        )
        assert not warn_a

        # Session B: SHOULD warn (independent session)
        warn_b, task_id = should_warn(
            "coder-1", "test-team",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=str(session_b),
        )
        assert warn_b
        assert task_id == "1"

    def test_end_to_end_two_sessions_full_lifecycle(self, tmp_path):
        """Full E2E: Two sessions, same project, same agent — markers isolated.

        Simulates the real scenario:
        1. Session A starts, agent writes code, gets warned, marker created
        2. Session B starts simultaneously, same agent writes code
        3. Session B should get its own warning (not suppressed by session A)
        4. Session B gets warned, its marker is created
        5. Both sessions now have their own markers
        """
        from teachback_check import should_warn, _mark_warned, _was_already_warned

        # Shared task directory (both sessions see the same tasks)
        task_dir = tmp_path / "tasks" / "pact-test"
        task_dir.mkdir(parents=True)
        (task_dir / "10.json").write_text(json.dumps({
            "owner": "backend-coder",
            "status": "in_progress",
            "metadata": {},
        }))

        session_a = tmp_path / "sessions" / "my-proj" / "session-aaa"
        session_b = tmp_path / "sessions" / "my-proj" / "session-bbb"

        # Step 1: Session A — agent hasn't sent teachback
        warn_a, tid_a = should_warn(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=str(session_a),
        )
        assert warn_a
        assert tid_a == "10"

        # Step 2: Session A — mark warned
        _mark_warned("backend-coder", "10", sessions_dir=str(session_a))
        assert _was_already_warned("backend-coder", "10", sessions_dir=str(session_a))

        # Step 3: Session B — should still warn (independent)
        warn_b, tid_b = should_warn(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / "tasks"),
            sessions_dir=str(session_b),
        )
        assert warn_b
        assert tid_b == "10"

        # Step 4: Session B — mark warned
        _mark_warned("backend-coder", "10", sessions_dir=str(session_b))
        assert _was_already_warned("backend-coder", "10", sessions_dir=str(session_b))

        # Step 5: Verify full isolation
        assert _was_already_warned("backend-coder", "10", sessions_dir=str(session_a))
        assert _was_already_warned("backend-coder", "10", sessions_dir=str(session_b))
        # Each directory has exactly one marker
        assert len(list(session_a.glob("teachback-warned-*"))) == 1
        assert len(list(session_b.glob("teachback-warned-*"))) == 1


# =============================================================================
# _get_marker_path() — Session Dir Integration (Test Engineer)
# =============================================================================

class TestMarkerPathSessionDirIntegration:
    """Tests for _get_marker_path() using get_session_dir() (no override).

    These complement TestMarkerPath which uses sessions_dir override. Here
    we test the production path where get_session_dir() is called.
    """

    def test_production_path_includes_slug_and_session_id(self):
        """Without sessions_dir, marker path should include slug/session_id."""
        from teachback_check import _get_marker_path

        mock_dir = "/home/user/.claude/pact-sessions/my-project/abc-123-def"
        with patch("teachback_check.get_session_dir", return_value=mock_dir):
            result = _get_marker_path("coder-1", "42")

        assert str(result) == f"{mock_dir}/teachback-warned-coder-1-42"

    def test_production_path_empty_session_dir_falls_back(self):
        """When get_session_dir() returns '', falls back to pact-sessions root."""
        from teachback_check import _get_marker_path

        with patch("teachback_check.get_session_dir", return_value=""):
            result = _get_marker_path("coder-1", "42")

        # Fallback path: ~/.claude/pact-sessions/teachback-warned-coder-1-42
        assert "pact-sessions" in str(result)
        assert result.name == "teachback-warned-coder-1-42"
        # Should NOT be under a slug/session_id subdirectory
        assert result.parent.name == "pact-sessions"

    def test_sessions_dir_override_takes_precedence(self, tmp_path):
        """When sessions_dir is provided, get_session_dir() should not be called."""
        from teachback_check import _get_marker_path

        with patch("teachback_check.get_session_dir") as mock_get:
            result = _get_marker_path("coder-1", "42", sessions_dir=str(tmp_path))

        # get_session_dir should not have been called
        mock_get.assert_not_called()
        assert result.parent == tmp_path


# =============================================================================
# Legacy advisory emission (#401 B1 remediation)
# =============================================================================

class TestLegacyAdvisoryEmission:
    """Tests for _emit_legacy_advisory and its integration with main().

    Closes the B1 shipping gap from task #17 architectural review: the
    legacy missing_teachback_sent warning path must emit a
    teachback_gate_advisory journal event so Phase 2 readiness diagnostic
    (scripts/check_teachback_phase2_readiness.py) can distinguish
    legacy-advisory false positives from new teachback_gate reason codes.

    Per COMPONENT-DESIGN.md §Hook 5, JOURNAL-EVENTS.md §Writer site audit
    line 341, RISK-MAP.md §Risk #5.
    """

    def test_emit_legacy_advisory_calls_append_event(self):
        """Happy path — emit calls append_event with correct schema shape."""
        from teachback_check import _emit_legacy_advisory

        with patch("teachback_check.append_event") as mock_append, \
             patch("teachback_check.make_event") as mock_make:
            mock_make.return_value = {"dummy": "event"}

            _emit_legacy_advisory(
                task_id="42",
                agent_name="backend-coder-1",
                tool_name="Edit",
            )

        mock_make.assert_called_once_with(
            "teachback_gate_advisory",
            task_id="42",
            agent="backend-coder-1",
            would_have_blocked=True,
            reason="missing_teachback_sent",
            tool_name="Edit",
        )
        mock_append.assert_called_once_with({"dummy": "event"})

    def test_emit_legacy_advisory_fail_open_on_append_error(self):
        """Journal errors must NOT raise — fail-open SACROSANCT."""
        from teachback_check import _emit_legacy_advisory

        with patch(
            "teachback_check.append_event",
            side_effect=OSError("disk full"),
        ):
            # Should not raise
            _emit_legacy_advisory(
                task_id="42",
                agent_name="coder-1",
                tool_name="Write",
            )

    def test_emit_legacy_advisory_fail_open_on_make_event_error(self):
        """make_event errors must NOT raise either."""
        from teachback_check import _emit_legacy_advisory

        with patch(
            "teachback_check.make_event",
            side_effect=ValueError("bad schema"),
        ):
            _emit_legacy_advisory(
                task_id="42",
                agent_name="coder-1",
                tool_name="Bash",
            )

    def test_main_emits_advisory_on_warn_path(self, capsys, pact_context):
        """Integration: main() emits advisory when should_warn returns True.

        This is the counter-test-by-revert anchor: if _emit_legacy_advisory
        is removed from main()'s warn branch, this test fails.
        """
        from teachback_check import main

        pact_context(team_name="pact-test")

        stdin_payload = json.dumps({"tool_name": "Edit"})
        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
             patch("sys.stdin", io.StringIO(stdin_payload)), \
             patch("teachback_check.should_warn", return_value=(True, "42")), \
             patch("teachback_check._mark_warned"), \
             patch("teachback_check.append_event") as mock_append, \
             patch("teachback_check.make_event", side_effect=lambda *a, **k: {"args": a, "kwargs": k}):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_append.assert_called_once()
        # Inspect the event shape fed through make_event
        event = mock_append.call_args[0][0]
        assert event["args"] == ("teachback_gate_advisory",)
        assert event["kwargs"]["task_id"] == "42"
        assert event["kwargs"]["agent"] == "backend-coder-1"
        assert event["kwargs"]["would_have_blocked"] is True
        assert event["kwargs"]["reason"] == "missing_teachback_sent"
        assert event["kwargs"]["tool_name"] == "Edit"

    def test_main_does_not_emit_when_should_warn_false(self, capsys, pact_context):
        """Negative: no emission when there is no warning to advise about."""
        from teachback_check import main

        pact_context(team_name="pact-test")

        with patch("teachback_check.resolve_agent_name", return_value="coder-1"), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("teachback_check.should_warn", return_value=(False, "")), \
             patch("teachback_check.append_event") as mock_append:
            with pytest.raises(SystemExit):
                main()

        mock_append.assert_not_called()

    def test_main_tool_name_defaults_to_empty_string(self, capsys, pact_context):
        """Non-string or missing tool_name in stdin must not raise."""
        from teachback_check import main

        pact_context(team_name="pact-test")

        stdin_payload = json.dumps({"tool_name": None})
        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
             patch("sys.stdin", io.StringIO(stdin_payload)), \
             patch("teachback_check.should_warn", return_value=(True, "42")), \
             patch("teachback_check._mark_warned"), \
             patch("teachback_check.append_event") as mock_append, \
             patch("teachback_check.make_event", side_effect=lambda *a, **k: {"kwargs": k}):
            with pytest.raises(SystemExit):
                main()

        mock_append.assert_called_once()
        event = mock_append.call_args[0][0]
        assert event["kwargs"]["tool_name"] == ""

    def test_main_journal_error_does_not_block_warning(self, capsys, pact_context):
        """Fail-open at main() level — journal error must not suppress the warning."""
        from teachback_check import main

        pact_context(team_name="pact-test")

        stdin_payload = json.dumps({"tool_name": "Edit"})
        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
             patch("sys.stdin", io.StringIO(stdin_payload)), \
             patch("teachback_check.should_warn", return_value=(True, "42")), \
             patch("teachback_check._mark_warned"), \
             patch("teachback_check.append_event", side_effect=OSError("disk full")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # The systemMessage still emits even though the journal append failed
        output = json.loads(capsys.readouterr().out.strip())
        assert "systemMessage" in output
        assert "TEACHBACK REMINDER" in output["systemMessage"]

    def test_legacy_emit_gated_on_advisory_mode(
        self, capsys, pact_context, monkeypatch,
    ):
        """C12 (round 3): legacy advisory emit must fire ONLY when
        teachback_check._TEACHBACK_MODE == TEACHBACK_MODE_ADVISORY.

        Mirrors teachback_gate.py:578 symmetry. Post-Phase-2 flip,
        teachback_gate stops emitting advisory events; the readiness
        diagnostic must observe a consistent single-mode stream. If the
        legacy path keeps emitting after the flip, it poisons the
        diagnostic with stale advisory events while blocked events
        accumulate alongside.

        Counter-test-by-revert: if the mode guard is removed from
        main()'s warn branch, this test fails (the append_event call
        fires even in blocking mode).
        """
        import teachback_check
        from shared import TEACHBACK_MODE_BLOCKING
        from teachback_check import main

        pact_context(team_name="pact-test")
        # Flip the mode to blocking — legacy emit must suppress.
        monkeypatch.setattr(teachback_check, "_TEACHBACK_MODE", TEACHBACK_MODE_BLOCKING)

        stdin_payload = json.dumps({"tool_name": "Edit"})
        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
             patch("sys.stdin", io.StringIO(stdin_payload)), \
             patch("teachback_check.should_warn", return_value=(True, "42")), \
             patch("teachback_check._mark_warned"), \
             patch("teachback_check.append_event") as mock_append:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Blocking mode: legacy emit MUST NOT fire.
        mock_append.assert_not_called()
        # The systemMessage warning still emits (mode gate only affects
        # observability, not the user-facing reminder).
        output = json.loads(capsys.readouterr().out.strip())
        assert "systemMessage" in output
        assert "TEACHBACK REMINDER" in output["systemMessage"]

    def test_legacy_emit_fires_in_advisory_mode(
        self, capsys, pact_context, monkeypatch,
    ):
        """C12 positive case — advisory mode keeps the legacy emit live.

        Confirms the mode guard does not over-block: when
        _TEACHBACK_MODE == TEACHBACK_MODE_ADVISORY (the default), the
        legacy advisory event fires as before. Paired with
        test_legacy_emit_gated_on_advisory_mode, this is the
        bi-directional symmetry check — absence of this test would let
        a bug that ALWAYS suppresses the emit slip through.
        """
        import teachback_check
        from shared import TEACHBACK_MODE_ADVISORY
        from teachback_check import main

        pact_context(team_name="pact-test")
        monkeypatch.setattr(teachback_check, "_TEACHBACK_MODE", TEACHBACK_MODE_ADVISORY)

        stdin_payload = json.dumps({"tool_name": "Edit"})
        with patch("teachback_check.resolve_agent_name", return_value="backend-coder-1"), \
             patch("sys.stdin", io.StringIO(stdin_payload)), \
             patch("teachback_check.should_warn", return_value=(True, "42")), \
             patch("teachback_check._mark_warned"), \
             patch("teachback_check.append_event") as mock_append, \
             patch("teachback_check.make_event", side_effect=lambda *a, **k: {"args": a, "kwargs": k}):
            with pytest.raises(SystemExit):
                main()

        # Advisory mode: legacy emit DOES fire.
        mock_append.assert_called_once()


class TestCheckTeachbackSentPathSanitization:
    """Cycle 8 round7-security C: check_teachback_sent must reject any
    team_name that isn't a positive-regex path component before joining
    it into the tasks_base_dir path. Mirrors the sibling guard in
    shared.teachback_scan.scan_teachback_state (PR #426 pattern).

    Fail-open contract: unsafe team_name returns (True, "") so the
    gate allows. This matches the existing early-return semantics
    for missing agent_name / team_name (see line 166-167 pre-guard).

    Counter-test-by-revert: removing the is_safe_path_component guard
    causes test_unsafe_team_name_with_escape to fail — the scanner
    would descend into the escape target and find the crafted task
    file, returning (False, "99") instead of (True, "").
    """

    def test_unsafe_team_name_with_escape_returns_fail_open(self, tmp_path):
        from teachback_check import check_teachback_sent

        # Craft a real adversarial scenario: sibling dir of tasks_base_dir
        # with a task file that, if discovered, would return
        # (False, "99") because metadata.teachback_sent is absent.
        inner = tmp_path / "inner"
        inner.mkdir()
        outside = tmp_path / "outside_target"
        outside.mkdir()
        (outside / "99.json").write_text(json.dumps({
            "owner": "coder-1",
            "status": "in_progress",
            "metadata": {},  # no teachback_sent → would yield (False, "99")
        }), encoding="utf-8")

        confirmed, task_id = check_teachback_sent(
            "coder-1",
            "../outside_target",  # unsafe — contains "/" and ".."
            tasks_base_dir=str(inner),
        )
        # With guard: early fail-open, no descent into escape target.
        # Without guard (revert): would return (False, "99").
        assert confirmed is True, (
            "Cycle 8 round7-security C flip: unsafe team_name must "
            "short-circuit BEFORE Path() join descends into the escape "
            "target. Reverting the is_safe_path_component guard would "
            "let the scanner read 99.json and return (False, '99')."
        )
        assert task_id == ""

    def test_unsafe_team_name_with_null_byte_returns_fail_open(self, tmp_path):
        from teachback_check import check_teachback_sent

        confirmed, task_id = check_teachback_sent(
            "coder-1",
            "team\x00injected",
            tasks_base_dir=str(tmp_path),
        )
        assert confirmed is True
        assert task_id == ""

    def test_safe_team_name_proceeds_past_guard(self, tmp_path):
        # Counter-test in the positive direction: a legitimate team_name
        # does NOT short-circuit at the path guard — the scanner proceeds
        # to the task_dir.exists() check (dir doesn't exist here, so
        # still (True, "") but via the next-in-line early-return path).
        from teachback_check import check_teachback_sent

        confirmed, task_id = check_teachback_sent(
            "coder-1",
            "pact-test",
            tasks_base_dir=str(tmp_path),
        )
        assert confirmed is True
        assert task_id == ""
