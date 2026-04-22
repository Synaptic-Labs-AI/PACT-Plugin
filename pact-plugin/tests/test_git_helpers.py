"""
Smoke tests for shared/git_helpers.py — narrow git CLI wrapper.

Per arch §8, substantive unit test coverage is the test-engineer's scope.
This file asserts only the minimum invariants a stage-ready CODE handoff
requires: the wrapper invokes git with the correct argv shape, passes
timeout=5, returns the CompletedProcess on success, and collapses
TimeoutExpired + FileNotFoundError into None.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch


def test_run_git_success_returns_completed_process():
    """Happy path: run_git returns the CompletedProcess (regardless of returncode)."""
    from shared.git_helpers import run_git

    mock_result = MagicMock(spec=subprocess.CompletedProcess)
    mock_result.returncode = 0
    mock_result.stdout = "some output\n"
    with patch("shared.git_helpers.subprocess.run", return_value=mock_result) as mock_run:
        result = run_git(["diff", "--name-only", "--cached"])

    assert result is mock_result
    args, kwargs = mock_run.call_args
    assert args[0] == ["git", "diff", "--name-only", "--cached"]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 5


def test_run_git_returns_completed_process_on_non_zero_exit():
    """Non-zero exit is NOT an exception — caller triages returncode."""
    from shared.git_helpers import run_git

    mock_result = MagicMock(spec=subprocess.CompletedProcess)
    mock_result.returncode = 1
    with patch("shared.git_helpers.subprocess.run", return_value=mock_result):
        result = run_git(["check-ignore", "-q", ".env"])

    assert result is mock_result
    assert result.returncode == 1


def test_run_git_timeout_returns_none():
    """TimeoutExpired → None (fail-open)."""
    from shared.git_helpers import run_git

    with patch(
        "shared.git_helpers.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=5),
    ):
        result = run_git(["check-ignore", "-q", ".env"])

    assert result is None


def test_run_git_file_not_found_returns_none():
    """FileNotFoundError → None (fail-open when git binary missing)."""
    from shared.git_helpers import run_git

    with patch(
        "shared.git_helpers.subprocess.run",
        side_effect=FileNotFoundError("git"),
    ):
        result = run_git(["check-ignore", "-q", ".env"])

    assert result is None


def test_run_git_custom_timeout():
    """timeout kwarg is honored when caller overrides the default."""
    from shared.git_helpers import run_git

    mock_result = MagicMock(spec=subprocess.CompletedProcess)
    mock_result.returncode = 0
    with patch("shared.git_helpers.subprocess.run", return_value=mock_result) as mock_run:
        run_git(["status"], timeout=10)

    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 10


def test_run_git_text_false_passes_through():
    """text=False is passed through for callers needing bytes."""
    from shared.git_helpers import run_git

    mock_result = MagicMock(spec=subprocess.CompletedProcess)
    mock_result.returncode = 0
    with patch("shared.git_helpers.subprocess.run", return_value=mock_result) as mock_run:
        run_git(["show", ":binary"], text=False)

    _, kwargs = mock_run.call_args
    assert kwargs["text"] is False


def test_run_git_does_not_swallow_unexpected_exceptions():
    """Narrow catch: unexpected exception classes propagate (not None)."""
    from shared.git_helpers import run_git

    class UnexpectedError(Exception):
        pass

    with patch(
        "shared.git_helpers.subprocess.run",
        side_effect=UnexpectedError("deliberate"),
    ):
        try:
            run_git(["status"])
        except UnexpectedError:
            return
    raise AssertionError("run_git should not swallow unexpected exception classes")
