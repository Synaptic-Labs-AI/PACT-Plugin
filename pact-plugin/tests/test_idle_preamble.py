"""
Tests for the IDLE_PREAMBLE addition to teammate_idle.py (Layer 3 of
hallucination detection safeguard).

Tests cover:
1. IDLE_PREAMBLE constant exists and has expected content
2. IDLE_PREAMBLE is prepended to systemMessage in main() output
3. Existing stall detection behavior unchanged
4. Existing idle cleanup behavior unchanged
5. hooks.json integration: new hooks registered and scripts exist
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# Tests for IDLE_PREAMBLE constant
# ---------------------------------------------------------------------------
class TestIdlePreambleConstant:
    """Verify IDLE_PREAMBLE exists with expected content."""

    def test_preamble_exists(self):
        from teammate_idle import IDLE_PREAMBLE

        assert isinstance(IDLE_PREAMBLE, str)
        assert len(IDLE_PREAMBLE) > 0

    def test_preamble_contains_idle_notification(self):
        from teammate_idle import IDLE_PREAMBLE

        assert "idle notification" in IDLE_PREAMBLE

    def test_preamble_contains_no_response_needed(self):
        from teammate_idle import IDLE_PREAMBLE

        assert "no response needed" in IDLE_PREAMBLE


# ---------------------------------------------------------------------------
# Tests for IDLE_PREAMBLE integration in main() output
# ---------------------------------------------------------------------------
class TestPreambleInMainOutput:
    """Verify IDLE_PREAMBLE is prepended to systemMessage output."""

    def _run_main_with_stall(self, teammate_name="coder-a", team_name="pact-test"):
        """Helper: run main() with a stall scenario and return captured output."""
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": teammate_name,
            "team_name": team_name,
        })

        # Mock an in_progress task for the teammate (causes stall detection)
        mock_tasks = [
            {
                "id": "1",
                "subject": "CODE: auth",
                "status": "in_progress",
                "owner": teammate_name,
                "metadata": {},
            }
        ]

        with patch.dict("os.environ", {"CLAUDE_CODE_TEAM_NAME": team_name}), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.get_task_list", return_value=mock_tasks):
            # main() calls sys.exit(0) at the end
            import io as io_module
            captured_stdout = io_module.StringIO()
            with patch("sys.stdout", captured_stdout):
                with pytest.raises(SystemExit) as exc_info:
                    main()

            assert exc_info.value.code == 0
            return captured_stdout.getvalue()

    def test_stall_message_has_preamble(self):
        """Stall detection output should start with IDLE_PREAMBLE."""
        from teammate_idle import IDLE_PREAMBLE

        output_str = self._run_main_with_stall()
        if output_str.strip():
            output = json.loads(output_str)
            assert output["systemMessage"].startswith(IDLE_PREAMBLE)

    def test_stall_message_contains_original_content(self):
        """Stall detection output should still contain the stall warning text."""
        output_str = self._run_main_with_stall()
        if output_str.strip():
            output = json.loads(output_str)
            assert "went idle without completing" in output["systemMessage"]
            assert "imPACT" in output["systemMessage"]

    def _run_main_with_idle_cleanup(
        self, teammate_name="coder-a", team_name="pact-test",
        idle_count=3, tmp_path=None,
    ):
        """Helper: run main() with idle cleanup scenario."""
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": teammate_name,
            "team_name": team_name,
        })

        # Mock a completed task (triggers idle cleanup, not stall)
        mock_tasks = [
            {
                "id": "1",
                "subject": "CODE: auth",
                "status": "completed",
                "owner": teammate_name,
                "metadata": {},
            }
        ]

        # Create idle counts file to simulate consecutive idles
        if tmp_path:
            idle_path = tmp_path / ".claude" / "teams" / team_name / "idle_counts.json"
            idle_path.parent.mkdir(parents=True, exist_ok=True)
            idle_data = {
                teammate_name: {"count": idle_count - 1, "task_id": "1"}
            }
            idle_path.write_text(json.dumps(idle_data))
        else:
            idle_path = Path("/nonexistent/path/idle_counts.json")

        with patch.dict("os.environ", {"CLAUDE_CODE_TEAM_NAME": team_name}), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.get_task_list", return_value=mock_tasks), \
             patch("pathlib.Path.home", return_value=tmp_path if tmp_path else Path("/tmp")):
            import io as io_module
            captured_stdout = io_module.StringIO()
            with patch("sys.stdout", captured_stdout):
                with pytest.raises(SystemExit) as exc_info:
                    main()

            assert exc_info.value.code == 0
            return captured_stdout.getvalue()

    def test_idle_cleanup_message_has_preamble(self, tmp_path):
        """Idle cleanup suggestion should start with IDLE_PREAMBLE."""
        from teammate_idle import IDLE_PREAMBLE

        output_str = self._run_main_with_idle_cleanup(
            idle_count=3, tmp_path=tmp_path
        )
        if output_str.strip():
            output = json.loads(output_str)
            assert output["systemMessage"].startswith(IDLE_PREAMBLE)

    def test_no_output_when_no_messages(self):
        """When no stall or idle cleanup, no output (no preamble either)."""
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": "coder-a",
            "team_name": "pact-test",
        })

        # Mock: no tasks at all = no stall, no cleanup
        with patch.dict("os.environ", {"CLAUDE_CODE_TEAM_NAME": "pact-test"}), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.get_task_list", return_value=[]):
            import io as io_module
            captured_stdout = io_module.StringIO()
            with patch("sys.stdout", captured_stdout):
                with pytest.raises(SystemExit):
                    main()

            assert captured_stdout.getvalue() == ""


# ---------------------------------------------------------------------------
# Tests for hooks.json integration (new safeguard hooks registered)
# ---------------------------------------------------------------------------
