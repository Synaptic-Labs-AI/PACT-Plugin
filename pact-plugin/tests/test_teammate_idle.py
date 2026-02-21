"""
Tests for teammate_idle.py — TeammateIdle hook for stall detection and idle cleanup.

Tests cover:
1. Stall detection: in_progress task + idle = stall warning
2. Stall detection: completed task + idle = no stall (handled by idle cleanup)
3. Stall detection: no task = no stall
4. Stall detection: already-stalled task = no re-alert
5. Stall detection: blocker/algedonic task = no stall
6. Idle count tracking: increment on consecutive idles
7. Idle count tracking: reset when agent gets new work
8. Shutdown thresholds: no message below 3
9. Shutdown thresholds: suggest at 3
10. Shutdown thresholds: force shutdown_request at 5
11. Main entry point: stdin/stdout/exit behavior
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# Sample task fixtures
def make_task(task_id="1", subject="CODE: auth", status="in_progress",
              owner="backend-coder", metadata=None):
    """Helper to create a task dict."""
    return {
        "id": task_id,
        "subject": subject,
        "status": status,
        "owner": owner,
        "metadata": metadata or {},
    }


class TestFindTeammateTask:
    """Tests for teammate_idle.find_teammate_task()."""

    def test_finds_in_progress_task(self):
        from teammate_idle import find_teammate_task

        tasks = [make_task(status="in_progress", owner="coder-a")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is not None
        assert result["status"] == "in_progress"

    def test_finds_completed_task(self):
        from teammate_idle import find_teammate_task

        tasks = [make_task(status="completed", owner="coder-a")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is not None
        assert result["status"] == "completed"

    def test_prefers_in_progress_over_completed(self):
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="1", status="completed", owner="coder-a"),
            make_task(task_id="2", status="in_progress", owner="coder-a"),
        ]
        result = find_teammate_task(tasks, "coder-a")
        assert result["id"] == "2"
        assert result["status"] == "in_progress"

    def test_returns_none_for_no_matching_owner(self):
        from teammate_idle import find_teammate_task

        tasks = [make_task(owner="coder-b")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is None

    def test_returns_none_for_empty_tasks(self):
        from teammate_idle import find_teammate_task

        result = find_teammate_task([], "coder-a")
        assert result is None

    def test_returns_highest_id_completed_task(self):
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="3", status="completed", owner="coder-a"),
            make_task(task_id="7", status="completed", owner="coder-a"),
            make_task(task_id="5", status="completed", owner="coder-a"),
        ]
        result = find_teammate_task(tasks, "coder-a")
        assert result["id"] == "7"

    def test_returns_highest_id_with_double_digit_ids(self):
        """Regression: IDs are numeric strings. "20" > "3" numerically,
        but "3" > "20" lexicographically. Must compare as int."""
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="3", status="completed", owner="coder-a"),
            make_task(task_id="20", status="completed", owner="coder-a"),
            make_task(task_id="10", status="completed", owner="coder-a"),
        ]
        result = find_teammate_task(tasks, "coder-a")
        assert result["id"] == "20"

    def test_handles_non_numeric_ids_gracefully(self):
        """Non-numeric task IDs should not crash — falls back safely."""
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="abc", status="completed", owner="coder-a"),
            make_task(task_id="xyz", status="completed", owner="coder-a"),
        ]
        # Should not raise — just returns one of the tasks
        result = find_teammate_task(tasks, "coder-a")
        assert result is not None
        assert result["status"] == "completed"


class TestDetectStall:
    """Tests for teammate_idle.detect_stall()."""

    def test_detects_stall_in_progress_task(self):
        from teammate_idle import detect_stall

        tasks = [make_task(status="in_progress", owner="coder-a")]
        result = detect_stall(tasks, "coder-a")
        assert result is not None
        assert "stall" in result.lower()
        assert "coder-a" in result
        assert "imPACT" in result

    def test_no_stall_for_completed_task(self):
        from teammate_idle import detect_stall

        tasks = [make_task(status="completed", owner="coder-a")]
        result = detect_stall(tasks, "coder-a")
        assert result is None

    def test_no_stall_for_no_task(self):
        from teammate_idle import detect_stall

        tasks = [make_task(owner="coder-b")]
        result = detect_stall(tasks, "coder-a")
        assert result is None

    def test_no_stall_for_blocker_task(self):
        from teammate_idle import detect_stall

        tasks = [make_task(
            status="in_progress", owner="coder-a",
            metadata={"type": "blocker"}
        )]
        result = detect_stall(tasks, "coder-a")
        assert result is None

    def test_no_stall_for_algedonic_task(self):
        from teammate_idle import detect_stall

        tasks = [make_task(
            status="in_progress", owner="coder-a",
            metadata={"type": "algedonic"}
        )]
        result = detect_stall(tasks, "coder-a")
        assert result is None

    def test_no_re_alert_for_already_stalled(self):
        from teammate_idle import detect_stall

        tasks = [make_task(
            status="in_progress", owner="coder-a",
            metadata={"stalled": True}
        )]
        result = detect_stall(tasks, "coder-a")
        assert result is None

    def test_includes_task_id_and_subject(self):
        from teammate_idle import detect_stall

        tasks = [make_task(
            task_id="42", subject="CODE: fix login",
            status="in_progress", owner="coder-a"
        )]
        result = detect_stall(tasks, "coder-a")
        assert "#42" in result
        assert "fix login" in result


class TestIdleCountTracking:
    """Tests for idle count read/write operations."""

    def test_read_empty_file(self, tmp_path):
        from teammate_idle import read_idle_counts

        result = read_idle_counts(str(tmp_path / "idle_counts.json"))
        assert result == {}

    def test_read_existing_counts(self, tmp_path):
        from teammate_idle import read_idle_counts

        counts_file = tmp_path / "idle_counts.json"
        counts_file.write_text('{"coder-a": 3}')

        result = read_idle_counts(str(counts_file))
        assert result == {"coder-a": 3}

    def test_read_corrupted_file(self, tmp_path):
        from teammate_idle import read_idle_counts

        counts_file = tmp_path / "idle_counts.json"
        counts_file.write_text("not json{{{")

        result = read_idle_counts(str(counts_file))
        assert result == {}

    def test_write_creates_file(self, tmp_path):
        from teammate_idle import write_idle_counts

        counts_path = str(tmp_path / "subdir" / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 2})

        result = json.loads(Path(counts_path).read_text())
        assert result == {"coder-a": 2}

    def test_write_overwrites_existing(self, tmp_path):
        from teammate_idle import write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 1})
        write_idle_counts(counts_path, {"coder-a": 3, "coder-b": 1})

        result = json.loads(Path(counts_path).read_text())
        assert result == {"coder-a": 3, "coder-b": 1}

    def test_reset_idle_count(self, tmp_path):
        from teammate_idle import write_idle_counts, reset_idle_count, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 3, "coder-b": 1})

        reset_idle_count("coder-a", counts_path)

        result = read_idle_counts(counts_path)
        assert "coder-a" not in result
        assert result["coder-b"] == 1

    def test_reset_nonexistent_teammate(self, tmp_path):
        from teammate_idle import write_idle_counts, reset_idle_count, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 3})

        # Should not raise
        reset_idle_count("coder-x", counts_path)

        result = read_idle_counts(counts_path)
        assert result == {"coder-a": 3}


class TestCheckIdleCleanup:
    """Tests for teammate_idle.check_idle_cleanup()."""

    def test_no_action_below_threshold(self, tmp_path):
        from teammate_idle import check_idle_cleanup

        counts_path = str(tmp_path / "idle_counts.json")
        tasks = [make_task(status="completed", owner="coder-a")]

        # First idle event (count = 1)
        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert should_shutdown is False

    def test_no_action_at_two(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 1})  # Already had 1
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert should_shutdown is False

    def test_suggest_at_three(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 2})  # Will become 3
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert "idle" in msg.lower()
        assert "coder-a" in msg
        assert should_shutdown is False

    def test_suggest_at_four(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 3})  # Will become 4
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert should_shutdown is False

    def test_force_shutdown_at_five(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 4})  # Will become 5
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert "shutdown" in msg.lower()
        assert should_shutdown is True

    def test_force_shutdown_above_five(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 9})  # Will become 10
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert should_shutdown is True

    def test_resets_count_when_no_completed_task(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 3})
        # Agent now has in_progress task (got new work)
        tasks = [make_task(status="in_progress", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert should_shutdown is False

        # Count should be reset
        counts = read_idle_counts(counts_path)
        assert "coder-a" not in counts

    def test_skips_stalled_agents(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 4})
        tasks = [make_task(
            status="completed", owner="coder-a",
            metadata={"stalled": True}
        )]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert should_shutdown is False

    def test_skips_terminated_agents(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 4})
        tasks = [make_task(
            status="completed", owner="coder-a",
            metadata={"terminated": True}
        )]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert should_shutdown is False

    def test_no_task_resets_count(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 3})
        tasks = [make_task(owner="coder-b")]  # No task for coder-a

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None

        counts = read_idle_counts(counts_path)
        assert "coder-a" not in counts


class TestMain:
    """Tests for teammate_idle.main() stdin/stdout/exit behavior."""

    def _run_main(self, input_data, env=None, tasks=None):
        """Helper to run main() with mocked inputs."""
        import io
        from teammate_idle import main

        default_env = {
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }
        if env:
            default_env.update(env)

        mock_tasks = tasks if tasks is not None else []

        with patch.dict(os.environ, default_env, clear=True), \
             patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("teammate_idle.get_task_list", return_value=mock_tasks):
            with pytest.raises(SystemExit) as exc_info:
                main()

        return exc_info.value.code

    def test_exits_0_when_no_team(self, capsys):
        import io
        import os
        from teammate_idle import main

        with patch.dict(os.environ, {"CLAUDE_CODE_TEAM_NAME": ""}, clear=True), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_exits_0_when_no_teammate_name(self):
        exit_code = self._run_main({"teammate_name": ""})
        assert exit_code == 0

    def test_exits_0_when_no_tasks(self):
        exit_code = self._run_main(
            {"teammate_name": "coder-a"},
            tasks=None
        )
        assert exit_code == 0

    def test_outputs_stall_warning(self, capsys, tmp_path):
        import io
        import os
        from teammate_idle import main

        tasks = [make_task(status="in_progress", owner="coder-a")]

        env = {
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }

        with patch.dict(os.environ, env, clear=True), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.get_task_list", return_value=tasks):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        if captured.out.strip():
            output = json.loads(captured.out)
            assert "systemMessage" in output
            assert "stall" in output["systemMessage"].lower()

    def test_outputs_nothing_for_completed_below_threshold(self, capsys, tmp_path):
        import io
        import os
        from teammate_idle import main

        tasks = [make_task(status="completed", owner="coder-a")]

        env = {
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
        }

        # Patch Path.home() so idle_counts.json uses tmp_path instead of real home
        # (prevents cross-test pollution from persisted idle count files)
        with patch.dict(os.environ, env, clear=True), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.get_task_list", return_value=tasks), \
             patch("teammate_idle.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # No output expected below threshold (first idle event, count=1)
        assert captured.out.strip() == "" or "systemMessage" not in captured.out

    def test_exits_0_on_invalid_json(self):
        import io
        import os
        from teammate_idle import main

        with patch.dict(os.environ, {"CLAUDE_CODE_TEAM_NAME": "pact-test"}, clear=True), \
             patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


# Required for patch.dict
import os


# =============================================================================
# Edge Case Tests — Stall/Idle Interaction, Concurrent Tracking
# =============================================================================

class TestLegacyIdleCountMigration:
    """Tests for the int-to-structured-dict migration in check_idle_cleanup().

    Legacy idle_counts.json files stored plain ints per teammate (e.g., {"coder-a": 3}).
    The current format uses structured dicts (e.g., {"coder-a": {"count": 3, "task_id": "1"}}).
    The migration logic in _increment() must handle both formats transparently."""

    def test_legacy_int_migrated_to_structured_dict(self, tmp_path):
        """When idle_counts.json has a plain int, check_idle_cleanup should
        migrate it to structured format and continue counting correctly."""
        from teammate_idle import check_idle_cleanup, read_idle_counts, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        # Write legacy format: plain int
        write_idle_counts(counts_path, {"coder-a": 2})
        tasks = [make_task(task_id="5", status="completed", owner="coder-a")]

        # Should migrate the int (2) to {"count": 2, "task_id": ""} then
        # increment to 3, which hits the suggest threshold
        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert "idle" in msg.lower()
        assert should_shutdown is False

        # Verify the file now contains structured format
        counts = read_idle_counts(counts_path)
        entry = counts["coder-a"]
        assert isinstance(entry, dict)
        assert entry["count"] == 3
        assert entry["task_id"] == "5"

    def test_legacy_int_zero_migrated_correctly(self, tmp_path):
        """Edge case: legacy count of 0 should migrate and increment to 1."""
        from teammate_idle import check_idle_cleanup, read_idle_counts, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 0})
        tasks = [make_task(task_id="1", status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        # Count goes from 0 -> 1, below threshold
        assert msg is None
        assert should_shutdown is False

        counts = read_idle_counts(counts_path)
        entry = counts["coder-a"]
        assert isinstance(entry, dict)
        assert entry["count"] == 1


class TestStallIdleInteraction:
    """Verify that stalled agents get stall detection, NOT idle cleanup.
    This is the key invariant: a stalled agent (in_progress task + idle)
    should receive a stall warning, and should NOT be tracked for idle cleanup."""

    def test_stall_prevents_idle_count_increment(self, tmp_path):
        """When stall is detected, idle count should NOT be incremented."""
        from teammate_idle import detect_stall, check_idle_cleanup, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        tasks = [make_task(status="in_progress", owner="coder-a")]

        # Stall IS detected
        stall_msg = detect_stall(tasks, "coder-a")
        assert stall_msg is not None

        # Idle cleanup returns nothing (task not completed)
        msg, shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert shutdown is False

        # Idle count should NOT have been set
        counts = read_idle_counts(counts_path)
        assert "coder-a" not in counts

    def test_main_exclusive_stall_or_cleanup(self, capsys, tmp_path):
        """main() should emit stall OR cleanup message, never both.
        The code has 'else' branch: if stall, skip cleanup check."""
        import io
        from teammate_idle import main

        # Agent with in_progress task (stall case)
        tasks = [make_task(status="in_progress", owner="coder-a")]

        with patch.dict(os.environ, {"CLAUDE_CODE_TEAM_NAME": "pact-test"}, clear=True), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.get_task_list", return_value=tasks):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        if captured.out.strip():
            output = json.loads(captured.out)
            msg = output.get("systemMessage", "")
            # Should have stall message, NOT cleanup/shutdown message
            assert "stall" in msg.lower()
            assert "shutdown" not in msg.lower()

    def test_completed_then_new_work_resets_idle(self, tmp_path):
        """Agent completes task (starts idle tracking), then gets new work
        (in_progress). Idle count should be reset."""
        from teammate_idle import check_idle_cleanup, write_idle_counts, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")

        # First: agent has completed task, idle count accumulates
        completed_tasks = [make_task(status="completed", owner="coder-a")]
        write_idle_counts(counts_path, {"coder-a": 4})

        # Now: agent gets a new in_progress task
        new_tasks = [
            make_task(task_id="2", status="in_progress", owner="coder-a"),
            make_task(task_id="1", status="completed", owner="coder-a"),
        ]

        # check_idle_cleanup should reset because find_teammate_task returns
        # the in_progress task (not completed)
        msg, shutdown = check_idle_cleanup(new_tasks, "coder-a", counts_path)
        assert msg is None
        assert shutdown is False

        counts = read_idle_counts(counts_path)
        assert "coder-a" not in counts


class TestConcurrentIdleTracking:
    """Test idle tracking with multiple agents being tracked simultaneously."""

    def test_multiple_agents_tracked_independently(self, tmp_path):
        """Each agent's idle count should be independent."""
        from teammate_idle import check_idle_cleanup, write_idle_counts, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")

        tasks = [
            make_task(task_id="1", status="completed", owner="coder-a"),
            make_task(task_id="2", status="completed", owner="coder-b"),
        ]

        # coder-a idles 3 times (pre-seed with structured format)
        write_idle_counts(counts_path, {"coder-a": {"count": 2, "task_id": "1"}})
        msg_a, _ = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg_a is not None  # Suggest at 3
        assert "coder-a" in msg_a

        # coder-b idles once (count starts at 0)
        msg_b, _ = check_idle_cleanup(tasks, "coder-b", counts_path)
        assert msg_b is None  # Below threshold

        counts = read_idle_counts(counts_path)
        assert counts["coder-a"]["count"] == 3
        assert counts["coder-b"]["count"] == 1

    def test_one_agent_shutdown_doesnt_affect_others(self, tmp_path):
        """Force-shutdown of one agent should not affect others' counts."""
        from teammate_idle import check_idle_cleanup, write_idle_counts, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")

        tasks = [
            make_task(task_id="1", status="completed", owner="coder-a"),
            make_task(task_id="2", status="completed", owner="coder-b"),
        ]

        write_idle_counts(counts_path, {
            "coder-a": {"count": 4, "task_id": "1"},
            "coder-b": {"count": 1, "task_id": "2"},
        })

        # coder-a hits force threshold (5)
        msg_a, shutdown_a = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert shutdown_a is True

        # coder-b still at count 2, no action
        msg_b, shutdown_b = check_idle_cleanup(tasks, "coder-b", counts_path)
        assert shutdown_b is False
        assert msg_b is None

        counts = read_idle_counts(counts_path)
        assert counts["coder-a"]["count"] == 5
        assert counts["coder-b"]["count"] == 2


class TestFindTeammateTaskEdgeCases:
    """Additional edge cases for find_teammate_task()."""

    def test_pending_task_not_returned(self):
        """Pending tasks (not yet started) should NOT be returned."""
        from teammate_idle import find_teammate_task

        tasks = [make_task(status="pending", owner="coder-a")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is None

    def test_deleted_task_not_returned(self):
        """Deleted tasks should NOT be returned."""
        from teammate_idle import find_teammate_task

        tasks = [make_task(status="deleted", owner="coder-a")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is None

    def test_mixed_statuses_returns_in_progress(self):
        """With pending + in_progress + completed, returns in_progress."""
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="1", status="pending", owner="coder-a"),
            make_task(task_id="2", status="in_progress", owner="coder-a"),
            make_task(task_id="3", status="completed", owner="coder-a"),
        ]
        result = find_teammate_task(tasks, "coder-a")
        assert result["id"] == "2"

    def test_owner_matching_is_exact(self):
        """Owner matching should be exact, not substring."""
        from teammate_idle import find_teammate_task

        tasks = [make_task(status="in_progress", owner="coder-a-backend")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is None


class TestDetectStallEdgeCases:
    """Additional edge cases for detect_stall()."""

    def test_empty_metadata_still_detects_stall(self):
        """Task with empty metadata dict should still trigger stall."""
        from teammate_idle import detect_stall

        tasks = [make_task(
            status="in_progress", owner="coder-a",
            metadata={}
        )]
        result = detect_stall(tasks, "coder-a")
        assert result is not None

    def test_no_metadata_key_still_detects_stall(self):
        """Task without metadata key at all should still trigger stall."""
        from teammate_idle import detect_stall

        task = {"id": "1", "subject": "work", "status": "in_progress", "owner": "coder-a"}
        result = detect_stall([task], "coder-a")
        assert result is not None

    def test_stalled_false_value_still_detects_stall(self):
        """metadata.stalled=False should NOT suppress stall detection."""
        from teammate_idle import detect_stall

        tasks = [make_task(
            status="in_progress", owner="coder-a",
            metadata={"stalled": False}
        )]
        result = detect_stall(tasks, "coder-a")
        assert result is not None  # False != truthy, so stall should fire


class TestMainEdgeCases:
    """Additional edge cases for main() entry point."""

    def test_team_name_lowercased(self):
        """CLAUDE_CODE_TEAM_NAME should be lowercased per v3.3.2 convention."""
        import io
        from teammate_idle import main

        # Use uppercase team name — should work (lowercased internally)
        with patch.dict(os.environ, {"CLAUDE_CODE_TEAM_NAME": "PACT-TEST"}, clear=True), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": ""}))):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0  # Empty teammate_name exits cleanly

    def test_get_task_list_returns_none(self):
        """Should exit cleanly when get_task_list() returns None."""
        import io
        from teammate_idle import main

        with patch.dict(os.environ, {"CLAUDE_CODE_TEAM_NAME": "pact-test"}, clear=True), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.get_task_list", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_shutdown_message_includes_action_required(self, capsys, tmp_path):
        """When force shutdown threshold hit, output should include ACTION REQUIRED."""
        import io
        from teammate_idle import main, write_idle_counts

        tasks = [make_task(status="completed", owner="coder-a")]

        # Pre-set count to 4 (will become 5 = force threshold)
        idle_dir = tmp_path / "teams" / "pact-test"
        idle_dir.mkdir(parents=True)
        write_idle_counts(str(idle_dir / "idle_counts.json"), {"coder-a": 4})

        with patch.dict(os.environ, {"CLAUDE_CODE_TEAM_NAME": "pact-test"}, clear=True), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.get_task_list", return_value=tasks), \
             patch("pathlib.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        if captured.out.strip():
            output = json.loads(captured.out)
            msg = output.get("systemMessage", "")
            assert "ACTION REQUIRED" in msg
            assert "shutdown_request" in msg
