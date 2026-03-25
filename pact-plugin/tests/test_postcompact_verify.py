"""
Tests for hooks/postcompact_verify.py — PostCompact hook that verifies
compaction preserved critical context and writes the compact summary to disk.

Tests cover:
1. Compact summary file writing (path, permissions, content)
2. Gap detection (feature ID, phase, agent names)
3. Verification message composition
4. Subprocess integration (JSON output, exit code)
5. Fail-open on malformed input and errors
6. Outer exception handler (hook_error_json output on unexpected errors)
"""
import json
import os
import stat
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


HOOK_PATH = str(Path(__file__).parent.parent / "hooks" / "postcompact_verify.py")


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
# Helpers
# ---------------------------------------------------------------------------


def _create_task_file(task_dir: Path, task_id: str, data: dict) -> None:
    """Write a task JSON file."""
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task_id}.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _create_team_config(
    teams_dir: Path, team_name: str, members: list[dict], name: str | None = None
) -> None:
    """Write a team config.json."""
    team_dir = teams_dir / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    config = {"members": members}
    if name is not None:
        config["name"] = name
    (team_dir / "config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Unit tests: write_compact_summary
# ---------------------------------------------------------------------------


class TestWriteCompactSummary:
    """Test compact summary file writing."""

    def test_writes_file(self, tmp_path):
        from postcompact_verify import write_compact_summary
        result = write_compact_summary("Test summary", str(tmp_path))
        assert result is True
        path = tmp_path / "compact-summary.txt"
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "Test summary"

    def test_creates_parent_dirs(self, tmp_path):
        from postcompact_verify import write_compact_summary
        deep_dir = str(tmp_path / "a" / "b" / "c")
        result = write_compact_summary("content", deep_dir)
        assert result is True
        assert (Path(deep_dir) / "compact-summary.txt").exists()

    def test_secure_permissions(self, tmp_path):
        from postcompact_verify import write_compact_summary
        write_compact_summary("secure content", str(tmp_path))
        path = tmp_path / "compact-summary.txt"
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_overwrites_existing_file(self, tmp_path):
        from postcompact_verify import write_compact_summary
        write_compact_summary("first", str(tmp_path))
        write_compact_summary("second", str(tmp_path))
        path = tmp_path / "compact-summary.txt"
        assert path.read_text(encoding="utf-8") == "second"

    def test_returns_false_on_error(self, tmp_path):
        from postcompact_verify import write_compact_summary
        # Point at a file path where parent can't be created
        fake_file = tmp_path / "blocker"
        fake_file.write_text("x", encoding="utf-8")
        result = write_compact_summary("test", str(fake_file / "nested"))
        assert result is False

    def test_empty_summary_writes_empty_file(self, tmp_path):
        from postcompact_verify import write_compact_summary
        write_compact_summary("", str(tmp_path))
        path = tmp_path / "compact-summary.txt"
        assert path.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# Unit tests: check_summary_gaps
# ---------------------------------------------------------------------------


class TestCheckSummaryGaps:
    """Test gap detection in compact summaries."""

    def test_no_gaps_when_all_items_present(self):
        from postcompact_verify import check_summary_gaps
        summary = "Working on task #5 auth feature. Phase: CODE. Agent coder active."
        expected = {
            "feature_id": "5",
            "current_phase": "Phase: CODE",
            "agent_names": ["coder"],
        }
        gaps = check_summary_gaps(summary, expected)
        assert gaps == []

    def test_missing_feature_id(self):
        from postcompact_verify import check_summary_gaps
        summary = "Working on auth feature. Phase: CODE."
        expected = {"feature_id": "42", "current_phase": "Phase: CODE", "agent_names": []}
        gaps = check_summary_gaps(summary, expected)
        assert any("42" in g for g in gaps)

    def test_missing_phase(self):
        from postcompact_verify import check_summary_gaps
        summary = "Working on task #5. Agent coder active."
        expected = {"feature_id": "5", "current_phase": "Phase: TEST", "agent_names": ["coder"]}
        gaps = check_summary_gaps(summary, expected)
        assert any("phase" in g.lower() for g in gaps)

    def test_phase_name_only_match(self):
        from postcompact_verify import check_summary_gaps
        summary = "Currently in CODE phase with coder."
        expected = {
            "feature_id": None,
            "current_phase": "Phase: CODE",
            "agent_names": ["coder"],
        }
        gaps = check_summary_gaps(summary, expected)
        # "CODE" is in the summary even without "Phase: CODE" prefix
        assert not any("phase" in g.lower() for g in gaps)

    def test_missing_agent_names(self):
        from postcompact_verify import check_summary_gaps
        summary = "Task #5 in Phase: CODE."
        expected = {
            "feature_id": "5",
            "current_phase": "Phase: CODE",
            "agent_names": ["backend-coder", "test-engineer"],
        }
        gaps = check_summary_gaps(summary, expected)
        assert any("agent" in g.lower() for g in gaps)

    def test_partial_agent_match_no_gap(self):
        from postcompact_verify import check_summary_gaps
        summary = "Working with backend-coder on task."
        expected = {
            "feature_id": None,
            "current_phase": None,
            "agent_names": ["backend-coder", "test-engineer"],
        }
        gaps = check_summary_gaps(summary, expected)
        # At least one agent mentioned = no gap
        assert not any("agent" in g.lower() for g in gaps)

    def test_no_expected_items_no_gaps(self):
        from postcompact_verify import check_summary_gaps
        summary = "Some random summary."
        expected = {
            "feature_id": None,
            "current_phase": None,
            "agent_names": [],
        }
        gaps = check_summary_gaps(summary, expected)
        assert gaps == []

    def test_empty_summary_all_gaps(self):
        from postcompact_verify import check_summary_gaps
        expected = {
            "feature_id": "5",
            "current_phase": "Phase: CODE",
            "agent_names": ["coder"],
        }
        gaps = check_summary_gaps("", expected)
        assert len(gaps) == 3

    def test_case_insensitive_agent_match(self):
        from postcompact_verify import check_summary_gaps
        summary = "BACKEND-CODER is working."
        expected = {"feature_id": None, "current_phase": None, "agent_names": ["backend-coder"]}
        gaps = check_summary_gaps(summary, expected)
        assert not any("agent" in g.lower() for g in gaps)


# ---------------------------------------------------------------------------
# Unit tests: build_verification_message
# ---------------------------------------------------------------------------


class TestBuildVerificationMessage:
    """Test verification message composition."""

    def test_no_gaps_message(self, tmp_path):
        from postcompact_verify import build_verification_message
        result = build_verification_message(
            "Some summary",
            str(tmp_path / "no-tasks"),
            str(tmp_path / "no-teams"),
        )
        assert "preserved" in result.lower()

    def test_gaps_message_includes_items(self, tmp_path):
        from postcompact_verify import build_verification_message
        tasks_dir = tmp_path / "tasks"
        _create_task_file(tasks_dir / "pact-t", "42", {
            "id": "42",
            "status": "in_progress",
            "subject": "Build auth",
        })
        result = build_verification_message(
            "Some unrelated summary",
            str(tasks_dir),
            str(tmp_path / "no-teams"),
        )
        assert "missing" in result.lower()
        assert "42" in result
        assert "TaskList" in result


# ---------------------------------------------------------------------------
# Integration tests: subprocess
# ---------------------------------------------------------------------------


class TestPostcompactSubprocess:
    """Verify hook output via subprocess."""

    def test_emits_system_message(self):
        result = run_hook(json.dumps({"compact_summary": "Test summary"}))
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output

    def test_exits_zero_with_empty_summary(self):
        result = run_hook(json.dumps({"compact_summary": ""}))
        assert result.returncode == 0

    def test_exits_zero_with_no_summary_field(self):
        result = run_hook(json.dumps({"other_field": "data"}))
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Fail-open tests
# ---------------------------------------------------------------------------


class TestPostcompactFailOpen:
    """Verify fail-open behavior."""

    def test_empty_stdin_exits_zero(self):
        result = run_hook("")
        assert result.returncode == 0

    def test_malformed_json_exits_zero(self):
        result = run_hook("not json")
        assert result.returncode == 0

    def test_null_input_exits_zero(self):
        result = run_hook("null")
        assert result.returncode == 0

    def test_array_input_exits_zero(self):
        result = run_hook("[]")
        assert result.returncode == 0

    def test_malformed_json_still_emits_message(self):
        result = run_hook("not json")
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants."""

    def test_compact_summary_filename(self):
        from postcompact_verify import COMPACT_SUMMARY_FILENAME
        assert COMPACT_SUMMARY_FILENAME == "compact-summary.txt"


# ---------------------------------------------------------------------------
# Outer exception handler tests
# ---------------------------------------------------------------------------


class TestPostcompactOuterExceptionHandler:
    """Verify that main() catches unexpected exceptions, exits 0,
    emits hook_error_json on stdout and error info on stderr."""

    def test_exits_zero_on_unexpected_error(self):
        """main() must exit 0 even when build_verification_message raises."""
        from postcompact_verify import main

        stdin_data = json.dumps({"compact_summary": "test"})
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("postcompact_verify.build_verification_message",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stderr_contains_error_info(self, capsys):
        """Error details must appear on stderr for logging."""
        from postcompact_verify import main

        stdin_data = json.dumps({"compact_summary": "test"})
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("postcompact_verify.build_verification_message",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "postcompact_verify" in captured.err
        assert "test error" in captured.err

    def test_stdout_contains_hook_error_json(self, capsys):
        """Stdout must contain structured JSON from hook_error_json."""
        from postcompact_verify import main

        stdin_data = json.dumps({"compact_summary": "test"})
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("postcompact_verify.build_verification_message",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "systemMessage" in output
        assert "PACT hook warning" in output["systemMessage"]
        assert "postcompact_verify" in output["systemMessage"]
        assert "test error" in output["systemMessage"]
