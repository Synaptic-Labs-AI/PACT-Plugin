"""
Tests for shared/session_journal.py -- append-only JSONL event store
for GC-proof workflow state persistence.

Tests cover:

make_event():
1. Sets v=1 schema version
2. Sets ts to current UTC time (ISO 8601)
3. Passes through type-specific keyword fields
4. Timestamp is always last (ts set after user fields)

append_event():
5. Write event, read it back, fields match (roundtrip)
6. Creates directory when teams dir doesn't exist (mkdir -p)
7. Returns False when v field is missing
8. Returns False when v field is not an int
8a. Returns False when v is True (bool subclass of int)
8b. Returns False when v is False (bool subclass of int)
9. Returns False when type field is missing
10. Returns False when type field is empty string
11. Returns False when team_name is empty
12. Returns False on write error (fail-open, no exception)
13. Auto-sets ts when missing from event
14. Creates file with 0o600 permissions
15. Creates directory with 0o700 permissions

read_events():
16. Returns all events when no type filter
17. Returns only matching events when type filter applied
18. Returns empty list when journal file missing
19. Skips malformed JSON lines
20. Skips empty lines
21. Returns empty list on outer exception (fail-open)
22. Returns events in chronological order

read_last_event():
23. Returns the most recent event of given type
24. Returns None when no matching events
25. Returns None when journal is missing

get_journal_path():
26. Returns correct absolute path string
27. Does not check file existence

Concurrency (P1):
28. Multiple threads can append without interleaving
29. Large event (~3KB) writes and reads correctly

Integration:
30. _build_journal_resume produces correct markdown from events
31. _build_journal_resume returns None for empty journal
32. _build_journal_resume includes handoff decisions (truncated)
33. _build_journal_resume includes phase progress
34. _build_journal_resume includes session_end warnings
35. _check_journal_paused_state returns None when no paused events
36. _check_journal_paused_state returns formatted context for paused PR
37. _check_journal_paused_state includes consolidation warning
38. _check_journal_paused_state handles stale (>14 day) events
39. _check_journal_paused_state returns None when pr_number is None
40. _check_journal_paused_state handles MERGED/CLOSED PR
41. restore_last_session prefers journal over slug-level fallback
42. check_paused_state prefers journal over slug-level fallback
43. _extract_prev_team_name returns None on IOError (fail-open)
"""
import json
import os
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def journal_home(tmp_path, monkeypatch):
    """Redirect Path.home() to tmp_path for filesystem isolation."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def team_name():
    """Standard test team name."""
    return "pact-test1234"


@pytest.fixture
def journal_file(journal_home, team_name):
    """Return the expected journal file path (does not create it)."""
    return journal_home / ".claude" / "teams" / team_name / "session-journal.jsonl"


# ---------------------------------------------------------------------------
# make_event()
# ---------------------------------------------------------------------------


class TestMakeEvent:
    """Tests for make_event() -- event construction with defaults."""

    def test_sets_schema_version(self):
        from shared.session_journal import make_event

        event = make_event("session_start")
        assert event["v"] == 1

    def test_sets_utc_timestamp(self):
        from shared.session_journal import make_event

        before = datetime.now(timezone.utc).replace(microsecond=0)
        event = make_event("session_start")
        after = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)

        ts = datetime.strptime(event["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        assert before <= ts <= after

    def test_passes_through_custom_fields(self):
        from shared.session_journal import make_event

        event = make_event("agent_handoff", agent="backend-coder", task_id="7")
        assert event["type"] == "agent_handoff"
        assert event["agent"] == "backend-coder"
        assert event["task_id"] == "7"

    def test_ts_is_set_last(self):
        """Timestamp should not be overridden by user-provided ts in kwargs."""
        from shared.session_journal import make_event

        # Even if caller passes ts, make_event overwrites it with current time
        event = make_event("test", ts="user-provided")
        assert event["ts"] != "user-provided"
        # ts should be a valid ISO 8601 string
        assert event["ts"].endswith("Z")


# ---------------------------------------------------------------------------
# append_event()
# ---------------------------------------------------------------------------


class TestAppendEvent:
    """Tests for append_event() -- atomic JSONL append."""

    def test_roundtrip_append_and_read(self, journal_home, team_name):
        """P0: Write event, read it back, all fields match."""
        from shared.session_journal import append_event, make_event, read_events

        event = make_event(
            "agent_handoff",
            agent="backend-coder",
            task_id="7",
            task_subject="CODE: implement auth",
            handoff={"produced": ["src/auth.ts"]},
        )

        result = append_event(event, team_name)
        assert result is True

        events = read_events(team_name)
        assert len(events) == 1

        read_back = events[0]
        assert read_back["v"] == 1
        assert read_back["type"] == "agent_handoff"
        assert read_back["agent"] == "backend-coder"
        assert read_back["task_id"] == "7"
        assert read_back["handoff"]["produced"] == ["src/auth.ts"]
        assert "ts" in read_back

    def test_creates_directory(self, journal_home, team_name, journal_file):
        """P0: mkdir -p behavior when teams dir doesn't exist."""
        from shared.session_journal import append_event, make_event

        assert not journal_file.parent.exists()

        event = make_event("session_start", team="test")
        result = append_event(event, team_name)

        assert result is True
        assert journal_file.exists()

    def test_rejects_missing_v(self, journal_home, team_name):
        """P0: Returns False when v field is missing."""
        from shared.session_journal import append_event

        event = {"type": "test", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event, team_name)
        assert result is False

    def test_rejects_non_int_v(self, journal_home, team_name):
        """P0: Returns False when v is not an int."""
        from shared.session_journal import append_event

        event = {"v": "1", "type": "test", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event, team_name)
        assert result is False

    def test_rejects_bool_v_true(self, journal_home, team_name):
        """P0: Returns False when v is True (bool is subclass of int)."""
        from shared.session_journal import append_event

        event = {"v": True, "type": "test", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event, team_name)
        assert result is False

    def test_rejects_bool_v_false(self, journal_home, team_name):
        """P0: Returns False when v is False (bool is subclass of int)."""
        from shared.session_journal import append_event

        event = {"v": False, "type": "test", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event, team_name)
        assert result is False

    def test_rejects_missing_type(self, journal_home, team_name):
        """P0: Returns False when type field is missing."""
        from shared.session_journal import append_event

        event = {"v": 1, "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event, team_name)
        assert result is False

    def test_rejects_empty_type(self, journal_home, team_name):
        """P0: Returns False when type is empty string."""
        from shared.session_journal import append_event

        event = {"v": 1, "type": "", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event, team_name)
        assert result is False

    def test_rejects_empty_team_name(self, journal_home):
        """P0: Returns False when team_name is empty."""
        from shared.session_journal import append_event, make_event

        event = make_event("test")
        result = append_event(event, "")
        assert result is False

    def test_fail_open_on_write_error(self, journal_home, team_name):
        """P0: Returns False on write error, no exception raised."""
        from shared.session_journal import append_event, make_event

        event = make_event("test")

        # Force a write error by making the parent read-only
        journal_dir = journal_home / ".claude" / "teams" / team_name
        journal_dir.mkdir(parents=True)
        os.chmod(str(journal_dir), 0o444)

        try:
            result = append_event(event, team_name)
            # Should return False, not raise
            assert result is False
        finally:
            # Restore permissions for cleanup
            os.chmod(str(journal_dir), 0o755)

    def test_auto_sets_ts_when_missing(self, journal_home, team_name):
        """P0: Auto-sets timestamp when ts is not in the event dict."""
        from shared.session_journal import append_event, read_events

        event = {"v": 1, "type": "test_auto_ts"}
        result = append_event(event, team_name)
        assert result is True

        events = read_events(team_name)
        assert len(events) == 1
        assert "ts" in events[0]
        assert events[0]["ts"].endswith("Z")

    def test_file_permissions(self, journal_home, team_name, journal_file):
        """File should be created with 0o600 permissions."""
        from shared.session_journal import append_event, make_event

        event = make_event("test")
        append_event(event, team_name)

        assert journal_file.exists()
        stat = journal_file.stat()
        # Check file permission bits (masking away file type bits)
        assert stat.st_mode & 0o777 == 0o600

    def test_directory_permissions(self, journal_home, team_name, journal_file):
        """Parent directory should be created with 0o700 permissions."""
        from shared.session_journal import append_event, make_event

        event = make_event("test")
        append_event(event, team_name)

        dir_stat = journal_file.parent.stat()
        assert dir_stat.st_mode & 0o777 == 0o700

    def test_multiple_appends_sequential(self, journal_home, team_name):
        """Multiple sequential appends produce separate lines."""
        from shared.session_journal import append_event, make_event, read_events

        for i in range(5):
            event = make_event("test", seq=i)
            append_event(event, team_name)

        events = read_events(team_name)
        assert len(events) == 5
        for i, event in enumerate(events):
            assert event["seq"] == i


# ---------------------------------------------------------------------------
# read_events()
# ---------------------------------------------------------------------------


class TestReadEvents:
    """Tests for read_events() -- JSONL reading with type filtering."""

    def test_returns_all_events_without_filter(self, journal_home, team_name):
        """P0: Returns all events when no type filter."""
        from shared.session_journal import append_event, make_event, read_events

        append_event(make_event("session_start", team="t1"), team_name)
        append_event(make_event("agent_handoff", agent="coder"), team_name)
        append_event(make_event("session_end"), team_name)

        events = read_events(team_name)
        assert len(events) == 3
        assert events[0]["type"] == "session_start"
        assert events[1]["type"] == "agent_handoff"
        assert events[2]["type"] == "session_end"

    def test_filters_by_type(self, journal_home, team_name):
        """P0: Returns only matching events when type filter applied."""
        from shared.session_journal import append_event, make_event, read_events

        append_event(make_event("session_start", team="t1"), team_name)
        append_event(make_event("agent_handoff", agent="coder1"), team_name)
        append_event(make_event("phase_transition", phase="CODE"), team_name)
        append_event(make_event("agent_handoff", agent="coder2"), team_name)

        handoffs = read_events(team_name, event_type="agent_handoff")
        assert len(handoffs) == 2
        assert handoffs[0]["agent"] == "coder1"
        assert handoffs[1]["agent"] == "coder2"

    def test_returns_empty_for_missing_file(self, journal_home, team_name):
        """P0: Returns empty list when journal file doesn't exist."""
        from shared.session_journal import read_events

        events = read_events(team_name)
        assert events == []

    def test_skips_malformed_lines(self, journal_home, team_name, journal_file):
        """P0: Malformed JSON lines are silently skipped."""
        from shared.session_journal import append_event, make_event, read_events

        # Write a valid event first
        append_event(make_event("test", seq=1), team_name)

        # Inject a malformed line directly
        with open(str(journal_file), "a") as f:
            f.write("this is not json\n")
            f.write('{"v":1,"type":"test","seq":2,"ts":"2026-01-01T00:00:00Z"}\n')

        events = read_events(team_name)
        assert len(events) == 2
        assert events[0]["seq"] == 1
        assert events[1]["seq"] == 2

    def test_skips_empty_lines(self, journal_home, team_name, journal_file):
        """Empty lines in journal are silently skipped."""
        from shared.session_journal import read_events

        # Write events with empty lines interspersed
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        with open(str(journal_file), "w") as f:
            f.write('{"v":1,"type":"test","seq":1,"ts":"2026-01-01T00:00:00Z"}\n')
            f.write("\n")
            f.write("   \n")
            f.write('{"v":1,"type":"test","seq":2,"ts":"2026-01-01T00:00:00Z"}\n')

        events = read_events(team_name)
        assert len(events) == 2

    def test_fail_open_on_outer_exception(self, journal_home, team_name):
        """Returns empty list on unexpected exception (fail-open)."""
        from shared.session_journal import read_events

        with patch("shared.session_journal._journal_path", side_effect=RuntimeError("boom")):
            events = read_events(team_name)
            assert events == []

    def test_chronological_order(self, journal_home, team_name):
        """Events are returned in the order they were written."""
        from shared.session_journal import append_event, make_event, read_events

        for label in ["alpha", "beta", "gamma"]:
            append_event(make_event("test", label=label), team_name)

        events = read_events(team_name)
        assert [e["label"] for e in events] == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# read_last_event()
# ---------------------------------------------------------------------------


class TestReadLastEvent:
    """Tests for read_last_event() -- most recent event of a given type."""

    def test_returns_most_recent(self, journal_home, team_name):
        """P0: Returns the last matching event."""
        from shared.session_journal import append_event, make_event, read_last_event

        append_event(make_event("checkpoint", data="old"), team_name)
        append_event(make_event("agent_handoff", agent="coder"), team_name)
        append_event(make_event("checkpoint", data="new"), team_name)

        last = read_last_event(team_name, "checkpoint")
        assert last is not None
        assert last["data"] == "new"

    def test_returns_none_when_no_match(self, journal_home, team_name):
        """P0: Returns None when no events match the type."""
        from shared.session_journal import append_event, make_event, read_last_event

        append_event(make_event("session_start", team="t"), team_name)

        result = read_last_event(team_name, "checkpoint")
        assert result is None

    def test_returns_none_for_missing_journal(self, journal_home, team_name):
        """P0: Returns None when journal file doesn't exist."""
        from shared.session_journal import read_last_event

        result = read_last_event(team_name, "checkpoint")
        assert result is None


# ---------------------------------------------------------------------------
# get_journal_path()
# ---------------------------------------------------------------------------


class TestGetJournalPath:
    """Tests for get_journal_path() -- path string helper."""

    def test_returns_correct_path(self, journal_home, team_name):
        from shared.session_journal import get_journal_path

        path = get_journal_path(team_name)
        expected = str(journal_home / ".claude" / "teams" / team_name / "session-journal.jsonl")
        assert path == expected

    def test_does_not_require_file_existence(self, journal_home):
        """Should return path even when file doesn't exist."""
        from shared.session_journal import get_journal_path

        path = get_journal_path("nonexistent-team")
        assert "nonexistent-team" in path
        assert not Path(path).exists()


# ---------------------------------------------------------------------------
# P1: Concurrency and Large Events
# ---------------------------------------------------------------------------


class TestConcurrentAppends:
    """P1: Thread-safety of concurrent append operations."""

    def test_concurrent_threads_no_interleaving(self, journal_home, team_name):
        """Multiple threads appending simultaneously produce valid JSONL."""
        from shared.session_journal import append_event, make_event, read_events

        num_threads = 10
        events_per_thread = 20
        errors = []

        def writer(thread_id):
            try:
                for seq in range(events_per_thread):
                    event = make_event("test", thread=thread_id, seq=seq)
                    result = append_event(event, team_name)
                    if not result:
                        errors.append(f"Thread {thread_id} seq {seq} failed")
            except Exception as e:
                errors.append(f"Thread {thread_id}: {e}")

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

        events = read_events(team_name)
        assert len(events) == num_threads * events_per_thread

        # Verify each event is complete (no interleaved JSON)
        for event in events:
            assert "v" in event
            assert "type" in event
            assert "thread" in event
            assert "seq" in event

    def test_large_event_integrity(self, journal_home, team_name):
        """P1: A ~3KB event writes and reads back correctly."""
        from shared.session_journal import append_event, make_event, read_events

        # Create a large handoff payload (~3KB)
        large_handoff = {
            "produced": [f"src/file_{i}.ts" for i in range(50)],
            "decisions": [
                f"Decision {i}: " + "x" * 40 for i in range(20)
            ],
            "uncertainty": [f"Risk {i}: potential issue with " + "y" * 30 for i in range(10)],
            "integration": [f"Component_{i}" for i in range(15)],
            "open_questions": [f"Q{i}: Should we " + "z" * 20 for i in range(5)],
        }

        event = make_event(
            "agent_handoff",
            agent="backend-coder",
            task_id="42",
            handoff=large_handoff,
        )

        # Verify the event is indeed large
        serialized = json.dumps(event)
        assert len(serialized) > 2000, f"Event only {len(serialized)} bytes, expected >2KB"

        result = append_event(event, team_name)
        assert result is True

        events = read_events(team_name)
        assert len(events) == 1
        assert events[0]["handoff"]["produced"] == large_handoff["produced"]
        assert len(events[0]["handoff"]["decisions"]) == 20


# ---------------------------------------------------------------------------
# Integration: _build_journal_resume()
# ---------------------------------------------------------------------------


class TestBuildJournalResume:
    """Integration tests for _build_journal_resume() in session_resume.py."""

    def test_returns_none_for_empty_journal(self, journal_home, team_name):
        """Returns None when no events exist in the journal."""
        from shared.session_resume import _build_journal_resume

        result = _build_journal_resume(team_name)
        assert result is None

    def test_returns_none_for_nonexistent_team(self, journal_home):
        """Returns None for a team with no journal file."""
        from shared.session_resume import _build_journal_resume

        result = _build_journal_resume("nonexistent-team-xyz")
        assert result is None

    def test_includes_handoff_summary(self, journal_home, team_name):
        """Produces resume with agent handoffs and first decision."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        append_event(
            make_event(
                "agent_handoff",
                agent="backend-coder",
                task_subject="CODE: implement auth",
                handoff={"decisions": ["Used JWT for token-based auth"]},
            ),
            team_name,
        )

        result = _build_journal_resume(team_name)
        assert result is not None
        assert "Previous session summary" in result
        assert "journal" in result
        assert "## Completed Work" in result
        assert "backend-coder" in result
        assert "CODE: implement auth" in result
        assert "Used JWT" in result

    def test_truncates_long_decisions(self, journal_home, team_name):
        """Decision summaries longer than 80 chars are truncated to 77+..."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        long_decision = "A" * 100

        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_subject="CODE: test",
                handoff={"decisions": [long_decision]},
            ),
            team_name,
        )

        result = _build_journal_resume(team_name)
        assert result is not None
        # Should be truncated to 77 chars + "..."
        assert "A" * 77 + "..." in result
        assert "A" * 100 not in result

    def test_includes_phase_progress(self, journal_home, team_name):
        """Includes completed and in-progress phases."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        append_event(
            make_event("phase_transition", phase="PREPARE", status="completed"),
            team_name,
        )
        append_event(
            make_event("phase_transition", phase="ARCHITECT", status="completed"),
            team_name,
        )
        append_event(
            make_event("phase_transition", phase="CODE", status="started"),
            team_name,
        )

        result = _build_journal_resume(team_name)
        assert result is not None
        assert "Completed phases: PREPARE, ARCHITECT" in result
        assert "Last active phase: CODE" in result

    def test_includes_session_end_warnings(self, journal_home, team_name):
        """Includes warnings from session_end events."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        append_event(make_event("session_start", team=team_name), team_name)
        append_event(
            make_event(
                "session_end",
                warning="Session ended without memory consolidation. PR #42 is open.",
            ),
            team_name,
        )

        result = _build_journal_resume(team_name)
        assert result is not None
        assert "**Warning**" in result
        assert "PR #42" in result

    def test_handoff_without_decisions(self, journal_home, team_name):
        """Handoff with no decisions should still appear, just without summary."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        append_event(
            make_event(
                "agent_handoff",
                agent="preparer",
                task_subject="PREPARE: research",
                handoff={"produced": ["docs/prep.md"]},
            ),
            team_name,
        )

        result = _build_journal_resume(team_name)
        assert result is not None
        assert "preparer: PREPARE: research" in result

    def test_multiple_handoffs(self, journal_home, team_name):
        """Multiple agent handoffs are all listed."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        for agent in ["preparer", "architect", "backend-coder"]:
            append_event(
                make_event(
                    "agent_handoff",
                    agent=agent,
                    task_subject=f"Task for {agent}",
                    handoff={"decisions": [f"{agent} decision"]},
                ),
                team_name,
            )

        result = _build_journal_resume(team_name)
        assert result is not None
        assert "preparer" in result
        assert "architect" in result
        assert "backend-coder" in result


# ---------------------------------------------------------------------------
# Integration: _check_journal_paused_state()
# ---------------------------------------------------------------------------


class TestCheckJournalPausedState:
    """Integration tests for _check_journal_paused_state() in session_resume.py."""

    def test_returns_none_when_no_paused_events(self, journal_home, team_name):
        """Returns None when no session_paused events exist."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        # Write some non-paused events
        append_event(make_event("session_start", team=team_name), team_name)

        result = _check_journal_paused_state(team_name)
        assert result is None

    def test_returns_none_for_nonexistent_team(self, journal_home):
        """Returns None for a team with no journal."""
        from shared.session_resume import _check_journal_paused_state

        result = _check_journal_paused_state("nonexistent-team")
        assert result is None

    def test_returns_formatted_context(self, journal_home, team_name):
        """Returns formatted paused work context with PR details."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=42,
                branch="feat/my-feature",
                worktree_path="/Users/dev/project/.worktrees/feat/my-feature",
                consolidation_completed=True,
            ),
            team_name,
        )

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = _check_journal_paused_state(team_name)

        assert result is not None
        assert "PR #42" in result
        assert "feat/my-feature" in result
        assert "Paused work detected" in result

    def test_includes_consolidation_warning(self, journal_home, team_name):
        """Includes memory consolidation warning when not completed."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=99,
                branch="feat/test",
                worktree_path="/tmp/wt",
                consolidation_completed=False,
            ),
            team_name,
        )

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = _check_journal_paused_state(team_name)

        assert result is not None
        assert "Memory consolidation did NOT complete" in result

    def test_no_consolidation_warning_when_completed(self, journal_home, team_name):
        """No consolidation warning when consolidation_completed is True."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=99,
                branch="feat/test",
                worktree_path="/tmp/wt",
                consolidation_completed=True,
            ),
            team_name,
        )

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = _check_journal_paused_state(team_name)

        assert result is not None
        assert "consolidation" not in result.lower()

    def test_returns_none_when_pr_number_is_none(self, journal_home, team_name):
        """Returns None when the paused event has no pr_number."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                branch="feat/test",
                worktree_path="/tmp/wt",
            ),
            team_name,
        )

        result = _check_journal_paused_state(team_name)
        assert result is None

    def test_stale_event_older_than_14_days(self, journal_home, team_name, journal_file):
        """Returns stale message for events older than 14 days."""
        from shared.session_resume import _check_journal_paused_state

        # Write a paused event with an old timestamp directly
        old_ts = (datetime.now(timezone.utc) - timedelta(days=15)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        event = {
            "v": 1,
            "type": "session_paused",
            "pr_number": 55,
            "branch": "feat/old",
            "worktree_path": "/tmp/old",
            "ts": old_ts,
        }
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        with open(str(journal_file), "w") as f:
            f.write(json.dumps(event) + "\n")

        result = _check_journal_paused_state(team_name)
        assert result is not None
        assert "Stale" in result or "older than 14 days" in result
        assert "PR #55" in result

    def test_merged_pr_returns_info(self, journal_home, team_name):
        """Returns informational message when PR is MERGED."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=77,
                branch="feat/done",
                worktree_path="/tmp/done",
            ),
            team_name,
        )

        with patch("shared.session_resume._check_pr_state", return_value="MERGED"):
            result = _check_journal_paused_state(team_name)

        assert result is not None
        assert "merged" in result.lower()
        assert "PR #77" in result

    def test_closed_pr_returns_info(self, journal_home, team_name):
        """Returns informational message when PR is CLOSED."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=88,
                branch="feat/abandoned",
                worktree_path="/tmp/abandoned",
            ),
            team_name,
        )

        with patch("shared.session_resume._check_pr_state", return_value="CLOSED"):
            result = _check_journal_paused_state(team_name)

        assert result is not None
        assert "closed" in result.lower()


# ---------------------------------------------------------------------------
# Integration: restore_last_session() and check_paused_state() prefer journal
# ---------------------------------------------------------------------------


class TestJournalPreference:
    """Tests that restore_last_session and check_paused_state prefer journal."""

    def test_restore_prefers_journal_over_slug(self, journal_home, team_name, tmp_path):
        """restore_last_session uses journal when prev_team_name is available."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import restore_last_session

        # Set up journal with handoff events
        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_subject="CODE: feature",
                handoff={"decisions": ["Built feature X"]},
            ),
            team_name,
        )

        # Also set up slug-level fallback (should be ignored)
        sessions_dir = tmp_path / "sessions"
        slug_dir = sessions_dir / "my-project"
        slug_dir.mkdir(parents=True)
        (slug_dir / "last-session.md").write_text("Slug-level content")

        result = restore_last_session(
            project_slug="my-project",
            sessions_dir=str(sessions_dir),
            prev_team_name=team_name,
        )

        assert result is not None
        assert "journal" in result  # Journal-based resume header
        assert "Slug-level content" not in result

    def test_restore_falls_back_to_slug(self, journal_home, tmp_path):
        """restore_last_session falls back to slug when no prev_team_name."""
        from shared.session_resume import restore_last_session

        sessions_dir = tmp_path / "sessions"
        slug_dir = sessions_dir / "my-project"
        slug_dir.mkdir(parents=True)
        (slug_dir / "last-session.md").write_text("Slug-level content here")

        result = restore_last_session(
            project_slug="my-project",
            sessions_dir=str(sessions_dir),
            prev_team_name=None,
        )

        assert result is not None
        assert "Slug-level content here" in result

    def test_check_paused_prefers_journal(self, journal_home, team_name, tmp_path):
        """check_paused_state uses journal when prev_team_name is available."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import check_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=42,
                branch="feat/test",
                worktree_path="/tmp/wt",
                consolidation_completed=True,
            ),
            team_name,
        )

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = check_paused_state(
                project_slug="my-project",
                sessions_dir=str(tmp_path),
                prev_team_name=team_name,
            )

        assert result is not None
        assert "PR #42" in result

    def test_check_paused_falls_back_to_slug(self, journal_home, tmp_path):
        """check_paused_state falls back to slug when no journal paused events."""
        from shared.session_resume import check_paused_state

        # Create slug-level paused-state.json
        slug_dir = tmp_path / "sessions" / "my-project"
        slug_dir.mkdir(parents=True)
        state = {
            "pr_number": 99,
            "branch": "feat/slug",
            "worktree_path": "/tmp/slug-wt",
            "consolidation_completed": True,
        }
        (slug_dir / "paused-state.json").write_text(json.dumps(state))

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = check_paused_state(
                project_slug="my-project",
                sessions_dir=str(tmp_path / "sessions"),
                prev_team_name=None,
            )

        assert result is not None
        assert "PR #99" in result


# ---------------------------------------------------------------------------
# Integration: handoff_gate journal write
# ---------------------------------------------------------------------------


class TestHandoffGateJournalWrite:
    """Tests that handoff_gate.main() writes agent_handoff event to journal."""

    def _run_main(self, input_data, task_data=None):
        """Helper to run handoff_gate.main() with given stdin and task file."""
        import io
        from unittest.mock import patch as _patch

        if task_data is None:
            task_data = {}

        stdin = io.StringIO(json.dumps(input_data))

        with _patch("sys.stdin", stdin), \
             _patch("handoff_gate._read_task_json", return_value=task_data), \
             _patch("handoff_gate.pact_context") as mock_ctx, \
             _patch("handoff_gate.get_team_name", return_value="pact-test1234"), \
             _patch("handoff_gate.append_event", return_value=True) as mock_append, \
             _patch("handoff_gate.make_event", wraps=None) as mock_make:

            # make_event needs to return a dict for append_event
            mock_make.return_value = {"v": 1, "type": "agent_handoff", "ts": "2026-01-01T00:00:00Z"}

            with pytest.raises(SystemExit) as exc:
                from handoff_gate import main
                main()

            return exc.value.code, mock_append, mock_make

    def test_writes_journal_on_valid_handoff(self):
        """Writes agent_handoff event when all gates pass."""
        input_data = {
            "task_id": "7",
            "task_subject": "CODE: auth",
            "team_name": "pact-test1234",
        }
        task_data = {
            "owner": "backend-coder",
            "metadata": {
                "handoff": {
                    "produced": ["src/auth.ts"],
                    "decisions": ["Used JWT"],
                    "uncertainty": [],
                    "integration": ["UserService"],
                    "open_questions": [],
                },
                "memory_saved": True,
            },
        }

        exit_code, mock_append, mock_make = self._run_main(input_data, task_data)

        assert exit_code == 0
        mock_append.assert_called_once()

    def test_no_journal_write_on_blocked(self):
        """Does NOT write journal event when handoff validation blocks."""
        input_data = {
            "task_id": "7",
            "task_subject": "CODE: auth",
            "team_name": "pact-test1234",
        }
        task_data = {
            "owner": "backend-coder",
            "metadata": {},  # Missing handoff -> blocks
        }

        exit_code, mock_append, _ = self._run_main(input_data, task_data)

        assert exit_code == 2  # Blocked
        mock_append.assert_not_called()


# ---------------------------------------------------------------------------
# Integration: session_init journal write
# ---------------------------------------------------------------------------


class TestSessionInitJournalWrite:
    """Tests that session_init.main() writes session_start event to journal."""

    def test_writes_session_start_event(self, journal_home, monkeypatch):
        """session_init.main() writes session_start event to journal."""
        import io

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(journal_home / "project"))
        (journal_home / "project").mkdir()
        (journal_home / "project" / "CLAUDE.md").write_text("# Project\n## Retrieved Context\n")

        input_data = {
            "session_id": "abc12345-test",
            "source": "startup",
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.get_task_list", return_value=[]), \
             patch("session_init.write_context"), \
             patch("session_init.check_resumption_context", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None):

            with pytest.raises(SystemExit) as exc:
                from session_init import main
                main()

            assert exc.value.code == 0

        # Verify session_start event was written to journal
        from shared.session_journal import read_events

        events = read_events("pact-abc12345")
        assert len(events) >= 1
        start_events = [e for e in events if e["type"] == "session_start"]
        assert len(start_events) == 1
        assert start_events[0]["team"] == "pact-abc12345"


# ---------------------------------------------------------------------------
# Integration: session_end journal write
# ---------------------------------------------------------------------------


class TestSessionEndJournalWrite:
    """Tests that session_end.main() writes session_end event to journal."""

    def test_writes_session_end_event(self, journal_home, monkeypatch):
        """session_end.main() writes session_end event to journal."""
        import io

        # Pre-create journal with a session_start event
        from shared.session_journal import append_event, make_event
        team = "pact-endtest12"
        append_event(make_event("session_start", team=team), team)

        input_data = {}

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("session_end.pact_context") as mock_ctx, \
             patch("session_end.get_project_dir", return_value=str(journal_home / "project")), \
             patch("session_end.get_session_dir", return_value=None), \
             patch("session_end.get_session_id", return_value="test-session-id"), \
             patch("session_end.get_team_name", return_value=team), \
             patch("session_end.get_task_list", return_value=[]):

            with pytest.raises(SystemExit) as exc:
                from session_end import main
                main()

            assert exc.value.code == 0

        # Verify session_end event was written
        from shared.session_journal import read_events

        events = read_events(team)
        end_events = [e for e in events if e["type"] == "session_end"]
        assert len(end_events) >= 1


# ---------------------------------------------------------------------------
# Integration: _extract_prev_team_name
# ---------------------------------------------------------------------------


class TestExtractPrevTeamName:
    """Tests for session_init._extract_prev_team_name()."""

    def test_extracts_team_from_claude_md(self, tmp_path):
        """Extracts team name from Current Session block."""
        from session_init import _extract_prev_team_name

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n"
            "## Current Session\n"
            "- Resume: `claude --resume abc123`\n"
            "- Team: `pact-abc12345`\n"
            "- Started: 2026-04-05\n"
        )

        result = _extract_prev_team_name(str(tmp_path))
        assert result == "pact-abc12345"

    def test_returns_none_when_no_claude_md(self, tmp_path):
        """Returns None when CLAUDE.md doesn't exist."""
        from session_init import _extract_prev_team_name

        result = _extract_prev_team_name(str(tmp_path))
        assert result is None

    def test_returns_none_when_no_team_line(self, tmp_path):
        """Returns None when CLAUDE.md has no Team line."""
        from session_init import _extract_prev_team_name

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\nNo session info here.\n")

        result = _extract_prev_team_name(str(tmp_path))
        assert result is None

    def test_returns_none_for_empty_project_dir(self):
        """Returns None when project_dir is empty string."""
        from session_init import _extract_prev_team_name

        result = _extract_prev_team_name("")
        assert result is None

    def test_returns_none_for_none_project_dir(self):
        """Returns None when project_dir is None."""
        from session_init import _extract_prev_team_name

        result = _extract_prev_team_name(None)
        assert result is None

    def test_returns_none_on_ioerror(self, tmp_path):
        """Returns None when CLAUDE.md read raises IOError (fail-open)."""
        from unittest.mock import patch as mock_patch
        from session_init import _extract_prev_team_name

        # Create CLAUDE.md so the existence check passes
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n## Current Session\n- Team: `pact-abc123`\n")

        # Patch read_text to raise IOError
        with mock_patch.object(Path, "read_text", side_effect=IOError("disk error")):
            result = _extract_prev_team_name(str(tmp_path))

        assert result is None
