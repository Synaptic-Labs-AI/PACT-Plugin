"""
Tests for s2_conflict_check.py — PreToolUse hook matching Task that checks if
a newly dispatched agent's scope overlaps with existing agent boundaries.

Tests cover:
1. Overlapping scopes → warning message
2. Disjoint scopes → no warning
3. No state file → no-op (graceful degradation)
4. Same agent re-dispatch → no self-conflict
5. Malformed state → graceful fail
6. main() stdin/stdout contract (hook I/O format)
7. Exception handler (fail-open)
8. suppressOutput bare exits (invalid JSON, no agent name)
9. Agent name extraction (name vs subagent_type)
10. _discover_worktree_path subprocess handling
"""
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def worktree(tmp_path):
    """Create a worktree directory with .pact/ subdirectory."""
    pact_dir = tmp_path / ".pact"
    pact_dir.mkdir()
    return tmp_path


@pytest.fixture
def state_file(worktree):
    """Return the path to s2-state.json within the worktree."""
    return worktree / ".pact" / "s2-state.json"


def _write_state(state_file, state_dict):
    """Helper to write a state dict to the state file."""
    state_file.write_text(json.dumps(state_dict))


def _make_state(boundaries=None, **kwargs):
    """Factory for generating s2-state.json content."""
    return {
        "version": 1,
        "session_team": "pact-test",
        "worktree": "/test/worktree",
        "created_at": "2026-04-01T00:00:00+00:00",
        "last_updated": "2026-04-01T00:00:00+00:00",
        "created_by": "orchestrate",
        "boundaries": boundaries or {},
        "conventions": [],
        "scope_claims": {},
        "drift_alerts": [],
        **kwargs,
    }


def _two_agent_overlapping():
    return {
        "backend-coder": {
            "owns": ["src/server/", "src/shared/"],
            "reads": ["src/types/"],
        },
        "frontend-coder": {
            "owns": ["src/client/", "src/shared/"],
            "reads": ["src/types/"],
        },
    }


def _two_agent_disjoint():
    return {
        "backend-coder": {
            "owns": ["src/server/", "src/api/"],
            "reads": ["src/types/"],
        },
        "frontend-coder": {
            "owns": ["src/client/", "src/ui/"],
            "reads": ["src/types/"],
        },
    }


# =============================================================================
# check_scope_overlap() — Core Logic
# =============================================================================


class TestCheckScopeOverlap:
    """Tests for the check_scope_overlap function."""

    def test_overlapping_scopes_returns_warning(self, worktree, state_file):
        from s2_conflict_check import check_scope_overlap

        _write_state(state_file, _make_state(boundaries=_two_agent_overlapping()))

        tool_input = {"name": "backend-coder", "prompt": "Do work"}
        result = check_scope_overlap(tool_input, worktree_path=str(worktree))

        assert result is not None
        assert "S2 Conflict Warning" in result
        assert "backend-coder" in result
        assert "frontend-coder" in result
        assert "src/shared/" in result

    def test_disjoint_scopes_returns_none(self, worktree, state_file):
        from s2_conflict_check import check_scope_overlap

        _write_state(state_file, _make_state(boundaries=_two_agent_disjoint()))

        tool_input = {"name": "backend-coder", "prompt": "Do work"}
        result = check_scope_overlap(tool_input, worktree_path=str(worktree))

        assert result is None

    def test_no_state_file_returns_none(self, worktree):
        from s2_conflict_check import check_scope_overlap

        tool_input = {"name": "backend-coder", "prompt": "Do work"}
        result = check_scope_overlap(tool_input, worktree_path=str(worktree))

        assert result is None

    def test_no_boundaries_returns_none(self, worktree, state_file):
        from s2_conflict_check import check_scope_overlap

        _write_state(state_file, _make_state(boundaries={}))

        tool_input = {"name": "backend-coder", "prompt": "Do work"}
        result = check_scope_overlap(tool_input, worktree_path=str(worktree))

        assert result is None

    def test_agent_not_in_boundaries_returns_none(self, worktree, state_file):
        """An agent being dispatched that has no boundary entry shouldn't warn."""
        from s2_conflict_check import check_scope_overlap

        _write_state(state_file, _make_state(boundaries=_two_agent_disjoint()))

        tool_input = {"name": "new-agent", "prompt": "Do work"}
        result = check_scope_overlap(tool_input, worktree_path=str(worktree))

        assert result is None

    def test_single_agent_boundary_no_overlap(self, worktree, state_file):
        """Only one agent registered — no pairwise comparison possible."""
        from s2_conflict_check import check_scope_overlap

        boundaries = {
            "backend-coder": {"owns": ["src/server/"], "reads": []},
        }
        _write_state(state_file, _make_state(boundaries=boundaries))

        tool_input = {"name": "backend-coder", "prompt": "Do work"}
        result = check_scope_overlap(tool_input, worktree_path=str(worktree))

        assert result is None

    def test_malformed_state_returns_none(self, worktree, state_file):
        from s2_conflict_check import check_scope_overlap

        state_file.write_text("{corrupted json")

        tool_input = {"name": "backend-coder", "prompt": "Do work"}
        result = check_scope_overlap(tool_input, worktree_path=str(worktree))

        assert result is None

    def test_no_worktree_path_returns_none(self):
        from s2_conflict_check import check_scope_overlap

        with patch("s2_conflict_check._discover_worktree_path", return_value=None):
            tool_input = {"name": "backend-coder", "prompt": "Do work"}
            result = check_scope_overlap(tool_input, worktree_path=None)

        assert result is None


# =============================================================================
# Agent Name Extraction
# =============================================================================


class TestExtractAgentName:
    """Tests for _extract_agent_name — name vs subagent_type."""

    def test_name_field_preferred(self):
        from s2_conflict_check import _extract_agent_name

        tool_input = {"name": "my-agent", "subagent_type": "pact-backend-coder"}
        assert _extract_agent_name(tool_input) == "my-agent"

    def test_subagent_type_fallback(self):
        from s2_conflict_check import _extract_agent_name

        tool_input = {"subagent_type": "pact-backend-coder"}
        assert _extract_agent_name(tool_input) == "pact-backend-coder"

    def test_neither_field_returns_none(self):
        from s2_conflict_check import _extract_agent_name

        tool_input = {"prompt": "Do work"}
        assert _extract_agent_name(tool_input) is None

    def test_empty_name_falls_through(self):
        from s2_conflict_check import _extract_agent_name

        tool_input = {"name": "", "subagent_type": "pact-backend-coder"}
        assert _extract_agent_name(tool_input) == "pact-backend-coder"


# =============================================================================
# Worktree Discovery
# =============================================================================


class TestDiscoverWorktreePath:
    """Tests for _discover_worktree_path subprocess handling."""

    def test_returns_git_toplevel(self):
        from s2_conflict_check import _discover_worktree_path

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/my/worktree\n"

        with patch("subprocess.run", return_value=mock_result):
            assert _discover_worktree_path() == "/my/worktree"

    def test_returns_none_on_non_zero_exit(self):
        from s2_conflict_check import _discover_worktree_path

        mock_result = MagicMock()
        mock_result.returncode = 128

        with patch("subprocess.run", return_value=mock_result):
            assert _discover_worktree_path() is None

    def test_returns_none_on_timeout(self):
        from s2_conflict_check import _discover_worktree_path

        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            assert _discover_worktree_path() is None

    def test_returns_none_on_file_not_found(self):
        from s2_conflict_check import _discover_worktree_path

        with patch("subprocess.run", side_effect=FileNotFoundError("git")):
            assert _discover_worktree_path() is None


# =============================================================================
# main() — Hook I/O Contract
# =============================================================================


class TestMain:
    """Tests for the main() entry point — stdin/stdout contract."""

    def test_overlap_produces_system_message(self, worktree, state_file, capsys):
        from s2_conflict_check import main

        _write_state(state_file, _make_state(boundaries=_two_agent_overlapping()))

        input_data = {
            "tool_name": "Task",
            "tool_input": {"name": "backend-coder", "prompt": "Do work"},
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("s2_conflict_check._discover_worktree_path", return_value=str(worktree)), \
             pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "systemMessage" in output
        assert "S2 Conflict Warning" in output["systemMessage"]

    def test_no_overlap_produces_suppress_output(self, worktree, state_file, capsys):
        from s2_conflict_check import main

        _write_state(state_file, _make_state(boundaries=_two_agent_disjoint()))

        input_data = {
            "tool_name": "Task",
            "tool_input": {"name": "backend-coder", "prompt": "Do work"},
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("s2_conflict_check._discover_worktree_path", return_value=str(worktree)), \
             pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "suppressOutput" in output
        assert output["suppressOutput"] is True

    def test_no_agent_name_produces_suppress_output(self, capsys):
        from s2_conflict_check import main

        input_data = {
            "tool_name": "Task",
            "tool_input": {"prompt": "Do work"},  # No name or subagent_type
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "suppressOutput" in output

    def test_invalid_json_stdin_produces_suppress_output(self, capsys):
        from s2_conflict_check import main

        with patch("sys.stdin", io.StringIO("not json")), \
             pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "suppressOutput" in output


# =============================================================================
# Exception Handler (Fail-Open)
# =============================================================================


class TestExceptionHandler:
    """Verify the outer exception handler produces systemMessage and exits 0."""

    def test_exception_produces_system_message(self, capsys):
        from s2_conflict_check import main

        input_data = {
            "tool_name": "Task",
            "tool_input": {"name": "agent", "prompt": "work"},
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("s2_conflict_check.check_scope_overlap",
                   side_effect=RuntimeError("unexpected error")), \
             pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "systemMessage" in output
        assert "s2_conflict_check" in output["systemMessage"]

    def test_exception_also_writes_stderr(self, capsys):
        from s2_conflict_check import main

        input_data = {
            "tool_name": "Task",
            "tool_input": {"name": "agent", "prompt": "work"},
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("s2_conflict_check.check_scope_overlap",
                   side_effect=RuntimeError("boom")), \
             pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "boom" in captured.err


# =============================================================================
# suppressOutput / systemMessage Mutual Exclusivity
# =============================================================================


class TestSuppressOutputExclusivity:
    """Verify that systemMessage and suppressOutput are never emitted together."""

    def test_error_path_has_system_message_not_suppress(self, capsys):
        from s2_conflict_check import main

        input_data = {
            "tool_name": "Task",
            "tool_input": {"name": "agent", "prompt": "work"},
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("s2_conflict_check.check_scope_overlap",
                   side_effect=RuntimeError("error")), \
             pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "systemMessage" in output
        assert "suppressOutput" not in output

    def test_warning_path_has_system_message_not_suppress(self, worktree, state_file, capsys):
        from s2_conflict_check import main

        _write_state(state_file, _make_state(boundaries=_two_agent_overlapping()))

        input_data = {
            "tool_name": "Task",
            "tool_input": {"name": "backend-coder", "prompt": "Do work"},
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("s2_conflict_check._discover_worktree_path", return_value=str(worktree)), \
             pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "systemMessage" in output
        assert "suppressOutput" not in output


# =============================================================================
# Debug Timing
# =============================================================================


class TestDebugTiming:
    """Verify PACT_DEBUG triggers timing output to stderr."""

    def test_debug_enabled_prints_timing(self, worktree, state_file, capsys):
        from s2_conflict_check import main

        _write_state(state_file, _make_state(boundaries=_two_agent_disjoint()))

        input_data = {
            "tool_name": "Task",
            "tool_input": {"name": "backend-coder", "prompt": "Do work"},
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("s2_conflict_check._discover_worktree_path", return_value=str(worktree)), \
             patch.dict(os.environ, {"PACT_DEBUG": "1"}), \
             pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "s2_conflict_check:" in captured.err
        assert "ms" in captured.err
