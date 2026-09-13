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

    def test_shutdown_message_advises_taskstop_directly(self, capsys, tmp_path):
        """At force threshold, output must include ACTION REQUIRED + a direct
        TaskStop instruction for the team-lead to act on."""
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
