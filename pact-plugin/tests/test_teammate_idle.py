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
- main(): stdin/stdout/exit behavior including the force-threshold ACTION
  REQUIRED stop advisory.
- Legacy int → structured-dict migration.
- Concurrent multi-agent tracking independence.
"""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


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

        with patch("teammate_idle.frame_team_and_name", return_value=(team_name, "")), \
             patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("teammate_idle.iter_team_task_jsons", return_value=mock_tasks):
            with pytest.raises(SystemExit) as exc_info:
                main()

        return exc_info.value.code

    def test_exits_0_when_no_team(self):
        import io
        from teammate_idle import main

        with patch("teammate_idle.frame_team_and_name", return_value=("", "")), \
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

        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.iter_team_task_jsons", return_value=tasks), \
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

        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.iter_team_task_jsons", return_value=tasks), \
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

        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


class TestMainEdgeCases:
    """Additional edge cases for main() entry point."""

    def test_an_empty_team_task_list_exits(self):
        import io
        from teammate_idle import main

        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.iter_team_task_jsons", return_value=[]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_shutdown_message_advises_taskstop_directly(self, capsys, tmp_path):
        """At force threshold, output must include ACTION REQUIRED + a direct
        TaskStop instruction for the team-lead to act on."""
        import io
        from teammate_idle import main, write_idle_counts

        tasks = [make_task(status="completed", owner="coder-a")]

        idle_dir = tmp_path / ".claude" / "teams" / "pact-test"
        idle_dir.mkdir(parents=True)
        write_idle_counts(str(idle_dir / "idle_counts.json"), {"coder-a": 4})

        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "coder-a"}))), \
             patch("teammate_idle.iter_team_task_jsons", return_value=tasks), \
             patch("pathlib.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip(), "Force-shutdown should emit a systemMessage"
        output = json.loads(captured.out)
        msg = output.get("systemMessage", "")
        assert "ACTION REQUIRED" in msg
        # NEGATIVE, not a presence pin. The advisory still contains the substring
        # "shutdown_request" — in the clause telling the lead NOT to send one — so
        # `assert "shutdown_request" in msg` would keep passing while pinning the
        # opposite of its intent. Gate on the phrase that can observe the change.
        assert "Send shutdown_request" not in msg, (
            "the idle advisory must not instruct a graceful request — the shutdown "
            "loops no longer send one, and a hook that advises otherwise re-creates "
            "the cross-surface divergence this change closed"
        )
        # The CALL FORM, not the bare word. A bare `"TaskStop" in msg` used to
        # follow this line and was UNREACHABLE: any message satisfying the call
        # form necessarily contains the bare word, and this assertion runs
        # first, so the weaker one could never be the sole failure. Removed
        # rather than kept as a line that cannot fail.
        assert 'TaskStop("coder-a")' in msg


class TestMainDrivesTheUnflaggedAdvisoryThroughARealStore:
    """Layer 2, driven through `teammate_idle.main()` against a real store.

    Every other arm on this surface calls the Layer 2 helper directly, and the
    `main()` arms above mock the team lookup and the task list. So the call
    from `main()` into the helper could be deleted with every one of them
    green: an unwired advisory fails closed, and a silent hook looks exactly
    like a hook with nothing to say. Only an arm asserting that the advisory
    DOES appear, reached through `main()`, can see that.

    THE STORE IS REAL AND CONFINED TO tmp_path. `CLAUDE_CONFIG_DIR` and `HOME`
    both point into the test's tmp tree, so the team config, the task store,
    the session context and the registry all live there; nothing touches the
    real config root.

    THE REGISTRY ROW IS STAMPED AT WRITE TIME. `main()` takes no clock, so the
    row cannot be aged against an injected one. A fixed calendar date would
    expire under the 24-hour TTL and turn this arm into a date bomb; a stamp
    taken at write time is read back within the same test, so no TTL boundary
    can fall between the write and the read.

    THE STDIN FRAME IS BUILT, NOT CAPTURED. It carries only the fields `main()`
    reads: `session_id`, which locates the session context, and
    `teammate_name`.
    """

    TEAM = "session-idlearm"
    SESSION_ID = "idle-arm-session"
    PROJECT_DIR = "/idle-arm/project"
    TEAMMATE = "idle-coder"
    TASK_ID = "13"
    ADVISORY_FRAGMENT = "background work and have no flagged wait"

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        from shared import background_work as bw
        from shared.pact_context import project_slug

        config = tmp_path / ".claude"
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", self.PROJECT_DIR)

        def write(path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")

        write(config / "teams" / self.TEAM / "config.json", {
            "leadSessionId": self.SESSION_ID,
            "members": [{"name": self.TEAMMATE,
                         "agentId": f"{self.TEAMMATE}@{self.TEAM}",
                         "agentType": "pact-backend-coder",
                         "backendType": "in-process"}],
        })
        write(config / "pact-sessions" / project_slug(self.PROJECT_DIR)
              / self.SESSION_ID / "pact-session-context.json", {
            "session_id": self.SESSION_ID,
            "project_dir": self.PROJECT_DIR,
            "team_name": self.TEAM,
        })

        def seed(wait=None):
            task = {"id": self.TASK_ID, "status": "in_progress",
                    "owner": self.TEAMMATE, "subject": "CODE: idle arm"}
            registered_at = bw.iso_now()
            if wait is not None:
                # Anchored at or after the launch, so the wait covers it.
                task["metadata"] = {"intentional_wait": {
                    "reason": "awaiting_blocker_resolution",
                    "expected_resolver": "lead",
                    "since": registered_at,
                    "covers_since": registered_at,
                }}
            write(config / "tasks" / self.TEAM / f"{self.TASK_ID}.json", task)
            assert bw.save_records([{
                "agent_name": self.TEAMMATE,
                "session_id": self.SESSION_ID,
                "task_ids": [self.TASK_ID],
                "registered_at": registered_at,
            }], team_name=self.TEAM) is True

        return seed

    def _idle_once(self, capsys):
        """One TeammateIdle tick through `main()`. True if the advisory fired."""
        import io
        from teammate_idle import main

        frame = {"hook_event_name": "TeammateIdle",
                 "session_id": self.SESSION_ID,
                 "teammate_name": self.TEAMMATE}
        capsys.readouterr()
        with patch("sys.stdin", io.StringIO(json.dumps(frame))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        out = capsys.readouterr().out.strip()
        payload = json.loads(out) if out else {}
        return self.ADVISORY_FRAGMENT in payload.get("systemMessage", "")

    def test_the_advisory_fires_on_the_THIRD_consecutive_unflagged_idle(
        self, store, capsys
    ):
        store()
        fired = [self._idle_once(capsys) for _ in range(3)]
        assert fired == [False, False, True], (
            "expected the unflagged-background advisory on exactly the third "
            "consecutive idle through teammate_idle.main(); got %r. All False "
            "means main() no longer reaches the Layer 2 check at all, which "
            "every helper-level arm would miss." % (fired,)
        )

    def test_a_FLAGGED_wait_covering_the_launch_keeps_every_idle_silent(
        self, store, capsys
    ):
        store(wait=True)
        fired = [self._idle_once(capsys) for _ in range(3)]
        assert fired == [False, False, False], (
            "a teammate that flagged a wait covering its launch drew the "
            "unflagged advisory on idle %r; the advisory tells it that it has "
            "no flagged wait, which is false" % (fired,)
        )

    def test_an_unflagged_idle_STAMPS_idled_at_so_the_lead_uses_its_shorter_window(
        self, store, capsys, tmp_path
    ):
        """Layer 2 hands Layer 3 a clock. An unflagged idle through `main()`
        stamps `idled_at` on the record, and the lead-side window runs from that
        stamp with the shorter threshold. Without the stamp the lead falls back
        to `registered_at` and the longer window, so a stranded teammate is
        surfaced later than it should be.
        """
        store()
        assert self._idle_once(capsys) is False
        registry = tmp_path / ".claude" / "teams" / self.TEAM / "background_work.json"
        (record,) = json.loads(registry.read_text(encoding="utf-8"))["records"]
        assert record.get("idled_at"), (
            "an unflagged idle through main() left the record without idled_at, "
            "so the lead-side scan falls back to registered_at and the longer "
            "window: %r" % (record,)
        )

    def test_a_NON_NUMERIC_idle_count_restarts_the_ramp_instead_of_breaking_it(
        self, store, capsys, tmp_path
    ):
        """A hand-edited or corrupted counter file can hold a count that is not a
        number. It must read as zero, so the advisory still fires on the third
        consecutive idle; it must not raise and silence the advisory for good.
        """
        store()
        counter = (tmp_path / ".claude" / "teams" / self.TEAM
                   / "unflagged_background_idle.json")
        counter.parent.mkdir(parents=True, exist_ok=True)
        counter.write_text(json.dumps(
            {self.TEAMMATE: {"count": "not-a-number", "task_id": self.TASK_ID}}),
            encoding="utf-8")
        fired = [self._idle_once(capsys) for _ in range(3)]
        assert fired == [False, False, True], (
            "with a non-numeric count on file, the advisory fired on idles %r "
            "instead of exactly the third; an unreadable count must restart the "
            "ramp at zero, not stop it" % (fired,)
        )


# ---------------------------------------------------------------------------
# A separate-process teammate's own process: no PACT context
# ---------------------------------------------------------------------------

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "teammate_idle.py"
FRAME_TEAM = "session-tiframe"
LEAD_SESSION = "ti-lead-session"
TMUX_SESSION = "ti-tmux-session"
MEMBER = "tmux-subject"
FRAME_PROJECT = "/ti-frame/project"


class TestIdleInASeparateProcessWithoutContext:
    """`python3 hooks/teammate_idle.py` with no pact-session-context.json.

    A separate-process teammate's TeammateIdle fires in its own process, which
    has no PACT context, so the team comes from the frame's `team_name` and
    `teammate_name`. No session registry entry is written, so only the frame
    route can resolve it.
    """

    @pytest.fixture
    def root(self, tmp_path):
        return tmp_path

    def _config(self, root, backend_type, lead_session=LEAD_SESSION):
        config = root / ".claude"
        (config / "teams" / FRAME_TEAM).mkdir(parents=True, exist_ok=True)
        (config / "teams" / FRAME_TEAM / "config.json").write_text(json.dumps({
            "leadSessionId": lead_session,
            "members": [{"name": MEMBER, "agentId": f"{MEMBER}@{FRAME_TEAM}",
                         "agentType": "pact-backend-coder", "backendType": backend_type}],
        }), encoding="utf-8")
        (config / "tasks" / FRAME_TEAM).mkdir(parents=True, exist_ok=True)
        return config

    def _task(self, config, status, covering_wait=False, registered_at=None):
        task = {"id": "7", "status": status, "owner": MEMBER, "subject": "CODE: frame arm"}
        if covering_wait:
            task["metadata"] = {"intentional_wait": {
                "reason": "awaiting_blocker_resolution", "expected_resolver": "lead",
                "since": registered_at, "covers_since": registered_at,
            }}
        (config / "tasks" / FRAME_TEAM / "7.json").write_text(json.dumps(task), encoding="utf-8")

    def _record(self, config):
        from shared.background_work import iso_now

        registered_at = iso_now()
        (config / "teams" / FRAME_TEAM / "background_work.json").write_text(json.dumps({
            "records": [{"agent_name": MEMBER, "session_id": TMUX_SESSION,
                         "task_ids": ["7"], "registered_at": registered_at}],
        }), encoding="utf-8")
        return registered_at

    def _idle(self, root, session_id=TMUX_SESSION) -> str:
        """One TeammateIdle through the real hook process; returns the systemMessage."""
        import os
        import subprocess
        import sys

        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID")}
        env.update(HOME=str(root), CLAUDE_CONFIG_DIR=str(root / ".claude"),
                   CLAUDE_PROJECT_DIR=FRAME_PROJECT)
        frame = {"hook_event_name": "TeammateIdle", "session_id": session_id,
                 "team_name": FRAME_TEAM, "teammate_name": MEMBER}
        proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(frame),
                              capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout or "{}")
        return out.get("systemMessage", "")

    def _records(self, config):
        path = config / "teams" / FRAME_TEAM / "background_work.json"
        return json.loads(path.read_text(encoding="utf-8"))["records"]

    def test_a_separate_process_idle_runs_the_teammate_layer_without_context(self, root):
        """REVERT PROOF. The tmux-member shape: a covering wait discharges the
        record, and no advisory, unflagged count or idle count is written."""
        import teammate_idle

        config = self._config(root, "tmux")
        registered_at = self._record(config)
        self._task(config, "in_progress", covering_wait=True, registered_at=registered_at)
        message = self._idle(root)
        assert self._records(config) == [], "the covering wait did not discharge the record"
        assert teammate_idle.UNFLAGGED_ADVISORY not in message
        unflagged = config / "teams" / FRAME_TEAM / "unflagged_background_idle.json"
        assert not unflagged.exists() or MEMBER not in json.loads(unflagged.read_text())
        assert not (config / "teams" / FRAME_TEAM / "idle_counts.json").exists()

    def test_a_separate_process_idle_advises_on_the_third_unflagged_idle(self, root):
        """REVERT PROOF. The in-process-member shape, where the stall split does
        not skip: the advisory arrives on the third idle and the record gains
        idled_at."""
        import teammate_idle

        config = self._config(root, "in-process")
        self._record(config)
        self._task(config, "in_progress")
        messages = [self._idle(root) for _ in range(3)]
        assert [teammate_idle.UNFLAGGED_ADVISORY in m for m in messages] == [False, False, True]
        (record,) = self._records(config)
        assert record.get("idled_at"), record

    def test_the_teammate_process_never_emits_the_shutdown_recommendation(self, root):
        """GUARD. Idle cleanup addresses the lead; in the teammate's own process
        five idles on a completed task show neither shutdown text nor TaskStop."""
        config = self._config(root, "tmux")
        self._task(config, "completed")
        messages = [self._idle(root) for _ in range(5)]
        assert not any("shutting down" in m or "TaskStop" in m for m in messages), messages
        assert not (config / "teams" / FRAME_TEAM / "idle_counts.json").exists()

    def test_a_lead_with_a_missing_context_file_still_runs_the_idle_cleanup(self, root):
        """The frame's session is the lead's, so this is the lead's process even
        without a context file: the cleanup still runs. Red at HEAD, which exits
        on the missing context, and red under a scope check that ignores the
        session."""
        from teammate_idle import write_idle_counts

        config = self._config(root, "in-process")
        self._task(config, "completed")
        write_idle_counts(str(config / "teams" / FRAME_TEAM / "idle_counts.json"), {MEMBER: 4})
        message = self._idle(root, session_id=LEAD_SESSION)
        assert "ACTION REQUIRED" in message and f'TaskStop("{MEMBER}")' in message, message

    def test_the_lead_process_idle_is_unchanged(self, root):
        """GUARD. With the lead's context file present, idle cleanup behaves as
        before for an in-process teammate."""
        from shared.pact_context import project_slug
        from teammate_idle import write_idle_counts

        config = self._config(root, "in-process")
        context = config / "pact-sessions" / project_slug(FRAME_PROJECT) / LEAD_SESSION
        context.mkdir(parents=True)
        (context / "pact-session-context.json").write_text(json.dumps({
            "session_id": LEAD_SESSION, "project_dir": FRAME_PROJECT, "team_name": FRAME_TEAM,
        }), encoding="utf-8")
        self._task(config, "completed")
        write_idle_counts(str(config / "teams" / FRAME_TEAM / "idle_counts.json"), {MEMBER: 4})
        message = self._idle(root, session_id=LEAD_SESSION)
        assert "ACTION REQUIRED" in message and f'TaskStop("{MEMBER}")' in message, message


def test_teammate_idle_takes_its_team_from_frame_team_and_name():
    """REVERT PROOF. main() takes its team from frame_team_and_name and its tasks
    from iter_team_task_jsons, never from the context-only get_team_name or
    get_task_list."""
    import ast

    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {
        getattr(node.func, "id", getattr(node.func, "attr", None))
        for node in ast.walk(main) if isinstance(node, ast.Call)
    }
    assert {"frame_team_and_name", "iter_team_task_jsons"} <= called, called
    assert not called & {"get_team_name", "get_task_list"}, called


# ---------------------------------------------------------------------------
# Fresh awaiting_lead_completion waits: bounded positive-inbox grace
# ---------------------------------------------------------------------------

_GRACE_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

class _FakeGraceClock:
    """Advance by each requested sleep; trip if the deadline is removed."""

    def __init__(self):
        self.sleeps = []

    def monotonic(self):
        # fsum keeps 100 x 0.1 at exactly 10.0, so float drift adds no extra poll.
        return math.fsum(self.sleeps)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if len(self.sleeps) > 100:
            raise AssertionError("grace polling exceeded its 10-second bound")


def _grace_task(
    status="in_progress",
    owner="CoderRaw",
    reason="awaiting_lead_completion",
    age: float = 0,
    covers_age: float | None = None,
):
    """A wait whose `since` is `age` old; `covers_since` defaults to the same value."""
    covers_age = age if covers_age is None else covers_age
    return make_task(
        status=status,
        owner=owner,
        metadata={"intentional_wait": {
            "reason": reason,
            "expected_resolver": "lead",
            "since": (_GRACE_NOW - timedelta(seconds=age)).isoformat(),
            "covers_since": (_GRACE_NOW - timedelta(seconds=covers_age)).isoformat(),
        }},
    )


def _call_grace(tasks, tmp_path, clock, now=_GRACE_NOW,
                team="TeamRaw", teammate="CoderRaw"):
    from teammate_idle import wait_for_inbox_evidence

    return wait_for_inbox_evidence(
        tasks,
        teammate,
        team,
        tmp_path / "teams",
        now=now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def _write_inbox(tmp_path, content, team="TeamRaw", teammate="CoderRaw"):
    path = tmp_path / "teams" / team / "inboxes" / f"{teammate}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


class TestInboxGraceGate:
    def test_already_queued_message_returns_without_sleep_and_uses_raw_names(
        self, tmp_path
    ):
        """An immediate positive returns at once, with no sleep; mixed-case names stay raw."""
        _write_inbox(tmp_path, json.dumps([{
            "from": "team-lead", "text": "wake", "timestamp": "ignored",
        }]))
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert clock.sleeps == []

    def test_message_appearing_after_empty_reads_is_seen_once(self, tmp_path):
        clock = _FakeGraceClock()
        valid = json.dumps([{"from": "team-lead", "text": "wake"}])
        with patch(
            "teammate_idle.state_file.read_text",
            side_effect=["[]", "[]", valid],
        ) as read:
            seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert read.call_count == 3
        assert clock.sleeps == [0.1, 0.1]

    def test_empty_reads_stop_at_the_fake_clock_deadline(self, tmp_path):
        """Counter-test: removing the deadline trips the fake clock bound."""
        clock = _FakeGraceClock()
        with patch("teammate_idle.state_file.read_text", return_value="[]") as read:
            seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is False
        assert read.call_count == 100
        assert len(clock.sleeps) == 100
        assert clock.sleeps == pytest.approx([0.1] * 100)
        assert clock.monotonic() == pytest.approx(10.0)

    def test_deeply_nested_inbox_is_retried_like_invalid_json(self, tmp_path):
        """Counter-test: an uncaught RecursionError escapes the hold and skips the re-read."""
        deep = "[" * 200_000 + "]" * 200_000
        with pytest.raises(RecursionError):
            json.loads(deep)
        valid = json.dumps([{"from": "team-lead", "text": "wake"}])
        clock = _FakeGraceClock()
        with patch(
            "teammate_idle.state_file.read_text", side_effect=[deep, valid]
        ) as read:
            seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert read.call_count == 2
        assert clock.sleeps == [0.1]

    def test_invalid_json_retries_within_the_remaining_grace(self, tmp_path):
        clock = _FakeGraceClock()
        valid = json.dumps([{"from": "team-lead", "text": "wake"}])
        with patch(
            "teammate_idle.state_file.read_text", side_effect=["{", valid]
        ) as read:
            seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert read.call_count == 2
        assert clock.sleeps == [0.1]

    @pytest.mark.parametrize("content", [
        "{",
        json.dumps([]),
        json.dumps([None]),
        json.dumps(["message"]),
        json.dumps([{}]),
        json.dumps([{"from": "", "text": "wake"}]),
        json.dumps([{"from": "team-lead", "text": ""}]),
        json.dumps([{"from": 3, "text": "wake"}]),
        json.dumps([{"from": "team-lead", "text": 3}]),
        json.dumps([{"text": "wake"}]),
        json.dumps([{"from": "team-lead"}]),
        json.dumps([{"from": "team-lead", "text": "wake", "read": True}]),
        '[{"from":"team-lead","text":"\\ud800"}]',
        '[{"from":"\\udfff","text":"wake"}]',
    ], ids=[
        "invalid-json", "empty-list", "null-entry", "string-entry", "empty-dict",
        "empty-from", "empty-text", "non-string-from", "non-string-text",
        "missing-from", "missing-text", "read-true", "surrogate-text",
        "surrogate-from",
    ])
    def test_retryable_content_without_a_valid_message_is_never_evidence(
        self, content, tmp_path
    ):
        """Counter-test: dropping the valid-message predicate blocks here."""
        clock = _FakeGraceClock()
        with patch("teammate_idle.state_file.read_text", return_value=content) as read:
            seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is False
        assert read.call_count == 100
        assert len(clock.sleeps) == 100

    def test_valid_message_beside_malformed_entries_is_evidence(self, tmp_path):
        clock = _FakeGraceClock()
        content = json.dumps([
            None,
            {"from": "", "text": "wake"},
            {"from": "team-lead", "text": "wake"},
        ])
        with patch("teammate_idle.state_file.read_text", return_value=content):
            seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert clock.sleeps == []

    def test_non_list_oversized_and_unreadable_inputs_fail_open(self, tmp_path):
        from shared import state_file

        for outcome in ("{}", state_file.TooLarge("over"), OSError("unreadable")):
            clock = _FakeGraceClock()
            with patch(
                "teammate_idle.state_file.read_text", side_effect=[outcome]
            ) as read:
                seen = _call_grace([_grace_task()], tmp_path, clock)

            assert seen is False
            assert read.call_count == 1
            assert read.call_args.kwargs["max_bytes"] == 1024 * 1024
            assert clock.sleeps == []

    def test_missing_then_created_inbox_is_seen_within_grace(self, tmp_path):
        valid = json.dumps([{"from": "team-lead", "text": "wake"}])

        class _CreatingClock(_FakeGraceClock):
            def sleep(self, seconds):
                super().sleep(seconds)
                _write_inbox(tmp_path, valid)

        clock = _CreatingClock()
        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert clock.sleeps == [0.1]

    def test_persistently_missing_inbox_uses_original_remaining_budget(self, tmp_path):
        clock = _FakeGraceClock()
        with patch(
            "teammate_idle.state_file.read_text",
            side_effect=FileNotFoundError("missing"),
        ) as read:
            seen = _call_grace([_grace_task(age=5.216)], tmp_path, clock)

        assert seen is False
        assert read.call_count == 48
        assert clock.sleeps[:-1] == pytest.approx([0.1] * 47)
        assert clock.sleeps[-1] == pytest.approx(0.084)

    def test_a_containment_refusal_ends_the_hold_at_once(self, tmp_path):
        """RED WHEN the hold treats state_file.Refused as a missing inbox and retries."""
        outside = tmp_path / "outside"
        (outside / "inboxes").mkdir(parents=True)
        (outside / "inboxes" / "CoderRaw.json").write_text(
            json.dumps([{"from": "team-lead", "text": "ok"}]), encoding="utf-8"
        )
        (tmp_path / "teams").mkdir()
        (tmp_path / "teams" / "TeamRaw").symlink_to(outside, target_is_directory=True)
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is False, "a message behind an out-of-root team link must never count"
        assert clock.sleeps == [], "a refusal must end the hold, not retry it"

    def test_non_missing_oserror_fails_open_without_retry(self, tmp_path):
        clock = _FakeGraceClock()
        with patch(
            "teammate_idle.state_file.read_text",
            side_effect=OSError("unreadable"),
        ) as read:
            seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is False
        assert read.call_count == 1
        assert clock.sleeps == []

    def test_undecodable_replacement_never_becomes_positive_evidence(self, tmp_path):
        """Invalid UTF-8 is retried to the deadline and never counts."""
        _write_inbox(tmp_path, b"\xff")
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is False
        assert len(clock.sleeps) == 100

    def test_half_written_non_ascii_message_is_retried_until_complete(self, tmp_path):
        """Counter-test: ending the hold on a decode error misses the finished message."""
        complete = json.dumps(
            [{"from": "team-lead", "text": "caf\u00e9"}], ensure_ascii=False
        ).encode("utf-8")
        half = complete[:complete.index(b"\xc3") + 1]
        _write_inbox(tmp_path, half)

        class _CompletingClock(_FakeGraceClock):
            def sleep(self, seconds):
                super().sleep(seconds)
                _write_inbox(tmp_path, complete)

        clock = _CompletingClock()
        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert clock.sleeps == [0.1]

    @pytest.mark.parametrize("payload", [
        b'[{"from":"team-lead","text":"\xff"}]',
        b'[{"from":"\xff","text":"wake"}]',
        b'[{"from":"team-lead","text":"wake","note":"\xff"}]',
    ], ids=["text", "from", "unrelated-field"])
    def test_replacement_decoded_valid_json_is_never_evidence(self, payload, tmp_path):
        """Invalid bytes would become valid JSON after replacement; strict decoding retries instead."""
        _write_inbox(tmp_path, payload)
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is False
        assert len(clock.sleeps) == 100

    @pytest.mark.parametrize("payload", [
        '[{"from":"team-lead","text":"\\ufffd"}]',
        '[{"from":"\\ufffd","text":"wake"}]',
        '[{"from":"team-lead","text":"wake","note":"\\ud800"}]',
        '[{"\\ud800":"x","from":"team-lead","text":"wake"}]',
        '[{"from":"team-lead","text":"wake","meta":{"\\udfff":"x"}}]',
    ], ids=[
        "replacement-text", "replacement-from", "surrogate-unrelated-value",
        "surrogate-key", "surrogate-nested-key",
    ])
    def test_escaped_replacement_and_surrogates_outside_from_and_text_still_count(
        self, payload, tmp_path
    ):
        """An escaped U+FFFD is real content, and only `from` and `text` are checked."""
        _write_inbox(tmp_path, payload)
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert clock.sleeps == []

    @pytest.mark.parametrize("bad", [
        {"from": "team-lead", "text": "\ud800"},
        {"from": "team-lead", "text": "old", "read": True},
        {"from": "", "text": "wake"},
    ], ids=["surrogate", "read", "empty-from"])
    @pytest.mark.parametrize("bad_first", [True, False], ids=["bad-first", "bad-last"])
    def test_a_bad_sibling_entry_does_not_veto_a_valid_entry(
        self, bad, bad_first, tmp_path
    ):
        valid = {"from": "team-lead", "text": "wake", "read": False}
        entries = [bad, valid] if bad_first else [valid, bad]
        clock = _FakeGraceClock()
        with patch(
            "teammate_idle.state_file.read_text", return_value=json.dumps(entries)
        ):
            seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert clock.sleeps == []

    def test_unread_flag_absent_still_is_evidence(self, tmp_path):
        _write_inbox(tmp_path, json.dumps([{"from": "team-lead", "text": "wake"}]))
        clock = _FakeGraceClock()

        assert _call_grace([_grace_task()], tmp_path, clock) is True

    def test_read_only_inbox_retries_to_the_deadline_without_evidence(self, tmp_path):
        """Counter-test: dropping the read clause blocks here on a consumed message."""
        _write_inbox(
            tmp_path,
            json.dumps([{"from": "team-lead", "text": "old", "read": True}]),
        )
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is False
        assert len(clock.sleeps) == 100

    def test_inbox_is_read_with_strict_decoding(self, tmp_path):
        clock = _FakeGraceClock()
        valid = json.dumps([{"from": "team-lead", "text": "wake"}])
        with patch("teammate_idle.state_file.read_text", return_value=valid) as read:
            _call_grace([_grace_task()], tmp_path, clock)

        assert read.call_args.kwargs["strict"] is True

    def test_escaped_ordinary_text_and_valid_non_bmp_characters_still_count(
        self, tmp_path
    ):
        _write_inbox(
            tmp_path,
            '[{"from":"team-lead","text":"\\u0077ake","note":"\\ud83d\\ude00"}]',
        )
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is True
        assert clock.sleeps == []

    @pytest.mark.parametrize("parent", ["team", "inboxes"])
    def test_symlinked_parent_directory_never_reads_external_inbox(
        self, parent, tmp_path
    ):
        outside = tmp_path / "outside"
        if parent == "team":
            external_inbox = outside / "inboxes" / "CoderRaw.json"
            link = tmp_path / "teams" / "TeamRaw"
        else:
            external_inbox = outside / "CoderRaw.json"
            link = tmp_path / "teams" / "TeamRaw" / "inboxes"
        external_inbox.parent.mkdir(parents=True)
        external_inbox.write_text(
            json.dumps([{"from": "team-lead", "text": "outside"}]),
            encoding="utf-8",
        )
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside, target_is_directory=True)
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is False
        assert clock.sleeps == []

    def test_symlinked_inbox_never_reads_its_external_target(self, tmp_path):
        outside = tmp_path / "outside.json"
        outside.write_text(
            json.dumps([{"from": "team-lead", "text": "outside"}]),
            encoding="utf-8",
        )
        inbox = _write_inbox(tmp_path, "[]")
        inbox.unlink()
        inbox.symlink_to(outside)
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task()], tmp_path, clock)

        assert seen is False
        assert clock.sleeps == []

    @pytest.mark.parametrize("team,teammate", [
        ("../escape", "CoderRaw"),
        ("TeamRaw", "../escape"),
        ("TeamRaw", ".."),
    ])
    def test_unsafe_path_components_do_no_read_or_sleep(
        self, team, teammate, tmp_path
    ):
        clock = _FakeGraceClock()
        with patch("teammate_idle.state_file.read_text") as read:
            # The owner is the unsafe name, so selection passes and the safe-name check decides.
            seen = _call_grace(
                [_grace_task(owner=teammate)], tmp_path, clock, team=team, teammate=teammate
            )

        assert seen is None
        read.assert_not_called()
        assert clock.sleeps == []

    @pytest.mark.parametrize("case", [
        "pending",
        "other-owner",
        "other-reason",
        "malformed",
        "naive",
        "future",
        "expired",
    ])
    def test_ineligible_wait_does_no_inbox_read_or_sleep(self, case, tmp_path):
        """Counter-test: broadening the wait predicate reaches this read."""
        task = _grace_task()
        if case == "pending":
            task["status"] = case
        elif case == "other-owner":
            task["owner"] = "someone-else"
        elif case == "other-reason":
            task["metadata"]["intentional_wait"]["reason"] = "awaiting_peer_response"
        elif case == "malformed":
            task["metadata"]["intentional_wait"] = "not-a-wait"
        elif case == "naive":
            task["metadata"]["intentional_wait"]["since"] = "2026-01-01T00:00:00"
        elif case == "future":
            task = _grace_task(age=-0.01)
        elif case == "expired":
            task = _grace_task(age=10.001)
        clock = _FakeGraceClock()

        with patch("teammate_idle.state_file.read_text") as read:
            seen = _call_grace([task], tmp_path, clock)

        assert seen is None
        read.assert_not_called()
        assert clock.sleeps == []

    @pytest.mark.parametrize("reverse", [False, True], ids=["wait-first", "wait-last"])
    def test_eligible_wait_is_found_with_another_active_task_in_any_order(
        self, reverse, tmp_path
    ):
        waiting = _grace_task()
        waiting["id"] = "1"
        other = make_task(task_id="2", owner="CoderRaw", status="in_progress")
        tasks = [waiting, other]
        if reverse:
            tasks.reverse()
        _write_inbox(tmp_path, json.dumps([{"from": "team-lead", "text": "wake"}]))
        clock = _FakeGraceClock()

        seen = _call_grace(tasks, tmp_path, clock)

        assert seen is True
        assert clock.sleeps == []

    def test_completed_eligible_wait_survives_another_active_task(self, tmp_path):
        waiting = _grace_task(status="completed")
        waiting["id"] = "1"
        other = make_task(task_id="2", owner="CoderRaw", status="in_progress")
        _write_inbox(tmp_path, json.dumps([{"from": "team-lead", "text": "wake"}]))
        clock = _FakeGraceClock()

        seen = _call_grace([waiting, other], tmp_path, clock)

        assert seen is True
        assert clock.sleeps == []

    def test_simultaneous_eligible_waits_use_earliest_since_then_numeric_task_id(self):
        from teammate_idle import _select_grace_task

        older = _grace_task(age=8)
        older["id"] = "20"
        newer = _grace_task(age=2)
        newer["id"] = "9"
        for tasks in ([newer, older], [older, newer]):
            selected, age = _select_grace_task(tasks, "CoderRaw", _GRACE_NOW)
            assert selected["id"] == "20"
            assert age == 8

        first = _grace_task(age=5)
        first["id"] = "20"
        second = _grace_task(age=5)
        second["id"] = "9"
        for tasks in ([first, second], [second, first]):
            selected, age = _select_grace_task(tasks, "CoderRaw", _GRACE_NOW)
            assert selected["id"] == "9"
            assert age == 5

    def test_completed_wait_sees_late_positive_evidence_within_grace(
        self, tmp_path
    ):
        """TaskUpdate-first completion must not discard the remaining grace."""
        task = _grace_task(status="completed", age=5.216)
        valid = json.dumps([{"from": "team-lead", "text": "accepted"}])
        clock = _FakeGraceClock()
        # The measured 3.112-second message is first visible to the 3.2-second poll.
        with patch(
            "teammate_idle.state_file.read_text",
            side_effect=["[]"] * 32 + [valid],
        ) as read:
            seen = _call_grace([task], tmp_path, clock)

        assert seen is True
        assert read.call_count == 33
        assert clock.sleeps == pytest.approx([0.1] * 32)
        assert clock.monotonic() == pytest.approx(3.2)

    def test_completed_wait_timeout_uses_original_remaining_budget(self, tmp_path):
        """Completed status neither restarts grace nor blocks without evidence."""
        task = _grace_task(status="completed", age=5.216)
        clock = _FakeGraceClock()
        with patch("teammate_idle.state_file.read_text", return_value="[]") as read:
            seen = _call_grace([task], tmp_path, clock)

        assert seen is False
        assert read.call_count == 48
        assert len(clock.sleeps) == 48
        assert clock.sleeps[:-1] == pytest.approx([0.1] * 47)
        assert clock.sleeps[-1] == pytest.approx(0.084)

    def test_eligible_upper_edge_reads_once_and_sleeps_the_remainder(self, tmp_path):
        """Counter-test: shrinking the eligibility window skips this read."""
        clock = _FakeGraceClock()
        with patch("teammate_idle.state_file.read_text", return_value="[]") as read:
            seen = _call_grace([_grace_task(age=9.95)], tmp_path, clock)

        assert seen is False
        assert read.call_count == 1
        assert clock.sleeps == pytest.approx([0.05])

    def test_restamped_since_keeps_the_original_covers_since_deadline(self, tmp_path):
        """A re-SET moves since but not covers_since, so grace does not restart."""
        _write_inbox(tmp_path, json.dumps([{"from": "team-lead", "text": "wake"}]))
        clock = _FakeGraceClock()
        task = _grace_task(age=0, covers_age=10.5)

        with patch("teammate_idle.state_file.read_text") as read:
            seen = _call_grace([task], tmp_path, clock)

        assert seen is None
        read.assert_not_called()
        assert clock.sleeps == []

    def test_restamped_since_inside_the_original_window_uses_its_remainder(
        self, tmp_path
    ):
        clock = _FakeGraceClock()
        task = _grace_task(age=0, covers_age=6)
        with patch("teammate_idle.state_file.read_text", return_value="[]") as read:
            seen = _call_grace([task], tmp_path, clock)

        assert seen is False
        assert read.call_count == 40
        assert clock.monotonic() == pytest.approx(4.0)

    def test_new_wait_with_matching_covers_since_gets_a_fresh_budget(self, tmp_path):
        _write_inbox(tmp_path, json.dumps([{"from": "team-lead", "text": "wake"}]))
        clock = _FakeGraceClock()

        seen = _call_grace([_grace_task(age=0.5, covers_age=0.5)], tmp_path, clock)

        assert seen is True

    def test_absent_covers_since_falls_back_to_since(self, tmp_path):
        task = _grace_task(age=9.95)
        del task["metadata"]["intentional_wait"]["covers_since"]
        clock = _FakeGraceClock()
        with patch("teammate_idle.state_file.read_text", return_value="[]") as read:
            seen = _call_grace([task], tmp_path, clock)

        assert seen is False
        assert read.call_count == 1
        assert clock.sleeps == pytest.approx([0.05])

    @pytest.mark.parametrize("covers", ["garbage", "2026-01-01T00:00:00", 3],
                             ids=["malformed", "naive", "non-string"])
    def test_present_but_invalid_covers_since_does_not_hold(self, covers, tmp_path):
        """Counter-test: falling back to a fresh since here would restart the hold."""
        task = _grace_task(age=0)
        task["metadata"]["intentional_wait"]["covers_since"] = covers
        clock = _FakeGraceClock()
        with patch("teammate_idle.state_file.read_text") as read:
            seen = _call_grace([task], tmp_path, clock)

        assert seen is None
        read.assert_not_called()
        assert clock.sleeps == []

    def test_second_idle_with_expired_wait_starts_no_new_grace(self, tmp_path):
        clock = _FakeGraceClock()
        task = _grace_task()
        with patch("teammate_idle.state_file.read_text", return_value="[]") as read:
            first = _call_grace([task], tmp_path, clock)
            second = _call_grace(
                [task], tmp_path, clock, now=_GRACE_NOW + timedelta(seconds=10)
            )

        assert first is False and second is None
        assert read.call_count == 100
        assert len(clock.sleeps) == 100


class TestMainInboxHold:
    def _run(self, capsys, in_process=True, gate=None, task=None):
        import io
        from unittest.mock import Mock
        from teammate_idle import main

        gate = gate or Mock(return_value=True)
        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "CoderRaw"}))), \
             patch("teammate_idle.iter_team_task_jsons", return_value=[task or _grace_task()]), \
             patch("teammate_idle.teammate_is_in_process", return_value=in_process), \
             patch("teammate_idle.wait_for_inbox_evidence", gate) as hold, \
             patch("teammate_idle.check_idle_cleanup", return_value=(None, False)) as cleanup, \
             patch("teammate_idle.check_unflagged_background", return_value=None):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 0
        return hold, cleanup, json.loads(capsys.readouterr().out)

    @pytest.mark.parametrize("seen", [True, False], ids=["evidence", "deadline"])
    def test_in_process_hold_returns_ordinary_output_without_a_decision(
        self, seen, capsys
    ):
        """Counter-test: restoring a block output changes this exact JSON."""
        from unittest.mock import Mock

        hold, cleanup, output = self._run(capsys, gate=Mock(return_value=seen))

        assert output == {"suppressOutput": True}
        hold.assert_called_once()
        cleanup.assert_called_once()

    def _run_rereads(self, capsys, result, in_process=True):
        import io
        from unittest.mock import Mock
        from teammate_idle import main

        before = [_grace_task()]
        after = [_grace_task(status="completed")]
        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "CoderRaw"}))), \
             patch("teammate_idle.iter_team_task_jsons", side_effect=[before, after]) as reads, \
             patch("teammate_idle.teammate_is_in_process", return_value=in_process), \
             patch("teammate_idle.wait_for_inbox_evidence", Mock(return_value=result)), \
             patch("teammate_idle.check_idle_cleanup", return_value=(None, False)) as cleanup, \
             patch("teammate_idle.check_unflagged_background", return_value=None) as advisory:
            with pytest.raises(SystemExit):
                main()
        capsys.readouterr()
        return reads, cleanup.call_args.args[0], advisory.call_args.args[0], after

    @pytest.mark.parametrize("result", [True, False], ids=["evidence", "deadline"])
    def test_a_hold_that_ran_rereads_tasks_for_cleanup_and_advisory(
        self, result, capsys
    ):
        """Counter-test: dropping the re-read leaves cleanup on the pre-hold snapshot."""
        reads, cleaned, advised, after = self._run_rereads(capsys, result)

        assert reads.call_count == 2
        assert cleaned == after and advised == after
        assert cleaned[0]["status"] == "completed"

    @pytest.mark.parametrize("result,in_process", [(None, True), (True, False)],
                             ids=["no-eligible-wait", "not-in-process"])
    def test_no_hold_keeps_the_single_task_read(self, result, in_process, capsys):
        reads, cleaned, _advised, after = self._run_rereads(capsys, result, in_process)

        assert reads.call_count == 1
        assert cleaned != after

    def test_a_raising_hold_writes_a_stderr_diagnostic_and_keeps_the_output(
        self, capsys
    ):
        """Counter-test: a silent except leaves stderr empty."""
        import io
        from unittest.mock import Mock
        from teammate_idle import main

        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "CoderRaw"}))), \
             patch("teammate_idle.iter_team_task_jsons", return_value=[_grace_task()]), \
             patch("teammate_idle.teammate_is_in_process", return_value=True), \
             patch("teammate_idle.wait_for_inbox_evidence",
                   Mock(side_effect=RuntimeError("inbox infrastructure failed"))), \
             patch("teammate_idle.check_idle_cleanup", return_value=(None, False)) as cleanup, \
             patch("teammate_idle.check_unflagged_background", return_value=None):
            with pytest.raises(SystemExit) as exc:
                main()

        captured = capsys.readouterr()
        assert exc.value.code == 0
        assert json.loads(captured.out) == {"suppressOutput": True}
        assert captured.err.strip() == (
            "Hook warning (teammate_idle): inbox infrastructure failed"
        )
        cleanup.assert_called_once()

    def test_hold_warning_is_one_bounded_line(self, capsys):
        """Counter-test: an unbounded error spills lines or length into stderr."""
        import io
        from unittest.mock import Mock
        from shared.error_output import ERROR_MAX_CHARS
        from teammate_idle import main

        long_error = "first line\nsecond\x1b[31m line\x07\r\n\t" + "x" * 5000
        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "CoderRaw"}))), \
             patch("teammate_idle.iter_team_task_jsons", return_value=[_grace_task()]), \
             patch("teammate_idle.teammate_is_in_process", return_value=True), \
             patch("teammate_idle.wait_for_inbox_evidence",
                   Mock(side_effect=RuntimeError(long_error))), \
             patch("teammate_idle.check_idle_cleanup", return_value=(None, False)), \
             patch("teammate_idle.check_unflagged_background", return_value=None):
            with pytest.raises(SystemExit):
                main()

        err = capsys.readouterr().err
        prefix = "Hook warning (teammate_idle): "
        assert err.count("\n") == 1 and err.endswith("\n")
        assert err.startswith(prefix + "first line second [31m line x")
        assert all(ch.isprintable() for ch in err[:-1]), repr(err[:80])
        assert len(err) <= len(prefix) + 200 + 1
        assert ERROR_MAX_CHARS == 200

    def test_warning_cap_is_the_shared_200_character_cap(self):
        """Counter-test: raising the public alias past 200 reds this pin."""
        from shared import error_output

        assert error_output.ERROR_MAX_CHARS == error_output._ERROR_MAX_CHARS == 200

    def test_sleep_guard_survives_a_module_reload(self, tmp_path, _no_real_grace_sleep):
        """Counter-test: a guard on one function's defaults is lost on reload and sleeps 10s."""
        import importlib.util
        import teammate_idle

        # A fresh, private execution of the hook file, as a reload would do; the
        # shared sys.modules entry is never touched, so no other test sees it.
        spec = importlib.util.spec_from_file_location(
            "_teammate_idle_reexecuted", teammate_idle.__file__
        )
        reloaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reloaded)
        assert reloaded is not teammate_idle
        with pytest.raises(AssertionError, match="real grace sleep reached"):
            reloaded.wait_for_inbox_evidence(
                [_grace_task()], "CoderRaw", "TeamRaw", tmp_path / "teams", now=_GRACE_NOW
            )

        assert _no_real_grace_sleep == [0.1]
        _no_real_grace_sleep.clear()

    def test_pane_or_undeterminable_mode_does_not_hold(self, capsys):
        hold, cleanup, output = self._run(capsys, in_process=False)

        assert output == {"suppressOutput": True}
        hold.assert_not_called()
        cleanup.assert_called_once()

    def test_completed_task_keeps_ordinary_cleanup_after_the_hold(
        self, capsys, tmp_path, monkeypatch
    ):
        """A completed task's fresh wait runs the REAL hold, and ordinary cleanup
        still follows on the post-hold re-read. Counter-test: making completed
        tasks ineligible skips the hold, so there is no re-read."""
        import io
        import teammate_idle
        from teammate_idle import main

        fixed_now = datetime.now(timezone.utc)
        since = fixed_now.isoformat()
        task = make_task(status="completed", owner="CoderRaw", metadata={"intentional_wait": {
            "reason": "awaiting_lead_completion", "expected_resolver": "lead",
            "since": since, "covers_since": since,
        }})
        inbox = tmp_path / "teams" / "pact-test" / "inboxes" / "CoderRaw.json"
        inbox.parent.mkdir(parents=True)
        inbox.write_text(json.dumps([{"from": "team-lead", "text": "wake"}]), encoding="utf-8")
        clock = _FakeGraceClock()
        for name, value in (("sleep", clock.sleep), ("monotonic", clock.monotonic),
                            ("now", fixed_now)):
            monkeypatch.setitem(
                teammate_idle.wait_for_inbox_evidence.__kwdefaults__, name, value
            )
        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "CoderRaw"}))), \
             patch("teammate_idle.get_claude_config_dir", return_value=tmp_path), \
             patch("teammate_idle.iter_team_task_jsons", return_value=[task]) as reads, \
             patch("teammate_idle.teammate_is_in_process", return_value=True), \
             patch("teammate_idle.check_idle_cleanup", return_value=(None, False)) as cleanup, \
             patch("teammate_idle.check_unflagged_background", return_value=None):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out) == {"suppressOutput": True}
        assert reads.call_count == 2, "the real hold did not run for a completed task"
        cleanup.assert_called_once()
        assert cleanup.call_args.args[0][0]["status"] == "completed"

    def test_main_fails_open_when_the_hold_errors(self, capsys):
        from unittest.mock import Mock

        _hold, cleanup, output = self._run(
            capsys, gate=Mock(side_effect=RuntimeError("inbox infrastructure failed"))
        )

        assert output == {"suppressOutput": True}
        cleanup.assert_called_once()

    @pytest.mark.parametrize("path", ["no-hold", "message", "deadline"])
    def test_no_path_emits_a_decision(self, path, capsys, tmp_path, monkeypatch):
        """The runtime never delivers a queued message into a blocked continuation,
        so main()'s real output carries no decision whether the real hold is
        skipped, ends on a message, or runs to its deadline."""
        import io
        import teammate_idle
        from teammate_idle import main

        # One fixed wall clock for since and the hold, so setup time cannot age the wait.
        fixed_now = datetime.now(timezone.utc)
        age = 9.95 if path == "deadline" else 0
        since = (fixed_now - timedelta(seconds=age)).isoformat()
        task = make_task(owner="CoderRaw", metadata={"intentional_wait": {
            "reason": "awaiting_lead_completion", "expected_resolver": "lead",
            "since": since, "covers_since": since,
        }})
        inbox = tmp_path / "teams" / "pact-test" / "inboxes" / "CoderRaw.json"
        inbox.parent.mkdir(parents=True)
        queued = [{"from": "team-lead", "text": "wake"}] if path == "message" else []
        inbox.write_text(json.dumps(queued), encoding="utf-8")
        clock = _FakeGraceClock()
        monkeypatch.setitem(
            teammate_idle.wait_for_inbox_evidence.__kwdefaults__, "sleep", clock.sleep
        )
        monkeypatch.setitem(
            teammate_idle.wait_for_inbox_evidence.__kwdefaults__,
            "monotonic", clock.monotonic,
        )
        monkeypatch.setitem(
            teammate_idle.wait_for_inbox_evidence.__kwdefaults__, "now", fixed_now
        )
        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(json.dumps({"teammate_name": "CoderRaw"}))), \
             patch("teammate_idle.get_claude_config_dir", return_value=tmp_path), \
             patch("teammate_idle.iter_team_task_jsons", return_value=[task]), \
             patch("teammate_idle.teammate_is_in_process", return_value=path != "no-hold"), \
             patch("teammate_idle.check_idle_cleanup", return_value=(None, False)), \
             patch("teammate_idle.check_unflagged_background", return_value=None):
            with pytest.raises(SystemExit) as exc:
                main()

        output = json.loads(capsys.readouterr().out)
        assert exc.value.code == 0
        assert "decision" not in output, output
        assert output == {"suppressOutput": True}
        # The deadline case must have really held, or it would prove nothing.
        assert (len(clock.sleeps) > 0) is (path == "deadline"), clock.sleeps

