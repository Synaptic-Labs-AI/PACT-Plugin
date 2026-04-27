"""
Tests for teammate_idle.py — TeammateIdle hook for threshold-escalation
resource cleanup of zombie teammates.

#538 C3 scope: detect_stall + the stall-nag surface were removed entirely;
this file now covers only the surviving check_idle_cleanup
threshold-escalation + TOCTOU / legacy-migration / concurrent tracking
paths. Stall-detection + intentional_wait suppression tests have been
retired because the gated surface no longer exists.

Tests cover:
- find_teammate_task: owner/status priority, multi-status fixtures.
- Idle count tracking: read/write/reset + TOCTOU atomicity.
- check_idle_cleanup: threshold 3 (suggest) + threshold 5 (force shutdown)
  + task reassignment reset + stalled/terminated skip.
- main(): stdin/stdout/exit behavior including force-shutdown ACTION
  REQUIRED emission.
- Legacy int → structured-dict migration.
- Concurrent multi-agent tracking independence.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


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

        tasks = [make_task(owner="coder-a", status="in_progress")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is not None
        assert result["owner"] == "coder-a"

    def test_finds_completed_task(self):
        from teammate_idle import find_teammate_task

        tasks = [make_task(owner="coder-a", status="completed")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is not None
        assert result["status"] == "completed"

    def test_prefers_in_progress_over_completed(self):
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="1", owner="coder-a", status="completed"),
            make_task(task_id="2", owner="coder-a", status="in_progress"),
        ]
        result = find_teammate_task(tasks, "coder-a")
        assert result["id"] == "2"

    def test_returns_none_for_no_matching_owner(self):
        from teammate_idle import find_teammate_task

        tasks = [make_task(owner="coder-b")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is None

    def test_returns_none_for_empty_tasks(self):
        from teammate_idle import find_teammate_task

        assert find_teammate_task([], "coder-a") is None

    def test_returns_highest_id_completed_task(self):
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="1", owner="coder-a", status="completed"),
            make_task(task_id="7", owner="coder-a", status="completed"),
            make_task(task_id="3", owner="coder-a", status="completed"),
        ]
        result = find_teammate_task(tasks, "coder-a")
        assert result["id"] == "7"

    def test_returns_highest_id_with_double_digit_ids(self):
        """String comparison would pick '9' over '20' — test numeric compare."""
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="9", owner="coder-a", status="completed"),
            make_task(task_id="20", owner="coder-a", status="completed"),
        ]
        result = find_teammate_task(tasks, "coder-a")
        assert result["id"] == "20"

    def test_handles_non_numeric_ids_gracefully(self):
        """Non-numeric IDs should not raise; best-effort comparison."""
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="abc", owner="coder-a", status="completed"),
            make_task(task_id="5", owner="coder-a", status="completed"),
        ]
        # Should not raise
        result = find_teammate_task(tasks, "coder-a")
        assert result is not None


class TestFindTeammateTaskEdgeCases:
    """Additional edge cases for find_teammate_task()."""

    def test_pending_task_not_returned(self):
        from teammate_idle import find_teammate_task

        tasks = [make_task(status="pending", owner="coder-a")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is None

    def test_deleted_task_not_returned(self):
        from teammate_idle import find_teammate_task

        tasks = [make_task(status="deleted", owner="coder-a")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is None

    def test_mixed_statuses_returns_in_progress(self):
        from teammate_idle import find_teammate_task

        tasks = [
            make_task(task_id="1", status="pending", owner="coder-a"),
            make_task(task_id="2", status="in_progress", owner="coder-a"),
            make_task(task_id="3", status="completed", owner="coder-a"),
        ]
        result = find_teammate_task(tasks, "coder-a")
        assert result["id"] == "2"

    def test_owner_matching_is_exact(self):
        from teammate_idle import find_teammate_task

        tasks = [make_task(status="in_progress", owner="coder-a-backend")]
        result = find_teammate_task(tasks, "coder-a")
        assert result is None


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
    """Tests for teammate_idle.check_idle_cleanup() threshold-escalation."""

    def test_no_action_below_threshold(self, tmp_path):
        from teammate_idle import check_idle_cleanup

        counts_path = str(tmp_path / "idle_counts.json")
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert should_shutdown is False

    def test_no_action_at_two(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 1})
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert should_shutdown is False

    def test_suggest_at_three(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 2})
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert "idle" in msg.lower()
        assert "coder-a" in msg
        assert should_shutdown is False

    def test_suggest_at_four(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 3})
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert should_shutdown is False

    def test_force_shutdown_at_five(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 4})
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert "shutdown" in msg.lower()
        assert should_shutdown is True

    def test_force_shutdown_above_five(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 9})
        tasks = [make_task(status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert should_shutdown is True

    def test_resets_count_when_no_completed_task(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 3})
        tasks = [make_task(status="in_progress", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert should_shutdown is False

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
        tasks = [make_task(owner="coder-b")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None

        counts = read_idle_counts(counts_path)
        assert "coder-a" not in counts


class TestLegacyIdleCountMigration:
    """Tests for the int-to-structured-dict migration in check_idle_cleanup().

    Legacy idle_counts.json files stored plain ints per teammate. The current
    format uses structured dicts. The migration logic must handle both."""

    def test_legacy_int_migrated_to_structured_dict(self, tmp_path):
        from teammate_idle import check_idle_cleanup, read_idle_counts, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 2})
        tasks = [make_task(task_id="5", status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is not None
        assert "idle" in msg.lower()
        assert should_shutdown is False

        counts = read_idle_counts(counts_path)
        entry = counts["coder-a"]
        assert isinstance(entry, dict)
        assert entry["count"] == 3
        assert entry["task_id"] == "5"

    def test_legacy_int_zero_migrated_correctly(self, tmp_path):
        from teammate_idle import check_idle_cleanup, read_idle_counts, write_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 0})
        tasks = [make_task(task_id="1", status="completed", owner="coder-a")]

        msg, should_shutdown = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg is None
        assert should_shutdown is False

        counts = read_idle_counts(counts_path)
        entry = counts["coder-a"]
        assert isinstance(entry, dict)
        assert entry["count"] == 1


class TestTaskReassignmentReset:
    """Verify that a task switch between idle events resets the count."""

    def test_completed_then_new_work_resets_idle(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")
        write_idle_counts(counts_path, {"coder-a": 4})

        # Agent gets new in_progress task — find_teammate_task now returns
        # the in_progress one, so cleanup resets (status != completed).
        new_tasks = [
            make_task(task_id="2", status="in_progress", owner="coder-a"),
            make_task(task_id="1", status="completed", owner="coder-a"),
        ]
        msg, shutdown = check_idle_cleanup(new_tasks, "coder-a", counts_path)
        assert msg is None
        assert shutdown is False

        counts = read_idle_counts(counts_path)
        assert "coder-a" not in counts


class TestConcurrentIdleTracking:
    """Independence + TOCTOU coverage for multi-agent idle tracking."""

    def test_multiple_agents_tracked_independently(self, tmp_path):
        from teammate_idle import check_idle_cleanup, write_idle_counts, read_idle_counts

        counts_path = str(tmp_path / "idle_counts.json")

        tasks = [
            make_task(task_id="1", status="completed", owner="coder-a"),
            make_task(task_id="2", status="completed", owner="coder-b"),
        ]

        write_idle_counts(counts_path, {"coder-a": {"count": 2, "task_id": "1"}})
        msg_a, _ = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert msg_a is not None
        assert "coder-a" in msg_a

        msg_b, _ = check_idle_cleanup(tasks, "coder-b", counts_path)
        assert msg_b is None

        counts = read_idle_counts(counts_path)
        assert counts["coder-a"]["count"] == 3
        assert counts["coder-b"]["count"] == 1

    def test_one_agent_shutdown_doesnt_affect_others(self, tmp_path):
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

        msg_a, shutdown_a = check_idle_cleanup(tasks, "coder-a", counts_path)
        assert shutdown_a is True

        msg_b, shutdown_b = check_idle_cleanup(tasks, "coder-b", counts_path)
        assert shutdown_b is False
        assert msg_b is None

        counts = read_idle_counts(counts_path)
        assert counts["coder-a"]["count"] == 5
        assert counts["coder-b"]["count"] == 2


class TestMain:
    """Tests for teammate_idle.main() stdin/stdout/exit behavior."""

    def _run_main(self, input_data, team_name="pact-test", tasks=None):
        """Helper to run main() with mocked inputs."""
        import io
        from teammate_idle import main

        mock_tasks = tasks if tasks is not None else []

        with patch("teammate_idle.get_team_name", return_value=team_name), \
             patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("teammate_idle.get_task_list", return_value=mock_tasks):
            with pytest.raises(SystemExit) as exc_info:
                main()

        return exc_info.value.code

    def test_exits_0_when_no_team(self):
        import io
        from teammate_idle import main

        with patch("teammate_idle.get_team_name", return_value=""), \
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
            tasks=None,
        )
        assert exit_code == 0

    def test_in_progress_task_emits_no_output(self, capsys, tmp_path):
        """Post-#538: in_progress + idle → no emission (stall-nag removed).
        The hook silently passes; no systemMessage, no stderr."""
        import io
        from teammate_idle import main

        tasks = [make_task(status="in_progress", owner="coder-a")]

        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.get_task_list", return_value=tasks), \
             patch("teammate_idle.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # suppressOutput JSON is allowed; systemMessage is not.
        if captured.out.strip():
            output = json.loads(captured.out)
            assert "systemMessage" not in output

    def test_completed_below_threshold_no_emission(self, capsys, tmp_path):
        import io
        from teammate_idle import main

        tasks = [make_task(status="completed", owner="coder-a")]

        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.get_task_list", return_value=tasks), \
             patch("teammate_idle.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        if captured.out.strip():
            output = json.loads(captured.out)
            assert "systemMessage" not in output

    def test_exits_0_on_invalid_json(self):
        import io
        from teammate_idle import main

        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


class TestMainEdgeCases:
    """Additional edge cases for main() entry point."""

    def test_get_task_list_returns_none(self):
        import io
        from teammate_idle import main

        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.get_task_list", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_shutdown_message_includes_action_required(self, capsys, tmp_path):
        """At force threshold, output must include ACTION REQUIRED +
        shutdown_request wording for the team-lead to act on."""
        import io
        from teammate_idle import main, write_idle_counts

        tasks = [make_task(status="completed", owner="coder-a")]

        idle_dir = tmp_path / ".claude" / "teams" / "pact-test"
        idle_dir.mkdir(parents=True)
        write_idle_counts(str(idle_dir / "idle_counts.json"), {"coder-a": 4})

        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.get_task_list", return_value=tasks), \
             patch("pathlib.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip(), "Force-shutdown should emit a systemMessage"
        output = json.loads(captured.out)
        msg = output.get("systemMessage", "")
        assert "ACTION REQUIRED" in msg
        assert "shutdown_request" in msg
